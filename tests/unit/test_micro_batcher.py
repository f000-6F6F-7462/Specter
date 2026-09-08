import asyncio

import pytest

from specter.infrastructure.ml.micro_batcher import MicroBatcher


async def test_flushes_on_max_batch() -> None:
    seen: list[list[int]] = []

    def double(batch: list[int]) -> list[int]:
        seen.append(list(batch))
        return [x * 2 for x in batch]

    async with MicroBatcher(double, max_batch=3, max_delay_ms=1000) as mb:
        results = await asyncio.gather(*(mb.submit(i) for i in range(3)))

    assert results == [0, 2, 4]
    assert seen == [[0, 1, 2]]  # one call, not three


async def test_flushes_on_timeout() -> None:
    calls = 0

    def identity(batch: list[int]) -> list[int]:
        nonlocal calls
        calls += 1
        return batch

    async with MicroBatcher(identity, max_batch=100, max_delay_ms=15) as mb:
        assert await mb.submit(7) == 7
        assert await mb.submit(8) == 8

    assert calls == 2  # each lone submit flushed on its own timeout


async def test_submit_many_scatters_results_in_order() -> None:
    async with MicroBatcher(lambda b: [x + 1 for x in b], max_batch=8, max_delay_ms=5) as mb:
        assert await mb.submit_many([10, 20, 30]) == [11, 21, 31]


async def test_error_is_raised_to_every_caller() -> None:
    def boom(_: list[int]) -> list[int]:
        raise RuntimeError("model exploded")

    async with MicroBatcher(boom, max_batch=2, max_delay_ms=1000) as mb:
        with pytest.raises(RuntimeError, match="model exploded"):
            await asyncio.gather(mb.submit(1), mb.submit(2))
