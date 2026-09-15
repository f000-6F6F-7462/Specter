"""Entry point of a camera process."""

import asyncio
import logging

from specter.config.settings import Settings
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


async def run(settings: Settings, camera_id: str, shutdown_requested: asyncio.Event) -> None:
    """Runs the process for one camera until shutdown is requested."""
    message_bus = await MessageBus.connect(
        settings.services.nats_url, client_name=f"specter-camera-{camera_id}"
    )
    try:
        await message_bus.declare_streams()
        logger.info("camera process started", extra={"camera_id": camera_id})
        await shutdown_requested.wait()
    finally:
        await message_bus.close()
    logger.info("camera process stopped", extra={"camera_id": camera_id})
