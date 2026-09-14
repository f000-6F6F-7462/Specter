from httpx import AsyncClient

from specter.core.di import Container
from specter.domain.streams import preview_key


def _body(**over: object) -> dict:
    body: dict = {
        "name": "Front door",
        "source": {"protocol": "rtsp", "url": "rtsp://cam/1"},
        "watchlist_ids": ["wl_a", "wl_b"],
        "sampling": {"target_fps": 8.0, "min_fps": 2.0},
        "detect_classes": ["face"],
    }
    body.update(over)
    return body


async def _create(client: AsyncClient, **over: object) -> dict:
    resp = await client.post("/api/streams", json=_body(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_lists_and_gets(client: AsyncClient) -> None:
    created = await _create(client)
    assert created["id"].startswith("stream_")
    assert created["desired_state"] == "stopped"
    assert created["live_status"] == "stopped"
    assert created["has_credentials"] is False
    assert created["watchlist_ids"] == ["wl_a", "wl_b"]
    assert created["health"] is None  # nothing has run yet

    listed = await client.get("/api/streams")
    assert [s["id"] for s in listed.json()] == [created["id"]]

    got = await client.get(f"/api/streams/{created['id']}")
    assert got.json()["sampling"]["target_fps"] == 8.0


async def test_patch_updates_fields(client: AsyncClient) -> None:
    created = await _create(client)
    patched = await client.patch(
        f"/api/streams/{created['id']}",
        json={"name": "Loading bay", "watchlist_ids": ["wl_c"], "detect_classes": ["person"]},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Loading bay"
    assert patched.json()["watchlist_ids"] == ["wl_c"]
    assert patched.json()["detect_classes"] == ["person"]


async def test_state_toggles_desired_state(client: AsyncClient) -> None:
    created = await _create(client)
    started = await client.put(f"/api/streams/{created['id']}/state", json={"running": True})
    assert started.json()["desired_state"] == "running"
    assert started.json()["live_status"] == "running"

    stopped = await client.put(f"/api/streams/{created['id']}/state", json={"running": False})
    assert stopped.json()["desired_state"] == "stopped"


async def test_disabled_stream_cannot_be_started(client: AsyncClient) -> None:
    created = await _create(client)
    await client.patch(f"/api/streams/{created['id']}", json={"enabled": False})
    resp = await client.put(f"/api/streams/{created['id']}/state", json={"running": True})
    assert resp.status_code == 422


async def test_delete(client: AsyncClient) -> None:
    created = await _create(client)
    assert (await client.delete(f"/api/streams/{created['id']}")).status_code == 204
    assert (await client.get(f"/api/streams/{created['id']}")).status_code == 404


async def test_cross_owner_is_404(client: AsyncClient) -> None:
    created = await _create(client)
    resp = await client.get(f"/api/streams/{created['id']}", headers={"X-Owner-Id": "o_bob"})
    assert resp.status_code == 404


async def test_unknown_stream_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/streams/stream_nope")).status_code == 404


async def test_preview_404_before_any_frame_written(client: AsyncClient) -> None:
    created = await _create(client)
    resp = await client.get(f"/api/streams/{created['id']}/preview")
    assert resp.status_code == 404


async def test_preview_returns_presigned_url_once_a_frame_is_written(
    client: AsyncClient, container: Container
) -> None:
    created = await _create(client)
    key = preview_key("o_alice", created["id"], container.codec.extension)
    await container.blob.put(key, b"fake-frame", container.codec.content_type)

    resp = await client.get(f"/api/streams/{created['id']}/preview")
    assert resp.status_code == 200
    assert resp.json()["snapshot_url"] == f"memory://{key}?ttl=900"


async def test_preview_cross_owner_is_404(client: AsyncClient, container: Container) -> None:
    created = await _create(client)
    key = preview_key("o_alice", created["id"], container.codec.extension)
    await container.blob.put(key, b"fake-frame", container.codec.content_type)

    resp = await client.get(
        f"/api/streams/{created['id']}/preview", headers={"X-Owner-Id": "o_bob"}
    )
    assert resp.status_code == 404
