"""``run_stream`` — the per-stream processing graph.

    frames ─▶ sampler ─▶ [bounded queue: overflow dropped] ─▶ detect ─▶ track
          ─▶ crop ─▶ embed ─▶ vector search ─▶ N-of-M policy ─▶ emit

One call runs until ``stop`` is set, the source ends, or something raises. Decode runs
in its own task so a slow inference pass sheds frames at the queue instead of stalling
the connection. The supervisor owns restarts.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from specter.application.pipeline.deps import PipelineDeps
from specter.application.pipeline.directory import ResolvedTarget, StreamDirectory
from specter.application.pipeline.emit import emit_match
from specter.application.pipeline.sampling import AdaptiveSampler, SampleOutcome
from specter.application.pipeline.stages import crops_from_tracks, filter_detections
from specter.application.ports import FrameSource
from specter.contracts import EVENTS_STREAM_STATUS, StreamStatusMessage
from specter.core.clock import Clock
from specter.core.ids import new_id
from specter.domain.matching import Candidate, NofMPolicy, TrackMatchState
from specter.domain.streams import StreamConfig, StreamHealth, StreamStatus, preview_key
from specter.domain.vision import Frame, Track

log = logging.getLogger(__name__)

_TrackKey = tuple[int, str]


@dataclass(slots=True)
class StreamMetrics:
    received: int = 0
    dropped: int = 0
    processed: int = 0
    searches: int = 0
    matches: int = 0
    queue_depth: int = 0
    """Current depth of the decode->inference hand-off queue."""


@dataclass(frozen=True, slots=True)
class StreamOutcome:
    stream_id: str
    reason: str
    metrics: StreamMetrics


@dataclass(slots=True)
class _Matcher:
    """The resolved matching context plus everything needed to know when it's stale.

    A full reload (``directory_refresh_s``) always wins the tie; otherwise a much
    cheaper watchlist-version check piggybacks on the health-publish tick, so a
    target/watchlist edit reaches a running stream in about
    ``health_publish_interval_s`` instead of waiting out the far longer
    ``directory_refresh_s``.
    """

    directory: StreamDirectory
    policies: dict[str, NofMPolicy]
    states: dict[_TrackKey, TrackMatchState] = field(default_factory=dict)
    _watchlist_versions: dict[str, int] = field(default_factory=dict)
    _last_refresh: float = 0.0

    def policy_for(self, resolved: ResolvedTarget | None, fallback: NofMPolicy) -> NofMPolicy:
        if resolved is None:
            return fallback
        return self.policies.get(resolved.watchlist_id, fallback)

    @classmethod
    async def load(cls, deps: PipelineDeps, stream: StreamConfig) -> "_Matcher":
        directory = await StreamDirectory.load(deps.uow_factory, stream)
        matcher = cls(directory=directory, policies=_policies(deps, directory))
        matcher._watchlist_versions = await _snapshot_versions(deps, directory)
        matcher._last_refresh = deps.clock.now()
        return matcher

    async def maybe_refresh(
        self, deps: PipelineDeps, stream: StreamConfig, vitals: "_HealthTracker"
    ) -> None:
        now = deps.clock.now()
        stale = now - self._last_refresh >= deps.tuning.directory_refresh_s
        if not stale and vitals.due(deps.tuning.health_publish_interval_s):
            stale = await _watchlists_changed(deps, self._watchlist_versions)
        if not stale:
            return
        self.directory = await StreamDirectory.load(deps.uow_factory, stream)
        self.policies = _policies(deps, self.directory)
        self._watchlist_versions = await _snapshot_versions(deps, self.directory)
        self._last_refresh = now


class _HealthTracker:
    """Turns running metrics into periodic ``StreamHealth`` snapshots.

    Rates (``fps_in``/``fps_processed``/``frames_dropped_pct``) are windowed — computed
    from the delta since the previous snapshot, not the stream's lifetime average — so a
    stream that struggled earlier and has since recovered reports as healthy *now*.
    """

    def __init__(self, clock: Clock, *, reconnect_count: int = 0, window: int = 200) -> None:
        self._clock = clock
        self._reconnect_count = reconnect_count
        self._latencies_ms: deque[float] = deque(maxlen=window)
        self.last_frame_at: datetime | None = None
        self._prev_received = 0
        self._prev_processed = 0
        self._prev_dropped = 0
        self._last_publish_at = clock.now()

    def record_frame(self, frame: Frame, latency_ms: float) -> None:
        self.last_frame_at = frame.captured_at or self._clock.wall()
        self._latencies_ms.append(latency_ms)

    def due(self, interval_s: float) -> bool:
        return self._clock.now() - self._last_publish_at >= interval_s

    def snapshot(
        self, status: StreamStatus, metrics: StreamMetrics, *, last_error: str | None = None
    ) -> StreamHealth:
        now = self._clock.now()
        elapsed = max(now - self._last_publish_at, 1e-6)
        received_delta = metrics.received - self._prev_received
        dropped_delta = metrics.dropped - self._prev_dropped
        health = StreamHealth(
            status=status,
            fps_in=max(received_delta / elapsed, 0.0),
            fps_processed=max((metrics.processed - self._prev_processed) / elapsed, 0.0),
            frames_dropped_pct=(dropped_delta / received_delta * 100.0) if received_delta else 0.0,
            last_frame_at=self.last_frame_at,
            reconnect_count=self._reconnect_count,
            inference_p95_ms=_p95(self._latencies_ms),
            queue_depth={"decode": metrics.queue_depth},
            last_error=last_error,
        )
        self._prev_received = metrics.received
        self._prev_processed = metrics.processed
        self._prev_dropped = metrics.dropped
        self._last_publish_at = now
        return health


def _p95(samples: deque[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


async def run_stream(
    deps: PipelineDeps, stream: StreamConfig, *, stop: asyncio.Event, reconnect_count: int = 0
) -> StreamOutcome:
    metrics = StreamMetrics()
    vitals = _HealthTracker(deps.clock, reconnect_count=reconnect_count)
    await _publish(deps, stream, StreamStatus.PROVISIONING, metrics)
    await _write_health(deps, stream, StreamStatus.PROVISIONING, metrics, vitals)
    matcher = await _Matcher.load(deps, stream)
    sampler = AdaptiveSampler(
        stream.sampling,
        motion_min_delta=deps.tuning.motion_min_delta,
        decrease_factor=deps.tuning.aimd_decrease_factor,
        increase_fps=deps.tuning.aimd_increase_fps,
    )
    source = deps.frame_source_factory(stream)

    announced = False
    current_status = StreamStatus.PROVISIONING
    reason = "source_exhausted"
    try:
        async for frame in _shed_frames(source, sampler, deps.tuning.queue_size, stop, metrics):
            metrics.processed += 1
            if not announced:
                announced = True
                current_status = StreamStatus.RUNNING
                await _publish(deps, stream, StreamStatus.RUNNING, metrics)

            await matcher.maybe_refresh(deps, stream, vitals)

            t0 = deps.clock.now()
            await _process_frame(deps, stream, matcher, frame, metrics)
            vitals.record_frame(frame, (deps.clock.now() - t0) * 1000.0)

            if vitals.due(deps.tuning.health_publish_interval_s):
                health = await _write_health(deps, stream, current_status, metrics, vitals)
                sampler.adjust_for_latency(health.inference_p95_ms)
                await _write_preview(deps, stream, frame)
        if stop.is_set():
            reason = "stopped"
    except asyncio.CancelledError:
        await _publish(deps, stream, StreamStatus.STOPPED, metrics, detail="cancelled")
        await _write_health(
            deps, stream, StreamStatus.STOPPED, metrics, vitals, last_error="cancelled"
        )
        deps.tracker.forget(stream.id)
        raise
    except Exception as exc:
        log.exception("stream %s pipeline failed", stream.id)
        await _publish(deps, stream, StreamStatus.ERROR, metrics, detail=repr(exc))
        await _write_health(deps, stream, StreamStatus.ERROR, metrics, vitals, last_error=repr(exc))
        deps.tracker.forget(stream.id)
        raise

    await _publish(deps, stream, StreamStatus.STOPPED, metrics)
    await _write_health(deps, stream, StreamStatus.STOPPED, metrics, vitals)
    deps.tracker.forget(stream.id)
    return StreamOutcome(stream_id=stream.id, reason=reason, metrics=metrics)


async def _shed_frames(
    source: FrameSource,
    sampler: AdaptiveSampler,
    queue_size: int,
    stop: asyncio.Event,
    metrics: StreamMetrics,
) -> AsyncIterator[Frame]:
    queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=queue_size)
    done = asyncio.Event()
    failure: list[Exception] = []

    async def _pump() -> None:
        try:
            async for frame in source:
                if stop.is_set():
                    break
                metrics.received += 1
                if sampler.classify(frame) is not SampleOutcome.PROCESS:
                    metrics.dropped += 1
                    continue
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    metrics.dropped += 1
                metrics.queue_depth = queue.qsize()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            failure.append(exc)  # stashed, then re-raised from the generator body
        finally:
            done.set()

    pump = asyncio.create_task(_pump(), name="pipeline-decode")
    try:
        while not (done.is_set() and queue.empty()):
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.1)
                metrics.queue_depth = queue.qsize()
                yield frame
            except TimeoutError:
                if stop.is_set() and queue.empty():
                    break
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        await source.aclose()
    if failure:
        raise failure[0]


async def _process_frame(
    deps: PipelineDeps,
    stream: StreamConfig,
    matcher: _Matcher,
    frame: Frame,
    metrics: StreamMetrics,
) -> None:
    detections = filter_detections(
        (await deps.detector.detect([frame]))[0],
        stream,
        frame,
        min_confidence=deps.tuning.min_detection_confidence,
    )
    tracks = deps.tracker.update(stream.id, detections)
    if not tracks or not matcher.directory.has_targets:
        return

    for modality, embedder in deps.embedders.items():
        pairs = crops_from_tracks(frame, tracks, modality=modality)
        if not pairs:
            continue
        embeddings = await embedder.embed([crop for crop, _ in pairs])
        for (_crop, track), embedding in zip(pairs, embeddings, strict=True):
            candidates = await deps.vectors.search(
                modality,
                embedding.vector,
                owner_id=stream.owner_id,
                watchlist_ids=matcher.directory.watchlist_ids,
                top_k=deps.tuning.top_k,
            )
            metrics.searches += 1
            await _decide(deps, stream, matcher, frame, track, modality, candidates, metrics)


async def _decide(
    deps: PipelineDeps,
    stream: StreamConfig,
    matcher: _Matcher,
    frame: Frame,
    track: Track,
    modality: str,
    candidates: list[Candidate],
    metrics: StreamMetrics,
) -> None:
    hit, resolved = _first_resolved(candidates, matcher.directory)
    state = matcher.states.setdefault((track.track_id, modality), TrackMatchState())
    policy = matcher.policy_for(resolved, _fallback_policy(deps))
    decision = policy.evaluate(state, hit, deps.clock.now())
    log.debug(
        "stream %s track %s [%s]: best_similarity=%.3f threshold=%.3f fire=%s "
        "(closest candidate: %s)",
        stream.id,
        track.track_id,
        modality,
        decision.similarity,
        policy.threshold,
        decision.fire,
        candidates[0].target_id if candidates else "none",
    )
    if not (decision.fire and resolved is not None):
        return

    # The in-process NofMPolicy cooldown (above) is enough within one run_stream call,
    # but a supervisor restart wipes it along with everything else in `matcher.states`.
    # This KV check is the belt-and-suspenders backstop that survives that restart.
    cooldown_key = f"{stream.id}:{track.track_id}:{resolved.target_id}"
    if await deps.health.in_cooldown(cooldown_key):
        return

    await emit_match(deps, stream, resolved, frame, track, decision, state)
    await deps.health.mark_cooldown(cooldown_key, ttl_s=int(deps.tuning.cooldown_s))
    metrics.matches += 1


def _first_resolved(
    candidates: list[Candidate], directory: StreamDirectory
) -> tuple[Candidate | None, ResolvedTarget | None]:
    for candidate in candidates:  # index returns them best-first
        resolved = directory.resolve(candidate.target_id)
        if resolved is not None:
            return candidate, resolved
    return None, None


def _fallback_policy(deps: PipelineDeps) -> NofMPolicy:
    return NofMPolicy(
        threshold=deps.tuning.default_threshold,
        need=deps.tuning.need,
        window=deps.tuning.window,
        ema_alpha=deps.tuning.ema_alpha,
        cooldown_s=deps.tuning.cooldown_s,
    )


def _policies(deps: PipelineDeps, directory: StreamDirectory) -> dict[str, NofMPolicy]:
    return directory.policies(
        need=deps.tuning.need,
        window=deps.tuning.window,
        ema_alpha=deps.tuning.ema_alpha,
        cooldown_s=deps.tuning.cooldown_s,
    )


async def _publish(
    deps: PipelineDeps,
    stream: StreamConfig,
    status: StreamStatus,
    metrics: StreamMetrics,
    *,
    detail: str | None = None,
) -> None:
    message = StreamStatusMessage(
        event_id=new_id("evt"),
        occurred_at=deps.clock.wall(),
        owner_id=stream.owner_id,
        stream_id=stream.id,
        status=status.value,
        detail=detail
        or (
            f"received={metrics.received} processed={metrics.processed} "
            f"dropped={metrics.dropped} matches={metrics.matches}"
        ),
    )
    await deps.bus.publish(EVENTS_STREAM_STATUS, stream.id, message)


async def _write_health(
    deps: PipelineDeps,
    stream: StreamConfig,
    status: StreamStatus,
    metrics: StreamMetrics,
    vitals: _HealthTracker,
    *,
    last_error: str | None = None,
) -> StreamHealth:
    health = vitals.snapshot(status, metrics, last_error=last_error)
    await deps.health.set_health(stream.id, health, ttl_s=deps.tuning.health_ttl_s)
    return health


async def _write_preview(deps: PipelineDeps, stream: StreamConfig, frame: Frame) -> None:
    """Overwrite the single ``preview_key`` blob with the latest frame, on the same
    cadence as the health tick — `GET /streams/{id}/preview` reads it back presigned."""
    key = preview_key(stream.owner_id, stream.id, deps.codec.extension)
    await deps.blob.put(key, deps.codec.encode(frame.image), deps.codec.content_type)


async def _snapshot_versions(deps: PipelineDeps, directory: StreamDirectory) -> dict[str, int]:
    return {wl: await deps.health.get_watchlist_version(wl) for wl in directory.watchlist_ids}


async def _watchlists_changed(deps: PipelineDeps, seen: dict[str, int]) -> bool:
    for watchlist_id, version in seen.items():
        if await deps.health.get_watchlist_version(watchlist_id) != version:
            return True
    return False
