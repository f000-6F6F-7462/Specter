"""Starts, supervises and stops one process per camera, and publishes each camera's status."""

import asyncio
import logging
import sys
import time
from asyncio.subprocess import Process
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from functools import partial

from nats.errors import Error as NatsError

from specter.camera_manager.go2rtc import Go2rtcClient, build_camera_source_url
from specter.core.shutdown import complete_unless_shutdown
from specter.entities.cameras import Camera, CameraStatus
from specter.messaging.message_bus import MessageBus
from specter.messaging.messages import (
    CameraStatusChangedMessage,
    ConfigurationChangedMessage,
    EntityKind,
)
from specter.messaging.shared_state import CameraHealthBucket
from specter.storage.cameras import list_cameras_to_run
from specter.storage.credentials import CredentialCipher
from specter.storage.database import DatabaseThread

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 30.0
# A camera that loses its stream is often the first sign that go2rtc restarted, so it triggers a
# reconcile at once; this gap keeps many cameras reconnecting together from repeating it.
RECONNECTING_RECONCILE_INTERVAL_SECONDS = 10.0
HEALTH_CHECK_INTERVAL_SECONDS = 2.0
# A camera process refreshes its health every few seconds, so a longer silence means it froze.
FROZEN_AFTER_SECONDS = 15.0
# Before its first report a process still starts Python, connects to NATS and reads its settings.
STARTUP_GRACE_SECONDS = 30.0
PROCESS_STOP_TIMEOUT_SECONDS = 10.0
INITIAL_RESTART_DELAY_SECONDS = 1.0
MAXIMUM_RESTART_DELAY_SECONDS = 60.0
# A process that ran this long counts as stable, so its next failure restarts it quickly again.
STABLE_RUN_SECONDS = 300.0
# Camera ids start with this prefix, which tells camera streams apart from others in go2rtc.
CAMERA_STREAM_NAME_PREFIX = "camera_"


class ProcessEnding(Enum):
    """Why supervision of a camera process ended."""

    NOT_STARTED = auto()
    EXITED = auto()
    FROZEN = auto()
    STOP_REQUESTED = auto()


def next_restart_delay_seconds(
    previous_delay_seconds: float | None, run_duration_seconds: float
) -> float:
    """Returns how long to wait before restarting a process that ran for the given time."""
    if previous_delay_seconds is None or run_duration_seconds >= STABLE_RUN_SECONDS:
        return INITIAL_RESTART_DELAY_SECONDS
    return min(previous_delay_seconds * 2, MAXIMUM_RESTART_DELAY_SECONDS)


def build_camera_process_command(camera_id: str) -> tuple[str, ...]:
    """Returns the command that runs a camera's process with the current Python interpreter."""
    return (sys.executable, "-m", "specter", "camera", "--camera-id", camera_id)


