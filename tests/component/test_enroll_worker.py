"""End-to-end: publish enroll jobs -> run the worker -> vectors + rows + status events."""

import dataclasses
from datetime import UTC, datetime

import numpy as np

from specter.application.enrollment import run_enrollment
from specter.contracts import EnrollJobMessage, EnrollmentStatusMessage
from specter.contracts.streams import ENROLL_GROUP, EVENTS_ENROLLMENT, JOBS_ENROLL
from specter.domain.catalog import ImageStatus
from specter.entrypoints.workers.enroll_worker import consume_forever
from specter.infrastructure.ml.fakes import FakeFaceEmbeddingService
from tests.component.conftest import EnrollFixture


def _job(fx: EnrollFixture, i: int) -> EnrollJobMessage:
    return EnrollJobMessage(
        event_id=f"evt_{i}",
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        owner_id="o_1",
        batch_id="bat_1",
        target_id=fx.target_id,
        image_id=fx.image_ids[i],
        blob_key=fx.blob_keys[i],
        modality="face",
    )


async def _reload_images(fx: EnrollFixture) -> list:
    async with fx.uow_factory() as uow:
        target = await uow.targets.get(fx.target_id)
        assert target is not None
        return list(target.images)


class TestHappyPath:
    async def test_worker_embeds_every_queued_image(self, enroll: EnrollFixture) -> None:
        for i in range(2):
            await enroll.bus.publish(JOBS_ENROLL, enroll.target_id, _job(enroll, i))

        await consume_forever(enroll.deps, consumer="c1")

        # vectors upserted, searchable, correct payload
        hits = await enroll.vectors.search(
            "face", np.ones(512), owner_id="o_1", watchlist_ids=["wl_1"], top_k=5
        )
        assert {h.target_id for h in hits} == {"tgt_1"}
        assert len(hits) == 2

        # rows flipped to embedded with quality + model version
        images = await _reload_images(enroll)
        assert {i.status for i in images} == {ImageStatus.EMBEDDED}
        assert all(i.model_version == FakeFaceEmbeddingService.model_version for i in images)
        assert all(i.quality is not None for i in images)

        # one status event per image, and every job acked
        published = enroll.bus.published(EVENTS_ENROLLMENT, EnrollmentStatusMessage)
        assert [m.status for m in published] == ["embedded", "embedded"]
        assert len(enroll.bus.acked) == 2

    async def test_reembedding_the_same_image_replaces_the_vector(
        self, enroll: EnrollFixture
    ) -> None:
        await enroll.bus.publish(JOBS_ENROLL, enroll.target_id, _job(enroll, 0))
        await enroll.bus.publish(JOBS_ENROLL, enroll.target_id, _job(enroll, 0))
        await consume_forever(enroll.deps, consumer="c1")

        hits = await enroll.vectors.search(
            "face", np.ones(512), owner_id="o_1", watchlist_ids=["wl_1"]
        )
        assert len(hits) == 1  # keyed by image_id


class TestRejection:
    async def test_no_face_marks_rejected_and_upserts_nothing(self, enroll: EnrollFixture) -> None:
        deps = dataclasses.replace(enroll.deps, faces=FakeFaceEmbeddingService(faces_found=0))
        await enroll.bus.publish(JOBS_ENROLL, enroll.target_id, _job(enroll, 0))
        await consume_forever(deps, consumer="c1")

        images = {i.id: i for i in await _reload_images(enroll)}
        assert images["img_0"].status is ImageStatus.REJECTED
        assert images["img_0"].rejection_reason.value == "no_detection"

        assert (
            await enroll.vectors.search(
                "face", np.ones(512), owner_id="o_1", watchlist_ids=["wl_1"]
            )
            == []
        )
        assert (
            enroll.bus.published(EVENTS_ENROLLMENT, EnrollmentStatusMessage)[0].status == "rejected"
        )
        assert len(enroll.bus.acked) == 1

    async def test_run_enrollment_on_stale_job_is_a_noop(self, enroll: EnrollFixture) -> None:
        stale = _job(enroll, 0).model_copy(update={"target_id": "tgt_gone"})
        status = await run_enrollment(enroll.deps, stale)
        assert status.status == "rejected"
        assert (
            await enroll.vectors.search(
                "face", np.ones(512), owner_id="o_1", watchlist_ids=["wl_1"]
            )
            == []
        )


async def test_non_job_message_is_acked_and_ignored(enroll: EnrollFixture) -> None:
    from specter.contracts import StreamStatusMessage

    await enroll.bus.publish(
        JOBS_ENROLL,
        "x",
        StreamStatusMessage(
            event_id="evt_x",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            owner_id="o_1",
            stream_id="st_1",
            status="running",
        ),
    )
    await consume_forever(enroll.deps, consumer="c1")
    assert len(enroll.bus.acked) == 1
    assert enroll.bus.published(EVENTS_ENROLLMENT, EnrollmentStatusMessage) == []


def test_enroll_group_name() -> None:
    assert ENROLL_GROUP == "enrollers"
