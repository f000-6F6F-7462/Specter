"""HealthStore over the same Redis instance as the EventBus — one component for
messaging *and* caching.

Keys: ``stream:health:{id}`` (a TTL'd JSON snapshot — one GET, no HSCAN needed for a
handful of scalar-ish fields), ``cooldown:{key}`` (a bare TTL'd marker, existence is the
signal), ``watchlist:version:{id}`` (an INCR counter).
"""

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from specter.domain.streams import StreamHealth, StreamStatus

_HEALTH_PREFIX = "stream:health:"
_COOLDOWN_PREFIX = "cooldown:"
_VERSION_PREFIX = "watchlist:version:"


class RedisHealthStore:
    def __init__(self, url: str) -> None:
        self._redis: Redis = Redis.from_url(url, decode_responses=True)

    async def set_health(self, stream_id: str, health: StreamHealth, *, ttl_s: int = 10) -> None:
        await self._redis.set(_HEALTH_PREFIX + stream_id, _dump(health), ex=ttl_s)

    async def get_health(self, stream_id: str) -> StreamHealth | None:
        raw: Any = await self._redis.get(_HEALTH_PREFIX + stream_id)
        return _load(raw) if raw is not None else None

    async def in_cooldown(self, key: str) -> bool:
        return bool(await self._redis.exists(_COOLDOWN_PREFIX + key))

    async def mark_cooldown(self, key: str, *, ttl_s: int) -> None:
        await self._redis.set(_COOLDOWN_PREFIX + key, "1", ex=ttl_s)

    async def bump_watchlist_version(self, watchlist_id: str) -> int:
        return int(await self._redis.incr(_VERSION_PREFIX + watchlist_id))

    async def aclose(self) -> None:
        await self._redis.aclose()


def _dump(health: StreamHealth) -> str:
    return json.dumps(
        {
            "status": health.status.value,
            "fps_in": health.fps_in,
            "fps_processed": health.fps_processed,
            "frames_dropped_pct": health.frames_dropped_pct,
            "last_frame_at": health.last_frame_at.isoformat() if health.last_frame_at else None,
            "reconnect_count": health.reconnect_count,
            "inference_p95_ms": health.inference_p95_ms,
            "queue_depth": health.queue_depth,
            "last_error": health.last_error,
        }
    )


def _load(raw: str) -> StreamHealth:
    data = json.loads(raw)
    last_frame_at = data["last_frame_at"]
    return StreamHealth(
        status=StreamStatus(data["status"]),
        fps_in=data["fps_in"],
        fps_processed=data["fps_processed"],
        frames_dropped_pct=data["frames_dropped_pct"],
        last_frame_at=datetime.fromisoformat(last_frame_at) if last_frame_at else None,
        reconnect_count=data["reconnect_count"],
        inference_p95_ms=data["inference_p95_ms"],
        queue_depth=data["queue_depth"],
        last_error=data["last_error"],
    )
