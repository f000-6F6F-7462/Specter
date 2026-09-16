"""The service token that the application server authenticates to the API with."""

import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from specter.api.dependencies import ServicesDependency

API_TOKEN_BYTES = 32
BEARER_SCHEME_NAME = "Bearer"

bearer_scheme = HTTPBearer(auto_error=False)


def generate_api_token() -> str:
    """Returns a new random token, safe to use in an HTTP header."""
    return secrets.token_urlsafe(API_TOKEN_BYTES)


def is_valid_api_token(expected_token: str, presented_token: str | None) -> bool:
    """Whether the presented token is the API token, compared in constant time."""
    return presented_token is not None and hmac.compare_digest(
        expected_token.encode(), presented_token.encode()
    )


def require_api_token(
    services: ServicesDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    """Rejects a request that does not carry the API token as its bearer token."""
    presented_token = None if credentials is None else credentials.credentials
    if not is_valid_api_token(services.api_token, presented_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid API token is required",
            headers={"WWW-Authenticate": BEARER_SCHEME_NAME},
        )
