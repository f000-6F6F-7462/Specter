"""The actual point of the shared inference layer: two concurrently-running streams
that share one ``BatchedDetector`` get their frames coalesced into shared forward
passes, through the real ``run_stream`` graph — not just the unit-level MicroBatcher
mechanics (see ``tests/unit/test_inference_service.py``).
"""

import asyncio
from collections.abc import Callable

from specter.application.pipeline import PipelineDeps, PipelineTuning, run_stream
from specter.core.clock import FrozenClock
from specter.domain.streams import SamplingConfig, StreamConfig, StreamProtocol, StreamSource
from specter.domain.vision import Detection, Frame
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
from specter.infrastructure.ml.embedder import FakeEmbedder
from specter.infrastructure.ml.inference_service import BatchedDetector
from specter.infrastructure.ml.tracker import IouTracker
from specter.infrastructure.vectors.memory import InMemoryVectorIndex

UowFactory = Callable[[], SqlAlchemyUnitOfWork]


class _CountingSyncDetector:
    """No detections — this test is about call coalescing, not matching."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]:
        self.batch_sizes.append(len(frames))
        return [[] for _ in frames]


def _stream(stream_id: str) -> StreamConfig:
    return StreamConfig(
        id=stream_id,
        owner_id="o_1",
        name=stream_id,
        source=StreamSource(protocol=StreamProtocol.RTSP, url=f"rtsp://{stream_id}"),
        sampling=SamplingConfig(target_fps=1_000.0, motion_gating=False),
    )


async def test_two_concurrent_streams_share_one_batched_detector() -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    stub = _CountingSyncDetector()
    clock = FrozenClock()
    async with BatchedDetector(stub, max_batch=32, max_delay_ms=200) as detector:
        # One PipelineDeps, shared across both run_stream calls below — exactly how
        # specter-ingest hands every stream the same Container-built detector.
        deps = PipelineDeps(
            uow_factory=uow_factory,
            frame_source_factory=lambda s: SyntheticFrameSource(
                s.id, count=5, size=(8, 8), fps=None
            ),
            detector=detector,
            tracker=IouTracker(),
            embedders={"face": FakeEmbedder("face")},
            vectors=InMemoryVectorIndex(),
            blob=MemoryBlobStore(),
            bus=MemoryBus(),
            health=InMemoryHealthStore(clock),
            clock=clock,
            codec=NumpyFrameCodec(),
            tuning=PipelineTuning(directory_refresh_s=1e9),
        )

        outcomes = await asyncio.gather(
            run_stream(deps, _stream("cam_a"), stop=asyncio.Event()),
            run_stream(deps, _stream("cam_b"), stop=asyncio.Event()),
        )
    await engine.dispose()

    total_frames = sum(o.metrics.processed for o in outcomes)
    assert total_frames == 10  # 5 frames x 2 streams

    assert sum(stub.batch_sizes) == 10  # every frame still reached the model
    assert len(stub.batch_sizes) < total_frames  # ...but coalesced into fewer calls
