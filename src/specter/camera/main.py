"""Entry point of a camera process."""

import asyncio
import logging
from datetime import UTC, datetime
from functools import partial

from nats.errors import Error as NatsError

from specter.camera.alert_publisher import AlertPublisher
from specter.camera.detector_client import DetectorClient
from specter.camera.person_identifier import PersonIdentifier
from specter.camera.scene_analyzer import SceneAnalyzer, load_scene_configuration
from specter.camera.stream_reader import StreamReader
from specter.config.settings import Settings
from specter.core.shutdown import complete_unless_shutdown
from specter.entities.cameras import Camera
from specter.messaging.client import MessageBus
from specter.messaging.messages import CameraHealthReport, ConfigurationChangedMessage, EntityKind
from specter.messaging.shared_state import CameraHealthBucket, MatchCooldownBucket
from specter.storage.cameras import find_camera_settings
from specter.storage.database import DatabaseThread, open_database
from specter.storage.evidence import EvidenceStore
from specter.storage.vector_index import VectorIndex
from specter.vision.sampling import FrameSampler, SamplingDecision

logger = logging.getLogger(__name__)

# Well below the camera health bucket's expiry, so a single late report does not look like a freeze.
HEALTH_REPORT_INTERVAL_SECONDS = 3.0
# Faces far from the camera need the stream's detail, but beyond 1080p a frame costs four times the
# memory and conversion work while models only see a scaled-down copy of it.
MAXIMUM_FRAME_WIDTH_PIXELS = 1920
MAXIMUM_FRAME_HEIGHT_PIXELS = 1080
# Changes to the camera itself restart its process, so only these are applied while it runs.
SCENE_ENTITY_KINDS = frozenset({EntityKind.ZONE, EntityKind.RULE, EntityKind.WATCHLIST})


async def run(settings: Settings, camera_id: str, shutdown_requested: asyncio.Event) -> None:
    """Runs the process for one camera until shutdown is requested."""
    message_bus = MessageBus(client_name=f"specter-camera-{camera_id}")
    try:
        is_connected = await complete_unless_shutdown(
            message_bus.connect(settings.services.nats_url), shutdown_requested
        )
        if is_connected:
            await message_bus.declare_streams(settings.matching.cooldown_seconds)
            await analyze_camera(settings, camera_id, message_bus, shutdown_requested)
    finally:
        await message_bus.close()
    logger.info("camera process stopped", extra={"camera_id": camera_id})


async def analyze_camera(
    settings: Settings, camera_id: str, message_bus: MessageBus, shutdown_requested: asyncio.Event
) -> None:
    """Analyzes the camera's frames, publishes its alerts and reports its health until shutdown."""
    database_thread = DatabaseThread(open_database(settings.paths.database_file))
    vector_index = VectorIndex.connect(settings.services.qdrant_url)
    try:
        camera = await asyncio.get_running_loop().run_in_executor(
            database_thread.executor, partial(find_camera_settings, camera_id)
        )
        if camera is None:
            logger.error("camera does not exist", extra={"camera_id": camera_id})
            return
        await analyze_existing_camera(
            settings, camera, message_bus, database_thread, vector_index, shutdown_requested
        )
    finally:
        await vector_index.close()
        database_thread.close()


async def analyze_existing_camera(
    settings: Settings,
    camera: Camera,
    message_bus: MessageBus,
    database_thread: DatabaseThread,
    vector_index: VectorIndex,
    shutdown_requested: asyncio.Event,
) -> None:
    """Runs the analysis of a camera that was read from the database, until shutdown."""
    health_bucket = await CameraHealthBucket.open(message_bus)
    # The camera manager registers every camera in go2rtc under the camera's id.
    stream_reader = StreamReader(
        camera.id,
        f"{settings.services.go2rtc_rtsp_url}/{camera.id}",
        maximum_width_pixels=MAXIMUM_FRAME_WIDTH_PIXELS,
        maximum_height_pixels=MAXIMUM_FRAME_HEIGHT_PIXELS,
    )
    detector_client = DetectorClient(camera.id, message_bus)
    scene_analyzer = SceneAnalyzer(
        camera,
        PersonIdentifier(
            camera,
            detector_client,
            vector_index,
            await MatchCooldownBucket.open(message_bus),
            settings.matching.cooldown_seconds,
        ),
        AlertPublisher(camera, message_bus, EvidenceStore(settings.paths.data_directory)),
    )
    scene_analyzer.configure(
        await asyncio.get_running_loop().run_in_executor(
            database_thread.executor, partial(load_scene_configuration, camera)
        )
    )
    await message_bus.subscribe_to_configuration_changes(
        partial(
            reload_scene_configuration,
            camera=camera,
            database_thread=database_thread,
            scene_analyzer=scene_analyzer,
        )
    )
    stream_reader.start()
    logger.info("camera process started", extra={"camera_id": camera.id})
    try:
        await complete_unless_shutdown(
            asyncio.gather(
                analyze_frames(
                    stream_reader, FrameSampler(camera.sampling), detector_client, scene_analyzer
                ),
                report_health(camera, stream_reader, health_bucket),
            ),
            shutdown_requested,
        )
    finally:
        await stream_reader.stop()
        detector_client.close()


async def reload_scene_configuration(
    message: ConfigurationChangedMessage,
    *,
    camera: Camera,
    database_thread: DatabaseThread,
    scene_analyzer: SceneAnalyzer,
) -> None:
    """Applies the owner's changed zones, rules or watchlists to the running analysis."""
    if message.owner_id != camera.owner_id or message.entity_kind not in SCENE_ENTITY_KINDS:
        return
    scene_analyzer.configure(
        await asyncio.get_running_loop().run_in_executor(
            database_thread.executor, partial(load_scene_configuration, camera)
        )
    )
    logger.info("scene configuration reloaded", extra={"camera_id": camera.id})


async def analyze_frames(
    stream_reader: StreamReader,
    frame_sampler: FrameSampler,
    detector_client: DetectorClient,
    scene_analyzer: SceneAnalyzer,
) -> None:
    """Detects objects in each admitted frame and analyzes them, adapting the frame rate."""
    while True:
        frame = await stream_reader.next_frame()
        if frame_sampler.decide(frame) is not SamplingDecision.PROCESS:
            continue
        detections = await detector_client.detect(frame)
        latency_milliseconds = detector_client.latency_percentile_milliseconds
        if latency_milliseconds is not None:
            frame_sampler.adapt_to_detection_latency(latency_milliseconds)
        if detections is not None:
            await scene_analyzer.analyze(frame, detections)


async def report_health(
    camera: Camera, stream_reader: StreamReader, health_bucket: CameraHealthBucket
) -> None:
    """Keeps refreshing the camera's health, which the camera manager turns into its status."""
    while True:
        report = CameraHealthReport(
            owner_id=camera.owner_id,
            camera_id=camera.id,
            status=stream_reader.status,
            reported_at=datetime.now(UTC),
        )
        try:
            await health_bucket.report(report)
        except NatsError as error:
            # The next report follows shortly, and NATS reconnects on its own meanwhile.
            logger.warning("cannot report camera health: %s", error)
        await asyncio.sleep(HEALTH_REPORT_INTERVAL_SECONDS)
