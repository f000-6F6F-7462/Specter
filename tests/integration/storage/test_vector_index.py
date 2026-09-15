import os
import time
from collections.abc import AsyncIterator

import pytest

from specter.entities.targets import EmbeddingModality
from specter.storage.vector_index import StoredEmbedding, VectorIndex

pytestmark = pytest.mark.integration

TEST_QDRANT_URL_ENVIRONMENT_VARIABLE = "SPECTER_TEST_QDRANT_URL"
DEFAULT_TEST_QDRANT_URL = "http://127.0.0.1:6333"
VECTOR_SIZE = 4


@pytest.fixture
async def vector_index() -> AsyncIterator[VectorIndex]:
    qdrant_url = os.environ.get(TEST_QDRANT_URL_ENVIRONMENT_VARIABLE, DEFAULT_TEST_QDRANT_URL)
    connected_vector_index = VectorIndex.connect(qdrant_url)
    await connected_vector_index.ensure_collections({EmbeddingModality.FACE: VECTOR_SIZE})
    yield connected_vector_index
    await connected_vector_index.close()


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
        [1.0, 0.0, 0.0, 0.0],
        owner_id=owner_id,
        watchlist_ids=[f"{owner_id}_watchlist"],
        limit=5,
    )
    return [candidate.target_id for candidate in candidates]


async def test_search_returns_most_similar_target_first_when_embeddings_exist(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", [1.0, 0.0, 0.0, 0.0]))
    await vector_index.upsert_embedding(build_embedding(owner_id, "bob", [0.0, 1.0, 0.0, 0.0]))

    target_ids = await search_owner(vector_index, owner_id)

    assert target_ids == [f"{owner_id}_jane", f"{owner_id}_bob"]


async def test_disabled_target_is_not_returned_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", [1.0, 0.0, 0.0, 0.0]))

    await vector_index.set_target_enabled(f"{owner_id}_jane", is_enabled=False)

    assert await search_owner(vector_index, owner_id) == []


async def test_deleted_target_is_not_returned_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", [1.0, 0.0, 0.0, 0.0]))

    await vector_index.delete_target(f"{owner_id}_jane")

    assert await search_owner(vector_index, owner_id) == []


async def test_synchronization_removes_stale_points_and_fixes_flags_when_drifted(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", [1.0, 0.0, 0.0, 0.0]))
    await vector_index.upsert_embedding(
        build_embedding(owner_id, "bob", [0.9, 0.1, 0.0, 0.0], is_enabled=False)
    )

    report = await vector_index.synchronize({f"{owner_id}_bob_image": True})

    assert report.removed_point_count >= 1
    assert report.updated_point_count >= 1
    assert await search_owner(vector_index, owner_id) == [f"{owner_id}_bob"]


async def test_camera_without_watchlists_matches_nothing_when_searching(
    vector_index: VectorIndex, owner_id: str
) -> None:
    await vector_index.upsert_embedding(build_embedding(owner_id, "jane", [1.0, 0.0, 0.0, 0.0]))

    candidates = await vector_index.search(
        EmbeddingModality.FACE, [1.0, 0.0, 0.0, 0.0], owner_id=owner_id, watchlist_ids=[], limit=5
    )

    assert candidates == []
