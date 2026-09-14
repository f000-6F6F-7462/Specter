"""In-memory BlobStore."""

from specter.core.errors import NotFoundError


class MemoryBlobStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self._objects[key] = (data, content_type)
        return key

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError:
            raise NotFoundError(f"blob not found: {key}") from None

    async def presigned_url(self, key: str, *, ttl_s: int = 900) -> str:
        if key not in self._objects:
            raise NotFoundError(f"blob not found: {key}")
        return f"memory://{key}?ttl={ttl_s}"

    def keys(self) -> list[str]:
        """Test helper."""
        return list(self._objects)
