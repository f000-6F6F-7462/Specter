"""Reading and writing watchlists."""

from datetime import UTC, datetime

from specter.core.errors import NotFoundError
from specter.entities.targets import TargetType
from specter.entities.watchlists import Watchlist, WatchlistKind
from specter.storage.columns import dump_json, format_utc_timestamp, load_json
from specter.storage.queries import delete_rows
from specter.storage.tables import WatchlistRecord


def save_watchlist(watchlist: Watchlist) -> None:
    """Inserts the watchlist, or replaces its stored fields if it already exists."""
    record_values = {
        "owner_id": watchlist.owner_id,
        "name": watchlist.name,
        "target_type": watchlist.target_type.value,
        "kind": watchlist.kind.value,
        "match_threshold_ratio": watchlist.match_threshold_ratio,
        "metadata_json": dump_json(dict(watchlist.metadata)),
    }
    updated_row_count = (
        WatchlistRecord.update(**record_values).where(WatchlistRecord.id == watchlist.id).execute()
    )
    if updated_row_count == 0:
        WatchlistRecord.insert(
            id=watchlist.id,
            created_at=format_utc_timestamp(datetime.now(UTC)),
            **record_values,
        ).execute()


def find_watchlist(watchlist_id: str) -> Watchlist | None:
    """Returns the watchlist, or None if it does not exist."""
    record = WatchlistRecord.get_or_none(WatchlistRecord.id == watchlist_id)
    return _build_watchlist(record) if record is not None else None


def get_watchlist(watchlist_id: str) -> Watchlist:
    """Returns the watchlist.

    Raises:
        NotFoundError: The watchlist does not exist.
    """
    watchlist = find_watchlist(watchlist_id)
    if watchlist is None:
        raise NotFoundError(f"watchlist {watchlist_id} does not exist")
    return watchlist


def list_owner_watchlists(owner_id: str) -> list[Watchlist]:
    """Returns the owner's watchlists, oldest first."""
    records = (
        WatchlistRecord.select()
        .where(WatchlistRecord.owner_id == owner_id)
        .order_by(WatchlistRecord.created_at)
    )
    return [_build_watchlist(record) for record in records]


def delete_watchlist(watchlist_id: str) -> None:
    """Deletes the watchlist with its targets and reference images, and detaches it from cameras.

    Raises:
        NotFoundError: The watchlist does not exist.
    """
    deleted_row_count = delete_rows(WatchlistRecord, WatchlistRecord.id == watchlist_id)
    if deleted_row_count == 0:
        raise NotFoundError(f"watchlist {watchlist_id} does not exist")


def _build_watchlist(record: WatchlistRecord) -> Watchlist:
    return Watchlist(
        id=record.id,
        owner_id=record.owner_id,
        name=record.name,
        target_type=TargetType(record.target_type),
        kind=WatchlistKind(record.kind),
        match_threshold_ratio=record.match_threshold_ratio,
        metadata=load_json(record.metadata_json),
    )
