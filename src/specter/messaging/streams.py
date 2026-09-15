"""JetStream streams and key-value buckets that Specter declares on the NATS server."""

from dataclasses import dataclass

from nats.js.api import KeyValueConfig, RetentionPolicy, StorageType, StreamConfig

from specter.messaging.subjects import (
    CONFIGURATION_CHANGED,
    ENROLLMENT_JOBS,
    ENROLLMENT_STATUS_CHANGED,
    CameraEvent,
    build_all_cameras_subject,
)

SECONDS_PER_DAY = 86_400
BYTES_PER_MEBIBYTE = 1024 * 1024

EVENTS_RETENTION_SECONDS = 7 * SECONDS_PER_DAY
EVENTS_MAX_SIZE_BYTES = 256 * BYTES_PER_MEBIBYTE
CAMERA_HEALTH_TIME_TO_LIVE_SECONDS = 10.0
UNLIMITED_MESSAGES_PER_SUBJECT = -1


@dataclass(frozen=True, slots=True)
class StreamDefinition:
    """A JetStream stream and the subjects it stores."""

    name: str
    subjects: tuple[str, ...]
    retention: RetentionPolicy = RetentionPolicy.LIMITS
    storage: StorageType = StorageType.FILE
    max_age_seconds: float | None = None
    max_size_bytes: int | None = None
    max_messages_per_subject: int = UNLIMITED_MESSAGES_PER_SUBJECT

    def to_stream_config(self) -> StreamConfig:
        """Returns the stream configuration in the form the NATS client expects."""
        return StreamConfig(
            name=self.name,
            subjects=list(self.subjects),
            retention=self.retention,
            storage=self.storage,
            max_age=self.max_age_seconds,
            max_bytes=self.max_size_bytes,
            max_msgs_per_subject=self.max_messages_per_subject,
        )


@dataclass(frozen=True, slots=True)
class KeyValueBucketDefinition:
    """A JetStream key-value bucket."""

    name: str
    storage: StorageType = StorageType.FILE
    time_to_live_seconds: float | None = None

    def to_key_value_config(self) -> KeyValueConfig:
        """Returns the bucket configuration in the form the NATS client expects."""
        return KeyValueConfig(bucket=self.name, storage=self.storage, ttl=self.time_to_live_seconds)


EVENTS_STREAM = StreamDefinition(
    name="EVENTS",
    subjects=(
        build_all_cameras_subject(CameraEvent.MATCH_CONFIRMED),
        build_all_cameras_subject(CameraEvent.RULE_TRIGGERED),
        ENROLLMENT_STATUS_CHANGED,
    ),
    max_age_seconds=EVENTS_RETENTION_SECONDS,
    max_size_bytes=EVENTS_MAX_SIZE_BYTES,
)

CAMERA_STATUS_STREAM = StreamDefinition(
    name="CAMERA_STATUS",
    subjects=(build_all_cameras_subject(CameraEvent.STATUS_CHANGED),),
    max_messages_per_subject=1,
)

ENROLLMENT_JOBS_STREAM = StreamDefinition(
    name="ENROLLMENT_JOBS",
    subjects=(ENROLLMENT_JOBS,),
    retention=RetentionPolicy.WORK_QUEUE,
)

CONFIGURATION_STREAM = StreamDefinition(
    name="CONFIGURATION",
    subjects=(CONFIGURATION_CHANGED,),
    max_messages_per_subject=1,
)

STREAM_DEFINITIONS = (
    EVENTS_STREAM,
    CAMERA_STATUS_STREAM,
    ENROLLMENT_JOBS_STREAM,
    CONFIGURATION_STREAM,
)

# Health and cooldowns change every few seconds and are cheap to rebuild, so they stay in
# memory to spare the device's flash storage from constant writes.
CAMERA_HEALTH_BUCKET = KeyValueBucketDefinition(
    name="camera_health",
    storage=StorageType.MEMORY,
    time_to_live_seconds=CAMERA_HEALTH_TIME_TO_LIVE_SECONDS,
)
MATCH_COOLDOWNS_BUCKET = KeyValueBucketDefinition(
    name="match_cooldowns", storage=StorageType.MEMORY
)
WATCHLIST_VERSIONS_BUCKET = KeyValueBucketDefinition(name="watchlist_versions")

KEY_VALUE_BUCKET_DEFINITIONS = (
    CAMERA_HEALTH_BUCKET,
    MATCH_COOLDOWNS_BUCKET,
    WATCHLIST_VERSIONS_BUCKET,
)
