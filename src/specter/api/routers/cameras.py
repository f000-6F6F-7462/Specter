"""Cameras of an owner: their sources, sampling, watchlists and whether they run."""

import asyncio
from dataclasses import replace
from functools import partial

from fastapi import APIRouter, status
from nats.errors import Error as NatsError
from pydantic import BaseModel, Field

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.notifications import publish_configuration_change
from specter.api.ownership import get_owner_camera, get_owner_watchlist
from specter.api.schemas import RequestModel
from specter.core.identifiers import new_identifier
from specter.entities.cameras import (
    DEFAULT_MINIMUM_FPS,
    DEFAULT_TARGET_FPS,
    Camera,
    CameraCredentials,
    CameraStatus,
    DesiredState,
    SamplingMode,
    SamplingSettings,
)
from specter.messaging.messages import ChangeKind, EntityKind
from specter.messaging.shared_state import CameraHealthBucket
from specter.storage.cameras import delete_camera, list_owner_cameras, save_camera

router = APIRouter(prefix="/owners/{owner_id}/cameras", tags=["cameras"])


class CredentialsBody(RequestModel):
    """The user name and password of a camera's stream."""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SamplingBody(RequestModel):
    """How many frames per second the camera's frames are analyzed at."""

    mode: SamplingMode = SamplingMode.ADAPTIVE
    target_fps: float = Field(default=DEFAULT_TARGET_FPS, gt=0)
    minimum_fps: float = Field(default=DEFAULT_MINIMUM_FPS, gt=0)
    is_motion_gating_enabled: bool = True


class CameraCreateBody(RequestModel):
    """A new camera, which stays stopped until it is started."""

    name: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    credentials: CredentialsBody | None = None
    watchlist_ids: list[str] = Field(default_factory=list)
    # Empty means objects of every class are analyzed.
    detection_classes: list[str] = Field(default_factory=list)
    sampling: SamplingBody = Field(default_factory=SamplingBody)
    is_enabled: bool = True
    metadata: dict[str, object] = Field(default_factory=dict)


class CameraUpdateBody(RequestModel):
    """Fields of a camera to change; omitted fields keep their value.

    Sending ``credentials`` as null removes the camera's credentials.
    """

    name: str | None = Field(default=None, min_length=1)
    source_url: str | None = Field(default=None, min_length=1)
    credentials: CredentialsBody | None = None
    watchlist_ids: list[str] | None = None
    detection_classes: list[str] | None = None
    sampling: SamplingBody | None = None
    is_enabled: bool | None = None
    # Replaces the whole metadata; changing only the metadata never restarts the camera.
    metadata: dict[str, object] | None = None


class CameraResponse(BaseModel):
    """A camera as clients see it; its password never leaves the device."""

    id: str
    owner_id: str
    name: str
    source_url: str
    username: str | None
    has_password: bool
    watchlist_ids: list[str]
    detection_classes: list[str]
    sampling: SamplingBody
    is_enabled: bool
    desired_state: DesiredState
    metadata: dict[str, object]
    # The camera process's latest report, or None when no process has reported lately.
    live_status: CameraStatus | None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_camera(
    owner_id: str, body: CameraCreateBody, services: ServicesDependency
) -> CameraResponse:
    """Adds a camera for the owner."""
    await require_owner_watchlists(services, owner_id, body.watchlist_ids)
    camera = Camera(
        id=new_identifier("camera"),
        owner_id=owner_id,
        name=body.name,
        source_url=body.source_url,
        credentials=build_credentials(body.credentials),
        watchlist_ids=tuple(body.watchlist_ids),
        detection_classes=frozenset(body.detection_classes),
        sampling=build_sampling_settings(body.sampling),
        is_enabled=body.is_enabled,
        metadata=body.metadata,
    )
    await store_camera(services, camera, ChangeKind.CREATED)
    return await build_camera_response(services, camera)


@router.get("")
async def list_cameras(owner_id: str, services: ServicesDependency) -> list[CameraResponse]:
    """Lists the owner's cameras, oldest first."""
    cameras = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_owner_cameras, owner_id, services.cipher)
    )
    return [await build_camera_response(services, camera) for camera in cameras]


