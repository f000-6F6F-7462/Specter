from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from specter.agent.alert_forwarder import AlertForwarder
from specter.agent.offline_queue import OfflineQueue


@pytest.mark.asyncio
async def test_alert_forwarder_forwards_live_when_online(tmp_path: Path) -> None:
    queue = OfflineQueue(tmp_path / "queue.sqlite3")

    mock_sio = MagicMock()
    mock_sio.connected = True
    mock_sio.emit = AsyncMock()

    mock_connector = MagicMock()
    mock_connector.is_connected = True
    mock_connector.sio = mock_sio

    forwarder = AlertForwarder(
        owner_id="specter",
        message_bus=MagicMock(),
        cloud_connector=mock_connector,
        offline_queue=queue,
    )

    is_live = await forwarder.handle_alert(
        alert_id="alert_1",
        event_kind="match_confirmed",
        payload={"message_id": "alert_1", "similarity": 0.9},
    )

    assert is_live is True
    assert queue.count() == 0
    mock_sio.emit.assert_called_once()
    args, kwargs = mock_sio.emit.call_args
    assert args[0] == "agent:alert"
    assert args[1]["alert_id"] == "alert_1"
    assert kwargs["namespace"] == "/agent"


@pytest.mark.asyncio
async def test_alert_forwarder_buffers_when_offline(tmp_path: Path) -> None:
    queue = OfflineQueue(tmp_path / "queue.sqlite3")

    mock_connector = MagicMock()
    mock_connector.is_connected = False

    forwarder = AlertForwarder(
        owner_id="specter",
        message_bus=MagicMock(),
        cloud_connector=mock_connector,
        offline_queue=queue,
    )

    is_live = await forwarder.handle_alert(
        alert_id="alert_offline_1",
        event_kind="rule_triggered",
        payload={"message_id": "alert_offline_1"},
    )

    assert is_live is False
    assert queue.count() == 1
    items = queue.peek()
    assert items[0].alert_id == "alert_offline_1"


@pytest.mark.asyncio
async def test_alert_forwarder_drains_queue_on_reconnect(tmp_path: Path) -> None:
    queue = OfflineQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("alert_q1", "match_confirmed", {"target": "t1"})
    queue.enqueue("alert_q2", "rule_triggered", {"rule": "r1"})
    assert queue.count() == 2

    mock_sio = MagicMock()
    mock_sio.connected = True
    mock_sio.emit = AsyncMock()

    mock_connector = MagicMock()
    mock_connector.is_connected = True
    mock_connector.sio = mock_sio

    forwarder = AlertForwarder(
        owner_id="specter",
        message_bus=MagicMock(),
        cloud_connector=mock_connector,
        offline_queue=queue,
    )

    drained_count = await forwarder.drain_queue()

    assert drained_count == 2
    assert queue.count() == 0
    assert mock_sio.emit.call_count == 2
