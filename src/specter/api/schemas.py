"""Request and response bodies that several routers of the HTTP API share."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from specter.entities.geometry import NormalizedPoint


class HealthStatus(StrEnum):
    """Overall state reported by the health endpoint."""

    OK = "ok"
    DEGRADED = "degraded"


class HealthResponse(BaseModel):
    """State of the API process and its connection to NATS."""

    status: HealthStatus
    version: str
    is_nats_connected: bool


class RequestModel(BaseModel):
    """Base of request bodies; unknown fields are rejected so client mistakes fail loudly."""

    model_config = ConfigDict(extra="forbid")


class PointBody(RequestModel):
    """A point as fractions of the frame's width and height, each from 0 to 1."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_point(cls, point: NormalizedPoint) -> "PointBody":
        """Returns the body of a stored point."""
        return cls(x=point.x, y=point.y)

    def to_point(self) -> NormalizedPoint:
        """Returns the point the body describes."""
        return NormalizedPoint(x=self.x, y=self.y)
