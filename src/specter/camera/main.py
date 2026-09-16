"""Entry point of a camera process."""

import asyncio
import logging

from specter.config.settings import Settings
from specter.core.shutdown import complete_unless_shutdown
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


async def run(settings: Settings, camera_id: str, shutdown_requested: asyncio.Event) -> None:
    """Runs the process for one camera until shutdown is requested."""
    message_bus = MessageBus(client_name=f"specter-camera-{camera_id}")
    try:
        is_connected = await complete_unless_shutdown(
            message_bus.connect(settings.services.nats_url), shutdown_requested
        )
        if is_connected:
            await message_bus.declare_streams(settings.matching.cooldown_seconds)
            logger.info("camera process started", extra={"camera_id": camera_id})
            await shutdown_requested.wait()
    finally:
        await message_bus.close()
    logger.info("camera process stopped", extra={"camera_id": camera_id})
