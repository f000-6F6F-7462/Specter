"""Serializes camera start requests and polls readiness during WebRTC offer handling."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class CameraAutoStarter:
    """Manages per-camera startup synchronization and readiness polling."""

    def __init__(
        self,
        specter_api_url: str,
        owner_id: str,
        http_client: httpx.AsyncClient,
        poll_interval_s: float = 0.5,
        max_poll_attempts: int = 10,
    ) -> None:
        """Initializes the auto-starter with API endpoint and HTTP client.

        Args:
            specter_api_url: Base URL for the Specter API.
            owner_id: Owner/tenant identifier.
            http_client: HTTP client used to dispatch requests.
            poll_interval_s: Interval between poll attempts in seconds.
            max_poll_attempts: Maximum number of polling attempts before giving up.
        """
        self._specter_api_url = specter_api_url.rstrip("/")
        self._owner_id = owner_id
        self._client = http_client
        self._poll_interval_s = poll_interval_s
        self._max_poll_attempts = max_poll_attempts
        self._start_locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, camera_id: str) -> asyncio.Lock:
        """Returns the asyncio.Lock dedicated to serializing start operations for a camera."""
        if camera_id not in self._start_locks:
            self._start_locks[camera_id] = asyncio.Lock()
        return self._start_locks[camera_id]

    async def ensure_camera_ready(
        self,
        camera_id: str,
        sdp_offer: str,
        headers: dict[str, str],
        webrtc_url: str,
    ) -> httpx.Response:
        """Starts the camera if not running and polls until the WebRTC endpoint responds.

        Args:
            camera_id: Target camera identifier.
            sdp_offer: SDP offer received from client browser.
            headers: Headers for the WebRTC request.
            webrtc_url: Target WebRTC endpoint URL.

        Returns:
            The final httpx.Response after startup and polling.
        """
        lock = self._get_lock(camera_id)
        async with lock:
            # Check if a concurrent request already started the camera
            check_resp = await self._client.post(
                webrtc_url, content=sdp_offer.encode("utf-8"), headers=headers
            )
            if check_resp.status_code not in (httpx.codes.CONFLICT, httpx.codes.NOT_FOUND):
                return check_resp

            start_url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/start"
            await self._client.post(start_url, headers=headers)

            response = check_resp
            for attempt in range(1, self._max_poll_attempts + 1):
                await asyncio.sleep(self._poll_interval_s)
                poll_resp = await self._client.post(
                    webrtc_url, content=sdp_offer.encode("utf-8"), headers=headers
                )
                if poll_resp.status_code not in (
                    httpx.codes.CONFLICT,
                    httpx.codes.NOT_FOUND,
                ):
                    logger.info(
                        "Camera %s started successfully after %.1fs (attempt %d)",
                        camera_id,
                        attempt * self._poll_interval_s,
                        attempt,
                    )
                    return poll_resp
                response = poll_resp

            return response
