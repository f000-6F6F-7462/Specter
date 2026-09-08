"""Contract-test fixtures.

Each port fixture is parametrised over its adapters. The in-memory param always runs;
the real param carries ``@pytest.mark.integration`` and is deselected from the default
gate (``addopts = -m 'not integration'``). Run it with a live stack:

    docker compose up -d
    pytest -m integration tests/contract
"""

import os
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from specter.application.ports import BlobStore, EventBus, VectorIndex
from specter.core.settings import S3Settings
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.vectors.memory import InMemoryVectorIndex

_REDIS_URL = os.environ.get("SPECTER_TEST_REDIS_URL", "redis://localhost:6379/15")
_QDRANT_URL = os.environ.get("SPECTER_TEST_QDRANT_URL", "http://localhost:6333")

_MEMORY = pytest.param("memory", id="memory")


def _real(name: str) -> object:  # a pytest ParameterSet
    return pytest.param(name, id=name, marks=pytest.mark.integration)


@pytest.fixture(params=[_MEMORY, _real("redis")])
async def event_bus(request: pytest.FixtureRequest) -> AsyncIterator[EventBus]:
    if request.param == "memory":
        yield MemoryBus()
        return
    from specter.infrastructure.bus.redis_stream import RedisStreamBus

    bus = RedisStreamBus(_REDIS_URL, block_ms=200)
    yield bus
    await bus.aclose()


@pytest.fixture(params=[_MEMORY, _real("minio")])
async def blob_store(request: pytest.FixtureRequest) -> AsyncIterator[BlobStore]:
    if request.param == "memory":
        yield MemoryBlobStore()
        return
    from specter.infrastructure.blob.minio import MinioBlobStore

    store = MinioBlobStore(
        S3Settings(bucket="specter-contract-tests", endpoint_url="http://localhost:9000")
    )
    await store.ensure_bucket()
    yield store


@pytest.fixture(params=[_MEMORY, _real("qdrant")])
async def vector_index(request: pytest.FixtureRequest) -> AsyncIterator[VectorIndex]:
    if request.param == "memory":
        yield InMemoryVectorIndex()
        return
    from specter.infrastructure.vectors.qdrant import QdrantVectorIndex

    index = QdrantVectorIndex(_QDRANT_URL)
    await index.ensure_collections()
    yield index
    await index.delete(target_id="tgt_ct")
    await index.aclose()


DrainFn = Callable[..., Awaitable[list]]


@pytest.fixture
def drain() -> DrainFn:
    import asyncio

    async def _drain(bus: EventBus, stream: str, *, group: str, count: int) -> list:
        got: list = []
        async with asyncio.timeout(5):
            async for delivery in bus.consume(stream, group=group, consumer="ct"):
                got.append(delivery.message)
                await delivery.ack()
                if len(got) >= count:
                    break
        return got

    return _drain
