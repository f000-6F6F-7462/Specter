"""VectorIndex over Qdrant.

One collection per modality (``faces`` / ``person_reid`` / ``vehicle_reid``), 512-d
cosine. Point id is a deterministic UUID from ``image_id`` so re-embedding replaces.
Every search is filtered by ``owner_id`` + ``enabled`` and, when given, ``watchlist_id``.
"""

import uuid
from collections.abc import Sequence

import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from specter.domain.matching import Candidate
from specter.domain.vision import Embedding, Vector

_COLLECTION = {"face": "faces", "person": "person_reid", "vehicle": "vehicle_reid"}
_DIM = 512


def _point_id(image_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, image_id))


def _unit(vector: Vector) -> list[float]:
    v = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(v))
    return (v / norm if norm else v).tolist()


class QdrantVectorIndex:
    def __init__(self, url: str) -> None:
        self._client = AsyncQdrantClient(url=url, check_compatibility=False)

    @staticmethod
    def _collection(modality: str) -> str:
        try:
            return _COLLECTION[modality]
        except KeyError:
            raise ValueError(f"unknown modality: {modality!r}") from None

    async def ensure_collections(self) -> None:
        existing = {c.name for c in (await self._client.get_collections()).collections}
        for name in _COLLECTION.values():
            if name not in existing:
                await self._client.create_collection(
                    name,
                    vectors_config=qm.VectorParams(size=_DIM, distance=qm.Distance.COSINE),
                )
            for field in ("owner_id", "watchlist_id"):
                await self._client.create_payload_index(
                    name, field, field_schema=qm.PayloadSchemaType.KEYWORD
                )

    async def upsert(self, points: Sequence[Embedding]) -> None:
        batches: dict[str, list[qm.PointStruct]] = {}
        for embedding in points:
            if embedding.image_id is None:
                raise ValueError("embedding.image_id is required for upsert")
            collection = self._collection(embedding.modality)
            batches.setdefault(collection, []).append(
                qm.PointStruct(
                    id=_point_id(embedding.image_id),
                    vector=_unit(embedding.vector),
                    payload=dict(embedding.payload or {}),
                )
            )
        for collection, structs in batches.items():
            await self._client.upsert(collection, points=structs, wait=True)

    async def search(
        self,
        modality: str,
        query: Vector,
        *,
        owner_id: str,
        watchlist_ids: Sequence[str],
        top_k: int = 5,
    ) -> list[Candidate]:
        must: list[qm.FieldCondition] = [
            qm.FieldCondition(key="owner_id", match=qm.MatchValue(value=owner_id)),
            qm.FieldCondition(key="enabled", match=qm.MatchValue(value=True)),
        ]
        if watchlist_ids:
            must.append(
                qm.FieldCondition(key="watchlist_id", match=qm.MatchAny(any=list(watchlist_ids)))
            )
        response = await self._client.query_points(
            self._collection(modality),
            query=_unit(query),
            query_filter=qm.Filter(must=must),
            limit=top_k,
            with_payload=True,
        )
        return [
            Candidate(target_id=str(point.payload["target_id"]), similarity=float(point.score))
            for point in response.points
            if point.payload
        ]

    async def delete(self, *, target_id: str) -> None:
        selector = qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="target_id", match=qm.MatchValue(value=target_id))]
            )
        )
        for collection in _COLLECTION.values():
            await self._client.delete(collection, points_selector=selector, wait=True)

    async def aclose(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> "QdrantVectorIndex":
        await self.ensure_collections()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None
