"""Relays WebRTC SDP signaling between cloud backend and local Specter/go2rtc engine."""

import logging
from typing import Any

import httpx

from specter.agent.cloud_connector import CloudConnector

logger = logging.getLogger(__name__)

SDP_MEDIA_TYPE = "application/sdp"


class WebRtcRelay:
    """Handles WebRTC signaling for camera streams over the Socket.IO cloud link."""

    def __init__(
        self,
        cloud_connector: CloudConnector,
        specter_api_url: str,
        owner_id: str,
        api_token: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initializes the WebRTC relay with connector and local Specter API endpoint."""
        self._connector = cloud_connector
        self._specter_api_url = specter_api_url.rstrip("/")
        self._owner_id = owner_id
        self._api_token = api_token
        self._client = http_client or httpx.AsyncClient(timeout=10.0)
        self._is_external_client = http_client is not None

    async def close(self) -> None:
        """Closes the internal HTTP client if owned."""
        if not self._is_external_client:
            await self._client.aclose()

    async def handle_offer(
        self,
        camera_id: str,
        sdp_offer: str,
        correlation_id: str,
        client_socket_id: str,
    ) -> str | None:
        """Exchanges SDP offer with local Specter API and returns SDP answer.

        Args:
            camera_id: Target camera identifier.
            sdp_offer: SDP offer received from client browser.
            correlation_id: Correlation ID for tracking this session.
            client_socket_id: Browser Socket.IO client ID.

        Returns:
            SDP answer string if successful, None otherwise.
        """
        url = f"{self._specter_api_url}/owners/{self._owner_id}/cameras/{camera_id}/live/webrtc"
        headers = {
            "Content-Type": SDP_MEDIA_TYPE,
        }
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        try:
            response = await self._client.post(
                url,
                content=sdp_offer.encode("utf-8"),
                headers=headers,
            )
            response.raise_for_status()
            answer_sdp = response.text

            # Emit webrtc:answer back to cloud backend
            if self._connector.is_connected and self._connector.sio.connected:
                await self._connector.sio.emit(
                    "webrtc:answer",
                    {
                        "cameraId": camera_id,
                        "sdp": answer_sdp,
                        "correlationId": correlation_id,
                        "clientSocketId": client_socket_id,
                    },
                    namespace="/agent",
                )
                logger.info(
                    "Relayed WebRTC answer for camera %s (correlation %s)",
                    camera_id,
                    correlation_id,
                )
            return answer_sdp
        except httpx.HTTPError as error:
            logger.error("Failed to exchange WebRTC SDP for camera %s: %s", camera_id, error)
            return None

    def register_handlers(self) -> None:
        """Registers webrtc:offer and webrtc:ice_candidate handlers on the Socket.IO client."""

        async def on_webrtc_offer(data: dict[str, Any]) -> None:
            camera_id = data.get("cameraId") or data.get("camera_id") or ""
            sdp = data.get("sdp", "")
            correlation_id = data.get("correlationId") or data.get("correlation_id") or ""
            client_socket_id = data.get("clientSocketId") or data.get("client_socket_id") or ""
            if camera_id and sdp:
                await self.handle_offer(camera_id, sdp, correlation_id, client_socket_id)

        async def on_webrtc_ice_candidate(data: dict[str, Any]) -> None:
            logger.debug("Received ICE candidate from browser: %s", data)

        self._connector.sio.on("webrtc:offer", on_webrtc_offer, namespace="/agent")
        self._connector.sio.on("webrtc:ice_candidate", on_webrtc_ice_candidate, namespace="/agent")
