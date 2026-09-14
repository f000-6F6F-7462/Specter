"""Real runtime face embedder for the matching pipeline.

The pipeline hands this adapter an already-cropped face patch (from the tracker) and
wants a normalised ArcFace vector — no detection step. It drives the ``recognition``
sub-model of an InsightFace ``buffalo_l`` pack directly. ``insightface`` / ``cv2`` load
lazily (``[ml]`` extra); the forward pass runs on a worker thread.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import numpy as np

from specter.domain.vision import Crop, Embedding, Vector
from specter.infrastructure.ml._insightface import load_face_app

_ARCFACE_INPUT = (112, 112)


class FaceEmbedder:
    modality = "face"
    model_version = "insightface-buffalo_l@1"

    def __init__(
        self,
        *,
        model_name: str = "buffalo_l",
        providers: Sequence[str] | None = None,
        det_size: tuple[int, int] = (640, 640),
    ) -> None:
        self._model_name = model_name
        self._providers = providers or ["CPUExecutionProvider"]
        self._det_size = det_size
        self._recognition: Any | None = None

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]:
        if not crops:
            return []
        vectors = await asyncio.to_thread(self._embed_sync, [crop.image for crop in crops])
        return [Embedding(modality=self.modality, vector=vector) for vector in vectors]

    def _model(self) -> Any:
        if self._recognition is None:
            app = load_face_app(self._model_name, self._providers, self._det_size)
            self._recognition = app.models["recognition"]
        return self._recognition

    def _embed_sync(self, images: list[np.ndarray]) -> list[Vector]:
        import cv2  # pylint: disable=import-outside-toplevel

        model = self._model()
        vectors: list[Vector] = []
        for image in images:
            patch = cv2.resize(image, _ARCFACE_INPUT)
            feat = np.asarray(model.get_feat(patch), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(feat)) or 1.0
            vectors.append(feat / norm)
        return vectors
