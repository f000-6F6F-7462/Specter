import asyncio
from functools import partial

import pytest

from specter.camera.main import run as run_camera
from specter.camera_manager.main import run as run_camera_manager
from specter.config.settings import ServiceSettings, Settings
from specter.detector.main import run as run_detector

pytestmark = pytest.mark.integration

PROCESS_STOP_TIMEOUT_SECONDS = 5.0


@pytest.mark.parametrize("process_name", ["camera-manager", "camera", "detector"])
async def test_process_stops_when_shutdown_is_requested(
    nats_server_url: str, process_name: str
) -> None:
    settings = Settings(services=ServiceSettings(nats_url=nats_server_url))
    processes = {
        "camera-manager": partial(run_camera_manager, settings),
        "camera": partial(run_camera, settings, "front_door"),
        "detector": partial(run_detector, settings),
    }
    shutdown_requested = asyncio.Event()
    process_task = asyncio.create_task(processes[process_name](shutdown_requested))

    shutdown_requested.set()

    await asyncio.wait_for(process_task, PROCESS_STOP_TIMEOUT_SECONDS)
