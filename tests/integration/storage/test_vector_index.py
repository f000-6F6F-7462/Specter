import time

import pytest

from specter.entities.targets import EmbeddingModality
from specter.storage.vector_index import StoredEmbedding, VectorIndex

pytestmark = pytest.mark.integration

EMBEDDING_SIZE = 512


def axis_vector(axis_index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_SIZE
    vector[axis_index] = 1.0
    return vector


@pytest.fixture
def owner_id() -> str:
    # Qdrant is shared between test runs, so each test works under its own owner.
    return f"owner_test_{time.time_ns()}"


def build_embedding(
    owner_id: str, target_id: str, vector: list[float], *, is_enabled: bool = True
) -> StoredEmbedding:
    return StoredEmbedding(
        owner_id=owner_id,
        watchlist_id=f"{owner_id}_watchlist",
        target_id=f"{owner_id}_{target_id}",
        reference_image_id=f"{owner_id}_{target_id}_image",
        modality=EmbeddingModality.FACE,
        vector=vector,
        model_version="test-model",
        is_enabled=is_enabled,
    )


async def search_owner(vector_index: VectorIndex, owner_id: str) -> list[str]:
    candidates = await vector_index.search(
        EmbeddingModality.FACE,
        axis_vector(0),
        owner_id=owner_id,
        watchlist_ids=[f"{owner_id}_watchlist"],
        limit=5,
    )
    return [candidate.target_id for candidate in candidates]


async def test_search_returns_most_similar_target_first_when_embeddings_exist(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", axis_vector(0)))
    await vector_index.upsert_embedding(build_embedding(owner_id, "bob", axis_vector(1)))

    target_ids = await search_owner(vector_index, owner_id)

    assert target_ids == [f"{owner_id}_jane", f"{owner_id}_bob"]


async def test_disabled_target_is_not_returned_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", axis_vector(0)))

    await vector_index.set_target_enabled(f"{owner_id}_jane", is_enabled=False)

    assert await search_owner(vector_index, owner_id) == []


async def test_deleted_target_is_not_returned_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", axis_vector(0)))

    await vector_index.delete_target(f"{owner_id}_jane")

    assert await search_owner(vector_index, owner_id) == []


async def test_synchronization_removes_stale_points_and_fixes_flags_when_drifted(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", axis_vector(0)))
    await vector_index.upsert_embedding(
        build_embedding(owner_id, "bob", axis_vector(0), is_enabled=False)
    )

    report = await vector_index.synchronize(
        {(f"{owner_id}_bob_image", EmbeddingModality.FACE): True}
    )

    assert report.removed_point_count >= 1
    assert report.updated_point_count >= 1
    assert await search_owner(vector_index, owner_id) == [f"{owner_id}_bob"]


async def test_camera_without_watchlists_matches_nothing_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", axis_vector(0)))

    candidates = await vector_index.search(
        EmbeddingModality.FACE, axis_vector(0), owner_id=owner_id, watchlist_ids=[], limit=5
    )

    assert candidates == []


async def test_collection_is_replaced_when_its_vector_size_changes(
    vector_index: VectorIndex,
) -> None:
    replaced_modalities = await vector_index.ensure_collections(
        {EmbeddingModality.APPEARANCE: EMBEDDING_SIZE // 2}
    )
    restored_modalities = await vector_index.ensure_collections(
        {EmbeddingModality.APPEARANCE: EMBEDDING_SIZE}
    )
    unchanged_modalities = await vector_index.ensure_collections(
        {EmbeddingModality.APPEARANCE: EMBEDDING_SIZE}
    )

    assert replaced_modalities == [EmbeddingModality.APPEARANCE]
    assert restored_modalities == [EmbeddingModality.APPEARANCE]
    assert unchanged_modalities == []
