"""Relays WebRTC SDP signaling between cloud backend and local Specter/go2rtc engine."""

import logging
from typing import Any, TypedDict

import httpx

from specter.agent.camera_autostart import CameraAutoStarter
from specter.agent.cloud_connector import CloudConnector

logger = logging.getLogger(__name__)

SDP_MEDIA_TYPE = "application/sdp"


class WebRtcErrorPayload(TypedDict):
    """Payload format for webrtc:error event."""

    cameraId: str
    correlationId: str
    clientSocketId: str
    error: str
    retryable: bool


def build_webrtc_error_payload(
    camera_id: str,
    correlation_id: str,
    client_socket_id: str,
    error: Exception | str,
    retryable: bool = True,
) -> WebRtcErrorPayload:
    """Builds a structured error payload for WebRTC negotiation failures.

    Args:
        camera_id: Target camera identifier.
        correlation_id: Session correlation ID.
        client_socket_id: Requesting client socket ID.
        error: Exception or error message string.
        retryable: Whether the client can retry the connection.

    Returns:
        A strongly typed WebRtcErrorPayload dictionary.
    """
    message = (
        f"Failed to negotiate camera stream: {error}" if isinstance(error, Exception) else error
    )
    return {
        "cameraId": camera_id,
        "correlationId": correlation_id,
        "clientSocketId": client_socket_id,
        "error": message,
        "retryable": retryable,
    }


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
        self._auto_starter = CameraAutoStarter(
            specter_api_url=self._specter_api_url,
            owner_id=self._owner_id,
            http_client=self._client,
        )

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
        logger.info(
            "Processing WebRTC offer for camera %s (correlation %s, client %s)",
            camera_id,
            correlation_id,
            client_socket_id,
        )
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
            if response.status_code == httpx.codes.CONFLICT:
                logger.info("Camera %s is not running; starting it automatically", camera_id)
                response = await self._auto_starter.ensure_camera_ready(
                    camera_id=camera_id,
                    sdp_offer=sdp_offer,
                    headers=headers,
                    webrtc_url=url,
                )
            if response.status_code == httpx.codes.NOT_FOUND and self._owner_id != "facealert":
                fallback_url = (
                    f"{self._specter_api_url}/owners/facealert/cameras/{camera_id}/live/webrtc"
                )
                response = await self._client.post(
                    fallback_url,
                    content=sdp_offer.encode("utf-8"),
                    headers=headers,
                )
            response.raise_for_status()
            answer_sdp = response.text

            # Emit webrtc:answer back to cloud backend
            if self._connector.is_connected or getattr(self._connector.sio, "connected", False):
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
        except Exception as error:
            logger.error("Failed to exchange WebRTC SDP for camera %s: %s", camera_id, error)
            if self._connector.is_connected or getattr(self._connector.sio, "connected", False):
                try:
                    payload = build_webrtc_error_payload(
                        camera_id=camera_id,
                        correlation_id=correlation_id,
                        client_socket_id=client_socket_id,
                        error=error,
                    )
                    await self._connector.sio.emit(
                        "webrtc:error",
                        payload,
                        namespace="/agent",
                    )
                except Exception as emit_err:
                    logger.debug("Failed to emit webrtc:error: %s", emit_err)
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
