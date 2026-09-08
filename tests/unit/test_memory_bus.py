from datetime import UTC, datetime

from specter.contracts import EnrollJobMessage
from specter.contracts.streams import JOBS_ENROLL
from specter.infrastructure.bus.memory import MemoryBus


def _job(n: int) -> EnrollJobMessage:
    return EnrollJobMessage(
        event_id=f"evt_{n}",
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        owner_id="o_1",
        batch_id="bat_1",
        target_id=f"tgt_{n}",
        image_id=f"img_{n}",
        blob_key=f"blobs/o_1/{n}.jpg",
        modality="face",
    )


async def test_publish_then_consume_drains_and_acks() -> None:
    bus = MemoryBus()
    await bus.publish(JOBS_ENROLL, "tgt_0", _job(0))
    await bus.publish(JOBS_ENROLL, "tgt_1", _job(1))

    seen: list[str] = []
    async for delivery in bus.consume(JOBS_ENROLL, group="enrollers", consumer="c1"):
        assert isinstance(delivery.message, EnrollJobMessage)
        seen.append(delivery.message.target_id)
        await delivery.ack()

    assert seen == ["tgt_0", "tgt_1"]
    assert len(bus.acked) == 2
    assert [m.target_id for m in bus.published(JOBS_ENROLL, EnrollJobMessage)] == ["tgt_0", "tgt_1"]


async def test_consume_of_empty_stream_returns_immediately() -> None:
    bus = MemoryBus()
    async for _ in bus.consume("nope", group="g", consumer="c"):
        raise AssertionError("should not yield")


async def _target_ids(bus: MemoryBus, group: str) -> list[str]:
    out: list[str] = []
    async for delivery in bus.consume(JOBS_ENROLL, group=group, consumer="c"):
        assert isinstance(delivery.message, EnrollJobMessage)
        out.append(delivery.message.target_id)
    return out


async def test_group_cursor_is_independent() -> None:
    bus = MemoryBus()
    await bus.publish(JOBS_ENROLL, "tgt_0", _job(0))

    assert await _target_ids(bus, "a") == ["tgt_0"]
    assert await _target_ids(bus, "b") == ["tgt_0"]  # each group sees it once
