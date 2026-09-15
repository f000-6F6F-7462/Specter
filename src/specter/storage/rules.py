"""Reading and writing zone occupancy and line crossing rules."""

from datetime import UTC, datetime

from peewee import IntegrityError

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.geometry import NormalizedPoint
from specter.entities.rules import CrossingDirection, LineCrossingRule, Rule, ZoneOccupancyRule
from specter.storage.columns import dump_json, format_utc_timestamp, load_json
from specter.storage.queries import delete_rows
from specter.storage.tables import (
    LineCrossingRuleRecord,
    ZoneOccupancyRuleRecord,
    ZoneRecord,
)


def save_rule(rule: Rule) -> None:
    """Inserts the rule, or replaces its stored fields if it already exists.

    Raises:
        InvalidEntityError: The rule's zone or camera does not exist.
    """
    try:
        match rule:
            case ZoneOccupancyRule():
                _save_zone_occupancy_rule(rule)
            case LineCrossingRule():
                _save_line_crossing_rule(rule)
    except IntegrityError as error:
        raise InvalidEntityError(
            f"rule {rule.id} refers to a zone or camera that does not exist"
        ) from error


def find_rule(rule_id: str) -> Rule | None:
    """Returns the rule of either kind, or None if it does not exist."""
    zone_occupancy_record = ZoneOccupancyRuleRecord.get_or_none(
        ZoneOccupancyRuleRecord.id == rule_id
    )
    if zone_occupancy_record is not None:
        return _build_zone_occupancy_rule(zone_occupancy_record)
    line_crossing_record = LineCrossingRuleRecord.get_or_none(LineCrossingRuleRecord.id == rule_id)
    if line_crossing_record is not None:
        return _build_line_crossing_rule(line_crossing_record)
    return None


def get_rule(rule_id: str) -> Rule:
    """Returns the rule of either kind.

    Raises:
        NotFoundError: The rule does not exist.
    """
    rule = find_rule(rule_id)
    if rule is None:
        raise NotFoundError(f"rule {rule_id} does not exist")
    return rule


def list_camera_rules(camera_id: str) -> list[Rule]:
    """Returns the camera's rules: occupancy rules of its zones first, then its line rules."""
    zone_occupancy_records = (
        ZoneOccupancyRuleRecord.select()
        .join(ZoneRecord, on=(ZoneOccupancyRuleRecord.zone_id == ZoneRecord.id))
        .where(ZoneRecord.camera_id == camera_id)
        .order_by(ZoneOccupancyRuleRecord.created_at)
    )
    line_crossing_records = (
        LineCrossingRuleRecord.select()
        .where(LineCrossingRuleRecord.camera_id == camera_id)
        .order_by(LineCrossingRuleRecord.created_at)
    )
    return [
        *(_build_zone_occupancy_rule(record) for record in zone_occupancy_records),
        *(_build_line_crossing_rule(record) for record in line_crossing_records),
    ]


def delete_rule(rule_id: str) -> None:
    """Deletes the rule of either kind.

    Raises:
        NotFoundError: The rule does not exist.
    """
    deleted_row_count = delete_rows(
        ZoneOccupancyRuleRecord, ZoneOccupancyRuleRecord.id == rule_id
    ) + delete_rows(LineCrossingRuleRecord, LineCrossingRuleRecord.id == rule_id)
    if deleted_row_count == 0:
        raise NotFoundError(f"rule {rule_id} does not exist")


def _save_zone_occupancy_rule(rule: ZoneOccupancyRule) -> None:
    record_values = {
        "zone_id": rule.zone_id,
        "object_classes_json": dump_json(sorted(rule.object_classes)),
        "minimum_dwell_seconds": rule.minimum_dwell_seconds,
        "is_enabled": rule.is_enabled,
    }
    updated_row_count = (
        ZoneOccupancyRuleRecord.update(**record_values)
        .where(ZoneOccupancyRuleRecord.id == rule.id)
        .execute()
    )
    if updated_row_count == 0:
        ZoneOccupancyRuleRecord.insert(
            id=rule.id, created_at=format_utc_timestamp(datetime.now(UTC)), **record_values
        ).execute()


def _save_line_crossing_rule(rule: LineCrossingRule) -> None:
    record_values = {
        "camera_id": rule.camera_id,
        "line_start_x": rule.line_start.x,
        "line_start_y": rule.line_start.y,
        "line_end_x": rule.line_end.x,
        "line_end_y": rule.line_end.y,
        "direction": rule.direction.value,
        "object_classes_json": dump_json(sorted(rule.object_classes)),
        "is_enabled": rule.is_enabled,
    }
    updated_row_count = (
        LineCrossingRuleRecord.update(**record_values)
        .where(LineCrossingRuleRecord.id == rule.id)
        .execute()
    )
    if updated_row_count == 0:
        LineCrossingRuleRecord.insert(
            id=rule.id, created_at=format_utc_timestamp(datetime.now(UTC)), **record_values
        ).execute()


def _build_zone_occupancy_rule(record: ZoneOccupancyRuleRecord) -> ZoneOccupancyRule:
    return ZoneOccupancyRule(
        id=record.id,
        zone_id=record.zone_id,
        object_classes=frozenset(load_json(record.object_classes_json)),
        minimum_dwell_seconds=record.minimum_dwell_seconds,
        is_enabled=record.is_enabled,
    )


def _build_line_crossing_rule(record: LineCrossingRuleRecord) -> LineCrossingRule:
    return LineCrossingRule(
        id=record.id,
        camera_id=record.camera_id,
        line_start=NormalizedPoint(x=record.line_start_x, y=record.line_start_y),
        line_end=NormalizedPoint(x=record.line_end_x, y=record.line_end_y),
        direction=CrossingDirection(record.direction),
        object_classes=frozenset(load_json(record.object_classes_json)),
        is_enabled=record.is_enabled,
    )
