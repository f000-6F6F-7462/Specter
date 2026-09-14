"""Embedder contract — holds for FakeEmbedder and FaceEmbedder alike."""

import numpy as np

from specter.application.ports import Embedder
from specter.domain.vision import Crop


def _crop(track_id: int) -> Crop:
    rng = np.random.default_rng(track_id)
    return Crop(
        stream_id="stream_ct",
        track_id=track_id,
        modality="face",
        image=rng.integers(0, 256, size=(112, 112, 3), dtype=np.uint8),
        quality=0.9,
    )


async def test_embed_returns_one_vector_per_crop(embedder: Embedder) -> None:
    out = await embedder.embed([_crop(1), _crop(2)])

    assert len(out) == 2
    for embedding in out:
        assert embedding.modality == embedder.modality == "face"
        assert embedding.vector.ndim == 1
        norm = float(np.linalg.norm(embedding.vector))
        # FakeEmbedder always returns a unit vector; the real FaceEmbedder runs face
        # detection first and returns an all-zero vector for a crop with no detected
        # face (these crops are random noise) rather than a meaningless unit vector.
        assert np.isclose(norm, 1.0, atol=1e-3) or np.isclose(norm, 0.0, atol=1e-6)


async def test_embed_of_nothing_is_empty(embedder: Embedder) -> None:
    assert await embedder.embed([]) == []
