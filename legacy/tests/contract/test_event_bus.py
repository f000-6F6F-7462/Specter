"""EventBus contract — holds for MemoryBus and RedisStreamBus alike."""

import asyncio
import os
from datetime import UTC, datetime

import pytest

from specter.application.ports import EventBus
from specter.contracts import EnrollJobMessage

_STREAM = "specter:test:ct-jobs"
_REDIS_URL = os.environ.get("SPECTER_TEST_REDIS_URL", "redis://localhost:6379/15")


def _job(n: int) -> EnrollJobMessage:
    return EnrollJobMessage(
        event_id=f"evt_{n}",
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        owner_id="o_ct",
        batch_id="bat_ct",
        target_id=f"tgt_{n}",
        image_id=f"img_{n}",
        blob_key=f"blobs/o_ct/{n}.jpg",
        modality="face",
    )


async def test_publish_then_consume_round_trips_in_order(event_bus: EventBus, drain) -> None:
    await event_bus.publish(_STREAM, "tgt_0", _job(0))
    await event_bus.publish(_STREAM, "tgt_1", _job(1))

    got = await drain(event_bus, _STREAM, group="g-order", count=2)

    assert [m.target_id for m in got] == ["tgt_0", "tgt_1"]
    assert all(isinstance(m, EnrollJobMessage) for m in got)
    assert got[0].modality == "face" and got[0].blob_key == "blobs/o_ct/0.jpg"


async def test_each_group_gets_every_message(event_bus: EventBus, drain) -> None:
    await event_bus.publish(_STREAM, "tgt_0", _job(0))

    first = await drain(event_bus, _STREAM, group="g-a", count=1)
    second = await drain(event_bus, _STREAM, group="g-b", count=1)

    assert [m.target_id for m in first] == ["tgt_0"]
    assert [m.target_id for m in second] == ["tgt_0"]


async def test_acked_message_is_not_redelivered_to_the_same_group(
    event_bus: EventBus, drain
) -> None:
    await event_bus.publish(_STREAM, "tgt_0", _job(0))
    await drain(event_bus, _STREAM, group="g-once", count=1)

    again: list = []
    try:
        async with asyncio.timeout(1):
            async for delivery in event_bus.consume(_STREAM, group="g-once", consumer="ct"):
                again.append(delivery.message)
    except TimeoutError:
        pass
    assert not again


@pytest.mark.integration
async def test_redis_consume_survives_repeated_idle_block_windows() -> None:
    """Regression: redis-py raises its own ``TimeoutError`` — not a broken connection —
    when ``XREADGROUP ... BLOCK`` finds nothing in time; ``consume`` must swallow that
    and keep polling rather than let it kill the caller's loop."""
    from specter.infrastructure.bus.redis_stream import RedisStreamBus

    bus = RedisStreamBus(_REDIS_URL, block_ms=300)

    async def _consume_one() -> None:
        async for _delivery in bus.consume("specter:test:ct-idle", group="g-idle", consumer="ct"):
            break

    task = asyncio.create_task(_consume_one())
    await asyncio.sleep(1.5)  # several idle BLOCK cycles

    assert not task.done()

    task.cancel()
    await bus.aclose()
