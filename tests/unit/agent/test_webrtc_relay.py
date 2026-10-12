"""Unit tests for the edge agent WebRTC signaling relay and Cloudflare TURN."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from specter.agent.webrtc_relay import WebRtcRelay
from specter.config.settings import AgentSettings


@pytest.mark.asyncio
async def test_webrtc_relay_handles_offer_and_emits_answer() -> None:
    connector = MagicMock()
    connector.is_connected = True
    connector.sio.connected = True
    connector.sio.emit = AsyncMock()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "v=0\r\no=go2rtc 123 456\r\ns=Specter Stream\r\n"
    mock_response.raise_for_status = MagicMock()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post.return_value = mock_response

    relay = WebRtcRelay(
        cloud_connector=connector,
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        api_token="test-secret-token",
        http_client=http_client,
    )

    sdp_offer = "v=0\r\no=browser 789 012\r\ns=-\r\n"
    answer = await relay.handle_offer(
        camera_id="cam-front",
        sdp_offer=sdp_offer,
        correlation_id="corr-123",
        client_socket_id="client-sock-456",
    )

    assert answer == mock_response.text
    http_client.post.assert_awaited_once_with(
        "http://127.0.0.1:8000/owners/test-owner/cameras/cam-front/live/webrtc",
        content=sdp_offer.encode("utf-8"),
        headers={
            "Content-Type": "application/sdp",
            "Authorization": "Bearer test-secret-token",
        },
    )

    connector.sio.emit.assert_awaited_once_with(
        "webrtc:answer",
        {
            "cameraId": "cam-front",
            "sdp": mock_response.text,
            "correlationId": "corr-123",
            "clientSocketId": "client-sock-456",
        },
        namespace="/agent",
    )


@pytest.mark.asyncio
async def test_webrtc_relay_handles_upstream_failure_gracefully() -> None:
    connector = MagicMock()
    connector.is_connected = True
    connector.sio.connected = True
    connector.sio.emit = AsyncMock()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post.side_effect = httpx.ConnectError("Connection refused")

    relay = WebRtcRelay(
        cloud_connector=connector,
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        http_client=http_client,
    )

    answer = await relay.handle_offer(
        camera_id="cam-offline",
        sdp_offer="v=0\r\no=browser ...",
        correlation_id="corr-999",
        client_socket_id="client-sock-999",
    )

    assert answer is None
    connector.sio.emit.assert_not_called()


def test_agent_settings_effective_ice_servers_cloudflare() -> None:
    # 1. Default without Cloudflare TURN credentials
    settings = AgentSettings()
    default_servers = settings.get_effective_ice_servers()
    assert len(default_servers) >= 1
    assert "stun:stun.cloudflare.com:3478" in default_servers[0]["urls"]

    # 2. Configured with Cloudflare Calls TURN credentials
    cf_settings = AgentSettings(
        cloudflare_turn_key_id="cf-turn-user",
        cloudflare_turn_api_token="cf-turn-pass",
    )
    cf_servers = cf_settings.get_effective_ice_servers()
    assert len(cf_servers) == 2
    assert "stun:turn.cloudflare.com:3478" in cf_servers[0]["urls"]
    assert cf_servers[1]["username"] == "cf-turn-user"
    assert cf_servers[1]["credential"] == "cf-turn-pass"
    assert any("turn:turn.cloudflare.com" in url for url in cf_servers[1]["urls"])
