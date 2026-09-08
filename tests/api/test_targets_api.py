import json

from httpx import AsyncClient

from specter.contracts import EnrollJobMessage
from specter.contracts.streams import JOBS_ENROLL
from specter.core.di import Container


async def _watchlist(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/watchlists", json={"name": "L", "type": "person", "kind": "blacklist"}
    )
    return resp.json()["id"]


def _spec(ref: str, names: list[str]) -> dict:
    return {"ref": ref, "label": f"Person {ref}", "type": "person", "image_names": names}


async def _enroll(client: AsyncClient, wl_id: str, specs: list[dict], files: list) -> dict:
    resp = await client.post(
        f"/api/watchlists/{wl_id}/targets",
        data={"targets": json.dumps(specs)},
        files=files,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


class TestEnrollment:
    async def test_enroll_creates_queued_targets(self, client: AsyncClient, jpeg: bytes) -> None:
        wl = await _watchlist(client)
        files = [
            ("images", ("a1.jpg", jpeg, "image/jpeg")),
            ("images", ("a2.jpg", jpeg, "image/jpeg")),
            ("images", ("b1.jpg", jpeg, "image/jpeg")),
        ]
        out = await _enroll(
            client,
            wl,
            [_spec("a", ["a1.jpg", "a2.jpg"]), _spec("b", ["b1.jpg"])],
            files,
        )
        assert out["batch_id"].startswith("bat_")
        assert {i["ref"] for i in out["items"]} == {"a", "b"}
        assert all(i["status"] == "queued" for i in out["items"])
        assert len(out["items"][0]["image_ids"]) == 2

        listed = await client.get(f"/api/watchlists/{wl}/targets")
        assert listed.status_code == 200
        assert len(listed.json()) == 2

        queued = await client.get(f"/api/watchlists/{wl}/targets", params={"status": "queued"})
        assert len(queued.json()) == 2
        ready = await client.get(f"/api/watchlists/{wl}/targets", params={"status": "ready"})
        assert ready.json() == []

    async def test_enroll_publishes_one_job_per_image(
        self, client: AsyncClient, container: Container, jpeg: bytes
    ) -> None:
        wl = await _watchlist(client)
        out = await _enroll(
            client,
            wl,
            [_spec("a", ["a1.jpg", "a2.jpg"]), _spec("b", ["b1.jpg"])],
            [
                ("images", ("a1.jpg", jpeg, "image/jpeg")),
                ("images", ("a2.jpg", jpeg, "image/jpeg")),
                ("images", ("b1.jpg", jpeg, "image/jpeg")),
            ],
        )
        jobs = container.bus.published(JOBS_ENROLL, EnrollJobMessage)  # type: ignore[attr-defined]
        assert len(jobs) == 3
        assert all(isinstance(j, EnrollJobMessage) for j in jobs)
        assert {j.target_id for j in jobs} == {i["target_id"] for i in out["items"]}
        assert all(j.modality == "face" and j.blob_key.endswith(".jpg") for j in jobs)

    async def test_enroll_non_person_type_is_422(self, client: AsyncClient, jpeg: bytes) -> None:
        wl = await _watchlist(client)
        spec = {"ref": "v", "label": "Van", "type": "vehicle", "image_names": ["v.jpg"]}
        resp = await client.post(
            f"/api/watchlists/{wl}/targets",
            data={"targets": json.dumps([spec])},
            files=[("images", ("v.jpg", jpeg, "image/jpeg"))],
        )
        assert resp.status_code == 422

    async def test_enrollment_batch_status(self, client: AsyncClient, jpeg: bytes) -> None:
        wl = await _watchlist(client)
        out = await _enroll(
            client,
            wl,
            [_spec("a", ["a1.jpg"])],
            [("images", ("a1.jpg", jpeg, "image/jpeg"))],
        )
        batch = await client.get(f"/api/enrollments/{out['batch_id']}")
        assert batch.status_code == 200
        body = batch.json()
        assert body["state"] == "queued"
        assert body["targets"][0]["images"][0]["status"] == "pending"

    async def test_missing_file_reference_is_422(self, client: AsyncClient, jpeg: bytes) -> None:
        wl = await _watchlist(client)
        resp = await client.post(
            f"/api/watchlists/{wl}/targets",
            data={"targets": json.dumps([_spec("a", ["missing.jpg"])])},
            files=[("images", ("a1.jpg", jpeg, "image/jpeg"))],
        )
        assert resp.status_code == 422

    async def test_bad_targets_json_is_422(self, client: AsyncClient) -> None:
        wl = await _watchlist(client)
        resp = await client.post(
            f"/api/watchlists/{wl}/targets", data={"targets": "not json"}, files=[]
        )
        assert resp.status_code == 422


class TestTargetLifecycle:
    async def _one_target(self, client: AsyncClient, jpeg: bytes) -> tuple[str, str]:
        wl = await _watchlist(client)
        out = await _enroll(
            client,
            wl,
            [_spec("a", ["a1.jpg"])],
            [("images", ("a1.jpg", jpeg, "image/jpeg"))],
        )
        return wl, out["items"][0]["target_id"]

    async def test_get_patch_delete(self, client: AsyncClient, jpeg: bytes) -> None:
        _, tid = await self._one_target(client, jpeg)

        got = await client.get(f"/api/targets/{tid}")
        assert got.status_code == 200
        assert got.json()["status"] == "queued"

        patched = await client.patch(
            f"/api/targets/{tid}", json={"enabled": False, "label": "Renamed"}
        )
        assert patched.json()["enabled"] is False
        assert patched.json()["label"] == "Renamed"

        assert (await client.delete(f"/api/targets/{tid}")).status_code == 204
        assert (await client.get(f"/api/targets/{tid}")).status_code == 404

    async def test_add_and_remove_images(self, client: AsyncClient, jpeg: bytes) -> None:
        _, tid = await self._one_target(client, jpeg)
        added = await client.post(
            f"/api/targets/{tid}/images",
            files=[("images", ("extra.jpg", jpeg, "image/jpeg"))],
        )
        assert added.status_code == 200
        image_ids = [i["id"] for i in added.json()["images"]]
        assert len(image_ids) == 2

        removed = await client.delete(f"/api/targets/{tid}/images/{image_ids[0]}")
        assert removed.status_code == 200
        assert len(removed.json()["images"]) == 1

        missing = await client.delete(f"/api/targets/{tid}/images/img_nope")
        assert missing.status_code == 404

    async def test_batch_delete(self, client: AsyncClient, jpeg: bytes) -> None:
        wl = await _watchlist(client)
        out = await _enroll(
            client,
            wl,
            [_spec("a", ["a.jpg"]), _spec("b", ["b.jpg"])],
            [
                ("images", ("a.jpg", jpeg, "image/jpeg")),
                ("images", ("b.jpg", jpeg, "image/jpeg")),
            ],
        )
        ids = ",".join(i["target_id"] for i in out["items"])
        resp = await client.delete(
            f"/api/watchlists/{wl}/targets", params={"ids": f"{ids},tgt_bogus"}
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert (await client.get(f"/api/watchlists/{wl}/targets")).json() == []

    async def test_cross_owner_target_is_404(self, client: AsyncClient, jpeg: bytes) -> None:
        _, tid = await self._one_target(client, jpeg)
        resp = await client.get(f"/api/targets/{tid}", headers={"X-Owner-Id": "o_bob"})
        assert resp.status_code == 404
