import asyncio
import socket

from specter.api.main import serve
from specter.config.settings import ApiSettings, Settings

STOP_TIMEOUT_SECONDS = 10.0
STARTUP_SECONDS = 0.5


def find_free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


async def test_api_stops_cleanly_when_shutdown_is_requested(api_settings: Settings) -> None:
    settings = api_settings.model_copy(update={"api": ApiSettings(port=find_free_port())})
    shutdown_requested = asyncio.Event()
    serving_task = asyncio.create_task(serve(settings, shutdown_requested))
    await asyncio.sleep(STARTUP_SECONDS)

    shutdown_requested.set()

    await asyncio.wait_for(serving_task, STOP_TIMEOUT_SECONDS)
    assert serving_task.exception() is None
