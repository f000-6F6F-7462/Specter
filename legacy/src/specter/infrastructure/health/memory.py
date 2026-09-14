"""In-memory HealthStore. TTLs are measured against an injected ``Clock`` (monotonic
seconds) so tests can freeze and advance time instead of racing a real wall clock.
"""

from collections import defaultdict

from specter.core.clock import Clock
from specter.domain.streams import StreamHealth


class InMemoryHealthStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._health: dict[str, tuple[StreamHealth, float]] = {}
        self._cooldowns: dict[str, float] = {}
        self._versions: dict[str, int] = defaultdict(int)

    async def set_health(self, stream_id: str, health: StreamHealth, *, ttl_s: int = 10) -> None:
        self._health[stream_id] = (health, self._clock.now() + ttl_s)

    async def get_health(self, stream_id: str) -> StreamHealth | None:
        entry = self._health.get(stream_id)
        if entry is None:
            return None
        health, expires_at = entry
        if self._clock.now() >= expires_at:
            del self._health[stream_id]
            return None
        return health

    async def in_cooldown(self, key: str) -> bool:
        expires_at = self._cooldowns.get(key)
        if expires_at is None:
            return False
        if self._clock.now() >= expires_at:
            del self._cooldowns[key]
            return False
        return True

    async def mark_cooldown(self, key: str, *, ttl_s: int) -> None:
        self._cooldowns[key] = self._clock.now() + ttl_s

    async def bump_watchlist_version(self, watchlist_id: str) -> int:
        self._versions[watchlist_id] += 1
        return self._versions[watchlist_id]

    async def get_watchlist_version(self, watchlist_id: str) -> int:
        return self._versions[watchlist_id]
