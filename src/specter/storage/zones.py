"""Reading and writing zones."""

from datetime import UTC, datetime

from peewee import IntegrityError

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.geometry import NormalizedPoint
from specter.entities.zones import Zone
from specter.storage.columns import dump_json, format_utc_timestamp, load_json
from specter.storage.queries import INSERTION_ORDER, delete_rows
from specter.storage.tables import ZoneRecord


def save_zone(zone: Zone) -> None:
    """Inserts the zone, or replaces its stored fields if it already exists.

    Raises:
        InvalidEntityError: The zone's camera does not exist.
    """
    record_values = {
        "camera_id": zone.camera_id,
        "name": zone.name,
        "polygon_json": dump_json([[point.x, point.y] for point in zone.polygon]),
    }
    try:
        updated_row_count = (
            ZoneRecord.update(**record_values).where(ZoneRecord.id == zone.id).execute()
        )
        if updated_row_count == 0:
            ZoneRecord.insert(
                id=zone.id,
                created_at=format_utc_timestamp(datetime.now(UTC)),
                **record_values,
            ).execute()
    except IntegrityError as error:
        raise InvalidEntityError(
            f"zone {zone.id} belongs to a camera that does not exist"
        ) from error


def find_zone(zone_id: str) -> Zone | None:
    """Returns the zone, or None if it does not exist."""
    record = ZoneRecord.get_or_none(ZoneRecord.id == zone_id)
    return _build_zone(record) if record is not None else None


def get_zone(zone_id: str) -> Zone:
    """Returns the zone.

    Raises:
        NotFoundError: The zone does not exist.
    """
    zone = find_zone(zone_id)
    if zone is None:
        raise NotFoundError(f"zone {zone_id} does not exist")
    return zone


def list_camera_zones(camera_id: str) -> list[Zone]:
    """Returns the camera's zones, oldest first."""
    records = (
        ZoneRecord.select()
        .where(ZoneRecord.camera_id == camera_id)
        .order_by(ZoneRecord.created_at, INSERTION_ORDER)
    )
    return [_build_zone(record) for record in records]


def delete_zone(zone_id: str) -> None:
    """Deletes the zone together with its occupancy rules.

    Raises:
        NotFoundError: The zone does not exist.
    """
    if delete_rows(ZoneRecord, ZoneRecord.id == zone_id) == 0:
        raise NotFoundError(f"zone {zone_id} does not exist")


def _build_zone(record: ZoneRecord) -> Zone:
    return Zone(
        id=record.id,
        camera_id=record.camera_id,
        name=record.name,
        polygon=tuple(NormalizedPoint(x=x, y=y) for x, y in load_json(record.polygon_json)),
    )
