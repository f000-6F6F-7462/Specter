"""Publishing and consuming Specter messages over NATS and JetStream."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from nats.aio.client import Client as NatsConnection
from nats.aio.msg import Msg
from nats.errors import Error as NatsError
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import DeliverPolicy
from nats.js.client import JetStreamContext
from nats.js.errors import BucketNotFoundError, NotFoundError
from nats.js.kv import KeyValue
from pydantic import ValidationError

from specter.messaging.messages import EnrollmentJobMessage, SpecterMessage
from specter.messaging.streams import (
    ENROLLMENT_JOBS_CONSUMER,
    STREAM_DEFINITIONS,
    KeyValueBucketDefinition,
    WorkQueueConsumerDefinition,
    build_key_value_bucket_definitions,
)
from specter.messaging.subjects import build_message_subject

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SECONDS = 2
UNLIMITED_RECONNECT_ATTEMPTS = -1
# How long a worker waits for a job before it checks again whether shutdown was requested.
JOB_FETCH_TIMEOUT_SECONDS = 1.0
# JetStream stores a message once per id within the duplicate window, so a retried publish that
# already reached the server is not stored twice.
MESSAGE_ID_HEADER = "Nats-Msg-Id"


@dataclass(frozen=True, slots=True)
class JobDelivery:
    """Which delivery of a job a worker is handling."""

    attempt_number: int
    is_last_attempt: bool


class MessageBus:
    """A connection to NATS that publishes Specter's messages and subscribes to them.

    The client name is shown in the NATS server's monitoring, to tell processes apart.
    """

    def __init__(self, client_name: str) -> None:
        self._client_name = client_name
        self._connection = NatsConnection()
        self._jetstream = self._connection.jetstream()

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the NATS server is currently open."""
        return self._connection.is_connected

    async def connect(self, nats_url: str) -> None:
        """Connects to the NATS server, retrying until it becomes reachable.

        A lost connection is re-established the same way, and each failed attempt is logged, so a
        process survives NATS starting after it or restarting.
        """
        await self._connection.connect(
            nats_url,
            name=self._client_name,
            max_reconnect_attempts=UNLIMITED_RECONNECT_ATTEMPTS,
            reconnect_time_wait=RECONNECT_DELAY_SECONDS,
            error_cb=self._log_connection_error,
        )
        logger.info("connected to NATS", extra={"nats_url": nats_url})

    async def close(self) -> None:
        """Delivers pending messages and closes the connection, or stops connecting if not open."""
        if self._connection.is_connected:
            await self._connection.drain()
        elif not self._connection.is_closed:
            await self._connection.close()

    async def declare_streams(self, match_cooldown_seconds: float) -> None:
        """Creates or updates every JetStream stream and key-value bucket Specter uses."""
        for stream in STREAM_DEFINITIONS:
            stream_config = stream.to_stream_config()
            try:
                await self._jetstream.stream_info(stream.name)
            except NotFoundError:
                await self._jetstream.add_stream(stream_config)
            else:
                await self._jetstream.update_stream(stream_config)
        for bucket in build_key_value_bucket_definitions(match_cooldown_seconds):
            await self._declare_key_value_bucket(bucket)

    async def open_bucket(self, bucket_name: str) -> KeyValue:
        """Returns a key-value bucket that ``declare_streams`` created."""
        return await self._jetstream.key_value(bucket_name)

    async def publish(self, message: SpecterMessage) -> None:
        """Stores the message in the stream that captures its subject, once per message id."""
        await self._jetstream.publish(
            build_message_subject(message),
            message.model_dump_json().encode(),
            headers={MESSAGE_ID_HEADER: message.message_id},
        )

    async def subscribe_from_latest[MessageT: SpecterMessage](
        self,
        subject: str,
        message_type: type[MessageT],
        handle_message: Callable[[MessageT], Awaitable[None]],
    ) -> None:
        """Delivers the latest stored message of every matching subject, then each new one.

        Suits state such as configuration changes, where a process that starts needs only the
        latest message per subject. A message that fails to parse, or whose handler raises, is
        logged and skipped so a single bad message cannot stop the subscription.
        """
        deliver = partial(
            self._deliver_message, message_type=message_type, handle_message=handle_message
        )
        await self._jetstream.subscribe(
            subject,
            cb=deliver,
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
        )

    async def subscribe_to_work_queue(
        self, consumer: WorkQueueConsumerDefinition
    ) -> JetStreamContext.PullSubscription:
        """Creates the durable consumer if needed and returns a subscription that pulls its jobs."""
        return await self._jetstream.pull_subscribe(
            consumer.subject,
            durable=consumer.name,
            stream=consumer.stream_name,
            config=consumer.to_consumer_config(),
        )

    async def _declare_key_value_bucket(self, bucket: KeyValueBucketDefinition) -> None:
        try:
            existing_bucket = await self._jetstream.key_value(bucket.name)
        except BucketNotFoundError:
            await self._jetstream.create_key_value(bucket.to_key_value_config())
            return
        status = await existing_bucket.status()
        # A changed setting, such as the match cooldown, must also reach a bucket that exists.
        if (status.ttl or None) != bucket.time_to_live_seconds:
            await self._jetstream.update_stream(
                status.stream_info.config.evolve(
                    max_age=bucket.time_to_live_seconds,
                    duplicate_window=bucket.duplicate_window_seconds,
                )
            )

    @staticmethod
    async def _deliver_message[MessageT: SpecterMessage](
        raw_message: Msg,
        *,
        message_type: type[MessageT],
        handle_message: Callable[[MessageT], Awaitable[None]],
    ) -> None:
        try:
            await handle_message(message_type.model_validate_json(raw_message.data))
        except Exception:
            logger.exception("failed to handle a message", extra={"subject": raw_message.subject})

    @staticmethod
    async def _log_connection_error(error: Exception) -> None:
        # One line per failed attempt; the library's own handler would add a full traceback.
        logger.warning("NATS connection error: %s", error)


