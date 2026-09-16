import asyncio
from datetime import UTC, datetime

import pytest

from specter.config.settings import MatchingSettings
from specter.entities.cameras import CameraStatus
from specter.messaging.message_bus import MessageBus
from specter.messaging.messages import CameraHealthReport
from specter.messaging.shared_state import CameraHealthBucket, MatchCooldownBucket
from specter.messaging.streams import MATCH_COOLDOWNS_BUCKET_NAME

pytestmark = pytest.mark.integration

MATCH_COOLDOWN_SECONDS = MatchingSettings().cooldown_seconds
SHORT_COOLDOWN_SECONDS = 1.0
COOLDOWN_EXPIRY_MARGIN_SECONDS = 0.5


async def test_health_report_is_read_back_when_camera_reported(
    message_bus: MessageBus, unique_owner_id: str, unique_camera_id: str
) -> None:
    health_bucket = await CameraHealthBucket.open(message_bus)
    report = CameraHealthReport(
        owner_id=unique_owner_id,
        camera_id=unique_camera_id,
        status=CameraStatus.RUNNING,
        reported_at=datetime.now(UTC),
    )

    await health_bucket.report(report)

    assert await health_bucket.read(unique_owner_id, unique_camera_id) == report


async def test_health_is_missing_when_camera_never_reported(
    message_bus: MessageBus, unique_owner_id: str, unique_camera_id: str
) -> None:
    health_bucket = await CameraHealthBucket.open(message_bus)

    assert await health_bucket.read(unique_owner_id, unique_camera_id) is None


async def test_cooldown_is_claimed_once_when_claimed_twice(
    message_bus: MessageBus, unique_owner_id: str, unique_camera_id: str
) -> None:
    cooldowns = await MatchCooldownBucket.open(message_bus)

    first_claim = await cooldowns.claim(unique_owner_id, unique_camera_id, "target_jane")
    second_claim = await cooldowns.claim(unique_owner_id, unique_camera_id, "target_jane")

    assert (first_claim, second_claim) == (True, False)


async def test_cooldown_can_be_claimed_again_when_it_expired(
    message_bus: MessageBus, unique_owner_id: str, unique_camera_id: str
) -> None:
    await message_bus.declare_streams(SHORT_COOLDOWN_SECONDS)
    try:
        cooldowns = await MatchCooldownBucket.open(message_bus)
        first_claim = await cooldowns.claim(unique_owner_id, unique_camera_id, "target_jane")
        await asyncio.sleep(SHORT_COOLDOWN_SECONDS + COOLDOWN_EXPIRY_MARGIN_SECONDS)
        claim_after_expiry = await cooldowns.claim(unique_owner_id, unique_camera_id, "target_jane")
    finally:
        await message_bus.declare_streams(MATCH_COOLDOWN_SECONDS)

    assert (first_claim, claim_after_expiry) == (True, True)


async def test_existing_cooldown_bucket_follows_the_setting_when_streams_are_declared_again(
    message_bus: MessageBus,
) -> None:
    await message_bus.declare_streams(SHORT_COOLDOWN_SECONDS)
    try:
        changed_status = await (await message_bus.open_bucket(MATCH_COOLDOWNS_BUCKET_NAME)).status()
    finally:
        await message_bus.declare_streams(MATCH_COOLDOWN_SECONDS)
    restored_status = await (await message_bus.open_bucket(MATCH_COOLDOWNS_BUCKET_NAME)).status()

    assert (changed_status.ttl, restored_status.ttl) == (
        SHORT_COOLDOWN_SECONDS,
        MATCH_COOLDOWN_SECONDS,
    )
