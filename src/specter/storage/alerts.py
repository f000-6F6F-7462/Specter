"""Reading and writing alerts and their reviews."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

from peewee import Expression, fn

from specter.core.errors import NotFoundError
from specter.entities.alerts import (
    Alert,
    AlertKind,
    AlertReview,
    Disposition,
    IdentityMatchAlert,
    RuleAlert,
)
from specter.entities.geometry import NormalizedBoundingBox
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality
from specter.storage.columns import format_utc_timestamp, parse_utc_timestamp
from specter.storage.tables import IdentityMatchAlertRecord, RuleAlertRecord

type AlertRecordType = type[IdentityMatchAlertRecord] | type[RuleAlertRecord]

RECORD_TYPE_BY_KIND: dict[AlertKind, AlertRecordType] = {
    AlertKind.IDENTITY_MATCH: IdentityMatchAlertRecord,
    AlertKind.RULE: RuleAlertRecord,
}
# Stored timestamps start with the UTC date, as YYYY-MM-DD.
DATE_TEXT_LENGTH = 10


@dataclass(frozen=True, slots=True)
class AlertPosition:
    """The last alert of a page, after which the next and older page starts."""

    created_at: datetime
    alert_id: str


@dataclass(frozen=True, slots=True)
class AlertFilter:
    """Which of an owner's alerts to list."""

    # None means every camera; an empty set matches no alert.
    camera_ids: frozenset[str] | None = None
    disposition: Disposition | None = None
    created_since: datetime | None = None
    created_until: datetime | None = None
    older_than: AlertPosition | None = None


@dataclass(frozen=True, slots=True)
class AlertCount:
    """How many alerts of one kind share a review state."""

    kind: AlertKind
    disposition: Disposition
    is_acknowledged: bool
    count: int


@dataclass(frozen=True, slots=True)
class DailyAlertCount:
    """How many alerts of one kind were raised on one UTC day."""

    day: date
    kind: AlertKind
    count: int


@dataclass(frozen=True, slots=True)
class AlertSummary:
    """Totals of the alerts that match a filter."""

    counts: list[AlertCount]
    daily_counts: list[DailyAlertCount]


def save_alert(alert: Alert) -> None:
    """Inserts the alert, unless an alert with its id was already saved."""
    match alert:
        case IdentityMatchAlert():
            IdentityMatchAlertRecord.insert(
                **_build_shared_values(alert),
                watchlist_id=alert.watchlist_id,
                target_id=alert.target_id,
                modality=alert.modality.value,
                similarity_ratio=alert.similarity_ratio,
                margin_ratio=alert.margin_ratio,
            ).on_conflict_ignore().execute()
        case RuleAlert():
            RuleAlertRecord.insert(
                **_build_shared_values(alert),
                rule_id=alert.rule_id,
                rule_kind=alert.rule_kind.value,
                zone_id=alert.zone_id,
                dwell_seconds=alert.dwell_seconds,
                crossing_direction=(
                    alert.crossing_direction.value if alert.crossing_direction else None
                ),
            ).on_conflict_ignore().execute()


def find_alert(alert_id: str) -> Alert | None:
    """Returns the alert of either kind, or None if it does not exist."""
    identity_match_record = IdentityMatchAlertRecord.get_or_none(
        IdentityMatchAlertRecord.id == alert_id
    )
    if identity_match_record is not None:
        return _build_identity_match_alert(identity_match_record)
    rule_record = RuleAlertRecord.get_or_none(RuleAlertRecord.id == alert_id)
    if rule_record is not None:
        return _build_rule_alert(rule_record)
    return None


def save_alert_review(alert_id: str, review: AlertReview) -> None:
    """Stores the latest review of an alert.

    Raises:
        NotFoundError: The alert does not exist.
    """
    review_values = {
        "disposition": review.disposition.value,
        "is_acknowledged": review.is_acknowledged,
        "note": review.note,
    }
    updated_row_count = int(
        IdentityMatchAlertRecord.update(**review_values)
        .where(IdentityMatchAlertRecord.id == alert_id)
        .execute()
    ) + int(RuleAlertRecord.update(**review_values).where(RuleAlertRecord.id == alert_id).execute())
    if updated_row_count == 0:
        raise NotFoundError(f"alert {alert_id} does not exist")


