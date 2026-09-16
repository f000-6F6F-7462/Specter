"""Reference-image embeddings in Qdrant, searched to identify tracks."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from specter.entities.targets import EmbeddingModality
from specter.vision.identity_matching import Candidate

COLLECTION_NAMES_BY_MODALITY: Mapping[EmbeddingModality, str] = {
    EmbeddingModality.FACE: "face_embeddings",
    EmbeddingModality.APPEARANCE: "appearance_embeddings",
}
KEYWORD_PAYLOAD_FIELDS = ("owner_id", "watchlist_id", "target_id", "reference_image_id")
IS_ENABLED_PAYLOAD_FIELD = "is_enabled"
SCROLL_PAGE_SIZE = 256
HTTP_NOT_FOUND = 404

# Deriving point ids from the reference image makes re-embedding an image replace its point.
POINT_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "embeddings.specter")

type PointId = int | str | uuid.UUID


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    """One reference image's embedding, with the ids it is filtered and synchronized by."""

    owner_id: str
    watchlist_id: str
    target_id: str
    reference_image_id: str
    modality: EmbeddingModality
    vector: Sequence[float]
    model_version: str
    is_enabled: bool = True


@dataclass(frozen=True, slots=True)
class SynchronizationReport:
    """How many points a synchronization with the database removed or corrected."""

    removed_point_count: int
    updated_point_count: int


def build_point_id(reference_image_id: str, modality: EmbeddingModality) -> str:
    """Returns the Qdrant point id of a reference image's embedding."""
    return str(uuid.uuid5(POINT_ID_NAMESPACE, f"{reference_image_id}:{modality}"))