@router.get("/{camera_id}")
async def read_camera(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> CameraResponse:
    """Returns one of the owner's cameras."""
    return await build_camera_response(
        services, await get_owner_camera(services, owner_id, camera_id)
    )


@router.patch("/{camera_id}")
async def update_camera(
    owner_id: str, camera_id: str, body: CameraUpdateBody, services: ServicesDependency
) -> CameraResponse:
    """Changes the given fields; a running camera restarts unless only its metadata changed."""
    camera = await get_owner_camera(services, owner_id, camera_id)
    changes = body.model_dump(exclude_unset=True)
    if body.watchlist_ids is not None:
        await require_owner_watchlists(services, owner_id, body.watchlist_ids)
    updated_camera = replace(
        camera,
        name=changes.get("name", camera.name),
        source_url=changes.get("source_url", camera.source_url),
        credentials=(
            build_credentials(body.credentials) if "credentials" in changes else camera.credentials
        ),
        watchlist_ids=(
            camera.watchlist_ids if body.watchlist_ids is None else tuple(body.watchlist_ids)
        ),
        detection_classes=(
            camera.detection_classes
            if body.detection_classes is None
            else frozenset(body.detection_classes)
        ),
        sampling=camera.sampling
        if body.sampling is None
        else build_sampling_settings(body.sampling),
        metadata=camera.metadata if body.metadata is None else body.metadata,
    )
    if body.is_enabled is not None:
        updated_camera = updated_camera.enable() if body.is_enabled else updated_camera.disable()
    await store_camera(services, updated_camera, ChangeKind.UPDATED)
    return await build_camera_response(services, updated_camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_camera(owner_id: str, camera_id: str, services: ServicesDependency) -> None:
    """Deletes the camera with its zones and rules; its alerts stay."""
    await get_owner_camera(services, owner_id, camera_id)
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_camera, camera_id)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.CAMERA, camera_id, ChangeKind.DELETED
    )


@router.post("/{camera_id}/start")
async def start_camera(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> CameraResponse:
    """Asks the camera manager to run the camera.

    Raises:
        InvalidEntityError: The camera is disabled.
    """
    camera = (await get_owner_camera(services, owner_id, camera_id)).start()
    await store_camera(services, camera, ChangeKind.UPDATED)
    return await build_camera_response(services, camera)


@router.post("/{camera_id}/stop")
async def stop_camera(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> CameraResponse:
    """Asks the camera manager to stop the camera."""
    camera = (await get_owner_camera(services, owner_id, camera_id)).stop()
    await store_camera(services, camera, ChangeKind.UPDATED)
    return await build_camera_response(services, camera)


async def store_camera(services: ApiServices, camera: Camera, change_kind: ChangeKind) -> None:
    """Saves the camera and tells the camera manager about the change."""
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_camera, camera, services.cipher)
    )
    await publish_configuration_change(
        services.message_bus, camera.owner_id, EntityKind.CAMERA, camera.id, change_kind
    )


async def require_owner_watchlists(
    services: ApiServices, owner_id: str, watchlist_ids: list[str]
) -> None:
    """Makes sure every watchlist exists and belongs to the owner.

    Raises:
        NotFoundError: A watchlist does not exist or belongs to another owner.
    """
    for watchlist_id in watchlist_ids:
        await get_owner_watchlist(services, owner_id, watchlist_id)


async def build_camera_response(services: ApiServices, camera: Camera) -> CameraResponse:
    """Returns the camera's response, with its live status when NATS can be reached."""
    return CameraResponse(
        id=camera.id,
        owner_id=camera.owner_id,
        name=camera.name,
        source_url=camera.source_url,
        username=None if camera.credentials is None else camera.credentials.username,
        has_password=camera.credentials is not None,
        watchlist_ids=list(camera.watchlist_ids),
        detection_classes=sorted(camera.detection_classes),
        sampling=SamplingBody(
            mode=camera.sampling.mode,
            target_fps=camera.sampling.target_fps,
            minimum_fps=camera.sampling.minimum_fps,
            is_motion_gating_enabled=camera.sampling.is_motion_gating_enabled,
        ),
        is_enabled=camera.is_enabled,
        desired_state=camera.desired_state,
        metadata=dict(camera.metadata),
        live_status=await read_live_status(services, camera),
    )


async def read_live_status(services: ApiServices, camera: Camera) -> CameraStatus | None:
    """Returns the camera process's latest reported status, or None when it cannot be read."""
    if not services.message_bus.is_connected:
        return None
    try:
        health_bucket = await CameraHealthBucket.open(services.message_bus)
        report = await health_bucket.read(camera.owner_id, camera.id)
    except NatsError:
        return None
    return None if report is None else report.status


def build_credentials(body: CredentialsBody | None) -> CameraCredentials | None:
    """Returns the credentials a request body describes."""
    if body is None:
        return None
    return CameraCredentials(username=body.username, password=body.password)


def build_sampling_settings(body: SamplingBody) -> SamplingSettings:
    """Returns the sampling settings a request body describes."""
    return SamplingSettings(
        mode=body.mode,
        target_fps=body.target_fps,
        minimum_fps=body.minimum_fps,
        is_motion_gating_enabled=body.is_motion_gating_enabled,
    )
