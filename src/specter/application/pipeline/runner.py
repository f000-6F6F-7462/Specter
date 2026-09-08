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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from specter.application.pipeline.deps import PipelineDeps
from specter.application.pipeline.directory import ResolvedTarget, StreamDirectory
from specter.application.pipeline.emit import emit_match
from specter.application.pipeline.sampling import AdaptiveSampler, SampleOutcome
from specter.application.pipeline.stages import crops_from_tracks, filter_detections
from specter.application.ports import FrameSource
from specter.contracts import EVENTS_STREAM_STATUS, StreamStatusMessage
from specter.core.ids import new_id
from specter.domain.matching import Candidate, NofMPolicy, TrackMatchState
from specter.domain.streams import StreamConfig, StreamStatus
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


@dataclass(frozen=True, slots=True)
class StreamOutcome:
    stream_id: str
    reason: str
    metrics: StreamMetrics


@dataclass(slots=True)
class _Matcher:
    directory: StreamDirectory
    policies: dict[str, NofMPolicy]
    states: dict[_TrackKey, TrackMatchState] = field(default_factory=dict)

    def policy_for(self, resolved: ResolvedTarget | None, fallback: NofMPolicy) -> NofMPolicy:
        if resolved is None:
            return fallback
        return self.policies.get(resolved.watchlist_id, fallback)


async def run_stream(
    deps: PipelineDeps, stream: StreamConfig, *, stop: asyncio.Event
) -> StreamOutcome:
    metrics = StreamMetrics()
    await _publish(deps, stream, StreamStatus.PROVISIONING, metrics)
    directory = await StreamDirectory.load(deps.uow_factory, stream)
    matcher = _Matcher(directory=directory, policies=_policies(deps, directory))
    sampler = AdaptiveSampler(stream.sampling, motion_min_delta=deps.tuning.motion_min_delta)
    source = deps.frame_source_factory(stream)

    last_refresh = deps.clock.now()
    announced = False
    reason = "source_exhausted"
    try:
        async for frame in _shed_frames(source, sampler, deps.tuning.queue_size, stop, metrics):
            metrics.processed += 1
            if not announced:
                announced = True
                await _publish(deps, stream, StreamStatus.RUNNING, metrics)

            now = deps.clock.now()
            if now - last_refresh >= deps.tuning.directory_refresh_s:
                matcher.directory = await StreamDirectory.load(deps.uow_factory, stream)
                matcher.policies = _policies(deps, matcher.directory)
                last_refresh = now

            await _process_frame(deps, stream, matcher, frame, metrics)
        if stop.is_set():
            reason = "stopped"
    except asyncio.CancelledError:
        await _publish(deps, stream, StreamStatus.STOPPED, metrics, detail="cancelled")
        deps.tracker.forget(stream.id)
        raise
    except Exception as exc:
        log.exception("stream %s pipeline failed", stream.id)
        await _publish(deps, stream, StreamStatus.ERROR, metrics, detail=repr(exc))
        deps.tracker.forget(stream.id)
        raise

    await _publish(deps, stream, StreamStatus.STOPPED, metrics)
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            failure.append(exc)  # stashed, then re-raised from the generator body
        finally:
            done.set()

    pump = asyncio.create_task(_pump(), name="pipeline-decode")
    try:
        while not (done.is_set() and queue.empty()):
            try:
                yield await asyncio.wait_for(queue.get(), timeout=0.1)
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
    if decision.fire and resolved is not None:
        await emit_match(deps, stream, resolved, frame, track, decision, state)
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