class VectorIndex:
    """Stores and searches reference-image embeddings, one Qdrant collection per modality."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    @classmethod
    def connect(cls, qdrant_url: str) -> "VectorIndex":
        """Returns an index backed by the Qdrant server at the URL."""
        # The device pins Qdrant's version, and checking it would contact Qdrant before it is used.
        return cls(AsyncQdrantClient(url=qdrant_url, check_compatibility=False))

    async def close(self) -> None:
        """Closes the connection to Qdrant."""
        await self._client.close()

    async def ensure_collections(
        self, vector_sizes_by_modality: Mapping[EmbeddingModality, int]
    ) -> list[EmbeddingModality]:
        """Creates missing collections, with indexes on every field that searches filter by.

        A collection whose vectors have another size belongs to a model that is no longer used, so
        it is replaced. Returns the modalities whose collections were replaced.
        """
        replaced_modalities: list[EmbeddingModality] = []
        for modality, vector_size in vector_sizes_by_modality.items():
            collection_name = COLLECTION_NAMES_BY_MODALITY[modality]
            if await self._client.collection_exists(collection_name):
                if await self._read_vector_size(collection_name) == vector_size:
                    continue
                await self._client.delete_collection(collection_name)
                replaced_modalities.append(modality)
            await self._client.create_collection(
                collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size, distance=models.Distance.COSINE
                ),
            )
            for field_name in KEYWORD_PAYLOAD_FIELDS:
                await self._client.create_payload_index(
                    collection_name, field_name, field_schema=models.PayloadSchemaType.KEYWORD
                )
            await self._client.create_payload_index(
                collection_name,
                IS_ENABLED_PAYLOAD_FIELD,
                field_schema=models.PayloadSchemaType.BOOL,
            )
        return replaced_modalities

    async def upsert_embedding(self, embedding: StoredEmbedding) -> None:
        """Stores the embedding, replacing an earlier one of the same image and modality."""
        await self._client.upsert(
            COLLECTION_NAMES_BY_MODALITY[embedding.modality],
            points=[
                models.PointStruct(
                    id=build_point_id(embedding.reference_image_id, embedding.modality),
                    vector=list(embedding.vector),
                    payload={
                        "owner_id": embedding.owner_id,
                        "watchlist_id": embedding.watchlist_id,
                        "target_id": embedding.target_id,
                        "reference_image_id": embedding.reference_image_id,
                        "model_version": embedding.model_version,
                        IS_ENABLED_PAYLOAD_FIELD: embedding.is_enabled,
                    },
                )
            ],
            wait=True,
        )

    async def search(
        self,
        modality: EmbeddingModality,
        vector: Sequence[float],
        *,
        owner_id: str,
        watchlist_ids: Sequence[str],
        limit: int,
    ) -> list[Candidate]:
        """Returns the most similar enabled images of the owner's watchlists, most similar first.

        A camera without watchlists matches nothing, and so does a modality that nothing was
        enrolled for yet, whose collection does not exist.
        """
        if not watchlist_ids:
            return []
        try:
            response = await self._client.query_points(
                COLLECTION_NAMES_BY_MODALITY[modality],
                query=list(vector),
                query_filter=models.Filter(
                    must=[
                        _match_field("owner_id", owner_id),
                        _match_field(IS_ENABLED_PAYLOAD_FIELD, True),
                        models.FieldCondition(
                            key="watchlist_id", match=models.MatchAny(any=list(watchlist_ids))
                        ),
                    ]
                ),
                limit=limit,
                with_payload=True,
            )
        except UnexpectedResponse as error:
            if error.status_code == HTTP_NOT_FOUND:
                return []
            raise
        return [
            Candidate(
                target_id=str(point.payload["target_id"]),
                watchlist_id=str(point.payload["watchlist_id"]),
                similarity_ratio=float(point.score),
            )
            for point in response.points
            if point.payload is not None
        ]

    async def set_target_enabled(self, target_id: str, *, is_enabled: bool) -> None:
        """Includes or excludes every embedding of the target from searches."""
        for collection_name in await self._list_existing_collection_names():
            await self._client.set_payload(
                collection_name,
                payload={IS_ENABLED_PAYLOAD_FIELD: is_enabled},
                points=models.FilterSelector(filter=_filter_by_field("target_id", target_id)),
                wait=True,
            )

    async def delete_target(self, target_id: str) -> None:
        """Deletes every embedding of the target."""
        await self._delete_matching("target_id", target_id)

    async def delete_watchlist(self, watchlist_id: str) -> None:
        """Deletes every embedding of the watchlist's targets."""
        await self._delete_matching("watchlist_id", watchlist_id)

    async def delete_owner(self, owner_id: str) -> None:
        """Deletes every embedding of the owner."""
        await self._delete_matching("owner_id", owner_id)

    async def delete_reference_image(self, reference_image_id: str) -> None:
        """Deletes every embedding of the reference image."""
        await self._delete_matching("reference_image_id", reference_image_id)

    async def synchronize(
        self, is_enabled_by_embedding: Mapping[tuple[str, EmbeddingModality], bool]
    ) -> SynchronizationReport:
        """Removes points that are no longer embedded and corrects stale enabled flags.

        Args:
            is_enabled_by_embedding: Every embedded reference image and modality in the database,
                with whether its target is enabled.
        """
        removed_point_count = 0
        updated_point_count = 0
        for modality in await self._list_existing_modalities():
            collection_name = COLLECTION_NAMES_BY_MODALITY[modality]
            stale_point_ids, point_ids_by_expected_state = await self._find_drifted_points(
                collection_name, modality, is_enabled_by_embedding
            )
            if stale_point_ids:
                await self._client.delete(
                    collection_name,
                    points_selector=models.PointIdsList(points=stale_point_ids),
                    wait=True,
                )
                removed_point_count += len(stale_point_ids)
            for is_enabled, point_ids in point_ids_by_expected_state.items():
                await self._client.set_payload(
                    collection_name,
                    payload={IS_ENABLED_PAYLOAD_FIELD: is_enabled},
                    points=point_ids,
                    wait=True,
                )
                updated_point_count += len(point_ids)
        return SynchronizationReport(
            removed_point_count=removed_point_count, updated_point_count=updated_point_count
        )

    async def _find_drifted_points(
        self,
        collection_name: str,
        modality: EmbeddingModality,
        is_enabled_by_embedding: Mapping[tuple[str, EmbeddingModality], bool],
    ) -> tuple[list[PointId], dict[bool, list[PointId]]]:
        stale_point_ids: list[PointId] = []
        point_ids_by_expected_state: dict[bool, list[PointId]] = {}
        next_offset: PointId | None = None
        while True:
            records, next_offset = await self._client.scroll(
                collection_name,
                limit=SCROLL_PAGE_SIZE,
                offset=next_offset,
                with_payload=["reference_image_id", IS_ENABLED_PAYLOAD_FIELD],
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                expected_state = is_enabled_by_embedding.get(
                    (str(payload.get("reference_image_id")), modality)
                )
                if expected_state is None:
                    stale_point_ids.append(record.id)
                elif payload.get(IS_ENABLED_PAYLOAD_FIELD) != expected_state:
                    point_ids_by_expected_state.setdefault(expected_state, []).append(record.id)
            if next_offset is None:
                return stale_point_ids, point_ids_by_expected_state

    async def _delete_matching(self, field_name: str, value: str) -> None:
        for collection_name in await self._list_existing_collection_names():
            await self._client.delete(
                collection_name,
                points_selector=models.FilterSelector(filter=_filter_by_field(field_name, value)),
                wait=True,
            )

    async def _list_existing_collection_names(self) -> list[str]:
        return [
            COLLECTION_NAMES_BY_MODALITY[modality]
            for modality in await self._list_existing_modalities()
        ]

    async def _list_existing_modalities(self) -> list[EmbeddingModality]:
        # Deletions can arrive before the detector ever created a collection.
        return [
            modality
            for modality, collection_name in COLLECTION_NAMES_BY_MODALITY.items()
            if await self._client.collection_exists(collection_name)
        ]

    async def _read_vector_size(self, collection_name: str) -> int | None:
        vectors = (await self._client.get_collection(collection_name)).config.params.vectors
        return vectors.size if isinstance(vectors, models.VectorParams) else None


def _match_field(field_name: str, value: str | bool) -> models.FieldCondition:
    return models.FieldCondition(key=field_name, match=models.MatchValue(value=value))


def _filter_by_field(field_name: str, value: str) -> models.Filter:
    return models.Filter(must=[_match_field(field_name, value)])
