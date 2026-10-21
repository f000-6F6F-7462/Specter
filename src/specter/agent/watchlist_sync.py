"""Reconciles watchlists and targets between cloud backend and local Specter engine."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class WatchlistSyncReconciler:
    """Synchronizes watchlists and target enrollments between the cloud and local Specter API."""

    def __init__(
        self,
        specter_api_url: str,
        owner_id: str,
        api_token: str,
        backend_url: str = "",
        agent_token: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initializes the watchlist reconciler with connection parameters."""
        self._specter_api_url = specter_api_url.rstrip("/")
        self._owner_id = owner_id
        self._api_token = api_token
        self._backend_url = backend_url.rstrip("/")
        self._agent_token = agent_token
        self._http_client = http_client

    @property
    def _specter_headers(self) -> dict[str, str]:
        """Headers required to authenticate with the local Specter HTTP API."""
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        return headers

    @property
    def _cloud_headers(self) -> dict[str, str]:
        """Headers required to authenticate with the cloud agent HTTP API."""
        headers = {"Content-Type": "application/json"}
        if self._agent_token:
            headers["Authorization"] = f"Bearer {self._agent_token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns the managed or ambient HTTP client."""
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=15.0)

    async def fetch_local_watchlists(self) -> list[dict[str, Any]]:
        """Fetches the list of watchlists currently in the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/watchlists"
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.get(url, headers=self._specter_headers)
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            return result
        finally:
            if should_close:
                await client.aclose()

    async def fetch_local_targets(self, watchlist_id: str) -> list[dict[str, Any]]:
        """Fetches targets for a specific watchlist from the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/watchlists/{watchlist_id}/targets"
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.get(url, headers=self._specter_headers)
            response.raise_for_status()
            result: list[dict[str, Any]] = response.json()
            return result
        except httpx.HTTPError as error:
            logger.warning(
                "Failed to fetch targets for local watchlist %s: %s", watchlist_id, error
            )
            return []
        finally:
            if should_close:
                await client.aclose()

    async def push_local_watchlists(self, connector: Any) -> None:
        """Pushes all local watchlists and targets to the cloud backend."""
        try:
            watchlists = await self.fetch_local_watchlists()
            all_targets: list[dict[str, Any]] = []
            for wl in watchlists:
                wl_id = wl.get("id")
                if wl_id:
                    targets = await self.fetch_local_targets(wl_id)
                    all_targets.extend(targets)

            if connector.sio.connected:
                await connector.sio.emit(
                    "agent:watchlists:sync",
                    {"watchlists": watchlists, "targets": all_targets},
                    namespace="/agent",
                )
                logger.info(
                    "Pushed %d local watchlists and %d targets to cloud",
                    len(watchlists),
                    len(all_targets),
                )
        except Exception as error:
            logger.warning("Failed to push local watchlists to cloud: %s", error)

    async def create_local_watchlist(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Creates a watchlist in the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/watchlists"
        body = {
            "name": payload.get("name", ""),
            "target_type": payload.get("target_type", "person"),
            "appearance_match_threshold_ratio": payload.get(
                "appearance_match_threshold_ratio", 0.7
            ),
            "face_match_threshold_ratio": payload.get("face_match_threshold_ratio", 0.6),
        }
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.post(url, json=body, headers=self._specter_headers)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            logger.info("Created local watchlist %s (%s)", result.get("id"), result.get("name"))
            return result
        except httpx.HTTPError as error:
            logger.error("Failed to create local watchlist %s: %s", payload.get("name"), error)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def update_local_watchlist(
        self, watchlist_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Updates a watchlist in the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/watchlists/{watchlist_id}"
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.patch(url, json=changes, headers=self._specter_headers)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            logger.info("Updated local watchlist %s", watchlist_id)
            return result
        except httpx.HTTPError as error:
            logger.error("Failed to update local watchlist %s: %s", watchlist_id, error)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def delete_local_watchlist(self, watchlist_id: str) -> bool:
        """Deletes a watchlist in the local Specter engine."""
        url = f"{self._specter_api_url}/owners/{self._owner_id}/watchlists/{watchlist_id}"
        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.delete(url, headers=self._specter_headers)
            response.raise_for_status()
            logger.info("Deleted local watchlist %s", watchlist_id)
            return True
        except httpx.HTTPError as error:
            logger.error("Failed to delete local watchlist %s: %s", watchlist_id, error)
            return False
        finally:
            if should_close:
                await client.aclose()

    async def reconcile_watchlists(self, cloud_watchlists: list[dict[str, Any]]) -> dict[str, int]:
        """Reconciles local watchlists against the cloud watchlists.

        Returns:
            Dictionary with counts of created, unchanged, and failed operations.
        """
        stats = {"created": 0, "unchanged": 0, "failed": 0}
        local_watchlists = await self.fetch_local_watchlists()
        local_names = {wl.get("name"): wl for wl in local_watchlists}

        for cloud_wl in cloud_watchlists:
            name = cloud_wl.get("name")
            if not name:
                continue
            if name in local_names:
                stats["unchanged"] += 1
            else:
                created = await self.create_local_watchlist(cloud_wl)
                if created is not None:
                    stats["created"] += 1
                else:
                    stats["failed"] += 1

        logger.info(
            "Watchlist reconciliation complete: created=%d, unchanged=%d, failed=%d",
            stats["created"],
            stats["unchanged"],
            stats["failed"],
        )
        return stats
