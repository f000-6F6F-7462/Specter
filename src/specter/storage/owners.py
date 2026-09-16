"""Removing everything that belongs to one owner."""

from dataclasses import dataclass

from specter.storage.database import database_proxy
from specter.storage.queries import delete_rows
from specter.storage.tables import (
    CameraRecord,
    IdentityMatchAlertRecord,
    RuleAlertRecord,
    WatchlistRecord,
)


@dataclass(frozen=True, slots=True)
class DeletedOwnerData:
    """The ids of the owner's cameras and watchlists that were deleted with the owner's data."""

    camera_ids: tuple[str, ...]
    watchlist_ids: tuple[str, ...]


def delete_owner_data(owner_id: str) -> DeletedOwnerData:
    """Deletes the owner's cameras, watchlists and alerts, and everything that belongs to them.

    Zones, rules, targets, reference images and their embeddings go with their camera or watchlist.
    """
    with database_proxy.atomic():
        camera_ids = tuple(
            record.id for record in CameraRecord.select().where(CameraRecord.owner_id == owner_id)
        )
        watchlist_ids = tuple(
            record.id
            for record in WatchlistRecord.select().where(WatchlistRecord.owner_id == owner_id)
        )
        delete_rows(CameraRecord, CameraRecord.owner_id == owner_id)
        delete_rows(WatchlistRecord, WatchlistRecord.owner_id == owner_id)
        delete_rows(IdentityMatchAlertRecord, IdentityMatchAlertRecord.owner_id == owner_id)
        delete_rows(RuleAlertRecord, RuleAlertRecord.owner_id == owner_id)
    return DeletedOwnerData(camera_ids=camera_ids, watchlist_ids=watchlist_ids)
