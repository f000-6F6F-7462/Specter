"""Deterministic ML fakes for tests and for running without the ``[ml]`` extra.

``FakeFaceEmbeddingService`` maps image bytes to a stable unit vector (same bytes ->
same vector), and can be told to return a rejection instead.
"""

import hashlib

import numpy as np

from specter.application.ports import ReferenceEmbedding
from specter.domain.quality import QualityReport, RejectionReason

_GOOD_QUALITY = QualityReport(score=0.92, blur=0.10, face_px=160, yaw_deg=8.0, brightness=0.55)
_BAD_QUALITY = QualityReport(score=0.20, blur=0.90, face_px=22, yaw_deg=60.0, brightness=0.35)


class FakeFaceEmbeddingService:
    model_version = "fake-arcface@1"

    def __init__(
        self,
        *,
        dim: int = 512,
        faces_found: int = 1,
        rejection: RejectionReason | None = None,
    ) -> None:
        self._dim = dim
        self._faces_found = faces_found
        self._rejection = rejection

    async def embed_reference(self, image: bytes) -> ReferenceEmbedding:
        if self._faces_found == 0:
            return ReferenceEmbedding(rejection=RejectionReason.NO_DETECTION, faces_found=0)
        if self._faces_found > 1:
            return ReferenceEmbedding(
                rejection=RejectionReason.MULTIPLE_FACES, faces_found=self._faces_found
            )
        if self._rejection is not None:
            return ReferenceEmbedding(
                rejection=self._rejection, quality=_BAD_QUALITY, faces_found=1
            )
        return ReferenceEmbedding(vector=self._vector(image), quality=_GOOD_QUALITY, faces_found=1)

    def _vector(self, image: bytes) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(image).digest()[:8], "big")
        v = np.random.default_rng(seed).standard_normal(self._dim).astype(np.float32)
        return v / np.linalg.norm(v)
