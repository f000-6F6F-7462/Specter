"""EventBus over Redis Streams.

``publish`` -> ``XADD`` with approximate ``MAXLEN``. ``consume`` creates the group if
needed, then loops ``XREADGROUP ... BLOCK`` forever; each delivery's ``ack`` is an
``XACK``. Poison entries (unparseable payload) are acked and dropped.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from specter.contracts import BrokerMessage, parse_message


@dataclass(slots=True)
class _Delivery:
    id: str
    message: BrokerMessage
    _redis: Redis
    _stream: str
    _group: str

    async def ack(self) -> None:
        await self._redis.xack(self._stream, self._group, self.id)


class RedisStreamBus:
    def __init__(
        self,
        url: str,
        *,
        maxlen: int = 100_000,
        block_ms: int = 5_000,
        batch: int = 32,
    ) -> None:
        self._redis: Redis = Redis.from_url(url, decode_responses=True)
        self._maxlen = maxlen
        self._block_ms = block_ms
        self._batch = batch

    async def publish(self, stream: str, key: str, message: BrokerMessage) -> None:
        await self._redis.xadd(
            stream,
            {"key": key, "data": message.model_dump_json()},
            maxlen=self._maxlen,
            approximate=True,
        )

    async def consume(self, stream: str, *, group: str, consumer: str) -> AsyncIterator[_Delivery]:
        await self._ensure_group(stream, group)
        while True:
            response: Any = await self._redis.xreadgroup(
                group, consumer, {stream: ">"}, count=self._batch, block=self._block_ms
            )
            for _name, entries in response or []:
                for entry_id, fields in entries:
                    try:
                        message = parse_message(fields["data"])
                    except ValueError, KeyError:
                        await self._redis.xack(stream, group, entry_id)
                        continue
                    yield _Delivery(
                        id=entry_id,
                        message=message,
                        _redis=self._redis,
                        _stream=stream,
                        _group=group,
                    )

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def aclose(self) -> None:
        await self._redis.aclose()
