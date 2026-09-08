"""Enrollment component fixtures: a real Unit of Work over SQLite + in-memory adapters."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest

from specter.application.enrollment import EnrollmentDeps
from specter.application.ports import UnitOfWork
from specter.core.clock import FrozenClock
from specter.domain.catalog import ImageStatus, ReferenceImage, Target, TargetType, Watchlist
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import create_all, create_engine, session_factory
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork
from specter.infrastructure.ml.fakes import FakeFaceEmbeddingService
from specter.infrastructure.vectors.memory import InMemoryVectorIndex


@dataclass
class EnrollFixture:
    deps: EnrollmentDeps
    bus: MemoryBus
    vectors: InMemoryVectorIndex
    uow_factory: Callable[[], UnitOfWork]
    watchlist_id: str
    target_id: str
    image_ids: list[str]
    blob_keys: list[str]


@pytest.fixture
async def enroll(tmp_path, monkeypatch) -> AsyncIterator[EnrollFixture]:
    monkeypatch.chdir(tmp_path)
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    blob = MemoryBlobStore()
    keys = ["blobs/o_1/a.jpg", "blobs/o_1/b.jpg"]
    for i, key in enumerate(keys):
        await blob.put(key, f"image-bytes-{i}".encode(), "image/jpeg")

    images = [
        ReferenceImage(id=f"img_{i}", blob_key=key, status=ImageStatus.PENDING)
        for i, key in enumerate(keys)
    ]
    async with uow_factory() as uow:
        await uow.watchlists.add(
            Watchlist(id="wl_1", owner_id="o_1", name="VIPs", type=TargetType.PERSON)
        )
        await uow.targets.add(
            Target(
                id="tgt_1",
                watchlist_id="wl_1",
                label="Jane",
                type=TargetType.PERSON,
                images=images,
                batch_id="bat_1",
            )
        )

    bus = MemoryBus()
    vectors = InMemoryVectorIndex()
    deps = EnrollmentDeps(
        uow_factory=uow_factory,
        blob=blob,
        faces=FakeFaceEmbeddingService(),
        vectors=vectors,
        bus=bus,
        clock=FrozenClock(),
    )
    yield EnrollFixture(
        deps=deps,
        bus=bus,
        vectors=vectors,
        uow_factory=uow_factory,
        watchlist_id="wl_1",
        target_id="tgt_1",
        image_ids=["img_0", "img_1"],
        blob_keys=keys,
    )
    await engine.dispose()