class CameraSupervisor:
    """Keeps one camera's process running and publishes the camera's status from its health.

    A process that exits or stops reporting its health is restarted with growing delays, which
    start over once a process ran long enough to count as stable.
    """

    def __init__(
        self,
        camera: Camera,
        message_bus: MessageBus,
        health_bucket: CameraHealthBucket,
        handle_reconnecting: Callable[[], None],
    ) -> None:
        self._camera = camera
        self._message_bus = message_bus
        self._health_bucket = health_bucket
        self._handle_reconnecting = handle_reconnecting
        self._stop_requested = asyncio.Event()
        self._published_status: CameraStatus | None = None

    @property
    def camera(self) -> Camera:
        """The camera as it was when supervision started."""
        return self._camera

    def request_stop(self) -> None:
        """Asks ``run`` to stop the process and return."""
        self._stop_requested.set()

    async def run(self) -> None:
        """Runs the camera's process, restarting it after failures, until a stop is requested."""
        restart_delay_seconds: float | None = None
        while not self._stop_requested.is_set():
            started_at = time.monotonic()
            process_ending = await self._run_process_once()
            if process_ending is ProcessEnding.STOP_REQUESTED:
                break
            await self._publish_status(CameraStatus.FAILED)
            restart_delay_seconds = next_restart_delay_seconds(
                restart_delay_seconds, time.monotonic() - started_at
            )
            logger.warning(
                "camera process failed, restarting",
                extra={
                    "camera_id": self._camera.id,
                    "process_ending": process_ending.name,
                    "restart_delay_seconds": restart_delay_seconds,
                },
            )
            await complete_unless_shutdown(
                asyncio.sleep(restart_delay_seconds), self._stop_requested
            )
        await self._publish_status(CameraStatus.STOPPED)

    async def _run_process_once(self) -> ProcessEnding:
        try:
            process = await asyncio.create_subprocess_exec(
                *build_camera_process_command(self._camera.id)
            )
        except OSError:
            logger.exception("cannot start camera process", extra={"camera_id": self._camera.id})
            return ProcessEnding.NOT_STARTED
        await self._publish_status(CameraStatus.STARTING)
        process_ending = await self._watch_process(process)
        await self._stop_process(process)
        return process_ending

    async def _watch_process(self, process: Process) -> ProcessEnding:
        process_started_at = datetime.now(UTC)
        last_reported_at: datetime | None = None
        silence_deadline = time.monotonic() + STARTUP_GRACE_SECONDS
        while True:
            if self._stop_requested.is_set():
                return ProcessEnding.STOP_REQUESTED
            if process.returncode is not None:
                return ProcessEnding.EXITED
            try:
                report = await self._health_bucket.read(self._camera.owner_id, self._camera.id)
            except NatsError:
                # Without NATS the process cannot report either, so its silence proves nothing.
                silence_deadline = max(silence_deadline, time.monotonic() + FROZEN_AFTER_SECONDS)
                report = None
            # A report older than the process was left behind by the previous run.
            if (
                report is not None
                and report.reported_at > process_started_at
                and report.reported_at != last_reported_at
            ):
                last_reported_at = report.reported_at
                silence_deadline = time.monotonic() + FROZEN_AFTER_SECONDS
                if (
                    report.status is CameraStatus.RECONNECTING
                    and self._published_status is not CameraStatus.RECONNECTING
                ):
                    self._handle_reconnecting()
                await self._publish_status(report.status)
            if time.monotonic() > silence_deadline:
                return ProcessEnding.FROZEN
            await complete_unless_shutdown(
                asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS), self._stop_requested
            )

    @staticmethod
    async def _stop_process(process: Process) -> None:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), PROCESS_STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                # A process stuck in native code, such as the video decoder, can ignore SIGTERM.
                with suppress(ProcessLookupError):
                    process.kill()
        await process.wait()

    async def _publish_status(self, status: CameraStatus) -> None:
        if status is self._published_status:
            return
        message = CameraStatusChangedMessage(
            occurred_at=datetime.now(UTC),
            owner_id=self._camera.owner_id,
            camera_id=self._camera.id,
            status=status,
        )
        try:
            await self._message_bus.publish(message)
        except NatsError as error:
            logger.warning("cannot publish camera status: %s", error)
            return
        self._published_status = status


@dataclass(frozen=True, slots=True)
class SupervisedCamera:
    """A camera's supervisor and the task that runs it."""

    supervisor: CameraSupervisor
    task: asyncio.Task[None]


