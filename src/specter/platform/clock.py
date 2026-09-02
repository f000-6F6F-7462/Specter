"""Time as an injected dependency.

`now()` returns monotonic seconds and is what decision logic (cooldowns, staleness)
"""

from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...

    def wall(self) -> datetime: ...


class SystemClock:
    __slots__ = ()

    def now(self) -> float:
        return monotonic()

    def wall(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Deterministic test double. `advance()` moves monotonic and wall time together."""

    __slots__ = ("_mono", "_wall")

    def __init__(self, *, mono: float = 1_000.0, wall: datetime | None = None) -> None:
        self._mono = mono
        self._wall = wall if wall is not None else datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> float:
        return self._mono

    def wall(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += timedelta(seconds=seconds)
