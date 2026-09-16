import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specter.camera_manager.alert_recorder import AlertRecorder
from specter.entities.alerts import IdentityMatchAlert, RuleAlert
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality
from specter.messaging.client import MessageBus
from specter.messaging.messages import (
    MatchConfirmedMessage,
    MessageBoundingBox,
    RuleTriggeredMessage,
)
from specter.storage.alerts import find_alert
from specter.storage.database import DatabaseThread, open_database
from specter.storage.migrate import apply_migrations

pytestmark = pytest.mark.integration

RECORDING_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.1
RECORDER_STOP_TIMEOUT_SECONDS = 5.0
BOUNDING_BOX = MessageBoundingBox(x=0.25, y=0.1, width=0.2, height=0.6)


async def wait_for_alert(database_thread: DatabaseThread, alert_id: str) -> object:
    deadline = time.monotonic() + RECORDING_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        alert = await asyncio.get_running_loop().run_in_executor(
            database_thread.executor, find_alert, alert_id
        )
        if alert is not None:
            return alert
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"alert {alert_id} was not recorded in time")


async def test_published_events_are_saved_as_alerts_when_recorder_runs(
    message_bus: MessageBus, tmp_path: Path, unique_owner_id: str, unique_camera_id: str
) -> None:
    now = datetime.now(UTC)
    match_message = MatchConfirmedMessage(
        occurred_at=now,
        owner_id=unique_owner_id,
        camera_id=unique_camera_id,
        track_id=3,
        watchlist_id="watchlist_wanted",
        target_id="target_jane",
        modality=EmbeddingModality.FACE,
        similarity_ratio=0.91,
        margin_ratio=0.4,
        threshold_ratio=0.5,
        object_class="person",
        bounding_box=BOUNDING_BOX,
        first_seen_at=now,
        frame_captured_at=now,
    )
    rule_message = RuleTriggeredMessage(
        occurred_at=now,
        owner_id=unique_owner_id,
        camera_id=unique_camera_id,
        rule_id="rule_gate",
        rule_kind=RuleKind.LINE_CROSSING,
        track_id=4,
        object_class="car",
        bounding_box=BOUNDING_BOX,
        crossing_direction=CrossingDirection.LEFT_TO_RIGHT,
        frame_captured_at=now,
    )
    database = open_database(tmp_path / "specter.sqlite3")
    apply_migrations(database)
    database_thread = DatabaseThread(database)
    shutdown_requested = asyncio.Event()
    recorder_task = asyncio.create_task(
        AlertRecorder(message_bus, database_thread).run(shutdown_requested)
    )
    try:
        await message_bus.publish(match_message)
        await message_bus.publish(rule_message)
        match_alert = await wait_for_alert(database_thread, match_message.message_id)
        rule_alert = await wait_for_alert(database_thread, rule_message.message_id)
    finally:
        shutdown_requested.set()
        await asyncio.wait_for(recorder_task, RECORDER_STOP_TIMEOUT_SECONDS)
        database_thread.close()

    assert isinstance(match_alert, IdentityMatchAlert)
    assert match_alert.target_id == "target_jane"
    assert isinstance(rule_alert, RuleAlert)
    assert rule_alert.crossing_direction is CrossingDirection.LEFT_TO_RIGHT
