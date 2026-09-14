from httpx import AsyncClient

from specter.core.di import Container


async def _create(client: AsyncClient, **body: object) -> dict:
    payload = {"name": "VIPs", "type": "person", "kind": "blacklist"} | body
    resp = await client.post("/api/watchlists", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestWatchlistCrud:
    async def test_create_then_get(self, client: AsyncClient) -> None:
        created = await _create(client, match_threshold=0.9)
        assert created["id"].startswith("wl_")
        assert created["owner_id"] == "o_alice"
        assert created["target_count"] == 0

        got = await client.get(f"/api/watchlists/{created['id']}")
        assert got.status_code == 200
        assert got.json()["match_threshold"] == 0.9

    async def test_list_only_returns_own(self, client: AsyncClient) -> None:
        await _create(client, name="Alice list")
        resp = await client.get("/api/watchlists", headers={"X-Owner-Id": "o_bob"})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_patch_updates_fields(self, client: AsyncClient, container: Container) -> None:
        wl = await _create(client)
        before = await container.health.get_watchlist_version(wl["id"])
        resp = await client.patch(
            f"/api/watchlists/{wl['id']}", json={"name": "Renamed", "match_threshold": 0.5}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
        assert resp.json()["match_threshold"] == 0.5
        # A running stream picks this up without waiting out directory_refresh_s.
        assert await container.health.get_watchlist_version(wl["id"]) == before + 1

    async def test_delete_then_404(self, client: AsyncClient, container: Container) -> None:
        wl = await _create(client)
        before = await container.health.get_watchlist_version(wl["id"])
        assert (await client.delete(f"/api/watchlists/{wl['id']}")).status_code == 204
        assert (await client.get(f"/api/watchlists/{wl['id']}")).status_code == 404
        assert await container.health.get_watchlist_version(wl["id"]) == before + 1

    async def test_get_missing_is_404_problem_json(self, client: AsyncClient) -> None:
        resp = await client.get("/api/watchlists/wl_nope")
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["title"] == "not_found"

    async def test_cross_owner_access_is_404(self, client: AsyncClient) -> None:
        wl = await _create(client)
        resp = await client.get(f"/api/watchlists/{wl['id']}", headers={"X-Owner-Id": "o_bob"})
        assert resp.status_code == 404

    async def test_bad_threshold_is_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/watchlists", json={"name": "x", "type": "person", "match_threshold": 2}
        )
        assert resp.status_code == 422


class TestAuth:
    async def test_missing_api_key_is_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/watchlists", headers={"X-Api-Key": ""})
        assert resp.status_code == 401

    async def test_wrong_api_key_is_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/watchlists", headers={"X-Api-Key": "nope"})
        assert resp.status_code == 401

    async def test_missing_owner_is_422(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/watchlists", headers={"X-Api-Key": "test-key", "X-Owner-Id": ""}
        )
        assert resp.status_code == 422

    async def test_healthz_needs_no_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/health", headers={"X-Api-Key": ""})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
