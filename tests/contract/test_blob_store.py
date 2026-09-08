"""BlobStore contract — holds for MemoryBlobStore and MinioBlobStore alike."""

import pytest

from specter.application.ports import BlobStore
from specter.core.errors import NotFoundError

_KEY = "blobs/o_ct/contract/x.jpg"


async def test_put_then_get_round_trips_bytes(blob_store: BlobStore) -> None:
    returned = await blob_store.put(_KEY, b"payload-bytes", "image/jpeg")
    assert returned == _KEY
    assert await blob_store.get(_KEY) == b"payload-bytes"


async def test_get_missing_raises_not_found(blob_store: BlobStore) -> None:
    with pytest.raises(NotFoundError):
        await blob_store.get("blobs/o_ct/does-not-exist.jpg")


async def test_presigned_url_references_the_key(blob_store: BlobStore) -> None:
    await blob_store.put(_KEY, b"x", "image/jpeg")
    url = await blob_store.presigned_url(_KEY, ttl_s=60)
    assert isinstance(url, str) and _KEY in url