def list_identity_match_alerts(
    owner_id: str, alert_filter: AlertFilter, limit: int
) -> list[IdentityMatchAlert]:
    """Returns up to ``limit`` of the owner's identity match alerts, newest first."""
    records = (
        IdentityMatchAlertRecord.select()
        .where(*_build_filter_conditions(IdentityMatchAlertRecord, owner_id, alert_filter))
        .order_by(IdentityMatchAlertRecord.created_at.desc(), IdentityMatchAlertRecord.id.desc())
        .limit(limit)
    )
    return [_build_identity_match_alert(record) for record in records]


def list_rule_alerts(owner_id: str, alert_filter: AlertFilter, limit: int) -> list[RuleAlert]:
    """Returns up to ``limit`` of the owner's rule alerts, newest first."""
    records = (
        RuleAlertRecord.select()
        .where(*_build_filter_conditions(RuleAlertRecord, owner_id, alert_filter))
        .order_by(RuleAlertRecord.created_at.desc(), RuleAlertRecord.id.desc())
        .limit(limit)
    )
    return [_build_rule_alert(record) for record in records]


def list_alerts(
    owner_id: str, alert_filter: AlertFilter, limit: int, kind: AlertKind | None = None
) -> list[Alert]:
    """Returns up to ``limit`` of the owner's alerts of the given kind or both, newest first."""
    alerts: list[Alert] = []
    if kind in (None, AlertKind.IDENTITY_MATCH):
        alerts.extend(list_identity_match_alerts(owner_id, alert_filter, limit))
    if kind in (None, AlertKind.RULE):
        alerts.extend(list_rule_alerts(owner_id, alert_filter, limit))
    # Both tables are ordered the same way, so the newest of their pages form the merged page.
    alerts.sort(key=lambda alert: (alert.created_at, alert.id), reverse=True)
    return alerts[:limit]


def summarize_alerts(owner_id: str, alert_filter: AlertFilter) -> AlertSummary:
    """Returns totals of the owner's alerts by kind and review state, and by UTC day."""
    counts: list[AlertCount] = []
    daily_counts: list[DailyAlertCount] = []
    for kind, record_type in RECORD_TYPE_BY_KIND.items():
        conditions = _build_filter_conditions(record_type, owner_id, alert_filter)
        # peewee's stubs type every row as a record, even rows read as tuples.
        review_rows = cast(
            "list[tuple[str, bool, int]]",
            list(
                record_type.select(
                    record_type.disposition, record_type.is_acknowledged, fn.COUNT(record_type.id)
                )
                .where(*conditions)
                .group_by(record_type.disposition, record_type.is_acknowledged)
                .tuples()
            ),
        )
        counts.extend(
            AlertCount(
                kind=kind,
                disposition=Disposition(disposition),
                is_acknowledged=bool(is_acknowledged),
                count=count,
            )
            for disposition, is_acknowledged, count in review_rows
        )
        day_text = fn.SUBSTR(record_type.created_at, 1, DATE_TEXT_LENGTH)
        day_rows = cast(
            "list[tuple[str, int]]",
            list(
                record_type.select(day_text, fn.COUNT(record_type.id))
                .where(*conditions)
                .group_by(day_text)
                .tuples()
            ),
        )
        daily_counts.extend(
            DailyAlertCount(day=date.fromisoformat(day), kind=kind, count=count)
            for day, count in day_rows
        )
    daily_counts.sort(key=lambda daily_count: (daily_count.day, daily_count.kind))
    return AlertSummary(counts=counts, daily_counts=daily_counts)


def clear_snapshot_paths_under(day_directory: str) -> int:
    """Forgets the snapshots that were stored in a directory removed by evidence retention.

    Returns how many alerts changed.
    """
    path_prefix = f"{day_directory.rstrip('/')}/"
    cleared_identity_match_count = int(
        IdentityMatchAlertRecord.update(snapshot_path=None)
        .where(IdentityMatchAlertRecord.snapshot_path.startswith(path_prefix))
        .execute()
    )
    cleared_rule_count = int(
        RuleAlertRecord.update(snapshot_path=None)
        .where(RuleAlertRecord.snapshot_path.startswith(path_prefix))
        .execute()
    )
    return cleared_identity_match_count + cleared_rule_count


