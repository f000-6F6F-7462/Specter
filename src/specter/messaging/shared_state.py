"""State that processes share through JetStream key-value buckets.

Keys start with the owner id, so a consumer can watch a single owner's keys.
"""

from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError
from nats.js.kv import KeyValue

from specter.messaging.message_bus import MessageBus
from specter.messaging.messages import CameraHealthReport
from specter.messaging.streams import CAMERA_HEALTH_BUCKET, MATCH_COOLDOWNS_BUCKET_NAME

KEY_SEPARATOR = "."
EMPTY_VALUE = b""


class CameraHealthBucket:
    """Health reports that camera processes refresh, which expire when a process stops."""

    def __init__(self, bucket: KeyValue) -> None:
        self._bucket = bucket

    @classmethod
    async def open(cls, message_bus: MessageBus) -> "CameraHealthBucket":
        """Opens the declared camera health bucket."""
        return cls(await message_bus.open_bucket(CAMERA_HEALTH_BUCKET.name))

    async def report(self, report: CameraHealthReport) -> None:
        """Stores the camera's latest health report."""
        await self._bucket.put(
            _build_key(report.owner_id, report.camera_id), report.model_dump_json().encode()
        )

    async def read(self, owner_id: str, camera_id: str) -> CameraHealthReport | None:
        """Returns the camera's latest report, or None if its process has not reported lately."""
        try:
            entry = await self._bucket.get(_build_key(owner_id, camera_id))
        except KeyNotFoundError:
            return None
        if entry.value is None:
            return None
        return CameraHealthReport.model_validate_json(entry.value)


class MatchCooldownBucket:
    """Cooldowns that stop the same target on the same camera from raising repeated alerts."""

    def __init__(self, bucket: KeyValue) -> None:
        self._bucket = bucket

    @classmethod
    async def open(cls, message_bus: MessageBus) -> "MatchCooldownBucket":
        """Opens the declared match cooldowns bucket."""
        return cls(await message_bus.open_bucket(MATCH_COOLDOWNS_BUCKET_NAME))

    async def claim(self, owner_id: str, camera_id: str, target_id: str) -> bool:
        """Starts a cooldown for the target on the camera.

        Returns False when a cooldown is already running. Creating the key succeeds for exactly
        one caller, so two processes that confirm the same match raise a single alert.
        """
        try:
            await self._bucket.create(_build_key(owner_id, camera_id, target_id), EMPTY_VALUE)
        except KeyWrongLastSequenceError:
            return False
        return True


def _build_key(*identifiers: str) -> str:
    return KEY_SEPARATOR.join(identifiers)
