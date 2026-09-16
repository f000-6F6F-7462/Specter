"""Entry point of a camera process."""

import asyncio
import logging
from datetime import UTC, datetime
from functools import partial

from nats.errors import Error as NatsError

from specter.camera.detection_client import DetectionClient
from specter.camera.stream_reader import StreamReader
from specter.config.settings import Settings
from specter.core.shutdown import complete_unless_shutdown
from specter.entities.cameras import Camera
from specter.inference.model_store import ModelRole, load_model_manifest
from specter.messaging.client import MessageBus
from specter.messaging.messages import CameraHealthReport
from specter.messaging.shared_state import CameraHealthBucket
from specter.storage.cameras import find_camera_settings
from specter.storage.database import DatabaseThread, open_database
from specter.vision.sampling import FrameSampler, SamplingDecision

logger = logging.getLogger(__name__)

# Well below the camera health bucket's expiry, so a single late report does not look like a freeze.
HEALTH_REPORT_INTERVAL_SECONDS = 3.0


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
    """Sends the camera's frames to the detector and reports its health until shutdown."""
    database_thread = DatabaseThread(open_database(settings.paths.database_file))
    try:
        camera = await asyncio.get_running_loop().run_in_executor(
            database_thread.executor, partial(find_camera_settings, camera_id)
        )
    finally:
        database_thread.close()
    if camera is None:
        logger.error("camera does not exist", extra={"camera_id": camera_id})
        return

    model_manifest = load_model_manifest()
    object_detection_model = model_manifest.models[
        model_manifest.profiles[settings.device.hardware_profile][ModelRole.OBJECT_DETECTION]
    ]
    health_bucket = await CameraHealthBucket.open(message_bus)
    # The camera manager registers every camera in go2rtc under the camera's id.
    stream_reader = StreamReader(
        camera.id,
        f"{settings.services.go2rtc_rtsp_url}/{camera.id}",
        maximum_width_pixels=object_detection_model.input_width,
        maximum_height_pixels=object_detection_model.input_height,
    )
    detection_client = DetectionClient(
        camera.id,
        message_bus,
        object_detection_model.input_width,
        object_detection_model.input_height,
    )
    stream_reader.start()
    logger.info("camera process started", extra={"camera_id": camera.id})
    try:
        await complete_unless_shutdown(
            asyncio.gather(
                analyze_frames(stream_reader, FrameSampler(camera.sampling), detection_client),
                report_health(camera, stream_reader, health_bucket),
            ),
            shutdown_requested,
        )
    finally:
        await stream_reader.stop()
        detection_client.close()


async def analyze_frames(
    stream_reader: StreamReader, frame_sampler: FrameSampler, detection_client: DetectionClient
) -> None:
    """Sends each admitted frame to the detector, adapting the frame rate to how fast it answers."""
    while True:
        frame = await stream_reader.next_frame()
        if frame_sampler.decide(frame) is not SamplingDecision.PROCESS:
            continue
        detections = await detection_client.detect(frame)
        latency_milliseconds = detection_client.latency_percentile_milliseconds
        if latency_milliseconds is not None:
            frame_sampler.adapt_to_detection_latency(latency_milliseconds)
        if detections is not None:
            # TODO: follow the detected objects with the tracker.
            logger.debug(
                "objects detected",
                extra={"camera_id": frame.camera_id, "object_count": len(detections)},
            )


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
