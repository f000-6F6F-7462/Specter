from pathlib import Path

from specter.agent.offline_queue import OfflineQueue


def test_offline_queue_enqueue_and_peek(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.sqlite3"
    queue = OfflineQueue(db_path)

    assert queue.count() == 0

    inserted = queue.enqueue(
        alert_id="alert_1",
        event_kind="match_confirmed",
        payload={"similarity": 0.85, "target_id": "target_abc"},
        snapshot_path="/var/lib/specter/evidence/snapshot1.jpg",
    )
    assert inserted is True
    assert queue.count() == 1

    # Duplicate alert_id is ignored
    duplicate = queue.enqueue(
        alert_id="alert_1",
        event_kind="match_confirmed",
        payload={"similarity": 0.85},
    )
    assert duplicate is False
    assert queue.count() == 1

    # Insert a second item
    queue.enqueue(
        alert_id="alert_2",
        event_kind="rule_triggered",
        payload={"rule_kind": "line_crossing"},
    )
    assert queue.count() == 2

    # Peek retains items in order
    items = queue.peek(limit=10)
    assert len(items) == 2
    assert items[0].alert_id == "alert_1"
    assert items[0].payload["similarity"] == 0.85
    assert items[0].snapshot_path == "/var/lib/specter/evidence/snapshot1.jpg"
    assert items[1].alert_id == "alert_2"


def test_offline_queue_acknowledge_and_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.sqlite3"
    queue = OfflineQueue(db_path)

    queue.enqueue("alert_a", "match_confirmed", {})
    queue.enqueue("alert_b", "match_confirmed", {})
    assert queue.count() == 2

    queue.increment_retry(["alert_a"])
    items = queue.peek(limit=5)
    item_a = next(i for i in items if i.alert_id == "alert_a")
    assert item_a.retry_count == 1

    removed = queue.acknowledge(["alert_a"])
    assert removed == 1
    assert queue.count() == 1

    remaining = queue.peek()
    assert len(remaining) == 1
    assert remaining[0].alert_id == "alert_b"
