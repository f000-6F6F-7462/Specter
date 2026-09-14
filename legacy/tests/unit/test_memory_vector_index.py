import numpy as np
import pytest

from specter.domain.vision import Embedding
from specter.infrastructure.vectors.memory import InMemoryVectorIndex


def _emb(image_id: str, vec: list[float], **payload: object) -> Embedding:
    base = {"owner_id": "o_1", "watchlist_id": "wl_1", "target_id": "tgt_1", "enabled": True}
    return Embedding(
        modality="face",
        vector=np.array(vec, dtype=np.float32),
        target_id=str(base["target_id"]),
        image_id=image_id,
        payload={**base, **payload},
    )


async def test_search_ranks_by_cosine_and_respects_top_k() -> None:
    index = InMemoryVectorIndex()
    await index.upsert(
        [
            _emb("img_a", [1.0, 0.0, 0.0], target_id="tgt_a"),
            _emb("img_b", [0.9, 0.1, 0.0], target_id="tgt_b"),
            _emb("img_c", [0.0, 1.0, 0.0], target_id="tgt_c"),
        ]
    )
    hits = await index.search(
        "face", np.array([1.0, 0.0, 0.0]), owner_id="o_1", watchlist_ids=["wl_1"], top_k=2
    )
    assert [h.target_id for h in hits] == ["tgt_a", "tgt_b"]
    assert hits[0].similarity == pytest.approx(1.0)


async def test_filters_owner_watchlist_and_enabled_and_modality() -> None:
    index = InMemoryVectorIndex()
    await index.upsert(
        [
            _emb("keep", [1.0, 0.0]),
            _emb("wrong_owner", [1.0, 0.0], owner_id="o_2"),
            _emb("wrong_wl", [1.0, 0.0], watchlist_id="wl_2"),
            _emb("disabled", [1.0, 0.0], enabled=False),
        ]
    )
    other = Embedding(
        modality="vehicle",
        vector=np.array([1.0, 0.0], dtype=np.float32),
        target_id="tgt_1",
        image_id="wrong_modality",
        payload={"owner_id": "o_1", "watchlist_id": "wl_1", "target_id": "tgt_1", "enabled": True},
    )
    await index.upsert([other])

    hits = await index.search("face", np.array([1.0, 0.0]), owner_id="o_1", watchlist_ids=["wl_1"])
    assert {h.target_id for h in hits} == {"tgt_1"}
    assert len(hits) == 1


async def test_upsert_replaces_by_image_id_and_delete_by_target() -> None:
    index = InMemoryVectorIndex()
    await index.upsert([_emb("img_a", [1.0, 0.0])])
    await index.upsert([_emb("img_a", [0.0, 1.0])])  # replace
    hits = await index.search("face", np.array([0.0, 1.0]), owner_id="o_1", watchlist_ids=[])
    assert hits[0].similarity == pytest.approx(1.0)

    await index.delete(target_id="tgt_1")
    assert await index.search("face", np.array([0.0, 1.0]), owner_id="o_1", watchlist_ids=[]) == []


async def test_upsert_requires_image_id() -> None:
    index = InMemoryVectorIndex()
    with pytest.raises(ValueError, match="image_id"):
        await index.upsert([Embedding(modality="face", vector=np.array([1.0]))])
