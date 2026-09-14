"""FastAPI dependency providers.

Auth here is service-to-service only: a shared ``X-Api-Key`` and an opaque
``X-Owner-Id`` the calling backend assigns. No user model, no JWT.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from specter.application.ports import (
    BlobStore,
    EventBus,
    FrameCodec,
    HealthStore,
    UnitOfWorkFactory,
)
from specter.core.di import Container


def get_container(request: Request) -> Container:
    container = request.app.state.container
    assert isinstance(container, Container)  # set in create_app()
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def require_api_key(
    container: ContainerDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = container.settings.security.api_key.get_secret_value()
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Api-Key")


async def require_owner(
    x_owner_id: Annotated[str | None, Header()] = None,
) -> str:
    if not x_owner_id:
        raise HTTPException(422, "missing X-Owner-Id header")
    return x_owner_id


def get_uow_factory(container: ContainerDep) -> UnitOfWorkFactory:
    return container.uow_factory


def get_blob(container: ContainerDep) -> BlobStore:
    return container.blob


def get_bus(container: ContainerDep) -> EventBus:
    return container.bus


def get_health(container: ContainerDep) -> HealthStore:
    return container.health


def get_codec(container: ContainerDep) -> FrameCodec:
    return container.codec


OwnerDep = Annotated[str, Depends(require_owner)]
UowDep = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
BlobDep = Annotated[BlobStore, Depends(get_blob)]
BusDep = Annotated[EventBus, Depends(get_bus)]
HealthDep = Annotated[HealthStore, Depends(get_health)]
CodecDep = Annotated[FrameCodec, Depends(get_codec)]
