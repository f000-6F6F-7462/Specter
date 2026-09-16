"""Live video of a running camera, relayed from go2rtc so that go2rtc itself stays on the device.

Four ways to watch are offered: MSE over a websocket for low delay in most browsers, HLS for
browsers without MSE such as Safari on iPhone, WebRTC for the lowest delay, and single JPEG frames.
"""

import asyncio
import logging
from contextlib import suppress
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, status
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from specter.api.authentication import BEARER_SCHEME_NAME, is_valid_api_token
from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.ownership import get_owner_camera
from specter.core.errors import ExternalServiceError, NotFoundError

logger = logging.getLogger(__name__)

LIVE_VIDEO_PATH = "/owners/{owner_id}/cameras/{camera_id}/live"
router = APIRouter(prefix=LIVE_VIDEO_PATH, tags=["live video"])
# Browsers cannot send headers with a websocket, but the application server relays it and can, so
# the websocket checks the token itself instead of through the HTTP dependency.
websocket_router = APIRouter(prefix=LIVE_VIDEO_PATH, tags=["live video"])

SDP_MEDIA_TYPE = "application/sdp"
# The files that go2rtc's HLS playlists refer to; nothing else is relayed.
HLS_FILE_NAMES = frozenset({"playlist.m3u8", "segment.ts", "init.mp4", "segment.m4s"})
POLICY_VIOLATION_CLOSE_CODE = 1008
GO2RTC_UNAVAILABLE_CLOSE_CODE = 1011


@router.get(
    "/frame.jpeg", response_class=Response, responses={200: {"content": {"image/jpeg": {}}}}
)
async def read_frame(owner_id: str, camera_id: str, services: ServicesDependency) -> Response:
    """Returns the camera's latest frame as a JPEG."""
    await require_running_camera(services, owner_id, camera_id)
    go2rtc_response = await request_go2rtc(
        services, "GET", "/api/frame.jpeg", params={"src": camera_id}
    )
    return Response(
        content=go2rtc_response.content,
        media_type=go2rtc_response.headers.get("content-type"),
    )


@router.get("/stream.m3u8", response_class=Response)
async def read_hls_playlist(
    owner_id: str, camera_id: str, services: ServicesDependency
) -> Response:
    """Returns the HLS playlist, whose relative links lead to the ``hls`` files below."""
    await require_running_camera(services, owner_id, camera_id)
    go2rtc_response = await request_go2rtc(
        services, "GET", "/api/stream.m3u8", params={"src": camera_id}
    )
    return Response(
        content=go2rtc_response.content,
        media_type=go2rtc_response.headers.get("content-type"),
    )


@router.get("/hls/{file_name}", response_class=StreamingResponse)
async def read_hls_file(
    owner_id: str,
    camera_id: str,
    file_name: str,
    request: Request,
    services: ServicesDependency,
) -> StreamingResponse:
    """Relays an HLS playlist or segment that the camera's playlist links to."""
    if file_name not in HLS_FILE_NAMES:
        raise NotFoundError(f"live video file {file_name} does not exist")
    await require_running_camera(services, owner_id, camera_id)
    go2rtc_request = services.go2rtc_http_client.build_request(
        "GET", f"/api/hls/{file_name}", params=dict(request.query_params)
    )
    try:
        go2rtc_response = await services.go2rtc_http_client.send(go2rtc_request, stream=True)
    except httpx.HTTPError:
        raise ExternalServiceError("go2rtc cannot be reached") from None
    if go2rtc_response.status_code != status.HTTP_200_OK:
        await go2rtc_response.aclose()
        raise NotFoundError(f"live video file {file_name} is no longer available")
    return StreamingResponse(
        go2rtc_response.aiter_bytes(),
        media_type=go2rtc_response.headers.get("content-type"),
        background=BackgroundTask(go2rtc_response.aclose),
    )


@router.post(
    "/webrtc",
    response_class=Response,
    responses={200: {"content": {SDP_MEDIA_TYPE: {}}}},
)
async def negotiate_webrtc(
    owner_id: str,
    camera_id: str,
    offer: Annotated[str, Body(media_type=SDP_MEDIA_TYPE)],
    services: ServicesDependency,
) -> Response:
    """Answers a WebRTC offer; the media then flows directly between go2rtc and the viewer.

    The viewer must reach go2rtc's WebRTC port, since only the negotiation passes through the API.
    """
    await require_running_camera(services, owner_id, camera_id)
    go2rtc_response = await request_go2rtc(
        services,
        "POST",
        "/api/webrtc",
        params={"src": camera_id},
        content=offer.encode(),
        headers={"content-type": SDP_MEDIA_TYPE},
    )
    return Response(content=go2rtc_response.content, media_type=SDP_MEDIA_TYPE)


