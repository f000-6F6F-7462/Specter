"""Turns Specter's deliberate errors, and failures of the services it uses, into HTTP responses."""

from collections.abc import Awaitable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from specter.core.errors import ExternalServiceError, InvalidEntityError, NotFoundError


def register_error_handlers(app: FastAPI) -> None:
    """Maps missing entities to 404, broken entity rules to 422 and failed services to 503."""
    app.add_exception_handler(NotFoundError, respond_not_found)
    app.add_exception_handler(InvalidEntityError, respond_invalid_entity)
    app.add_exception_handler(ExternalServiceError, respond_external_service_error)


async def respond_not_found(_request: Request, error: Exception) -> JSONResponse:
    """Responds that the entity does not exist, or belongs to another owner."""
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(error)})


async def respond_invalid_entity(_request: Request, error: Exception) -> JSONResponse:
    """Responds that the request would break a rule of an entity."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(error)}
    )


async def respond_external_service_error(_request: Request, error: Exception) -> JSONResponse:
    """Responds that a service the request needs is unavailable, so the client can retry."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(error)}
    )


async def change_vector_index(operation: Awaitable[None]) -> None:
    """Runs a change to the vector index before the database changes, failing the request if needed.

    Removing or disabling a target in Qdrant first means a failure leaves it matching as before,
    rather than a target the database says is gone but cameras still recognize.

    Raises:
        ExternalServiceError: Qdrant cannot be reached or refused the change.
    """
    try:
        await operation
    except (UnexpectedResponse, ResponseHandlingException) as error:
        raise ExternalServiceError(f"the vector index cannot be changed: {error}") from error
