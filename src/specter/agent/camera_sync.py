"""Reconciles cameras between the remote cloud backend and the local Specter engine."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CameraSyncReconciler:
    """Synchronizes camera configurations from the cloud backend to the local Specter API."""

    def __init__(
        self,
        specter_api_url: str,
        owner_id: str,
        api_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initializes the camera reconciler with connection parameters."""
        self._specter_api_url = specter_api_url.rstrip("/")
        self._owner_id = owner_id
        self._api_token = api_token
        self._http_client = http_client

    @property
    def _headers(self) -> dict[str, str]:
        """Headers required to authenticate with the local Specter HTTP API."""
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns the managed or ambient HTTP client."""
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=15.0)

    async def fetch_local_cameras(self) -> list[dict[str, Any]]:
        """Fetches the list of cameras currently configured in the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras"
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            return result
        finally:
            if should_close:
                await client.aclose()

    async def reconcile(self, cloud_cameras: list[dict[str, Any]]) -> dict[str, int]:
        """Reconciles the local camera set against the desired state provided by the cloud backend.

        Args:
            cloud_cameras: Desired camera definitions received from fa-server.

        Returns:
            Dictionary with counts of created, updated, deleted, and unchanged cameras.
        """
        stats = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
        try:
            local_cameras = await self.fetch_local_cameras()
        except Exception as error:
            logger.error("Failed to fetch local cameras for reconciliation: %s", error)
            return stats

        local_cameras_by_id = {str(cam["id"]): cam for cam in local_cameras}
        local_cameras_by_name = {str(cam["name"]): cam for cam in local_cameras}
        cloud_cameras_by_id = {str(cam["id"]): cam for cam in cloud_cameras if "id" in cam}

        client = await self._get_client()
        should_close = self._http_client is None

        try:
            # 1. Create or update cameras that exist in cloud
            for cloud_cam in cloud_cameras:
                cloud_id = str(cloud_cam.get("id", ""))
                cloud_name = str(cloud_cam.get("name", ""))

                # Match by id or fallback to name if locally created
                matching_local = local_cameras_by_id.get(cloud_id) or local_cameras_by_name.get(
                    cloud_name
                )

                if matching_local is None:
                    # Create camera locally
                    await self._create_local_camera(client, cloud_cam)
                    stats["created"] += 1
                else:
                    # Update if changed
                    is_modified = await self._update_local_camera_if_needed(
                        client, matching_local, cloud_cam
                    )
                    if is_modified:
                        stats["updated"] += 1
                    else:
                        stats["unchanged"] += 1

            # 2. Delete local cameras that are no longer in cloud
            for local_id, local_cam in local_cameras_by_id.items():
                if local_id not in cloud_cameras_by_id and local_cam.get("name") not in {
                    c.get("name") for c in cloud_cameras
                }:
                    await self._delete_local_camera(client, local_id)
                    stats["deleted"] += 1

        finally:
            if should_close:
                await client.aclose()

        logger.info("Camera reconciliation completed: %s", stats)
        return stats

    async def _create_local_camera(
        self, client: httpx.AsyncClient, cloud_cam: dict[str, Any]
    ) -> None:
        """Creates a camera on the local Specter engine and starts it if desired."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras"
        body = {
            "name": cloud_cam.get("name"),
            "source_url": cloud_cam.get("source_url"),
            "credentials": cloud_cam.get("credentials"),
            "watchlist_ids": cloud_cam.get("watchlist_ids", []),
            "detection_classes": cloud_cam.get("detection_classes", []),
            "is_enabled": cloud_cam.get("is_enabled", True),
            "metadata": cloud_cam.get("metadata", {}),
        }
        try:
            response = await client.post(url, json=body, headers=self._headers)
            response.raise_for_status()
            created: dict[str, Any] = response.json()
            camera_id = created.get("id")
            logger.info("Created local camera '%s' (id: %s)", body["name"], camera_id)

            if cloud_cam.get("desired_state") == "running" and camera_id:
                start_url = (
                    f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/start"
                )
                start_response = await client.post(start_url, headers=self._headers)
                start_response.raise_for_status()
                logger.info("Started local camera (id: %s)", camera_id)
        except Exception as error:
            logger.error("Failed to create local camera '%s': %s", cloud_cam.get("name"), error)

    async def _update_local_camera_if_needed(
        self,
        client: httpx.AsyncClient,
        local_cam: dict[str, Any],
        cloud_cam: dict[str, Any],
    ) -> bool:
        """Updates local camera settings if they differ from cloud desired state."""
        camera_id = str(local_cam["id"])
        changes: dict[str, Any] = {}

        for field in ("name", "source_url", "is_enabled"):
            if field in cloud_cam and cloud_cam[field] != local_cam.get(field):
                changes[field] = cloud_cam[field]

        if "watchlist_ids" in cloud_cam and sorted(cloud_cam["watchlist_ids"]) != sorted(
            local_cam.get("watchlist_ids", [])
        ):
            changes["watchlist_ids"] = cloud_cam["watchlist_ids"]

        if "detection_classes" in cloud_cam and sorted(cloud_cam["detection_classes"]) != sorted(
            local_cam.get("detection_classes", [])
        ):
            changes["detection_classes"] = cloud_cam["detection_classes"]

        is_modified = False
        if changes:
            patch_url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}"
            try:
                response = await client.patch(patch_url, json=changes, headers=self._headers)
                response.raise_for_status()
                logger.info("Updated local camera %s with changes %s", camera_id, changes)
                is_modified = True
            except Exception as error:
                logger.error("Failed to update local camera %s: %s", camera_id, error)

        # Check desired_state differences
        desired_state = cloud_cam.get("desired_state")
        current_state = local_cam.get("desired_state")
        if desired_state and desired_state != current_state:
            action = "start" if desired_state == "running" else "stop"
            action_url = (
                f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/{action}"
            )
            try:
                response = await client.post(action_url, headers=self._headers)
                response.raise_for_status()
                logger.info(
                    "Applied desired state '%s' to local camera %s", desired_state, camera_id
                )
                is_modified = True
            except Exception as error:
                logger.error("Failed to set camera %s to %s: %s", camera_id, action, error)

        return is_modified

    async def _delete_local_camera(self, client: httpx.AsyncClient, camera_id: str) -> None:
        """Stops and deletes a camera from the local Specter engine."""
        try:
            # First stop if running
            stop_url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/stop"
            await client.post(stop_url, headers=self._headers)
        except Exception as error:
            logger.debug("Camera stop during deletion ignored: %s", error)

        try:
            delete_url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}"
            response = await client.delete(delete_url, headers=self._headers)
            response.raise_for_status()
            logger.info("Deleted local camera %s", camera_id)
        except Exception as error:
            logger.error("Failed to delete local camera %s: %s", camera_id, error)

    async def start_camera(self, camera_id: str) -> None:
        """Starts a local camera."""
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/start"
            response = await client.post(url, headers=self._headers)
            response.raise_for_status()
            logger.info("Started local camera %s", camera_id)
        finally:
            if should_close:
                await client.aclose()

    async def stop_camera(self, camera_id: str) -> None:
        """Stops a local camera."""
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/stop"
            response = await client.post(url, headers=self._headers)
            response.raise_for_status()
            logger.info("Stopped local camera %s", camera_id)
        finally:
            if should_close:
                await client.aclose()

