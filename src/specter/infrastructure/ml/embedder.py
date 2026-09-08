"""Deterministic runtime embedder fake.

Turns a track crop into a unit vector. With ``constant`` set every crop maps to the
same vector (handy for match assertions); otherwise the vector is derived from the crop
pixels, so identical crops embed identically.
"""

import hashlib
from collections.abc import Sequence

import numpy as np

from specter.domain.vision import Crop, Embedding, Vector


def vector_from_bytes(raw: bytes, *, dim: int = 512) -> Vector:
    seed = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    vec = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


class FakeEmbedder:
    def __init__(
        self, modality: str = "face", *, dim: int = 512, constant: Vector | None = None
    ) -> None:
        self.modality = modality
        self._dim = dim
        self._constant = None if constant is None else np.asarray(constant, dtype=np.float32)

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]:
        return [Embedding(modality=self.modality, vector=self._vector(crop)) for crop in crops]

    def _vector(self, crop: Crop) -> Vector:
        if self._constant is not None:
            return self._constant
        return vector_from_bytes(np.ascontiguousarray(crop.image).tobytes(), dim=self._dim)
