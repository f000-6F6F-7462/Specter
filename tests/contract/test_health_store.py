"""HealthStore contract — holds for InMemoryHealthStore and RedisHealthStore alike."""

import asyncio

from specter.application.ports import HealthStore
from specter.domain.streams import StreamHealth, StreamStatus

_HEALTH = StreamHealth(
    status=StreamStatus.RUNNING,
    fps_in=9.5,
    fps_processed=9.0,
    frames_dropped_pct=1.2,
    reconnect_count=0,
    inference_p95_ms=12.5,
    queue_depth={"decode": 1},
)


async def test_get_health_is_none_before_anything_is_set(health_store: HealthStore) -> None:
    assert await health_store.get_health("stream_ct_missing") is None


async def test_set_then_get_health_round_trips(health_store: HealthStore) -> None:
    await health_store.set_health("stream_ct_1", _HEALTH, ttl_s=30)
    got = await health_store.get_health("stream_ct_1")
    assert got is not None
    assert got.status is StreamStatus.RUNNING
    assert got.fps_in == _HEALTH.fps_in
    assert got.queue_depth == {"decode": 1}


async def test_health_expires_after_its_ttl(health_store: HealthStore) -> None:
    await health_store.set_health("stream_ct_ttl", _HEALTH, ttl_s=1)
    assert await health_store.get_health("stream_ct_ttl") is not None
    await asyncio.sleep(1.3)
    assert await health_store.get_health("stream_ct_ttl") is None


async def test_cooldown_is_active_then_expires(health_store: HealthStore) -> None:
    key = "stream_ct:trk_1:tgt_1"
    assert await health_store.in_cooldown(key) is False
    await health_store.mark_cooldown(key, ttl_s=1)
    assert await health_store.in_cooldown(key) is True
    await asyncio.sleep(1.3)
    assert await health_store.in_cooldown(key) is False


async def test_watchlist_version_increments(health_store: HealthStore) -> None:
    first = await health_store.bump_watchlist_version("wl_ct")
    second = await health_store.bump_watchlist_version("wl_ct")
    assert second == first + 1


async def test_get_watchlist_version_defaults_to_zero(health_store: HealthStore) -> None:
    assert await health_store.get_watchlist_version("wl_ct_never_bumped") == 0


async def test_get_watchlist_version_reflects_bumps(health_store: HealthStore) -> None:
    await health_store.bump_watchlist_version("wl_ct_reads")
    await health_store.bump_watchlist_version("wl_ct_reads")
    assert await health_store.get_watchlist_version("wl_ct_reads") == 2