class CameraManager:
    """Keeps a process running for every camera that should run, and for no other camera.

    A camera configuration change triggers a reconcile at once, and so does a camera that starts
    reconnecting, since go2rtc may have restarted and lost its streams. A periodic reconcile also
    catches changes whose event was missed.
    """

    def __init__(
        self,
        *,
        database_thread: DatabaseThread,
        cipher: CredentialCipher,
        message_bus: MessageBus,
        health_bucket: CameraHealthBucket,
        go2rtc_client: Go2rtcClient,
    ) -> None:
        self._database_thread = database_thread
        self._cipher = cipher
        self._message_bus = message_bus
        self._health_bucket = health_bucket
        self._go2rtc_client = go2rtc_client
        self._supervised_cameras: dict[str, SupervisedCamera] = {}
        self._reconcile_requested = asyncio.Event()
        self._last_reconnecting_reconcile_at: float | None = None

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Reconciles camera processes until shutdown is requested, then stops them all."""
        await self._message_bus.subscribe_to_configuration_changes(
            self._handle_configuration_change
        )
        try:
            while not shutdown_requested.is_set():
                self._reconcile_requested.clear()
                await self._reconcile_or_log()
                await self._wait_for_next_reconcile(shutdown_requested)
        finally:
            await self._stop_all_cameras()

    async def _handle_configuration_change(self, message: ConfigurationChangedMessage) -> None:
        if message.entity_kind is EntityKind.CAMERA:
            self._reconcile_requested.set()

    def _request_reconcile_for_reconnecting_camera(self) -> None:
        now = time.monotonic()
        if (
            self._last_reconnecting_reconcile_at is not None
            and now - self._last_reconnecting_reconcile_at < RECONNECTING_RECONCILE_INTERVAL_SECONDS
        ):
            return
        self._last_reconnecting_reconcile_at = now
        self._reconcile_requested.set()

    async def _wait_for_next_reconcile(self, shutdown_requested: asyncio.Event) -> None:
        reconcile_requested = asyncio.wait_for(
            self._reconcile_requested.wait(), RECONCILE_INTERVAL_SECONDS
        )
        with suppress(TimeoutError):
            await complete_unless_shutdown(reconcile_requested, shutdown_requested)

    async def _reconcile_or_log(self) -> None:
        try:
            await self._reconcile()
        except Exception:
            # The next reconcile retries, so one failure, such as go2rtc restarting, must not
            # stop the manager.
            logger.exception("cannot reconcile camera processes")

    async def _reconcile(self) -> None:
        cameras = await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor, partial(list_cameras_to_run, self._cipher)
        )
        cameras_to_run = {camera.id: camera for camera in cameras}
        for camera_id, supervised_camera in list(self._supervised_cameras.items()):
            is_outdated = cameras_to_run.get(camera_id) != supervised_camera.supervisor.camera
            if is_outdated or supervised_camera.task.done():
                await self._stop_camera(camera_id)

        registered_stream_names = await self._go2rtc_client.list_stream_names()
        for stream_name in registered_stream_names - cameras_to_run.keys():
            if stream_name.startswith(CAMERA_STREAM_NAME_PREFIX):
                await self._go2rtc_client.remove_stream(stream_name)

        for camera in cameras_to_run.values():
            # A missing stream means go2rtc restarted, and a camera without a supervisor may have
            # a new source, so both are registered again.
            if (
                camera.id not in registered_stream_names
                or camera.id not in self._supervised_cameras
            ):
                await self._go2rtc_client.register_stream(
                    camera.id, build_camera_source_url(camera)
                )
            if camera.id not in self._supervised_cameras:
                self._start_camera(camera)

    def _start_camera(self, camera: Camera) -> None:
        supervisor = CameraSupervisor(
            camera,
            self._message_bus,
            self._health_bucket,
            self._request_reconcile_for_reconnecting_camera,
        )
        self._supervised_cameras[camera.id] = SupervisedCamera(
            supervisor=supervisor, task=asyncio.create_task(supervisor.run())
        )

    async def _stop_camera(self, camera_id: str) -> None:
        supervised_camera = self._supervised_cameras.pop(camera_id)
        supervised_camera.supervisor.request_stop()
        # Awaiting also raises an error that ended the supervisor early, so it gets logged.
        await supervised_camera.task

    async def _stop_all_cameras(self) -> None:
        for supervised_camera in self._supervised_cameras.values():
            supervised_camera.supervisor.request_stop()
        await asyncio.gather(
            *(supervised_camera.task for supervised_camera in self._supervised_cameras.values()),
            return_exceptions=True,
        )
        self._supervised_cameras.clear()