def _build_filter_conditions(
    record_type: AlertRecordType, owner_id: str, alert_filter: AlertFilter
) -> list[Expression]:
    conditions = [record_type.owner_id == owner_id]
    if alert_filter.camera_ids is not None:
        conditions.append(record_type.camera_id.in_(sorted(alert_filter.camera_ids)))
    if alert_filter.disposition is not None:
        conditions.append(record_type.disposition == alert_filter.disposition.value)
    if alert_filter.created_since is not None:
        conditions.append(
            record_type.created_at >= format_utc_timestamp(alert_filter.created_since)
        )
    if alert_filter.created_until is not None:
        conditions.append(
            record_type.created_at <= format_utc_timestamp(alert_filter.created_until)
        )
    if alert_filter.older_than is not None:
        position_created_at = format_utc_timestamp(alert_filter.older_than.created_at)
        conditions.append(
            (record_type.created_at < position_created_at)
            | (
                (record_type.created_at == position_created_at)
                & (record_type.id < alert_filter.older_than.alert_id)
            )
        )
    return conditions


def _build_shared_values(alert: Alert) -> dict[str, object]:
    return {
        "id": alert.id,
        "owner_id": alert.owner_id,
        "camera_id": alert.camera_id,
        "track_id": alert.track_id,
        "object_class": alert.object_class,
        "bounding_box_x": alert.bounding_box.x,
        "bounding_box_y": alert.bounding_box.y,
        "bounding_box_width": alert.bounding_box.width,
        "bounding_box_height": alert.bounding_box.height,
        "frame_captured_at": format_utc_timestamp(alert.frame_captured_at),
        "created_at": format_utc_timestamp(alert.created_at),
        "snapshot_path": alert.snapshot_path,
        "disposition": alert.review.disposition.value,
        "is_acknowledged": alert.review.is_acknowledged,
        "note": alert.review.note,
    }


def _build_identity_match_alert(record: IdentityMatchAlertRecord) -> IdentityMatchAlert:
    return IdentityMatchAlert(
        id=record.id,
        owner_id=record.owner_id,
        camera_id=record.camera_id,
        track_id=record.track_id,
        watchlist_id=record.watchlist_id,
        target_id=record.target_id,
        modality=EmbeddingModality(record.modality),
        similarity_ratio=record.similarity_ratio,
        margin_ratio=record.margin_ratio,
        object_class=record.object_class,
        bounding_box=NormalizedBoundingBox(
            x=record.bounding_box_x,
            y=record.bounding_box_y,
            width=record.bounding_box_width,
            height=record.bounding_box_height,
        ),
        frame_captured_at=parse_utc_timestamp(record.frame_captured_at),
        created_at=parse_utc_timestamp(record.created_at),
        snapshot_path=record.snapshot_path,
        review=AlertReview(
            disposition=Disposition(record.disposition),
            is_acknowledged=record.is_acknowledged,
            note=record.note,
        ),
    )


def _build_rule_alert(record: RuleAlertRecord) -> RuleAlert:
    return RuleAlert(
        id=record.id,
        owner_id=record.owner_id,
        camera_id=record.camera_id,
        track_id=record.track_id,
        rule_id=record.rule_id,
        rule_kind=RuleKind(record.rule_kind),
        zone_id=record.zone_id,
        object_class=record.object_class,
        bounding_box=NormalizedBoundingBox(
            x=record.bounding_box_x,
            y=record.bounding_box_y,
            width=record.bounding_box_width,
            height=record.bounding_box_height,
        ),
        dwell_seconds=record.dwell_seconds,
        crossing_direction=(
            CrossingDirection(record.crossing_direction) if record.crossing_direction else None
        ),
        frame_captured_at=parse_utc_timestamp(record.frame_captured_at),
        created_at=parse_utc_timestamp(record.created_at),
        snapshot_path=record.snapshot_path,
        review=AlertReview(
            disposition=Disposition(record.disposition),
            is_acknowledged=record.is_acknowledged,
            note=record.note,
        ),
    )
