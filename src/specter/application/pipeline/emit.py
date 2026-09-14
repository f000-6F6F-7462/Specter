"""Turn a fired match decision into a persisted ``Alert`` and a published
``MatchEventMessage``.

The domain builds the rich in-process ``MatchEvent``; this module is the only place that
knows the wire contract and the blob store.
"""

import numpy as np

from specter.application.pipeline.deps import PipelineDeps
from specter.application.pipeline.directory import ResolvedTarget
from specter.contracts import (
    EVENTS_MATCH,
    BBoxModel,
    Dedup,
    DetectionInfo,
    Evidence,
    MatchEventMessage,
    MatchInfo,
    StreamRef,
)
from specter.core.ids import new_id
from specter.domain.alerts import Alert, MatchEvent, MatchEvidence
from specter.domain.matching import MatchDecision, TrackMatchState
from specter.domain.streams import StreamConfig
from specter.domain.vision import BBox, Frame, Track


async def emit_match(
    deps: PipelineDeps,
    stream: StreamConfig,
    resolved: ResolvedTarget,
    frame: Frame,
    track: Track,
    decision: MatchDecision,
    state: TrackMatchState,
) -> MatchEvent:
    now = deps.clock.wall()
    event_id = new_id("evt")
    evidence = (
        await _capture_evidence(deps, stream, event_id, frame, track)
        if deps.tuning.capture_evidence
        else MatchEvidence()
    )
    hit_count = sum(1 for s in state.recent if s >= resolved.threshold)
    target_fps = max(stream.sampling.target_fps, 1e-6)

    event = MatchEvent(
        event_id=event_id,
        owner_id=stream.owner_id,
        occurred_at=now,
        stream_id=stream.id,
        stream_name=stream.name,
        camera_id=stream.camera_id,
        watchlist_id=resolved.watchlist_id,
        watchlist_name=resolved.watchlist_name,
        watchlist_kind=resolved.watchlist_kind,
        target_id=resolved.target_id,
        target_label=resolved.label,
        target_type=resolved.target_type,
        similarity=_clamp01(decision.similarity),
        threshold=_clamp01(resolved.threshold),
        detection_class=track.detection.cls,
        detection_confidence=_clamp01(track.detection.confidence),
        bbox=track.detection.bbox,
        frame_ts=frame.captured_at or now,
        frame_id=frame.seq,
        track_id=track.track_id,
        correlation_id=f"{stream.id}:{track.track_id}",
        hit_count=max(hit_count, 1),
        window_ms=int(len(state.recent) / target_fps * 1000),
        first_seen_at=now,
        evidence=evidence,
    )

    async with deps.uow_factory() as uow:
        await uow.alerts.add(Alert.from_event(event))
    await deps.bus.publish(EVENTS_MATCH, resolved.target_id, await _to_message(deps, event))
    return event


async def _capture_evidence(
    deps: PipelineDeps, stream: StreamConfig, event_id: str, frame: Frame, track: Track
) -> MatchEvidence:
    codec = deps.codec
    base = f"evidence/{stream.owner_id}/{event_id}"
    snapshot_key = f"{base}/snapshot{codec.extension}"
    crop_key = f"{base}/crop{codec.extension}"
    box = track.detection.bbox.clip(frame.image.shape[1], frame.image.shape[0])
    patch = np.ascontiguousarray(frame.image[box.y : box.y + box.h, box.x : box.x + box.w])
    await deps.blob.put(snapshot_key, codec.encode(frame.image), codec.content_type)
    await deps.blob.put(crop_key, codec.encode(patch), codec.content_type)
    return MatchEvidence(snapshot_key=snapshot_key, crop_key=crop_key)


async def _to_message(deps: PipelineDeps, event: MatchEvent) -> MatchEventMessage:
    ev = event.evidence
    return MatchEventMessage(
        event_id=event.event_id,
        occurred_at=event.occurred_at,
        owner_id=event.owner_id,
        stream=StreamRef(id=event.stream_id, name=event.stream_name, camera_id=event.camera_id),
        match=MatchInfo(
            watchlist_id=event.watchlist_id,
            watchlist_name=event.watchlist_name,
            kind=event.watchlist_kind,
            target_id=event.target_id,
            target_label=event.target_label,
            target_type=event.target_type,
            similarity=event.similarity,
            threshold=event.threshold,
            calibrated_confidence=event.calibrated_confidence,
        ),
        detection=DetectionInfo(
            object_class=event.detection_class,
            confidence=event.detection_confidence,
            bbox=_bbox_model(event.bbox),
            frame_ts=event.frame_ts,
            frame_id=event.frame_id,
            track_id=event.track_id,
        ),
        evidence=Evidence(
            snapshot_url=await _url(deps, ev.snapshot_key),
            crop_url=await _url(deps, ev.crop_key),
            clip_url=await _url(deps, ev.clip_key),
        ),
        dedup=Dedup(
            correlation_id=event.correlation_id,
            hit_count=event.hit_count,
            window_ms=event.window_ms,
            first_seen_at=event.first_seen_at,
        ),
    )


async def _url(deps: PipelineDeps, key: str | None) -> str | None:
    if key is None:
        return None
    return await deps.blob.presigned_url(key, ttl_s=deps.tuning.evidence_ttl_s)


def _bbox_model(box: BBox) -> BBoxModel:
    return BBoxModel(x=box.x, y=box.y, w=box.w, h=box.h, norm=False)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
