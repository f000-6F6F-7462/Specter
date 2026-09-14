"""Real face encoder: InsightFace SCRFD (detect) + ArcFace ``buffalo_l`` (embed).

Requires the ``[ml]`` extra. ``insightface`` / ``cv2`` are imported lazily so the rest
of the codebase runs without them. The model pack loads on first use.
"""

import asyncio
from typing import Any

import numpy as np

from specter.application.ports import ReferenceEmbedding
from specter.domain.quality import (
    ENROLLMENT_THRESHOLDS,
    QualityReport,
    RejectionReason,
    assess,
)
from specter.infrastructure.ml._insightface import load_face_app

_SHARP_VAR = 500.0  # Laplacian variance at/above which an image counts as sharp


class InsightFaceEmbeddingService:
    model_version = "insightface-buffalo_l@1"

    def __init__(
        self,
        *,
        model_name: str = "buffalo_l",
        providers: list[str] | None = None,
        det_size: tuple[int, int] = (640, 640),
    ) -> None:
        self._model_name = model_name
        self._providers = providers or ["CPUExecutionProvider"]
        self._det_size = det_size
        self._app: Any | None = None

    async def embed_reference(self, image: bytes) -> ReferenceEmbedding:
        return await asyncio.to_thread(self._embed_sync, image)

    def _face_app(self) -> Any:
        if self._app is None:
            self._app = load_face_app(self._model_name, self._providers, self._det_size)
        return self._app

    def _embed_sync(self, image: bytes) -> ReferenceEmbedding:
        import cv2  # pylint: disable=import-outside-toplevel

        frame = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return ReferenceEmbedding(rejection=RejectionReason.NO_DETECTION)
        faces = self._face_app().get(frame)
        if not faces:
            return ReferenceEmbedding(rejection=RejectionReason.NO_DETECTION, faces_found=0)
        if len(faces) > 1:
            return ReferenceEmbedding(
                rejection=RejectionReason.MULTIPLE_FACES, faces_found=len(faces)
            )
        face = faces[0]
        quality = _measure_quality(frame, face)
        verdict = assess(quality, ENROLLMENT_THRESHOLDS)
        if not verdict.passed:
            return ReferenceEmbedding(quality=quality, rejection=verdict.reasons[0], faces_found=1)
        vector = np.asarray(face.normed_embedding, dtype=np.float32)
        return ReferenceEmbedding(vector=vector, quality=quality, faces_found=1)


def _measure_quality(frame: Any, face: Any) -> QualityReport:
    import cv2  # pylint: disable=import-outside-toplevel

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in face.bbox[:4])
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, w), min(y2, h)
    crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else frame
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blur = max(0.0, 1.0 - min(laplacian_var / _SHARP_VAR, 1.0))
    pose = getattr(face, "pose", None)
    return QualityReport(
        score=float(getattr(face, "det_score", 0.9)),
        blur=blur,
        face_px=max(y2 - y1, 1),
        yaw_deg=float(abs(pose[1])) if pose is not None else 0.0,
        brightness=float(gray.mean()) / 255.0,
    )
