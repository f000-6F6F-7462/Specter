from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from specter.core.di import Container
from specter.domain.alerts import Alert, MatchEvidence
from specter.domain.vision import BBox


async def _seed_alerts(container: Container) -> None:
    now = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    rows = [
        Alert(
            id=f"evt_{n}",
            owner_id="o_alice",
            stream_id="st_1" if n < 2 else "st_2",
            watchlist_id="wl_1",
            target_id="tgt_1",
            similarity=0.7 + n * 0.1,
            bbox=BBox(1, 1, 10, 10),
            track_id=n,
            frame_ts=now,
            created_at=now.replace(minute=n),
            evidence=MatchEvidence(snapshot_key=f"blobs/o_alice/snap/{n}.jpg"),
        )
        for n in range(3)
    ]
    rows.append(
        Alert(
            id="evt_other",
            owner_id="o_bob",
            stream_id="st_1",
            watchlist_id="wl_1",
            target_id="tgt_1",
            similarity=0.99,
            bbox=BBox(1, 1, 2, 2),
            track_id=9,
            frame_ts=now,
            created_at=now,
        )
    )
    async with container.uow_factory() as uow:
        for alert in rows:
            await uow.alerts.add(alert)
    for alert in rows:
        if alert.evidence.snapshot_key:
            await container.blob.put(alert.evidence.snapshot_key, b"fake-jpeg", "image/jpeg")


@pytest.fixture
async def seeded(container: Container) -> Container:
    await _seed_alerts(container)
    return container


class TestListAlerts:
    async def test_lists_only_own_newest_first(
        self, client: AsyncClient, seeded: Container
    ) -> None:
        resp = await client.get("/api/alerts")
        assert resp.status_code == 200
        body = resp.json()
        ids = [a["id"] for a in body["items"]]
        assert ids == ["evt_2", "evt_1", "evt_0"]
        assert "evt_other" not in ids

    async def test_filter_by_stream_and_confidence(
        self, client: AsyncClient, seeded: Container
    ) -> None:
        by_stream = await client.get("/api/alerts", params={"stream_id": "st_2"})
        assert [a["id"] for a in by_stream.json()["items"]] == ["evt_2"]

        by_conf = await client.get("/api/alerts", params={"min_confidence": 0.85})
        assert {a["id"] for a in by_conf.json()["items"]} == {"evt_2"}

    async def test_pagination_cursor(self, client: AsyncClient, seeded: Container) -> None:
        page1 = await client.get("/api/alerts", params={"limit": 2})
        body1 = page1.json()
        assert len(body1["items"]) == 2
        assert body1["next_cursor"] is not None

        page2 = await client.get("/api/alerts", params={"limit": 2, "cursor": body1["next_cursor"]})
        assert len(page2.json()["items"]) == 1


class TestAlertActions:
    async def test_get_one_and_missing(self, client: AsyncClient, seeded: Container) -> None:
        got = await client.get("/api/alerts/evt_1")
        assert got.status_code == 200
        # presigned per-request, not the raw blob key
        assert got.json()["snapshot_url"] == "memory://blobs/o_alice/snap/1.jpg?ttl=900"
        assert (await client.get("/api/alerts/evt_nope")).status_code == 404

    async def test_cross_owner_is_404(self, client: AsyncClient, seeded: Container) -> None:
        assert (await client.get("/api/alerts/evt_other")).status_code == 404

    async def test_ack(self, client: AsyncClient, seeded: Container) -> None:
        resp = await client.post("/api/alerts/evt_0/ack")
        assert resp.status_code == 200
        assert resp.json()["acknowledged"] is True
        assert resp.json()["disposition"] == "unreviewed"

    async def test_resolve(self, client: AsyncClient, seeded: Container) -> None:
        resp = await client.post(
            "/api/alerts/evt_0/resolve",
            json={"disposition": "false_positive", "note": "reflection"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["disposition"] == "false_positive"
        assert body["acknowledged"] is True
        assert body["note"] == "reflection"

    async def test_resolve_to_unreviewed_is_422(
        self, client: AsyncClient, seeded: Container
    ) -> None:
        resp = await client.post("/api/alerts/evt_0/resolve", json={"disposition": "unreviewed"})
        assert resp.status_code == 422
