"""Request and response bodies of the HTTP API."""

from enum import StrEnum

from pydantic import BaseModel


class HealthStatus(StrEnum):
    """Overall state reported by the health endpoint."""

    OK = "ok"
    DEGRADED = "degraded"


class HealthResponse(BaseModel):
    """State of the API process and its connection to NATS."""

    status: HealthStatus
    version: str
    is_nats_connected: bool
