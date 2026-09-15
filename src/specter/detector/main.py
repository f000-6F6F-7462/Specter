"""Entry point of the detector process."""

import asyncio
import logging

from specter.config.settings import Settings
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the detector until shutdown is requested."""
    message_bus = await MessageBus.connect(
        settings.services.nats_url, client_name="specter-detector"
    )
    try:
        await message_bus.declare_streams()
        logger.info(
            "detector started",
            extra={
                "backend": settings.detector.backend,
                "hardware_profile": settings.device.hardware_profile,
            },
        )
        await shutdown_requested.wait()
    finally:
        await message_bus.close()
    logger.info("detector stopped")
