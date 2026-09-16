"""Entry point of the camera manager process."""

import asyncio
import logging

from specter.camera_manager.alert_recorder import AlertRecorder
from specter.camera_manager.evidence_retention import EvidenceRetention
from specter.camera_manager.go2rtc import Go2rtcClient
from specter.camera_manager.supervisor import CameraManager
from specter.config.settings import Settings
from specter.core.shutdown import complete_unless_shutdown
from specter.messaging.message_bus import MessageBus
from specter.messaging.shared_state import CameraHealthBucket
from specter.storage.credentials import CredentialCipher, load_or_create_credentials_key
from specter.storage.database import DatabaseThread, open_database
from specter.storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the camera manager until shutdown is requested."""
    message_bus = MessageBus(client_name="specter-camera-manager")
    try:
        is_connected = await complete_unless_shutdown(
            message_bus.connect(settings.services.nats_url), shutdown_requested
        )
        if is_connected:
            await message_bus.declare_streams(settings.matching.cooldown_seconds)
            await supervise_cameras(settings, message_bus, shutdown_requested)
    finally:
        await message_bus.close()
    logger.info("camera manager stopped")


async def supervise_cameras(
    settings: Settings, message_bus: MessageBus, shutdown_requested: asyncio.Event
) -> None:
    """Runs the cameras that should run, records their alerts and keeps evidence within limits."""
    cipher = CredentialCipher(
        load_or_create_credentials_key(settings.security.credentials_key_file)
    )
    database_thread = DatabaseThread(open_database(settings.paths.database_file))
    go2rtc_client = Go2rtcClient(settings.services.go2rtc_url)
    try:
        camera_manager = CameraManager(
            database_thread=database_thread,
            cipher=cipher,
            message_bus=message_bus,
            health_bucket=await CameraHealthBucket.open(message_bus),
            go2rtc_client=go2rtc_client,
        )
        logger.info("camera manager started")
        await asyncio.gather(
            camera_manager.run(shutdown_requested),
            AlertRecorder(message_bus, database_thread).run(shutdown_requested),
            EvidenceRetention(
                settings.evidence, EvidenceStore(settings.paths.data_directory), database_thread
            ).run(shutdown_requested),
        )
    finally:
        await go2rtc_client.close()
        database_thread.close()
