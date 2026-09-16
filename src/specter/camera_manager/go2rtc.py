"""Registers camera streams in go2rtc, which restreams each camera to its readers."""

from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from specter.core.errors import ExternalServiceError
from specter.entities.cameras import Camera

STREAMS_PATH = "/api/streams"
REQUEST_TIMEOUT_SECONDS = 5.0


def build_camera_source_url(camera: Camera) -> str:
    """Returns the URL that go2rtc connects to for the camera, with any credentials it has."""
    if camera.credentials is None:
        return camera.source_url
    source_parts = urlsplit(camera.source_url)
    # Encoding keeps characters such as "@" or ":" in a password from breaking the URL.
    username = quote(camera.credentials.username, safe="")
    password = quote(camera.credentials.password, safe="")
    host_and_port = source_parts.netloc.rpartition("@")[2]
    return urlunsplit(source_parts._replace(netloc=f"{username}:{password}@{host_and_port}"))


class Go2rtcClient:
    """go2rtc's stream registry, which go2rtc keeps in memory only.

    Streams are added with PATCH, because PUT would also write the source URL, and the camera's
    password in it, into go2rtc's configuration file. Errors never carry the underlying httpx
    error, whose message names the request URL and with it the password.
    """

    def __init__(self, api_url: str) -> None:
        self._http_client = httpx.AsyncClient(base_url=api_url, timeout=REQUEST_TIMEOUT_SECONDS)

    async def close(self) -> None:
        """Closes the connections to go2rtc."""
        await self._http_client.aclose()

    async def list_stream_names(self) -> set[str]:
        """Returns the names of the registered streams.

        Raises:
            ExternalServiceError: go2rtc cannot be reached or refused the request.
        """
        try:
            response = await self._http_client.get(STREAMS_PATH)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ExternalServiceError(
                f"cannot list go2rtc streams: {type(error).__name__}"
            ) from None
        streams: dict[str, object] | None = response.json()
        return set(streams or {})

    async def register_stream(self, stream_name: str, source_url: str) -> None:
        """Adds the stream, or replaces the source of the stream with the same name.

        Raises:
            ExternalServiceError: go2rtc cannot be reached or refused the stream.
        """
        try:
            response = await self._http_client.patch(
                STREAMS_PATH, params={"name": stream_name, "src": source_url}
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ExternalServiceError(
                f"cannot register go2rtc stream {stream_name}: {type(error).__name__}"
            ) from None

    async def remove_stream(self, stream_name: str) -> None:
        """Removes the stream if it is registered.

        Raises:
            ExternalServiceError: go2rtc cannot be reached or still has the stream.
        """
        try:
            await self._http_client.delete(STREAMS_PATH, params={"src": stream_name})
        except httpx.HTTPError as error:
            raise ExternalServiceError(
                f"cannot remove go2rtc stream {stream_name}: {type(error).__name__}"
            ) from None
        # go2rtc removes the stream from memory but answers 400, because it cannot write the
        # change to its read-only configuration file, so only the stream list confirms removal.
        if stream_name in await self.list_stream_names():
            raise ExternalServiceError(f"go2rtc still has stream {stream_name} after removing it")
