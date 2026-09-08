"""VectorIndex contract — holds for InMemoryVectorIndex and QdrantVectorIndex alike."""

import numpy as np

from specter.application.ports import VectorIndex
from specter.domain.vision import Embedding


def _emb(image_id: str, vec: list[float], *, watchlist_id: str = "wl_ct") -> Embedding:
    v = np.zeros(512, dtype=np.float32)
    v[: len(vec)] = vec
    return Embedding(
        modality="face",
        vector=v,
        target_id="tgt_ct",
        image_id=image_id,
        payload={
            "owner_id": "o_ct",
            "watchlist_id": watchlist_id,
            "target_id": "tgt_ct",
            "enabled": True,
        },
    )


def _query(vec: list[float]) -> np.ndarray:
    q = np.zeros(512, dtype=np.float32)
    q[: len(vec)] = vec
    return q


async def test_upsert_then_search_ranks_by_similarity(vector_index: VectorIndex) -> None:
    await vector_index.upsert([_emb("ct_a", [1.0, 0.0, 0.0]), _emb("ct_b", [0.2, 1.0, 0.0])])
    hits = await vector_index.search(
        "face", _query([1.0, 0.0, 0.0]), owner_id="o_ct", watchlist_ids=["wl_ct"], top_k=2
    )
    assert [h.target_id for h in hits] == ["tgt_ct", "tgt_ct"]
    assert hits[0].similarity > hits[1].similarity


async def test_search_filters_by_owner_and_watchlist(vector_index: VectorIndex) -> None:
    await vector_index.upsert(
        [_emb("ct_keep", [1.0, 0.0]), _emb("ct_other_wl", [1.0, 0.0], watchlist_id="wl_other")]
    )
    hits = await vector_index.search(
        "face", _query([1.0, 0.0]), owner_id="o_ct", watchlist_ids=["wl_ct"]
    )
    assert len(hits) == 1

    none = await vector_index.search(
        "face", _query([1.0, 0.0]), owner_id="o_someone_else", watchlist_ids=["wl_ct"]
    )
    assert none == []


async def test_delete_by_target_removes_every_point(vector_index: VectorIndex) -> None:
    await vector_index.upsert([_emb("ct_a", [1.0, 0.0]), _emb("ct_b", [0.0, 1.0])])
    await vector_index.delete(target_id="tgt_ct")
    hits = await vector_index.search(
        "face", _query([1.0, 0.0]), owner_id="o_ct", watchlist_ids=["wl_ct"]
    )
    assert hits == []
