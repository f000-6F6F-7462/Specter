"""Tells the other device processes that the API changed their configuration."""

import logging
from datetime import UTC, datetime

from nats.errors import ConnectionClosedError
from nats.errors import Error as NatsError

from specter.messaging.message_bus import MessageBus
from specter.messaging.messages import (
    ChangeKind,
    ConfigurationChangedMessage,
    EntityKind,
    SpecterMessage,
)

logger = logging.getLogger(__name__)


async def publish_configuration_change(
    message_bus: MessageBus,
    owner_id: str,
    entity_kind: EntityKind,
    entity_id: str,
    change_kind: ChangeKind,
) -> None:
    """Publishes that an entity changed, after the change is stored.

    The change is already saved when this runs, so a failed publish only delays it: the camera
    manager reconciles its cameras on a timer, and every process reads its configuration again
    when it restarts.
    """
    message = ConfigurationChangedMessage(
        occurred_at=datetime.now(UTC),
        owner_id=owner_id,
        entity_kind=entity_kind,
        entity_id=entity_id,
        change_kind=change_kind,
    )
    try:
        await publish_when_connected(message_bus, message)
    except NatsError as error:
        logger.warning(
            "cannot publish a configuration change: %s",
            error,
            extra={"entity_kind": entity_kind, "entity_id": entity_id},
        )


async def publish_when_connected(message_bus: MessageBus, message: SpecterMessage) -> None:
    """Publishes the message, failing at once rather than waiting while NATS is unreachable.

    Raises:
        nats.errors.ConnectionClosedError: NATS is not connected.
        nats.errors.Error: The message was not stored.
    """
    # A publish without a connection would wait for its acknowledgement until it times out, which
    # would hold up every API request that changes something.
    if not message_bus.is_connected:
        raise ConnectionClosedError
    await message_bus.publish(message)
