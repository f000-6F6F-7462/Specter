"""Cross-caller micro-batching for a sync inference function.

Collect calls from every caller for up to ``max_delay_ms`` or until ``max_batch``, run
ONE batched forward pass on a worker thread (torch / onnxruntime release the GIL), then
scatter results back. Used by the real-time pipeline.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Request:
    payload: Any
    future: asyncio.Future[Any]


class MicroBatcher:
    def __init__(
        self,
        fn: Callable[[list[Any]], Sequence[Any]],
        *,
        max_batch: int = 16,
        max_delay_ms: float = 8.0,
    ) -> None:
        self._fn = fn
        self._max_batch = max_batch
        self._max_delay = max_delay_ms / 1000
        self._queue: asyncio.Queue[_Request] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "MicroBatcher":
        self._task = asyncio.create_task(self._loop(), name="micro-batcher")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def submit(self, payload: Any) -> Any:
        request = _Request(payload=payload, future=asyncio.get_running_loop().create_future())
        await self._queue.put(request)
        return await request.future

    async def submit_many(self, payloads: Sequence[Any]) -> list[Any]:
        return await asyncio.gather(*(self.submit(p) for p in payloads))

    async def _loop(self) -> None:
        while True:
            batch = [await self._queue.get()]
            try:
                async with asyncio.timeout(self._max_delay):
                    while len(batch) < self._max_batch:
                        batch.append(await self._queue.get())
            except TimeoutError:
                pass
            await self._run(batch)

    async def _run(self, batch: list[_Request]) -> None:
        try:
            results = await asyncio.to_thread(self._fn, [r.payload for r in batch])
            for request, result in zip(batch, results, strict=True):
                if not request.future.done():
                    request.future.set_result(result)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Fan one model failure out to every caller in the batch.
            for request in batch:
                if not request.future.done():
                    request.future.set_exception(exc)