class EnrollmentJobWorker:
    """Takes enrollment jobs from their work queue one at a time and hands each to a handler.

    A job is acknowledged when the handler returns, and delivered again after the consumer's next
    redelivery delay when the handler raises. The handler is told which attempt is the last so it
    can record a final outcome, because the job is dropped after it. A job that cannot be parsed
    is dropped at once, since delivering it again cannot succeed.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        handle_job: Callable[[EnrollmentJobMessage, JobDelivery], Awaitable[None]],
        consumer: WorkQueueConsumerDefinition = ENROLLMENT_JOBS_CONSUMER,
    ) -> None:
        self._message_bus = message_bus
        self._handle_job = handle_job
        self._consumer = consumer

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Processes jobs until shutdown is requested."""
        subscription = await self._message_bus.subscribe_to_work_queue(self._consumer)
        while not shutdown_requested.is_set():
            for raw_job in await self._fetch_next_jobs(subscription):
                await self._process_job(raw_job)

    @staticmethod
    async def _fetch_next_jobs(subscription: JetStreamContext.PullSubscription) -> list[Msg]:
        try:
            return await subscription.fetch(batch=1, timeout=JOB_FETCH_TIMEOUT_SECONDS)
        except NatsTimeoutError:
            return []
        except NatsError:
            # Fetching fails while NATS reconnects; waiting keeps the worker from spinning.
            logger.warning("cannot fetch jobs from NATS, retrying", exc_info=True)
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            return []

    async def _process_job(self, raw_job: Msg) -> None:
        try:
            job = EnrollmentJobMessage.model_validate_json(raw_job.data)
        except ValidationError:
            logger.exception(
                "dropping a job that cannot be parsed", extra={"subject": raw_job.subject}
            )
            await raw_job.term()
            return

        attempt_number = raw_job.metadata.num_delivered
        delivery = JobDelivery(
            attempt_number=attempt_number,
            is_last_attempt=attempt_number >= self._consumer.max_deliveries,
        )
        try:
            await self._handle_job(job, delivery)
        except Exception:
            logger.exception(
                "job failed", extra={"message_id": job.message_id, "attempt_number": attempt_number}
            )
            if delivery.is_last_attempt:
                await raw_job.term()
            else:
                await raw_job.nak(delay=self._consumer.redelivery_delay_after(attempt_number))
            return
        # Waiting for the server to confirm keeps a finished job from being delivered again.
        await raw_job.ack_sync()
