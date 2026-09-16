"""Services that request handlers receive through FastAPI dependency injection."""

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends
from starlette.requests import HTTPConnection

from specter.config.settings import Settings
from specter.messaging.message_bus import MessageBus
from specter.storage.credentials import CredentialCipher
from specter.storage.database import DatabaseThread
from specter.storage.evidence import EvidenceStore
from specter.storage.reference_images import ReferenceImageStore
from specter.storage.vector_index import VectorIndex


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Everything the API process opens once at startup and shares between requests."""

    settings: Settings
    api_token: str
    message_bus: MessageBus
    database_thread: DatabaseThread
    cipher: CredentialCipher
    vector_index: VectorIndex
    evidence_store: EvidenceStore
    reference_image_store: ReferenceImageStore
    # go2rtc's API stays on the device, so live video reaches clients only through this client.
    go2rtc_http_client: httpx.AsyncClient


def get_services(connection: HTTPConnection) -> ApiServices:
    """Returns the services opened by the application's lifespan, for requests and websockets."""
    services: ApiServices = connection.app.state.services
    return services


ServicesDependency = Annotated[ApiServices, Depends(get_services)]
