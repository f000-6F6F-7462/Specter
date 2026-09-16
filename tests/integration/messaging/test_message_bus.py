import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import partial

import nats
import pytest
from nats.js.api import RetentionPolicy, StreamConfig

from specter.config.settings import MatchingSettings
from specter.entities.cameras import CameraStatus
from specter.entities.targets import EmbeddingModality, ImageStatus
from specter.messaging.client import EnrollmentJobWorker, JobDelivery, MessageBus
from specter.messaging.messages import (
    CameraStatusChangedMessage,
    EnrollmentJobMessage,
    EnrollmentStatusChangedMessage,
)
from specter.messaging.streams import EVENTS_STREAM, WorkQueueConsumerDefinition
from specter.messaging.subjects import build_message_subject

pytestmark = pytest.mark.integration

DELIVERY_TIMEOUT_SECONDS = 5.0
MATCH_COOLDOWN_SECONDS = MatchingSettings().cooldown_seconds
TEST_ACK_WAIT_SECONDS = 5.0
TEST_REDELIVERY_DELAY_SECONDS = 0.1
UNPARSEABLE_JOB_WAIT_SECONDS = 1.0


@pytest.fixture
async def job_consumer(nats_server_url: str) -> AsyncIterator[WorkQueueConsumerDefinition]:
    # A private work queue per test, so jobs never reach a real detector or another test.
    unique_suffix = time.time_ns()
    stream_name = f"TEST_JOBS_{unique_suffix}"
    subject = f"specter.tests.jobs.{unique_suffix}"
    connection = await nats.connect(nats_server_url)
    jetstream = connection.jetstream()
    await jetstream.add_stream(
        StreamConfig(name=stream_name, subjects=[subject], retention=RetentionPolicy.WORK_QUEUE)
    )
    try:
        yield WorkQueueConsumerDefinition(
            name="test_workers",
            stream_name=stream_name,
            subject=subject,
            ack_wait_seconds=TEST_ACK_WAIT_SECONDS,
            redelivery_delays_seconds=(
                TEST_REDELIVERY_DELAY_SECONDS,
                TEST_REDELIVERY_DELAY_SECONDS,
            ),
        )
    finally:
        await jetstream.delete_stream(stream_name)
        await connection.close()


async def test_declaring_streams_succeeds_when_repeated(message_bus: MessageBus) -> None:
    await message_bus.declare_streams(MATCH_COOLDOWN_SECONDS)
    await message_bus.declare_streams(MATCH_COOLDOWN_SECONDS)


async def test_message_is_stored_once_when_published_twice(
    message_bus: MessageBus, nats_server_url: str, unique_owner_id: str
) -> None:
    message = EnrollmentStatusChangedMessage(
        occurred_at=datetime.now(UTC),
        owner_id=unique_owner_id,
        target_id="target_jane",
        reference_image_id="image_1",
        status=ImageStatus.EMBEDDED,
    )

    await message_bus.publish(message)
    await message_bus.publish(message)

    subject = build_message_subject(message)
    inspection_connection = await nats.connect(nats_server_url)
    try:
        stream_info = await inspection_connection.jetstream().stream_info(
            EVENTS_STREAM.name, subjects_filter=subject
        )
    finally:
        await inspection_connection.close()
    assert stream_info.state.subjects == {subject: 1}


async def test_latest_status_then_new_ones_are_delivered_when_subscribing_from_latest(
    message_bus: MessageBus, unique_owner_id: str, unique_camera_id: str
) -> None:
    await message_bus.publish(
        build_camera_status_message(unique_owner_id, unique_camera_id, CameraStatus.STARTING)
    )
    running_message = build_camera_status_message(
        unique_owner_id, unique_camera_id, CameraStatus.RUNNING
    )
    await message_bus.publish(running_message)
    received_messages: asyncio.Queue[CameraStatusChangedMessage] = asyncio.Queue()

    await message_bus.subscribe_from_latest(
        build_message_subject(running_message), CameraStatusChangedMessage, received_messages.put
    )
    first_received = await asyncio.wait_for(received_messages.get(), DELIVERY_TIMEOUT_SECONDS)
    await message_bus.publish(
        build_camera_status_message(unique_owner_id, unique_camera_id, CameraStatus.STOPPED)
    )
    second_received = await asyncio.wait_for(received_messages.get(), DELIVERY_TIMEOUT_SECONDS)

    assert (first_received.status, second_received.status) == (
        CameraStatus.RUNNING,
        CameraStatus.STOPPED,
    )


