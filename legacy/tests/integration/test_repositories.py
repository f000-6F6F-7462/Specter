from datetime import UTC, datetime

import pytest

from specter.application.ports import UnitOfWorkFactory
from specter.core.errors import NotFoundError
from specter.domain.alerts import Alert, Disposition, MatchEvidence
from specter.domain.catalog import (
    EnrollmentStatus,
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
    WatchlistKind,
)
from specter.domain.quality import QualityReport
from specter.domain.streams import (
    DesiredState,
    RegionOfInterest,
    StreamConfig,
    StreamCredentials,
    StreamProtocol,
    StreamSource,
)
from specter.domain.vision import BBox


def _wl(**over: object) -> Watchlist:
    base: dict[str, object] = {
        "id": "wl_1",
        "owner_id": "o_1",
        "name": "List",
        "type": TargetType.PERSON,
        "kind": WatchlistKind.BLACKLIST,
    }
    base.update(over)
    return Watchlist(**base)  # type: ignore[arg-type]


def _target(tid: str, statuses: list[ImageStatus], **over: object) -> Target:
    images = [
        ReferenceImage(id=f"{tid}_img{i}", blob_key=f"k/{tid}/{i}", status=s)
        for i, s in enumerate(statuses)
    ]
    base: dict[str, object] = {
        "id": tid,
        "watchlist_id": "wl_1",
        "label": "T",
        "type": TargetType.PERSON,
        "images": images,
    }
    base.update(over)
    return Target(**base)  # type: ignore[arg-type]


def _alert(aid: str, **over: object) -> Alert:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    base: dict[str, object] = {
        "id": aid,
        "owner_id": "o_1",
        "stream_id": "st_1",
        "watchlist_id": "wl_1",
        "target_id": "tgt_1",
        "similarity": 0.9,
        "bbox": BBox(1, 2, 3, 4),
        "track_id": 5,
        "frame_ts": now,
        "created_at": now,
    }
    base.update(over)
    return Alert(**base)  # type: ignore[arg-type]


