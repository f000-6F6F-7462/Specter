"""JetStream streams, key-value buckets and consumers that Specter declares on the NATS server."""

from dataclasses import dataclass

from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    KeyValueConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)

from specter.messaging.subjects import (
    ENROLLMENT_JOBS,
    CameraEvent,
    OwnerEvent,
    build_all_cameras_subject,
    build_all_owners_subject,
)

SECONDS_PER_DAY = 86_400
BYTES_PER_MEBIBYTE = 1024 * 1024

EVENTS_RETENTION_SECONDS = 7 * SECONDS_PER_DAY
EVENTS_MAX_SIZE_BYTES = 256 * BYTES_PER_MEBIBYTE
CAMERA_HEALTH_TIME_TO_LIVE_SECONDS = 10.0
UNLIMITED_MESSAGES_PER_SUBJECT = -1
MATCH_COOLDOWNS_BUCKET_NAME = "match_cooldowns"
DEFAULT_DUPLICATE_WINDOW_SECONDS = 120.0

# Long enough for the slowest device to embed one image, so a job is delivered again only when
# its worker stopped without answering.
ENROLLMENT_JOB_ACK_WAIT_SECONDS = 120.0
# A failed job is retried with growing delays; the last attempt starts about seven minutes after
# the first one.
ENROLLMENT_JOB_REDELIVERY_DELAYS_SECONDS = (10.0, 30.0, 60.0, 300.0)


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

    @property
    def duplicate_window_seconds(self) -> float:
        """How long the server remembers written ids, which may not exceed how long values live."""
        if self.time_to_live_seconds is None:
            return DEFAULT_DUPLICATE_WINDOW_SECONDS
        return min(DEFAULT_DUPLICATE_WINDOW_SECONDS, self.time_to_live_seconds)

    def to_key_value_config(self) -> KeyValueConfig:
        """Returns the bucket configuration in the form the NATS client expects."""
        return KeyValueConfig(bucket=self.name, storage=self.storage, ttl=self.time_to_live_seconds)


@dataclass(frozen=True, slots=True)
class WorkQueueConsumerDefinition:
    """A durable consumer that gives each job to one worker and delivers it until acknowledged."""

    name: str
    stream_name: str
    subject: str
    ack_wait_seconds: float
    redelivery_delays_seconds: tuple[float, ...]

    @property
    def max_deliveries(self) -> int:
        """How many times a job is delivered before it is dropped."""
        return len(self.redelivery_delays_seconds) + 1

    def redelivery_delay_after(self, attempt_number: int) -> float:
        """Returns how long to wait before delivering a job again after the failed attempt."""
        delay_index = min(max(attempt_number, 1), len(self.redelivery_delays_seconds)) - 1
        return self.redelivery_delays_seconds[delay_index]

    def to_consumer_config(self) -> ConsumerConfig:
        """Returns the consumer configuration in the form the NATS client expects."""
        return ConsumerConfig(
            durable_name=self.name,
            filter_subject=self.subject,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=self.ack_wait_seconds,
            max_deliver=self.max_deliveries,
        )


EVENTS_STREAM = StreamDefinition(
    name="EVENTS",
    subjects=(
        build_all_cameras_subject(CameraEvent.MATCH_CONFIRMED),
        build_all_cameras_subject(CameraEvent.RULE_TRIGGERED),
        build_all_owners_subject(OwnerEvent.ENROLLMENT_STATUS_CHANGED),
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
    subjects=(build_all_owners_subject(OwnerEvent.CONFIGURATION_CHANGED),),
    max_messages_per_subject=1,
)

STREAM_DEFINITIONS = (
    EVENTS_STREAM,
    CAMERA_STATUS_STREAM,
    ENROLLMENT_JOBS_STREAM,
    CONFIGURATION_STREAM,
)

ENROLLMENT_JOBS_CONSUMER = WorkQueueConsumerDefinition(
    name="enrollment_workers",
    stream_name=ENROLLMENT_JOBS_STREAM.name,
    subject=ENROLLMENT_JOBS,
    ack_wait_seconds=ENROLLMENT_JOB_ACK_WAIT_SECONDS,
    redelivery_delays_seconds=ENROLLMENT_JOB_REDELIVERY_DELAYS_SECONDS,
)

# Health and cooldowns change every few seconds and are cheap to rebuild, so they stay in
# memory to spare the device's flash storage from constant writes.
CAMERA_HEALTH_BUCKET = KeyValueBucketDefinition(
    name="camera_health",
    storage=StorageType.MEMORY,
    time_to_live_seconds=CAMERA_HEALTH_TIME_TO_LIVE_SECONDS,
)
WATCHLIST_VERSIONS_BUCKET = KeyValueBucketDefinition(name="watchlist_versions")


def build_key_value_bucket_definitions(
    match_cooldown_seconds: float,
) -> tuple[KeyValueBucketDefinition, ...]:
    """Returns every key-value bucket; a match cooldown ends when its key expires."""
    match_cooldowns_bucket = KeyValueBucketDefinition(
        name=MATCH_COOLDOWNS_BUCKET_NAME,
        storage=StorageType.MEMORY,
        time_to_live_seconds=match_cooldown_seconds,
    )
    return (CAMERA_HEALTH_BUCKET, match_cooldowns_bucket, WATCHLIST_VERSIONS_BUCKET)
