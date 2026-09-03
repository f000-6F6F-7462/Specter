import pytest

from specter.core.errors import NotFoundError
from specter.infrastructure.blob.memory import MemoryBlobStore


async def test_put_get_url_roundtrip() -> None:
    store = MemoryBlobStore()
    key = await store.put("blobs/o/x.jpg", b"payload", "image/jpeg")
    assert key == "blobs/o/x.jpg"
    assert await store.get(key) == b"payload"
    assert (await store.presigned_url(key, ttl_s=60)).startswith("memory://blobs/o/x.jpg")
    assert store.keys() == ["blobs/o/x.jpg"]


async def test_missing_key_raises() -> None:
    store = MemoryBlobStore()
    with pytest.raises(NotFoundError):
        await store.get("nope")
    with pytest.raises(NotFoundError):
        await store.presigned_url("nope")
