"""End-to-end ``run_stream`` on fakes: synthetic frames -> centre-box detector ->
IoU tracker -> constant embedder -> in-memory vector index -> N-of-M policy -> alert +
match event.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from specter.application.pipeline import PipelineDeps, PipelineTuning, run_stream
from specter.contracts import (
    EVENTS_MATCH,
    EVENTS_STREAM_STATUS,
    MatchEventMessage,
    StreamStatusMessage,
)
from specter.core.clock import FrozenClock
from specter.domain.catalog import (
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
    WatchlistKind,
)
from specter.domain.streams import SamplingConfig, StreamConfig, StreamProtocol, StreamSource
from specter.domain.vision import Detection, Embedding, Frame
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import (
    SqlAlchemyUnitOfWork,
    create_all,
    create_engine,
    session_factory,
)
from specter.infrastructure.media.codec import NumpyFrameCodec
from specter.infrastructure.media.fakes import SyntheticFrameSource
from specter.infrastructure.ml.detector import FakeDetector
from specter.infrastructure.ml.embedder import FakeEmbedder
from specter.infrastructure.ml.tracker import IouTracker
from specter.infrastructure.vectors.memory import InMemoryVectorIndex

_MATCH_VECTOR = np.ones(512, dtype=np.float32) / np.sqrt(512)

UowFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(slots=True)
class PipelineHarness:
    deps: PipelineDeps
    stream: StreamConfig
    bus: MemoryBus
    blob: MemoryBlobStore
    uow_factory: UowFactory

    def match_events(self) -> list[MatchEventMessage]:
        return self.bus.published(EVENTS_MATCH, MatchEventMessage)

    def statuses(self) -> list[str]:
        return [m.status for m in self.bus.published(EVENTS_STREAM_STATUS, StreamStatusMessage)]


class _SlowDetector:
    """A real-shaped detector that stalls, to force queue-full shedding."""

    def __init__(self, delay_s: float) -> None:
        self._inner = FakeDetector()
        self._delay = delay_s

    async def detect(self, frames: Sequence[Frame]) -> list[list[Detection]]:
        await asyncio.sleep(self._delay)
        return await self._inner.detect(frames)


@pytest.fixture
async def harness(request: pytest.FixtureRequest) -> AsyncIterator[PipelineHarness]:
    params: Any = getattr(request, "param", None) or {}
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    await _seed_catalog(uow_factory)
    vectors = InMemoryVectorIndex()
    await vectors.upsert(
        [
            Embedding(
                modality="face",
                vector=_MATCH_VECTOR,
                target_id="tgt_1",
                image_id="img_1",
                payload={
                    "owner_id": "o_1",
                    "watchlist_id": "wl_1",
                    "target_id": "tgt_1",
                    "enabled": True,
                },
            )
        ]
    )

    bus, blob = MemoryBus(), MemoryBlobStore()
    detector: Any = (
        _SlowDetector(float(params["detector_delay_s"]))
        if "detector_delay_s" in params
        else FakeDetector()
    )
    frames = int(params.get("frames", 6))
    source_fps = params.get("source_fps")

    deps = PipelineDeps(
        uow_factory=uow_factory,
        frame_source_factory=lambda s: SyntheticFrameSource(
            s.id, count=frames, size=(64, 64), fps=source_fps, moving=True
        ),
        detector=detector,
        tracker=IouTracker(),
        embedders={"face": FakeEmbedder("face", constant=_MATCH_VECTOR)},
        vectors=vectors,
        blob=blob,
        bus=bus,
        clock=FrozenClock(),
        codec=NumpyFrameCodec(),
        tuning=PipelineTuning(
            need=1,
            window=1,
            ema_alpha=1.0,
            cooldown_s=1_000.0,
            directory_refresh_s=1e9,
            queue_size=int(params.get("queue_size", 8)),
        ),
    )
    stream = StreamConfig(
        id="stream_1",
        owner_id="o_1",
        name="Front cam",
        source=StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://cam/1"),
        watchlist_ids=["wl_1"],
        sampling=SamplingConfig(
            target_fps=float(params.get("target_fps", 10.0)), motion_gating=False
        ),
    )
    yield PipelineHarness(deps=deps, stream=stream, bus=bus, blob=blob, uow_factory=uow_factory)
    await engine.dispose()


async def _seed_catalog(uow_factory: UowFactory) -> None:
    async with uow_factory() as uow:
        await uow.watchlists.add(
            Watchlist(
                id="wl_1",
                owner_id="o_1",
                name="VIP",
                type=TargetType.PERSON,
                kind=WatchlistKind.BLACKLIST,
                match_threshold=0.78,
            )
        )
        await uow.targets.add(
            Target(
                id="tgt_1",
                watchlist_id="wl_1",
                label="Dana",
                type=TargetType.PERSON,
                images=[
                    ReferenceImage(
                        id="img_1",
                        blob_key="k",
                        status=ImageStatus.EMBEDDED,
                        model_version="fake@1",
                    )
                ],
            )
        )


async def test_match_fires_once_and_is_persisted_and_published(harness: PipelineHarness) -> None:
    outcome = await run_stream(harness.deps, harness.stream, stop=asyncio.Event())

    assert outcome.reason == "source_exhausted"
    assert outcome.metrics.processed == 6
    assert outcome.metrics.matches == 1  # cooldown suppresses the rest

    events = harness.match_events()
    assert len(events) == 1
    assert events[0].match.target_id == "tgt_1"
    assert events[0].match.threshold == pytest.approx(0.78)
    assert events[0].stream.id == "stream_1"
    assert events[0].evidence.snapshot_url and events[0].evidence.crop_url

    async with harness.uow_factory() as uow:
        alerts = await uow.alerts.list_for_owner("o_1")
    assert len(alerts) == 1
    assert alerts[0].target_id == "tgt_1"

    assert {"provisioning", "running", "stopped"}.issubset(set(harness.statuses()))
    assert len(harness.blob.keys()) == 2  # snapshot + crop


async def test_stop_before_start_processes_nothing(harness: PipelineHarness) -> None:
    stop = asyncio.Event()
    stop.set()
    outcome = await run_stream(harness.deps, harness.stream, stop=stop)
    assert outcome.reason == "stopped"
    assert outcome.metrics.processed == 0
    assert harness.match_events() == []


@pytest.mark.parametrize("harness", [{"frames": 20, "target_fps": 3.0}], indirect=True)
async def test_rate_cap_sheds_frames(harness: PipelineHarness) -> None:
    outcome = await run_stream(harness.deps, harness.stream, stop=asyncio.Event())
    m = outcome.metrics
    assert m.received == 20
    assert m.dropped > 0
    assert m.processed + m.dropped == m.received


@pytest.mark.parametrize(
    "harness",
    [
        {
            "frames": 40,
            "source_fps": 200.0,
            "target_fps": 1_000.0,
            "queue_size": 2,
            "detector_delay_s": 0.02,
        }
    ],
    indirect=True,
)
async def test_slow_inference_sheds_at_the_queue(harness: PipelineHarness) -> None:
    outcome = await run_stream(harness.deps, harness.stream, stop=asyncio.Event())
    assert outcome.metrics.dropped > 0
    assert outcome.metrics.processed >= 1