async def test_job_is_delivered_again_until_handler_succeeds(
    message_bus: MessageBus, nats_server_url: str, job_consumer: WorkQueueConsumerDefinition
) -> None:
    await publish_job(
        nats_server_url, job_consumer.subject, build_enrollment_job().model_dump_json()
    )
    deliveries: list[JobDelivery] = []
    shutdown_requested = asyncio.Event()
    handle_job = partial(
        fail_before_attempt,
        successful_attempt_number=2,
        deliveries=deliveries,
        shutdown_requested=shutdown_requested,
    )

    await asyncio.wait_for(
        EnrollmentJobWorker(message_bus, handle_job, job_consumer).run(shutdown_requested),
        DELIVERY_TIMEOUT_SECONDS,
    )

    assert deliveries == [
        JobDelivery(1, is_last_attempt=False),
        JobDelivery(2, is_last_attempt=False),
    ]
    assert await count_unfinished_jobs(nats_server_url, job_consumer) == 0


async def test_last_attempt_is_marked_when_job_keeps_failing(
    message_bus: MessageBus, nats_server_url: str, job_consumer: WorkQueueConsumerDefinition
) -> None:
    await publish_job(
        nats_server_url, job_consumer.subject, build_enrollment_job().model_dump_json()
    )
    deliveries: list[JobDelivery] = []
    shutdown_requested = asyncio.Event()
    handle_job = partial(
        fail_before_attempt,
        successful_attempt_number=None,
        deliveries=deliveries,
        shutdown_requested=shutdown_requested,
    )

    await asyncio.wait_for(
        EnrollmentJobWorker(message_bus, handle_job, job_consumer).run(shutdown_requested),
        DELIVERY_TIMEOUT_SECONDS,
    )

    assert [delivery.is_last_attempt for delivery in deliveries] == [False, False, True]
    assert await count_unfinished_jobs(nats_server_url, job_consumer) == 0


async def test_job_is_dropped_without_handling_when_it_cannot_be_parsed(
    message_bus: MessageBus, nats_server_url: str, job_consumer: WorkQueueConsumerDefinition
) -> None:
    await publish_job(nats_server_url, job_consumer.subject, "not a job")
    deliveries: list[JobDelivery] = []
    shutdown_requested = asyncio.Event()
    asyncio.get_running_loop().call_later(UNPARSEABLE_JOB_WAIT_SECONDS, shutdown_requested.set)
    handle_job = partial(
        fail_before_attempt,
        successful_attempt_number=1,
        deliveries=deliveries,
        shutdown_requested=shutdown_requested,
    )

    await asyncio.wait_for(
        EnrollmentJobWorker(message_bus, handle_job, job_consumer).run(shutdown_requested),
        DELIVERY_TIMEOUT_SECONDS,
    )

    assert deliveries == []
    assert await count_unfinished_jobs(nats_server_url, job_consumer) == 0


def build_camera_status_message(
    owner_id: str, camera_id: str, status: CameraStatus
) -> CameraStatusChangedMessage:
    return CameraStatusChangedMessage(
        occurred_at=datetime.now(UTC), owner_id=owner_id, camera_id=camera_id, status=status
    )


def build_enrollment_job() -> EnrollmentJobMessage:
    return EnrollmentJobMessage(
        occurred_at=datetime.now(UTC),
        owner_id="owner_tests",
        target_id="target_jane",
        reference_image_id="image_1",
        image_path="reference_images/image_1.jpg",
        modality=EmbeddingModality.FACE,
    )


async def fail_before_attempt(
    job: EnrollmentJobMessage,
    delivery: JobDelivery,
    *,
    successful_attempt_number: int | None,
    deliveries: list[JobDelivery],
    shutdown_requested: asyncio.Event,
) -> None:
    deliveries.append(delivery)
    if delivery.attempt_number == successful_attempt_number or delivery.is_last_attempt:
        shutdown_requested.set()
    if delivery.attempt_number != successful_attempt_number:
        raise RuntimeError(f"simulated failure of {job.reference_image_id}")


async def publish_job(nats_server_url: str, subject: str, payload: str) -> None:
    connection = await nats.connect(nats_server_url)
    try:
        await connection.jetstream().publish(subject, payload.encode())
    finally:
        await connection.close()


async def count_unfinished_jobs(nats_server_url: str, consumer: WorkQueueConsumerDefinition) -> int:
    connection = await nats.connect(nats_server_url)
    try:
        consumer_info = await connection.jetstream().consumer_info(
            consumer.stream_name, consumer.name
        )
    finally:
        await connection.close()
    unfinished_job_count: int = consumer_info.num_pending + consumer_info.num_ack_pending
    return unfinished_job_count
