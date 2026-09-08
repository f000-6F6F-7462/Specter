"""The ``specter-ingest`` supervisor reconciles running pipelines to the stream table."""

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from specter.application.pipeline import PipelineDeps, PipelineTuning
from specter.contracts import EVENTS_STREAM_STATUS, StreamStatusMessage
from specter.core.clock import FrozenClock
from specter.domain.streams import SamplingConfig, StreamConfig, StreamProtocol, StreamSource
from specter.entrypoints.workers.ingest_worker import supervise
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import (
    SqlAlchemyUnitOfWork,
    create_all,
    create_engine,
    session_factory,
)
from specter.infrastructure.media.fakes import SyntheticFrameSource
from specter.infrastructure.ml.detector import FakeDetector
from specter.infrastructure.ml.embedder import FakeEmbedder
from specter.infrastructure.ml.tracker import IouTracker
from specter.infrastructure.vectors.memory import InMemoryVectorIndex

UowFactory = Callable[[], SqlAlchemyUnitOfWork]


def _stream(stream_id: str, *, running: bool) -> StreamConfig:
    cfg = StreamConfig(
        id=stream_id,
        owner_id="o_1",
        name=stream_id,
        source=StreamSource(protocol=StreamProtocol.RTSP, url=f"rtsp://cam/{stream_id}"),
        sampling=SamplingConfig(target_fps=30.0, motion_gating=False),
    )
    if running:
        cfg.start()
    return cfg


@pytest.fixture
async def env() -> AsyncIterator[tuple[UowFactory, PipelineDeps, MemoryBus]]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    bus = MemoryBus()
    deps = PipelineDeps(
        uow_factory=uow_factory,
        frame_source_factory=lambda s: SyntheticFrameSource(s.id, count=None, fps=30.0),
        detector=FakeDetector(),
        tracker=IouTracker(),
        embedders={"face": FakeEmbedder("face")},
        vectors=InMemoryVectorIndex(),
        blob=MemoryBlobStore(),
        bus=bus,
        clock=FrozenClock(),
        tuning=PipelineTuning(directory_refresh_s=1e9),
    )
    yield uow_factory, deps, bus
    await engine.dispose()


def _statuses(bus: MemoryBus, stream_id: str) -> list[str]:
    return [
        m.status
        for m in bus.published(EVENTS_STREAM_STATUS, StreamStatusMessage)
        if m.stream_id == stream_id
    ]


async def test_only_running_streams_are_launched_and_torn_down(
    env: tuple[UowFactory, PipelineDeps, MemoryBus],
) -> None:
    uow_factory, deps, bus = env
    async with uow_factory() as uow:
        await uow.streams.add(_stream("stream_on", running=True))
        await uow.streams.add(_stream("stream_off", running=False))

    stop = asyncio.Event()
    task = asyncio.create_task(supervise(deps, uow_factory, stop=stop, poll_s=0.02))
    await asyncio.sleep(0.3)

    assert "running" in _statuses(bus, "stream_on")
    assert _statuses(bus, "stream_off") == []

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert _statuses(bus, "stream_on")[-1] == "stopped"


async def test_flipping_desired_state_starts_the_pipeline(
    env: tuple[UowFactory, PipelineDeps, MemoryBus],
) -> None:
    uow_factory, deps, bus = env
    async with uow_factory() as uow:
        await uow.streams.add(_stream("stream_x", running=False))

    stop = asyncio.Event()
    task = asyncio.create_task(supervise(deps, uow_factory, stop=stop, poll_s=0.02))
    await asyncio.sleep(0.1)
    assert _statuses(bus, "stream_x") == []

    async with uow_factory() as uow:
        cfg = await uow.streams.get("stream_x")
        assert cfg is not None
        cfg.start()
        await uow.streams.update(cfg)

    await asyncio.sleep(0.3)
    assert "running" in _statuses(bus, "stream_x")

    stop.set()
    await asyncio.wait_for(task, timeout=5)
