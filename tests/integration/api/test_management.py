import asyncio
import json
import time

import nats
import pytest
from fastapi.testclient import TestClient

from specter.config.settings import Settings
from specter.entities.targets import EmbeddingModality
from specter.messaging.messages import ConfigurationChangedMessage
from specter.messaging.streams import CONFIGURATION_STREAM
from specter.messaging.subjects import OwnerEvent, build_owner_subject
from specter.storage.vector_index import StoredEmbedding, VectorIndex

pytestmark = pytest.mark.integration

JPEG_BYTES = b"\xff\xd8\xff\xe0 reference photo"
EMBEDDING_SIZE = 512


async def read_last_configuration_change(
    nats_server_url: str, owner_id: str
) -> ConfigurationChangedMessage:
    connection = await nats.connect(nats_server_url)
    try:
        raw_message = await connection.jetstream().get_last_msg(
            CONFIGURATION_STREAM.name,
            build_owner_subject(owner_id, OwnerEvent.CONFIGURATION_CHANGED),
        )
    finally:
        await connection.close()
    assert raw_message.data is not None
    return ConfigurationChangedMessage.model_validate_json(raw_message.data)


async def store_embedding_and_count(
    settings: Settings, owner_id: str, watchlist_id: str, target_id: str, image_id: str
) -> int:
    vector_index = VectorIndex.connect(settings.services.qdrant_url)
    try:
        await vector_index.ensure_collections({EmbeddingModality.FACE: EMBEDDING_SIZE})
        await vector_index.upsert_embedding(
            StoredEmbedding(
                owner_id=owner_id,
                watchlist_id=watchlist_id,
                target_id=target_id,
                reference_image_id=image_id,
                modality=EmbeddingModality.FACE,
                vector=[1.0] + [0.0] * (EMBEDDING_SIZE - 1),
                model_version="test-model",
            )
        )
        return await count_owner_embeddings(settings, owner_id, watchlist_id)
    finally:
        await vector_index.close()


async def count_owner_embeddings(settings: Settings, owner_id: str, watchlist_id: str) -> int:
    vector_index = VectorIndex.connect(settings.services.qdrant_url)
    try:
        candidates = await vector_index.search(
            EmbeddingModality.FACE,
            [1.0] + [0.0] * (EMBEDDING_SIZE - 1),
            owner_id=owner_id,
            watchlist_ids=[watchlist_id],
            limit=10,
        )
    finally:
        await vector_index.close()
    return len(candidates)


def create_target_with_image(
    client: TestClient, owner_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    watchlist = client.post(
        f"/owners/{owner_id}/watchlists", json={"name": "Wanted", "target_type": "person"}
    ).json()
    target = client.post(
        f"/owners/{owner_id}/watchlists/{watchlist['id']}/targets",
        data={"targets": json.dumps([{"label": "Jane", "image_file_names": ["jane.jpg"]}])},
        files=[("images", ("jane.jpg", JPEG_BYTES, "image/jpeg"))],
    ).json()[0]
    return watchlist, target


def test_deleted_target_leaves_no_embeddings_or_images_and_is_announced(
    connected_api_client: TestClient,
    connected_api_settings: Settings,
    nats_server_url: str,
    unique_owner_id: str,
) -> None:
    watchlist, target = create_target_with_image(connected_api_client, unique_owner_id)
    image_id = target["reference_images"][0]["id"]  # type: ignore[index]
    stored_count = asyncio.run(
        store_embedding_and_count(
            connected_api_settings,
            unique_owner_id,
            str(watchlist["id"]),
            str(target["id"]),
            image_id,
        )
    )

    response = connected_api_client.delete(
        f"/owners/{unique_owner_id}/watchlists/{watchlist['id']}/targets/{target['id']}"
    )

    assert response.status_code == 204
    assert stored_count == 1
    assert (
        asyncio.run(
            count_owner_embeddings(connected_api_settings, unique_owner_id, str(watchlist["id"]))
        )
        == 0
    )
    images_directory = connected_api_settings.paths.data_directory / "reference_images"
    assert list(images_directory.rglob("*.jpg")) == []
    change = asyncio.run(read_last_configuration_change(nats_server_url, unique_owner_id))
    assert (change.entity_id, change.change_kind) == (target["id"], "deleted")


def test_deleted_owner_leaves_nothing_behind(
    connected_api_client: TestClient,
    connected_api_settings: Settings,
    unique_owner_id: str,
) -> None:
    watchlist, target = create_target_with_image(connected_api_client, unique_owner_id)
    camera = connected_api_client.post(
        f"/owners/{unique_owner_id}/cameras",
        json={"name": "Gate", "source_url": "rtsp://10.0.0.9/stream"},
    ).json()
    asyncio.run(
        store_embedding_and_count(
            connected_api_settings,
            unique_owner_id,
            str(watchlist["id"]),
            str(target["id"]),
            f"image_owner_{time.time_ns()}",
        )
    )

    response = connected_api_client.delete(f"/owners/{unique_owner_id}")

    assert response.status_code == 204
    assert connected_api_client.get(f"/owners/{unique_owner_id}/cameras").json() == []
    assert connected_api_client.get(f"/owners/{unique_owner_id}/watchlists").json() == []
    assert (
        connected_api_client.get(f"/owners/{unique_owner_id}/cameras/{camera['id']}").status_code
        == 404
    )
    assert (
        asyncio.run(
            count_owner_embeddings(connected_api_settings, unique_owner_id, str(watchlist["id"]))
        )
        == 0
    )
    assert not (
        connected_api_settings.paths.data_directory / "reference_images" / unique_owner_id
    ).exists()
