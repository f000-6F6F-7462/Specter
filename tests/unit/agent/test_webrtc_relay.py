"""Unit tests for the edge agent WebRTC signaling relay and Cloudflare TURN."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from specter.agent.camera_autostart import CameraAutoStarter
from specter.agent.webrtc_relay import WebRtcRelay, build_webrtc_error_payload
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
    connector.sio.emit.assert_awaited_once_with(
        "webrtc:error",
        {
            "cameraId": "cam-offline",
            "correlationId": "corr-999",
            "clientSocketId": "client-sock-999",
            "error": "Failed to negotiate camera stream: Connection refused",
            "retryable": True,
        },
        namespace="/agent",
    )


@pytest.mark.asyncio
async def test_webrtc_relay_auto_starts_camera_with_lock_and_polling() -> None:
    connector = MagicMock()
    connector.is_connected = True
    connector.sio.connected = True
    connector.sio.emit = AsyncMock()

    resp_conflict = MagicMock(status_code=409)
    resp_conflict.raise_for_status = MagicMock()

    resp_ok = MagicMock(status_code=200, text="v=0\r\ns=Live\r\n")
    resp_ok.raise_for_status = MagicMock()

    http_client = AsyncMock(spec=httpx.AsyncClient)
    # First call: 409 conflict, within lock check: 409 conflict, start called, poll 1: 200 ok
    http_client.post.side_effect = [
        resp_conflict,
        resp_conflict,
        MagicMock(status_code=200),
        resp_ok,
    ]

    relay = WebRtcRelay(
        cloud_connector=connector,
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        http_client=http_client,
    )

    answer = await relay.handle_offer(
        camera_id="cam-stopped",
        sdp_offer="v=0\r\no=browser ...",
        correlation_id="corr-auto",
        client_socket_id="client-sock-auto",
    )

    assert answer == "v=0\r\ns=Live\r\n"
    connector.sio.emit.assert_awaited_once_with(
        "webrtc:answer",
        {
            "cameraId": "cam-stopped",
            "sdp": "v=0\r\ns=Live\r\n",
            "correlationId": "corr-auto",
            "clientSocketId": "client-sock-auto",
        },
        namespace="/agent",
    )


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


def test_build_webrtc_error_payload() -> None:
    payload = build_webrtc_error_payload(
        camera_id="cam-1",
        correlation_id="corr-1",
        client_socket_id="sock-1",
        error=RuntimeError("something went wrong"),
        retryable=True,
    )
    assert payload == {
        "cameraId": "cam-1",
        "correlationId": "corr-1",
        "clientSocketId": "sock-1",
        "error": "Failed to negotiate camera stream: something went wrong",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_camera_autostarter_direct() -> None:
    http_client = AsyncMock(spec=httpx.AsyncClient)
    # 1. Concurrent check succeeds immediately
    resp_ok = MagicMock(status_code=200)
    http_client.post.return_value = resp_ok

    starter = CameraAutoStarter(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="test-owner",
        http_client=http_client,
        poll_interval_s=0.01,
        max_poll_attempts=2,
    )

    resp = await starter.ensure_camera_ready(
        camera_id="cam-ready",
        sdp_offer="v=0\r\n",
        headers={"Content-Type": "application/sdp"},
        webrtc_url="http://127.0.0.1:8000/owners/test-owner/cameras/cam-ready/live/webrtc",
    )
    assert resp.status_code == 200
