"""In-process EventBus.

The fallback when ``SPECTER_BUS != redis``. ``consume`` drains
what is currently on the stream and then returns — unlike the real Redis bus which
blocks — so component tests terminate.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from specter.contracts import BrokerMessage

type _Entry = tuple[str, str, BrokerMessage]


@dataclass(slots=True)
class _Delivery:
    id: str
    message: BrokerMessage
    _acked: set[str]

    async def ack(self) -> None:
        self._acked.add(self.id)


class MemoryBus:
    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, str, BrokerMessage]]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._acked: set[str] = set()
        self._seq = 0

    async def publish(self, stream: str, key: str, message: BrokerMessage) -> None:
        self._seq += 1
        self._streams.setdefault(stream, []).append((f"{self._seq}-0", key, message))

    async def consume(self, stream: str, *, group: str, consumer: str) -> AsyncIterator[_Delivery]:
        del consumer  # single-consumer semantics are enough for the fake
        cursor = (stream, group)
        entries = self._streams.setdefault(stream, [])
        while self._cursors.get(cursor, 0) < len(entries):
            index = self._cursors.get(cursor, 0)
            entry_id, _key, message = entries[index]
            self._cursors[cursor] = index + 1
            yield _Delivery(id=entry_id, message=message, _acked=self._acked)

    def published[M: BrokerMessage](self, stream: str, message_type: type[M]) -> list[M]:
        return [
            message
            for _id, _key, message in self._streams.get(stream, [])
            if isinstance(message, message_type)
        ]

    @property
    def acked(self) -> set[str]:
        return set(self._acked)
