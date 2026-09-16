"""Watchlists of an owner: named lists of targets that cameras look for."""

import asyncio
from dataclasses import replace
from functools import partial

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.errors import change_vector_index
from specter.api.notifications import publish_configuration_change
from specter.api.ownership import get_owner_watchlist
from specter.api.schemas import RequestModel
from specter.core.identifiers import new_identifier
from specter.entities.targets import TargetType
from specter.entities.watchlists import (
    DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO,
    DEFAULT_FACE_MATCH_THRESHOLD_RATIO,
    Watchlist,
    WatchlistKind,
)
from specter.messaging.messages import ChangeKind, EntityKind
from specter.storage.targets import list_watchlist_targets
from specter.storage.watchlists import delete_watchlist, list_owner_watchlists, save_watchlist

router = APIRouter(prefix="/owners/{owner_id}/watchlists", tags=["watchlists"])


class WatchlistCreateBody(RequestModel):
    """A new watchlist; its target type cannot change later."""

    name: str = Field(min_length=1)
    target_type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    face_match_threshold_ratio: float = Field(
        default=DEFAULT_FACE_MATCH_THRESHOLD_RATIO, ge=0.0, le=1.0
    )
    appearance_match_threshold_ratio: float = Field(
        default=DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO, ge=0.0, le=1.0
    )
    metadata: dict[str, object] = Field(default_factory=dict)


class WatchlistUpdateBody(RequestModel):
    """Fields of a watchlist to change; omitted fields keep their value."""

    name: str | None = Field(default=None, min_length=1)
    kind: WatchlistKind | None = None
    face_match_threshold_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    appearance_match_threshold_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, object] | None = None


class WatchlistResponse(BaseModel):
    """A watchlist of an owner."""

    id: str
    owner_id: str
    name: str
    target_type: TargetType
    kind: WatchlistKind
    face_match_threshold_ratio: float
    appearance_match_threshold_ratio: float
    metadata: dict[str, object]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    owner_id: str, body: WatchlistCreateBody, services: ServicesDependency
) -> WatchlistResponse:
    """Adds a watchlist for the owner."""
    watchlist = Watchlist(
        id=new_identifier("watchlist"),
        owner_id=owner_id,
        name=body.name,
        target_type=body.target_type,
        kind=body.kind,
        face_match_threshold_ratio=body.face_match_threshold_ratio,
        appearance_match_threshold_ratio=body.appearance_match_threshold_ratio,
        metadata=body.metadata,
    )
    await store_watchlist(services, watchlist, ChangeKind.CREATED)
    return build_watchlist_response(watchlist)


@router.get("")
async def list_watchlists(owner_id: str, services: ServicesDependency) -> list[WatchlistResponse]:
    """Lists the owner's watchlists."""
    watchlists = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_owner_watchlists, owner_id)
    )
    return [build_watchlist_response(watchlist) for watchlist in watchlists]


@router.get("/{watchlist_id}")
async def read_watchlist(
    owner_id: str, watchlist_id: str, services: ServicesDependency
) -> WatchlistResponse:
    """Returns one of the owner's watchlists."""
    return build_watchlist_response(await get_owner_watchlist(services, owner_id, watchlist_id))


@router.patch("/{watchlist_id}")
async def update_watchlist(
    owner_id: str, watchlist_id: str, body: WatchlistUpdateBody, services: ServicesDependency
) -> WatchlistResponse:
    """Changes the given fields; cameras apply new thresholds without restarting."""
    watchlist = await get_owner_watchlist(services, owner_id, watchlist_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    updated_watchlist = replace(watchlist, **changes)
    await store_watchlist(services, updated_watchlist, ChangeKind.UPDATED)
    return build_watchlist_response(updated_watchlist)


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist(owner_id: str, watchlist_id: str, services: ServicesDependency) -> None:
    """Deletes the watchlist with its targets, their images and embeddings; alerts stay."""
    await get_owner_watchlist(services, owner_id, watchlist_id)
    await change_vector_index(services.vector_index.delete_watchlist(watchlist_id))
    targets = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_watchlist_targets, watchlist_id)
    )
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_watchlist, watchlist_id)
    )
    for target in targets:
        await asyncio.to_thread(
            services.reference_image_store.delete_target_images, owner_id, target.id
        )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.WATCHLIST, watchlist_id, ChangeKind.DELETED
    )


async def store_watchlist(
    services: ApiServices, watchlist: Watchlist, change_kind: ChangeKind
) -> None:
    """Saves the watchlist and tells the cameras that use it about the change."""
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_watchlist, watchlist)
    )
    await publish_configuration_change(
        services.message_bus, watchlist.owner_id, EntityKind.WATCHLIST, watchlist.id, change_kind
    )


def build_watchlist_response(watchlist: Watchlist) -> WatchlistResponse:
    """Returns the watchlist's response."""
    return WatchlistResponse(
        id=watchlist.id,
        owner_id=watchlist.owner_id,
        name=watchlist.name,
        target_type=watchlist.target_type,
        kind=watchlist.kind,
        face_match_threshold_ratio=watchlist.face_match_threshold_ratio,
        appearance_match_threshold_ratio=watchlist.appearance_match_threshold_ratio,
        metadata=dict(watchlist.metadata),
    )
