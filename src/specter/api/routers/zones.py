"""Zones of a camera: named areas that zone occupancy rules watch."""

import asyncio
from dataclasses import replace
from functools import partial

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.notifications import publish_configuration_change
from specter.api.ownership import get_owner_camera, get_owner_zone
from specter.api.schemas import PointBody, RequestModel
from specter.core.identifiers import new_identifier
from specter.entities.zones import MINIMUM_POLYGON_POINT_COUNT, Zone
from specter.messaging.messages import ChangeKind, EntityKind
from specter.storage.zones import delete_zone, list_camera_zones, save_zone

router = APIRouter(prefix="/owners/{owner_id}/cameras/{camera_id}/zones", tags=["zones"])


class ZoneCreateBody(RequestModel):
    """A new zone, drawn as a polygon over the camera's frame."""

    name: str = Field(min_length=1)
    polygon: list[PointBody] = Field(min_length=MINIMUM_POLYGON_POINT_COUNT)


class ZoneUpdateBody(RequestModel):
    """Fields of a zone to change; omitted fields keep their value."""

    name: str | None = Field(default=None, min_length=1)
    polygon: list[PointBody] | None = Field(default=None, min_length=MINIMUM_POLYGON_POINT_COUNT)


class ZoneResponse(BaseModel):
    """A zone of a camera."""

    id: str
    camera_id: str
    name: str
    polygon: list[PointBody]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_zone(
    owner_id: str, camera_id: str, body: ZoneCreateBody, services: ServicesDependency
) -> ZoneResponse:
    """Adds a zone to the owner's camera."""
    await get_owner_camera(services, owner_id, camera_id)
    zone = Zone(
        id=new_identifier("zone"),
        camera_id=camera_id,
        name=body.name,
        polygon=tuple(point.to_point() for point in body.polygon),
    )
    await store_zone(services, owner_id, zone, ChangeKind.CREATED)
    return build_zone_response(zone)


@router.get("")
async def list_zones(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> list[ZoneResponse]:
    """Lists the zones of the owner's camera."""
    await get_owner_camera(services, owner_id, camera_id)
    zones = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_camera_zones, camera_id)
    )
    return [build_zone_response(zone) for zone in zones]


@router.get("/{zone_id}")
async def read_zone(
    owner_id: str, camera_id: str, zone_id: str, services: ServicesDependency
) -> ZoneResponse:
    """Returns a zone of the owner's camera."""
    return build_zone_response(await get_owner_zone(services, owner_id, camera_id, zone_id))


@router.patch("/{zone_id}")
async def update_zone(
    owner_id: str,
    camera_id: str,
    zone_id: str,
    body: ZoneUpdateBody,
    services: ServicesDependency,
) -> ZoneResponse:
    """Changes the given fields of the zone."""
    zone = await get_owner_zone(services, owner_id, camera_id, zone_id)
    updated_zone = replace(
        zone,
        name=body.name or zone.name,
        polygon=(
            zone.polygon
            if body.polygon is None
            else tuple(point.to_point() for point in body.polygon)
        ),
    )
    await store_zone(services, owner_id, updated_zone, ChangeKind.UPDATED)
    return build_zone_response(updated_zone)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_zone(
    owner_id: str, camera_id: str, zone_id: str, services: ServicesDependency
) -> None:
    """Deletes the zone together with the rules that watch it."""
    await get_owner_zone(services, owner_id, camera_id, zone_id)
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_zone, zone_id)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.ZONE, zone_id, ChangeKind.DELETED
    )


async def store_zone(
    services: ApiServices, owner_id: str, zone: Zone, change_kind: ChangeKind
) -> None:
    """Saves the zone and tells the camera's process about the change."""
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_zone, zone)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.ZONE, zone.id, change_kind
    )


def build_zone_response(zone: Zone) -> ZoneResponse:
    """Returns the zone's response."""
    return ZoneResponse(
        id=zone.id,
        camera_id=zone.camera_id,
        name=zone.name,
        polygon=[PointBody.from_point(point) for point in zone.polygon],
    )
