"""Real runtime face embedder for the matching pipeline.

The pipeline hands this adapter a *track* crop — typically a detector's ``person``
bounding box, not a tight face patch — so each crop still needs its own face
detected, aligned, and embedded (the same SCRFD + ArcFace ``buffalo_l`` pass the
enrollment encoder — :class:`InsightFaceEmbeddingService` — runs).
A crop with no detected face yields a zero vector rather than a garbage embedding
from whatever the crop happened to contain — it will never clear a match threshold,
so the track is safely (if silently) skipped for this frame. ``insightface`` / ``cv2``
load lazily (``[ml]`` extra); the forward pass runs on a worker thread.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import numpy as np

from specter.domain.vision import Crop, Embedding, Vector
from specter.infrastructure.ml._insightface import load_face_app

_EMBED_DIM = 512

log = logging.getLogger(__name__)


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
        self._app: Any | None = None

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]:
        if not crops:
            return []
        vectors = await asyncio.to_thread(self.embed_sync, [crop.image for crop in crops])
        return [Embedding(modality=self.modality, vector=vector) for vector in vectors]

    def _face_app(self) -> Any:
        if self._app is None:
            self._app = load_face_app(self._model_name, self._providers, self._det_size)
        return self._app

    def embed_sync(self, images: list[np.ndarray]) -> list[Vector]:
        """Synchronous batch embed — the seam ``BatchedEmbedder``'s ``MicroBatcher``
        calls directly (via ``asyncio.to_thread``) to coalesce crops from every
        concurrently-running stream into one call."""
        app = self._face_app()
        vectors: list[Vector] = []
        for image in images:
            faces = app.get(image)
            if not faces:
                log.debug("face_embedder: no face detected in a %sx%s crop", *image.shape[:2])
                vectors.append(np.zeros(_EMBED_DIM, dtype=np.float32))
                continue
            face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            vectors.append(np.asarray(face.normed_embedding, dtype=np.float32))
        return vectors
