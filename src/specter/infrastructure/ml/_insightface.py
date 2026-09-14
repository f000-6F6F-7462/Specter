"""Shared InsightFace loader.

Both the enrollment encoder (:class:`InsightFaceEmbeddingService`) and the pipeline
:class:`FaceEmbedder` need a prepared ``buffalo_l`` model pack. ``insightface`` is
imported lazily so the ``[ml]`` extra stays optional.
"""

from collections.abc import Sequence
from typing import Any


def load_face_app(model_name: str, providers: Sequence[str], det_size: tuple[int, int]) -> Any:
    """Return a prepared ``FaceAnalysis`` app (SCRFD detect + ArcFace embed)."""
    from insightface.app import FaceAnalysis  # pylint: disable=import-outside-toplevel

    app = FaceAnalysis(name=model_name, providers=list(providers))
    app.prepare(ctx_id=0, det_size=det_size)
    return app
