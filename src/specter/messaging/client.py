"""Publishing and subscribing Specter messages over NATS and JetStream."""

import logging
from collections.abc import Awaitable, Callable
from functools import partial

import nats
from nats.aio.client import Client as NatsConnection
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription
from nats.js.errors import BucketNotFoundError, NotFoundError
from pydantic import BaseModel

from specter.messaging.streams import KEY_VALUE_BUCKET_DEFINITIONS, STREAM_DEFINITIONS

logger = logging.getLogger(__name__)


class MessageBus:
    """A connection to NATS that sends and receives Specter's message models."""

    def __init__(self, connection: NatsConnection) -> None:
        self._connection = connection
        self._jetstream = connection.jetstream()

    @classmethod
    async def connect(cls, nats_url: str, *, client_name: str) -> "MessageBus":
        """Opens a connection to the NATS server.

        Args:
            client_name: Shown in the NATS server's monitoring, to tell processes apart.
        """
        return cls(await nats.connect(nats_url, name=client_name))

    @property
    def is_connected(self) -> bool:
        """Whether the connection to the NATS server is currently open."""
        return self._connection.is_connected

    async def close(self) -> None:
        """Delivers pending messages, then closes the connection."""
        await self._connection.drain()

    async def declare_streams(self) -> None:
        """Creates or updates every JetStream stream and key-value bucket Specter uses."""
        for stream in STREAM_DEFINITIONS:
            stream_config = stream.to_stream_config()
            try:
                await self._jetstream.stream_info(stream.name)
            except NotFoundError:
                await self._jetstream.add_stream(stream_config)
            else:
                await self._jetstream.update_stream(stream_config)
        for bucket in KEY_VALUE_BUCKET_DEFINITIONS:
            try:
                await self._jetstream.key_value(bucket.name)
            except BucketNotFoundError:
                await self._jetstream.create_key_value(bucket.to_key_value_config())

    async def publish(self, subject: str, message: BaseModel) -> None:
        """Stores the message in the JetStream stream that captures the subject."""
        await self._jetstream.publish(subject, message.model_dump_json().encode())

    async def subscribe[MessageT: BaseModel](
        self,
        subject: str,
        message_type: type[MessageT],
        handle_message: Callable[[MessageT], Awaitable[None]],
    ) -> Subscription:
        """Delivers each message on the subject to the handler, parsed as ``message_type``.

        A message that fails to parse, or whose handler raises, is logged and skipped so a
        single bad message cannot stop the subscription.
        """
        deliver = partial(self._deliver, message_type=message_type, handle_message=handle_message)
        return await self._connection.subscribe(subject, cb=deliver)

    @staticmethod
    async def _deliver[MessageT: BaseModel](
        raw_message: Msg,
        *,
        message_type: type[MessageT],
        handle_message: Callable[[MessageT], Awaitable[None]],
    ) -> None:
        try:
            await handle_message(message_type.model_validate_json(raw_message.data))
        except Exception:
            logger.exception("failed to handle a message on %s", raw_message.subject)
