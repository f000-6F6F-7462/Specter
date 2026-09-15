"""Entry point of the camera manager process."""

import asyncio
import logging

from specter.config.settings import Settings
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the camera manager until shutdown is requested."""
    message_bus = await MessageBus.connect(
        settings.services.nats_url, client_name="specter-camera-manager"
    )
    try:
        await message_bus.declare_streams()
        logger.info("camera manager started")
        await shutdown_requested.wait()
    finally:
        await message_bus.close()
    logger.info("camera manager stopped")