@websocket_router.websocket("/mse")
async def relay_mse(websocket: WebSocket, owner_id: str, camera_id: str) -> None:
    """Relays go2rtc's MSE websocket, over which the viewer asks for and receives the stream."""
    services: ApiServices = websocket.app.state.services
    authorization = websocket.headers.get("authorization", "")
    scheme, _, presented_token = authorization.partition(" ")
    if scheme != BEARER_SCHEME_NAME or not is_valid_api_token(services.api_token, presented_token):
        await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
        return
    try:
        await require_running_camera(services, owner_id, camera_id)
    except (NotFoundError, HTTPException):
        await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
        return
    try:
        async with websockets.connect(
            build_go2rtc_websocket_url(services, camera_id)
        ) as go2rtc_websocket:
            await websocket.accept()
            await relay_until_either_closes(websocket, go2rtc_websocket)
    except (OSError, websockets.WebSocketException):
        logger.warning("cannot relay live video from go2rtc", extra={"camera_id": camera_id})
        with suppress(RuntimeError):
            await websocket.close(code=GO2RTC_UNAVAILABLE_CLOSE_CODE)


async def relay_until_either_closes(
    websocket: WebSocket, go2rtc_websocket: websockets.ClientConnection
) -> None:
    """Relays messages both ways until the viewer or go2rtc closes its side."""
    relay_tasks = {
        asyncio.create_task(relay_viewer_messages(websocket, go2rtc_websocket)),
        asyncio.create_task(relay_go2rtc_messages(go2rtc_websocket, websocket)),
    }
    try:
        await asyncio.wait(relay_tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for relay_task in relay_tasks:
            relay_task.cancel()
        await asyncio.gather(*relay_tasks, return_exceptions=True)


async def relay_viewer_messages(
    websocket: WebSocket, go2rtc_websocket: websockets.ClientConnection
) -> None:
    """Forwards the viewer's requests, such as which codecs it plays, to go2rtc."""
    with suppress(WebSocketDisconnect):
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await go2rtc_websocket.send(message["text"])
            elif message.get("bytes") is not None:
                await go2rtc_websocket.send(message["bytes"])


async def relay_go2rtc_messages(
    go2rtc_websocket: websockets.ClientConnection, websocket: WebSocket
) -> None:
    """Forwards go2rtc's stream description and video data to the viewer."""
    async for message in go2rtc_websocket:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
        else:
            await websocket.send_text(message)


async def require_running_camera(services: ApiServices, owner_id: str, camera_id: str) -> None:
    """Makes sure the owner's camera runs, since only running cameras are registered in go2rtc.

    Raises:
        NotFoundError: The owner has no such camera.
        HTTPException: The camera is not running.
    """
    camera = await get_owner_camera(services, owner_id, camera_id)
    if not camera.should_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"camera {camera_id} is not running"
        )


async def request_go2rtc(
    services: ApiServices,
    method: str,
    path: str,
    *,
    params: dict[str, str],
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Sends a request to go2rtc and returns its successful response.

    Raises:
        ExternalServiceError: go2rtc cannot be reached or did not answer successfully.
    """
    try:
        go2rtc_response = await services.go2rtc_http_client.request(
            method, path, params=params, content=content, headers=headers
        )
    except httpx.HTTPError:
        raise ExternalServiceError("go2rtc cannot be reached") from None
    if go2rtc_response.status_code != status.HTTP_200_OK:
        raise ExternalServiceError(
            f"go2rtc cannot serve live video now (status {go2rtc_response.status_code})"
        )
    return go2rtc_response


def build_go2rtc_websocket_url(services: ApiServices, camera_id: str) -> str:
    """Returns the URL of go2rtc's MSE websocket for the camera."""
    api_url_parts = urlsplit(str(services.go2rtc_http_client.base_url))
    websocket_scheme = "wss" if api_url_parts.scheme == "https" else "ws"
    return urlunsplit((websocket_scheme, api_url_parts.netloc, "/api/ws", f"src={camera_id}", ""))
