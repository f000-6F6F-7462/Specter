"""Publishing and consuming Specter messages over NATS and JetStream."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
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

from specter.messaging.messages import ConfigurationChangedMessage, SpecterMessage
from specter.messaging.streams import (
    STREAM_DEFINITIONS,
    JobConsumerDefinition,
    KeyValueBucketDefinition,
    build_key_value_bucket_definitions,
)
from specter.messaging.subjects import OwnerEvent, build_all_owners_subject, build_message_subject

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SECONDS = 2
UNLIMITED_RECONNECT_ATTEMPTS = -1
# How long a worker waits for a job before it checks again whether shutdown was requested.
JOB_FETCH_TIMEOUT_SECONDS = 1.0
# JetStream stores a message once per id within the duplicate window, so a retried publish that
# already reached the server is not stored twice.
MESSAGE_ID_HEADER = "Nats-Msg-Id"

type RequestAnswer = Callable[[bytes], Awaitable[bytes]]


@dataclass(frozen=True, slots=True)
class JobDelivery:
    """Which delivery of a job a worker is handling."""

    attempt_number: int
    is_last_attempt: bool


type JobHandler = Callable[[bytes, JobDelivery], Awaitable[None]]


class MessageBus:
    """A connection to NATS that publishes Specter's messages and subscribes to them.

    The client name is shown in the NATS server's monitoring, to tell processes apart.
    """

    def __init__(self, client_name: str) -> None:
        self._client_name = client_name
        self._connection = NatsConnection()
        self._jetstream = self._connection.jetstream()
        self._request_tasks: set[asyncio.Task[None]] = set()

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

    async def subscribe_to_configuration_changes(
        self, handle_change: Callable[[ConfigurationChangedMessage], Awaitable[None]]
    ) -> None:
        """Delivers every owner's latest configuration change, then each new one.

        A process that starts needs only the latest change per owner to catch up. A message that
        fails to parse, or whose handler raises, is logged and skipped so a single bad message
        cannot stop the subscription.
        """
        await self._jetstream.subscribe(
            build_all_owners_subject(OwnerEvent.CONFIGURATION_CHANGED),
            cb=partial(self._deliver_configuration_change, handle_change=handle_change),
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.LAST_PER_SUBJECT,
        )

    async def subscribe_to_jobs(
        self, consumer: JobConsumerDefinition
    ) -> JetStreamContext.PullSubscription:
        """Creates the durable consumer if needed and returns a subscription that pulls its jobs."""
        return await self._jetstream.pull_subscribe(
            consumer.subject,
            durable=consumer.name,
            stream=consumer.stream_name,
            config=consumer.to_consumer_config(),
        )

    async def request(self, subject: str, payload: bytes, timeout_seconds: float) -> bytes:
        """Sends a request that one server answers, over core NATS without storing it.

        The payload's format belongs to the flow that uses the subject, which also reads the reply.

        Raises:
            nats.errors.Error: No server is subscribed, or none answered in time.
        """
        raw_reply = await self._connection.request(subject, payload, timeout=timeout_seconds)
        reply_payload: bytes = raw_reply.data
        return reply_payload

    async def serve_requests(self, subject: str, queue_group: str, answer: RequestAnswer) -> None:
        """Answers requests on the subject, sharing them with every server in the queue group.

        Each request is answered in its own task, so the answering side can gather requests that
        arrive together. A request whose answer raises gets no reply, and its sender times out.
        """
        await self._connection.subscribe(
            subject, queue=queue_group, cb=partial(self._start_answering, answer=answer)
        )

    async def _start_answering(self, raw_request: Msg, *, answer: RequestAnswer) -> None:
        answer_task = asyncio.create_task(self._answer_request(raw_request, answer=answer))
        # Holding the task keeps it from being garbage collected before it finishes.
        self._request_tasks.add(answer_task)
        answer_task.add_done_callback(self._request_tasks.discard)

    @staticmethod
    async def _answer_request(raw_request: Msg, *, answer: RequestAnswer) -> None:
        try:
            await raw_request.respond(await answer(raw_request.data))
        except Exception:
            logger.exception("failed to answer a request", extra={"subject": raw_request.subject})

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
    async def _deliver_configuration_change(
        raw_message: Msg,
        *,
        handle_change: Callable[[ConfigurationChangedMessage], Awaitable[None]],
    ) -> None:
        try:
            await handle_change(ConfigurationChangedMessage.model_validate_json(raw_message.data))
        except Exception:
            logger.exception("failed to handle a message", extra={"subject": raw_message.subject})

    @staticmethod
    async def _log_connection_error(error: Exception) -> None:
        # One line per failed attempt; the library's own handler would add a full traceback.
        logger.warning("NATS connection error: %s", error)


class JobWorker:
    """Takes a consumer's jobs one at a time and hands each job's payload to a handler.

    The payload's format belongs to the flow that owns the consumer, and its handler parses it. A
    job is acknowledged when the handler returns, and delivered again after the consumer's next
    redelivery delay when the handler raises. The handler is told which attempt is the last so it
    can record a final outcome, because the job is dropped after it. A job whose payload fails
    validation is dropped at once, since delivering it again cannot succeed.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        consumer: JobConsumerDefinition,
        handle_job: JobHandler,
    ) -> None:
        self._message_bus = message_bus
        self._consumer = consumer
        self._handle_job = handle_job

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Processes jobs until shutdown is requested."""
        subscription = await self._message_bus.subscribe_to_jobs(self._consumer)
        try:
            while not shutdown_requested.is_set():
                for raw_job in await self._fetch_next_jobs(subscription):
                    await self._process_job(raw_job)
        finally:
            # A subscription left open makes closing the connection wait for its drain timeout.
            with suppress(NatsError):
                await subscription.unsubscribe()

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
        attempt_number = raw_job.metadata.num_delivered
        delivery = JobDelivery(
            attempt_number=attempt_number,
            is_last_attempt=attempt_number >= self._consumer.max_deliveries,
        )
        log_context = {
            "consumer": self._consumer.name,
            "subject": raw_job.subject,
            "attempt_number": attempt_number,
        }
        try:
            await self._handle_job(raw_job.data, delivery)
        except ValidationError:
            logger.exception("dropping a job that cannot be parsed", extra=log_context)
            await raw_job.term()
            return
        except Exception:
            logger.exception("job failed", extra=log_context)
            if delivery.is_last_attempt:
                await raw_job.term()
            else:
                await raw_job.nak(delay=self._consumer.redelivery_delay_after(attempt_number))
            return
        # Waiting for the server to confirm keeps a finished job from being delivered again.
        await raw_job.ack_sync()
