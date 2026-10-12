import asyncio
from typing import Any

import pytest

from specter.agent.cloud_connector import CloudConnector
from specter.config.settings import Settings


@pytest.mark.asyncio
async def test_cloud_connector_initialization() -> None:
    settings = Settings()
    connector = CloudConnector(settings=settings)
    assert not connector.is_connected


@pytest.mark.asyncio
async def test_cloud_connector_event_callbacks() -> None:
    settings = Settings()
    received_sync: list[list[dict[str, Any]]] = []
    received_updates: list[dict[str, Any]] = []

    async def on_sync(cameras: list[dict[str, Any]]) -> None:
        received_sync.append(cameras)

    async def on_update(data: dict[str, Any]) -> None:
        received_updates.append(data)

    connector = CloudConnector(
        settings=settings,
        on_cameras_sync=on_sync,
        on_cameras_updated=on_update,
    )

    # Trigger internal handlers directly to verify callback wiring
    sync_handler = connector._sio.handlers["/agent"]["cameras:sync"]
    await sync_handler({"cameras": [{"id": "c1", "name": "Cam 1"}]})
    assert len(received_sync) == 1
    assert received_sync[0][0]["id"] == "c1"

    update_handler = connector._sio.handlers["/agent"]["cameras:updated"]
    await update_handler({"change_kind": "created", "camera_id": "c1"})
    assert len(received_updates) == 1
    assert received_updates[0]["camera_id"] == "c1"


@pytest.mark.asyncio
async def test_cloud_connector_heartbeat_loop_terminates_on_shutdown() -> None:
    settings = Settings()
    connector = CloudConnector(settings=settings)
    shutdown = asyncio.Event()

    task = asyncio.create_task(connector.run_heartbeat_loop(shutdown))
    await asyncio.sleep(0.01)
    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()
