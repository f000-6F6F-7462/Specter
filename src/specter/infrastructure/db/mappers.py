"""Translation between ORM rows and domain objects."""

from dataclasses import asdict
from typing import Any

from specter.domain.alerts import Alert, Disposition, MatchEvidence
from specter.domain.catalog import (
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
    WatchlistKind,
)
from specter.domain.quality import QualityReport, RejectionReason
from specter.domain.streams import (
    DesiredState,
    RegionOfInterest,
    SamplingConfig,
    SamplingMode,
    StreamConfig,
    StreamCredentials,
    StreamProtocol,
    StreamSource,
    TransportProtocol,
)
from specter.domain.vision import BBox
from specter.infrastructure.db.models import (
    AlertRow,
    ReferenceImageRow,
    StreamRow,
    TargetRow,
    WatchlistRow,
)

# watchlist


def watchlist_to_domain(row: WatchlistRow) -> Watchlist:
    return Watchlist(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        type=TargetType(row.type),
        kind=WatchlistKind(row.kind),
        match_threshold=row.match_threshold,
    )


def watchlist_to_row(wl: Watchlist) -> WatchlistRow:
    return WatchlistRow(
        id=wl.id,
        owner_id=wl.owner_id,
        name=wl.name,
        type=wl.type.value,
        kind=wl.kind.value,
        match_threshold=wl.match_threshold,
    )


def apply_watchlist(row: WatchlistRow, wl: Watchlist) -> None:
    row.name = wl.name
    row.kind = wl.kind.value
    row.match_threshold = wl.match_threshold


# target


def _quality_to_json(q: QualityReport | None) -> dict[str, Any] | None:
    return None if q is None else asdict(q)


def _quality_from_json(data: dict[str, Any] | None) -> QualityReport | None:
    return None if not data else QualityReport(**data)


def reference_image_to_domain(row: ReferenceImageRow) -> ReferenceImage:
    return ReferenceImage(
        id=row.id,
        blob_key=row.blob_key,
        status=ImageStatus(row.status),
        quality=_quality_from_json(row.quality),
        rejection_reason=(RejectionReason(row.rejection_reason) if row.rejection_reason else None),
        model_version=row.model_version,
    )


def reference_image_to_row(img: ReferenceImage) -> ReferenceImageRow:
    return ReferenceImageRow(
        id=img.id,
        blob_key=img.blob_key,
        status=img.status.value,
        quality=_quality_to_json(img.quality),
        rejection_reason=img.rejection_reason.value if img.rejection_reason else None,
        model_version=img.model_version,
    )


def apply_reference_image(row: ReferenceImageRow, img: ReferenceImage) -> None:
    row.blob_key = img.blob_key
    row.status = img.status.value
    row.quality = _quality_to_json(img.quality)
    row.rejection_reason = img.rejection_reason.value if img.rejection_reason else None
    row.model_version = img.model_version


def target_to_domain(row: TargetRow) -> Target:
    return Target(
        id=row.id,
        watchlist_id=row.watchlist_id,
        label=row.label,
        type=TargetType(row.type),
        images=[reference_image_to_domain(i) for i in row.images],
        enabled=row.enabled,
        metadata=dict(row.meta or {}),
        batch_id=row.batch_id,
    )


def target_to_row(target: Target) -> TargetRow:
    row = TargetRow(
        id=target.id,
        watchlist_id=target.watchlist_id,
        label=target.label,
        type=target.type.value,
        enabled=target.enabled,
        meta=dict(target.metadata),
        batch_id=target.batch_id,
    )
    row.images = [reference_image_to_row(i) for i in target.images]
    return row


def sync_target(row: TargetRow, target: Target) -> None:
    """Copy mutable fields and reconcile the image collection in place."""
    row.label = target.label
    row.enabled = target.enabled
    row.meta = dict(target.metadata)

    existing = {i.id: i for i in row.images}
    incoming = {i.id: i for i in target.images}
    for stale_id in existing.keys() - incoming.keys():
        row.images.remove(existing[stale_id])
    for img in target.images:
        current = existing.get(img.id)
        if current is None:
            row.images.append(reference_image_to_row(img))
        else:
            apply_reference_image(current, img)


# stream


