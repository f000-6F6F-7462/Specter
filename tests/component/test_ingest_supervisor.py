"""The ``specter-ingest`` supervisor reconciles running pipelines to the stream table."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace

import pytest

from specter.application.pipeline import PipelineDeps, PipelineTuning
from specter.contracts import EVENTS_STREAM_STATUS, StreamStatusMessage
from specter.core.clock import FrozenClock, SystemClock
from specter.domain.streams import SamplingConfig, StreamConfig, StreamProtocol, StreamSource
from specter.entrypoints.workers.ingest_worker import _Run, _Supervisor, supervise
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import (
    SqlAlchemyUnitOfWork,
    create_all,
    create_engine,
    session_factory,
)
from specter.infrastructure.health.memory import InMemoryHealthStore
from specter.infrastructure.media.codec import NumpyFrameCodec
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
    clock = FrozenClock()
    deps = PipelineDeps(
        uow_factory=uow_factory,
        frame_source_factory=lambda s: SyntheticFrameSource(s.id, count=None, fps=30.0),
        detector=FakeDetector(),
        tracker=IouTracker(),
        embedders={"face": FakeEmbedder("face")},
        vectors=InMemoryVectorIndex(),
        blob=MemoryBlobStore(),
        bus=bus,
        health=InMemoryHealthStore(clock),
        clock=clock,
        codec=NumpyFrameCodec(),
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


async def test_reconnect_count_climbs_across_crashes_and_reaches_health(
    env: tuple[UowFactory, PipelineDeps, MemoryBus],
) -> None:
    uow_factory, deps, _bus = env
    # A real clock, not the fixture's FrozenClock — the supervisor's crash backoff is
    # real wall-clock time (matched by the real asyncio.sleep below), and a frozen clock
    # would never advance past the cooldown it set, so the stream would crash once and
    # then sit "in cooldown" forever.
    real_clock = SystemClock()
    crashy = replace(
        deps,
        frame_source_factory=lambda s: SyntheticFrameSource(
            s.id, count=None, fps=200.0, fail_after=2
        ),
        clock=real_clock,
        health=InMemoryHealthStore(real_clock),
    )
    async with uow_factory() as uow:
        await uow.streams.add(_stream("stream_crash", running=True))

    stop = asyncio.Event()
    task = asyncio.create_task(
        supervise(crashy, uow_factory, stop=stop, poll_s=0.02, backoff_s=0.05)
    )
    await asyncio.sleep(0.6)  # several crash -> backoff -> restart cycles

    health = await crashy.health.get_health("stream_crash")
    assert health is not None
    assert health.reconnect_count >= 1

    stop.set()
    await asyncio.wait_for(task, timeout=5)


async def test_reap_tracks_reconnects_and_a_deliberate_stop_clears_them(
    env: tuple[UowFactory, PipelineDeps, MemoryBus],
) -> None:
    """Exercises _Supervisor's bookkeeping directly — deterministic, no real sleeps."""
    uow_factory, deps, _bus = env
    sup = _Supervisor(deps=deps, uow_factory=uow_factory)

    async def _boom() -> None:
        raise RuntimeError("boom")

    async def _crash_once() -> None:
        task = asyncio.create_task(_boom())
        await asyncio.sleep(0)  # let it fail
        run = _Run(config=_stream("s1", running=True), task=task, stop=asyncio.Event())
        sup.running["s1"] = run
        sup._reap("s1", run)

    await _crash_once()
    assert sup.reconnects["s1"] == 1

    await _crash_once()
    assert sup.reconnects["s1"] == 2

    # a well-behaved run that actually observes its stop event, like run_stream does
    async def _obedient_run(stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    stop_event = asyncio.Event()
    ok_task = asyncio.create_task(_obedient_run(stop_event))
    sup.running["s1"] = _Run(config=_stream("s1", running=True), task=ok_task, stop=stop_event)
    await sup._stop_one("s1")
    assert "s1" not in sup.reconnects
