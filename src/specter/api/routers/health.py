"""Health check used by Docker and monitoring."""

from fastapi import APIRouter, Response, status

from specter import __version__
from specter.api.dependencies import ServicesDependency
from specter.api.schemas import HealthResponse, HealthStatus

router = APIRouter(tags=["operations"])


@router.get(
    "/health",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def read_health(services: ServicesDependency, response: Response) -> HealthResponse:
    """Reports whether the API is running and connected to NATS.

    Responds with 503 while NATS is unreachable, so container health checks notice it.
    """
    is_nats_connected = services.message_bus.is_connected
    if not is_nats_connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=HealthStatus.OK if is_nats_connected else HealthStatus.DEGRADED,
        version=__version__,
        is_nats_connected=is_nats_connected,
    )
