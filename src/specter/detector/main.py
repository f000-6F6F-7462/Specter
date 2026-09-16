"""Entry point of the detector process."""

import asyncio
import logging

from specter.config.settings import Settings
from specter.core.shutdown import complete_unless_shutdown
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the detector until shutdown is requested."""
    message_bus = MessageBus(client_name="specter-detector")
    try:
        is_connected = await complete_unless_shutdown(
            message_bus.connect(settings.services.nats_url), shutdown_requested
        )
        if is_connected:
            await message_bus.declare_streams(settings.matching.cooldown_seconds)
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
