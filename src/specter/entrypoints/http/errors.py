"""Map domain errors problem+json responses."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from specter.core.errors import (
    ConfigurationError,
    ConflictError,
    DependencyFailure,
    NotFoundError,
    RuleViolation,
    SpecterError,
)

_STATUS: dict[type[SpecterError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    RuleViolation: 422,
    ConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    DependencyFailure: status.HTTP_502_BAD_GATEWAY,
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SpecterError)
    async def _handle_specter_error(_: Request, exc: SpecterError) -> JSONResponse:
        code = _STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(
            status_code=code,
            media_type="application/problem+json",
            content={
                "type": f"about:blank#{exc.code}",
                "title": exc.code,
                "status": code,
                "detail": str(exc),
            },
        )
