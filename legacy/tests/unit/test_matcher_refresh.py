"""_Matcher.maybe_refresh — the two ways a running stream's resolved watchlist/target
snapshot gets rebuilt: the slow unconditional ``directory_refresh_s`` timer, and the
much faster watchlist-version check piggybacked on the health-publish tick.
"""

from collections.abc import AsyncIterator, Callable

import pytest

from specter.application.pipeline.deps import PipelineDeps, PipelineTuning
from specter.application.pipeline.runner import _HealthTracker, _Matcher
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

_STREAM = StreamConfig(
    id="s1",
    owner_id="o_1",
    name="s1",
    source=StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://cam/1"),
    watchlist_ids=["wl_1"],
    sampling=SamplingConfig(target_fps=10.0, motion_gating=False),
)


@pytest.fixture
async def env() -> AsyncIterator[tuple[PipelineDeps, UowFactory, FrozenClock]]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

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

    clock = FrozenClock()
    deps = PipelineDeps(
        uow_factory=uow_factory,
        frame_source_factory=lambda s: SyntheticFrameSource(s.id, count=1),
        detector=FakeDetector(),
        tracker=IouTracker(),
        embedders={"face": FakeEmbedder("face")},
        vectors=InMemoryVectorIndex(),
        blob=MemoryBlobStore(),
        bus=MemoryBus(),
        health=InMemoryHealthStore(clock),
        clock=clock,
        codec=NumpyFrameCodec(),
        tuning=PipelineTuning(directory_refresh_s=100.0, health_publish_interval_s=2.0),
    )
    yield deps, uow_factory, clock
    await engine.dispose()


async def test_maybe_refresh_is_a_noop_before_either_interval_elapses(
    env: tuple[PipelineDeps, UowFactory, FrozenClock],
) -> None:
    deps, _uow_factory, clock = env
    matcher = await _Matcher.load(deps, _STREAM)
    directory_before = matcher.directory
    vitals = _HealthTracker(clock)

    await matcher.maybe_refresh(deps, _STREAM, vitals)

    assert matcher.directory is directory_before


async def test_maybe_refresh_reloads_unconditionally_after_directory_refresh_s(
    env: tuple[PipelineDeps, UowFactory, FrozenClock],
) -> None:
    deps, _uow_factory, clock = env
    matcher = await _Matcher.load(deps, _STREAM)
    directory_before = matcher.directory
    vitals = _HealthTracker(clock)

    clock.advance(deps.tuning.directory_refresh_s + 1.0)
    await matcher.maybe_refresh(deps, _STREAM, vitals)

    assert matcher.directory is not directory_before


async def test_maybe_refresh_notices_a_watchlist_edit_well_before_directory_refresh_s(
    env: tuple[PipelineDeps, UowFactory, FrozenClock],
) -> None:
    deps, uow_factory, clock = env
    matcher = await _Matcher.load(deps, _STREAM)
    assert matcher.directory.has_targets is True
    vitals = _HealthTracker(clock)

    # An operator disables the target elsewhere in the process — bumps the version,
    # exactly what application.catalog.use_cases.update_target does.
    async with uow_factory() as uow:
        target = await uow.targets.get("tgt_1")
        assert target is not None
        target.enabled = False
        await uow.targets.update(target)
    await deps.health.bump_watchlist_version("wl_1")

    clock.advance(deps.tuning.health_publish_interval_s + 0.1)  # not directory_refresh_s
    await matcher.maybe_refresh(deps, _STREAM, vitals)

    assert matcher.directory.has_targets is False


async def test_maybe_refresh_ignores_an_unrelated_health_tick(
    env: tuple[PipelineDeps, UowFactory, FrozenClock],
) -> None:
    """The health-publish tick alone doesn't force a reload — only a real version bump
    does; otherwise every stream would hit the database every couple of seconds."""
    deps, _uow_factory, clock = env
    matcher = await _Matcher.load(deps, _STREAM)
    directory_before = matcher.directory
    vitals = _HealthTracker(clock)

    clock.advance(deps.tuning.health_publish_interval_s + 0.1)
    await matcher.maybe_refresh(deps, _STREAM, vitals)

    assert matcher.directory is directory_before
