"""API-suite fixtures: an in-memory app and an authenticated HTTP client."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from specter.core.di import Container, build_container
from specter.entrypoints.http.asgi import create_app
from specter.infrastructure.db import create_all
from tests.support import test_settings


@pytest.fixture
async def container(tmp_path, monkeypatch) -> AsyncIterator[Container]:
    monkeypatch.chdir(tmp_path)
    built = build_container(test_settings())
    await create_all(built.engine)
    yield built
    await built.engine.dispose()


@pytest.fixture
async def client(container: Container) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app(container))
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-Api-Key": "test-key", "X-Owner-Id": "o_alice"},
    ) as http_client:
        yield http_client


@pytest.fixture
def jpeg() -> bytes:
    # Minimal valid-enough JPEG header.
    return b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64 + b"\xff\xd9"
