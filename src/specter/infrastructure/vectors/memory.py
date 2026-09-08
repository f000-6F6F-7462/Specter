"""In-process VectorIndex — numpy cosine over the loaded points.

Source of truth in tests, and the fast path for small watchlists at runtime. Keyed by
``image_id``; ``upsert`` replaces.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from specter.domain.matching import Candidate
from specter.domain.vision import Embedding, Vector


def _unit(vector: Vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(v))
    return v / norm if norm else v


@dataclass(slots=True)
class _Point:
    modality: str
    vector: np.ndarray
    payload: dict[str, object] = field(default_factory=dict)


class InMemoryVectorIndex:
    def __init__(self) -> None:
        self._points: dict[str, _Point] = {}

    async def upsert(self, points: Sequence[Embedding]) -> None:
        for embedding in points:
            if embedding.image_id is None:
                raise ValueError("embedding.image_id is required for upsert")
            self._points[embedding.image_id] = _Point(
                modality=embedding.modality,
                vector=_unit(embedding.vector),
                payload=dict(embedding.payload or {}),
            )

    async def search(
        self,
        modality: str,
        query: Vector,
        *,
        owner_id: str,
        watchlist_ids: Sequence[str],
        top_k: int = 5,
    ) -> list[Candidate]:
        q = _unit(query)
        wanted = set(watchlist_ids)
        scored: list[tuple[float, dict[str, object]]] = []
        for point in self._points.values():
            payload = point.payload
            if point.modality != modality:
                continue
            if payload.get("owner_id") != owner_id:
                continue
            if wanted and payload.get("watchlist_id") not in wanted:
                continue
            if payload.get("enabled") is False:
                continue
            scored.append((float(q @ point.vector), payload))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            Candidate(target_id=str(payload["target_id"]), similarity=score)
            for score, payload in scored[:top_k]
        ]

    async def delete(self, *, target_id: str) -> None:
        self._points = {
            image_id: point
            for image_id, point in self._points.items()
            if point.payload.get("target_id") != target_id
        }