def stream_to_domain(row: StreamRow) -> StreamConfig:
    creds = StreamCredentials(**row.credentials) if row.credentials else None
    source = StreamSource(
        protocol=StreamProtocol(row.protocol),
        url=row.url,
        credentials=creds,
        transport=TransportProtocol(row.transport),
    )
    sampling = SamplingConfig(
        mode=SamplingMode(row.sampling.get("mode", "adaptive")),
        target_fps=row.sampling.get("target_fps", 10.0),
        min_fps=row.sampling.get("min_fps", 3.0),
        motion_gating=row.sampling.get("motion_gating", True),
    )
    return StreamConfig(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        source=source,
        camera_id=row.camera_id,
        watchlist_ids=list(row.watchlist_ids or []),
        sampling=sampling,
        roi=[RegionOfInterest(**r) for r in (row.roi or [])],
        detect_classes=list(row.detect_classes or []),
        enabled=row.enabled,
        desired_state=DesiredState(row.desired_state),
    )


def _stream_columns(cfg: StreamConfig) -> dict[str, Any]:
    return {
        "owner_id": cfg.owner_id,
        "name": cfg.name,
        "camera_id": cfg.camera_id,
        "protocol": cfg.source.protocol.value,
        "url": cfg.source.url,
        "transport": cfg.source.transport.value,
        "credentials": (asdict(cfg.source.credentials) if cfg.source.credentials else None),
        "sampling": {
            "mode": cfg.sampling.mode.value,
            "target_fps": cfg.sampling.target_fps,
            "min_fps": cfg.sampling.min_fps,
            "motion_gating": cfg.sampling.motion_gating,
        },
        "roi": [asdict(r) for r in cfg.roi],
        "watchlist_ids": list(cfg.watchlist_ids),
        "detect_classes": list(cfg.detect_classes),
        "enabled": cfg.enabled,
        "desired_state": cfg.desired_state.value,
    }


def stream_to_row(cfg: StreamConfig) -> StreamRow:
    return StreamRow(id=cfg.id, **_stream_columns(cfg))


def apply_stream(row: StreamRow, cfg: StreamConfig) -> None:
    for key, value in _stream_columns(cfg).items():
        setattr(row, key, value)


# alert


def _bbox_to_json(box: BBox) -> dict[str, int]:
    return {"x": box.x, "y": box.y, "w": box.w, "h": box.h}


def _bbox_from_json(data: dict[str, int]) -> BBox:
    return BBox(x=data["x"], y=data["y"], w=data["w"], h=data["h"])


def alert_to_domain(row: AlertRow) -> Alert:
    return Alert(
        id=row.id,
        owner_id=row.owner_id,
        stream_id=row.stream_id,
        watchlist_id=row.watchlist_id,
        target_id=row.target_id,
        similarity=row.similarity,
        bbox=_bbox_from_json(row.bbox),
        track_id=row.track_id,
        frame_ts=row.frame_ts,
        created_at=row.created_at,
        calibrated_confidence=row.calibrated_confidence,
        evidence=MatchEvidence(row.snapshot_key, row.crop_key, row.clip_key),
        disposition=Disposition(row.disposition),
        acknowledged=row.acknowledged,
        note=row.note,
    )


def alert_to_row(alert: Alert) -> AlertRow:
    return AlertRow(
        id=alert.id,
        owner_id=alert.owner_id,
        stream_id=alert.stream_id,
        watchlist_id=alert.watchlist_id,
        target_id=alert.target_id,
        similarity=alert.similarity,
        calibrated_confidence=alert.calibrated_confidence,
        bbox=_bbox_to_json(alert.bbox),
        track_id=alert.track_id,
        frame_ts=alert.frame_ts,
        snapshot_key=alert.evidence.snapshot_key,
        crop_key=alert.evidence.crop_key,
        clip_key=alert.evidence.clip_key,
        disposition=alert.disposition.value,
        acknowledged=alert.acknowledged,
        note=alert.note,
        created_at=alert.created_at,
    )


def apply_alert(row: AlertRow, alert: Alert) -> None:
    row.disposition = alert.disposition.value
    row.acknowledged = alert.acknowledged
    row.note = alert.note
    row.calibrated_confidence = alert.calibrated_confidence