class TestWatchlistRepo:
    async def test_add_get_roundtrip(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.watchlists.add(_wl(name="VIPs", match_threshold=0.9))
        async with uow_factory() as uow:
            got = await uow.watchlists.get("wl_1")
        assert got is not None
        assert got.name == "VIPs"
        assert got.match_threshold == 0.9
        assert got.kind is WatchlistKind.BLACKLIST

    async def test_soft_delete_hides_from_get_and_list(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        async with uow_factory() as uow:
            await uow.watchlists.add(_wl())
        async with uow_factory() as uow:
            await uow.watchlists.soft_delete("wl_1")
        async with uow_factory() as uow:
            assert await uow.watchlists.get("wl_1") is None
            assert await uow.watchlists.list_for_owner("o_1") == []

    async def test_update_missing_raises(self, uow_factory: UnitOfWorkFactory) -> None:
        with pytest.raises(NotFoundError):
            async with uow_factory() as uow:
                await uow.watchlists.update(_wl())


class TestTargetRepo:
    async def _seed_watchlist(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.watchlists.add(_wl())

    async def test_add_with_images_and_status_filter(self, uow_factory: UnitOfWorkFactory) -> None:
        await self._seed_watchlist(uow_factory)
        async with uow_factory() as uow:
            await uow.targets.add(_target("tgt_ready", [ImageStatus.EMBEDDED]))
            await uow.targets.add(
                _target("tgt_partial", [ImageStatus.EMBEDDED, ImageStatus.PENDING])
            )
            await uow.targets.add(_target("tgt_queued", [ImageStatus.PENDING]))

        async with uow_factory() as uow:
            all_targets = await uow.targets.list_for_watchlist("wl_1")
            ready = await uow.targets.list_for_watchlist("wl_1", status=EnrollmentStatus.READY)
            count = await uow.targets.count_for_watchlist("wl_1")

        assert {t.id for t in all_targets} == {"tgt_ready", "tgt_partial", "tgt_queued"}
        assert [t.id for t in ready] == ["tgt_ready"]
        assert count == 3

    async def test_update_reconciles_image_collection(self, uow_factory: UnitOfWorkFactory) -> None:
        await self._seed_watchlist(uow_factory)
        async with uow_factory() as uow:
            await uow.targets.add(_target("tgt_1", [ImageStatus.PENDING, ImageStatus.PENDING]))

        async with uow_factory() as uow:
            target = await uow.targets.get("tgt_1")
            assert target is not None
            target.images[0].mark_embedded("arcface@1")
            target.remove_image("tgt_1_img1")
            target.images.append(
                ReferenceImage(id="tgt_1_img2", blob_key="k/new", status=ImageStatus.PENDING)
            )
            await uow.targets.update(target)

        async with uow_factory() as uow:
            reloaded = await uow.targets.get("tgt_1")
        assert reloaded is not None
        ids = {i.id: i for i in reloaded.images}
        assert set(ids) == {"tgt_1_img0", "tgt_1_img2"}
        assert ids["tgt_1_img0"].status is ImageStatus.EMBEDDED
        assert ids["tgt_1_img0"].model_version == "arcface@1"

    async def test_quality_json_roundtrip(self, uow_factory: UnitOfWorkFactory) -> None:
        await self._seed_watchlist(uow_factory)
        report = QualityReport(score=0.8, blur=0.1, face_px=120, yaw_deg=5.0, brightness=0.6)
        async with uow_factory() as uow:
            target = _target("tgt_1", [ImageStatus.EMBEDDED])
            target.images[0].quality = report
            await uow.targets.add(target)
        async with uow_factory() as uow:
            reloaded = await uow.targets.get("tgt_1")
        assert reloaded is not None
        assert reloaded.images[0].quality == report

    async def test_list_by_batch(self, uow_factory: UnitOfWorkFactory) -> None:
        await self._seed_watchlist(uow_factory)
        async with uow_factory() as uow:
            await uow.targets.add(_target("tgt_a", [ImageStatus.PENDING], batch_id="bat_1"))
            await uow.targets.add(_target("tgt_b", [ImageStatus.PENDING], batch_id="bat_1"))
            await uow.targets.add(_target("tgt_c", [ImageStatus.PENDING], batch_id="bat_2"))
        async with uow_factory() as uow:
            batch = await uow.targets.list_by_batch("bat_1")
        assert {t.id for t in batch} == {"tgt_a", "tgt_b"}


class TestStreamRepo:
    async def test_roundtrip_with_nested_value_objects(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        cfg = StreamConfig(
            id="st_1",
            owner_id="o_1",
            name="Lobby",
            source=StreamSource(
                protocol=StreamProtocol.RTSP,
                url="rtsp://cam/1",
                credentials=StreamCredentials(username="u", password="p"),
            ),
            watchlist_ids=["wl_1", "wl_2"],
            roi=[RegionOfInterest(0.1, 0.1, 0.5, 0.5)],
            detect_classes=["person", "car"],
            enabled=True,
            desired_state=DesiredState.RUNNING,
        )
        async with uow_factory() as uow:
            await uow.streams.add(cfg)
        async with uow_factory() as uow:
            got = await uow.streams.get("st_1")
            enabled = await uow.streams.list_enabled()
            owned = await uow.streams.list_for_owner("o_1")
        assert got is not None
        assert got.source.credentials == StreamCredentials("u", "p")
        assert got.roi[0].w == 0.5
        assert got.desired_state is DesiredState.RUNNING
        assert [s.id for s in enabled] == ["st_1"]
        assert [s.id for s in owned] == ["st_1"]

    async def test_update_and_delete(self, uow_factory: UnitOfWorkFactory) -> None:
        cfg = StreamConfig(
            id="st_1",
            owner_id="o_1",
            name="Lobby",
            source=StreamSource(protocol=StreamProtocol.RTMP, url="rtmp://x/y"),
            enabled=True,
        )
        async with uow_factory() as uow:
            await uow.streams.add(cfg)
        async with uow_factory() as uow:
            cfg.name = "Renamed"
            cfg.enabled = False
            await uow.streams.update(cfg)
        async with uow_factory() as uow:
            reloaded = await uow.streams.get("st_1")
            assert reloaded is not None
            assert reloaded.name == "Renamed"
            assert await uow.streams.list_enabled() == []
        async with uow_factory() as uow:
            await uow.streams.delete("st_1")
        async with uow_factory() as uow:
            assert await uow.streams.get("st_1") is None
        with pytest.raises(NotFoundError):
            async with uow_factory() as uow:
                await uow.streams.delete("st_1")


class TestAlertRepo:
    async def test_add_list_filters_and_cursor(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.alerts.add(_alert("aid_1", similarity=0.7))
            await uow.alerts.add(_alert("aid_2", similarity=0.95, stream_id="st_2"))
            await uow.alerts.add(_alert("aid_3", similarity=0.85))

        async with uow_factory() as uow:
            all_for_owner = await uow.alerts.list_for_owner("o_1")
            high = await uow.alerts.list_for_owner("o_1", min_confidence=0.8)
            one_stream = await uow.alerts.list_for_owner("o_1", stream_id="st_2")
            page1 = await uow.alerts.list_for_owner("o_1", limit=2)
            page2 = await uow.alerts.list_for_owner("o_1", limit=2, cursor=page1[-1].id)

        assert len(all_for_owner) == 3
        assert {a.id for a in high} == {"aid_2", "aid_3"}
        assert [a.id for a in one_stream] == ["aid_2"]
        assert len(page1) == 2 and len(page2) == 1
        assert page1[-1].id not in {a.id for a in page2}

    async def test_update_persists_disposition(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.alerts.add(_alert("aid_1", evidence=MatchEvidence(snapshot_key="s")))
        async with uow_factory() as uow:
            alert = await uow.alerts.get("aid_1")
            assert alert is not None
            alert.resolve(Disposition.FALSE_POSITIVE, note="glare")
            await uow.alerts.update(alert)
        async with uow_factory() as uow:
            reloaded = await uow.alerts.get("aid_1")
        assert reloaded is not None
        assert reloaded.disposition is Disposition.FALSE_POSITIVE
        assert reloaded.acknowledged is True
        assert reloaded.note == "glare"
        assert reloaded.evidence.snapshot_key == "s"


class TestUnitOfWork:
    async def test_rollback_on_exception(self, uow_factory: UnitOfWorkFactory) -> None:
        with pytest.raises(RuntimeError):
            async with uow_factory() as uow:
                await uow.watchlists.add(_wl())
                raise RuntimeError("boom")
        async with uow_factory() as uow:
            assert await uow.watchlists.get("wl_1") is None

    async def test_commit_on_clean_exit(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.watchlists.add(_wl())
        async with uow_factory() as uow:
            assert await uow.watchlists.get("wl_1") is not None
