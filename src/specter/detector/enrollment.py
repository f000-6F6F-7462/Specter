"""Enrolls reference images: embeds each kind of embedding once and stores it for matching."""

import asyncio
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import cv2
import numpy as np
from nats.errors import Error as NatsError

from specter.entities.geometry import BoundingBox
from specter.entities.targets import (
    EmbeddingModality,
    ImageStatus,
    QualityReport,
    RejectionReason,
)
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import Embedding, InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.face_embedder import FaceEmbedder
from specter.inference.face_quality import measure_face_quality
from specter.inference.object_detector import ObjectDetector
from specter.messaging.client import JobDelivery, MessageBus
from specter.messaging.messages import EnrollmentJobMessage, EnrollmentStatusChangedMessage
from specter.storage.database import DatabaseThread
from specter.storage.targets import (
    ImageEnrollment,
    find_image_enrollment,
    list_embedded_image_enabled_states,
    list_pending_image_enrollments,
    reset_outdated_image_embeddings,
    save_image_embedding,
)
from specter.storage.vector_index import StoredEmbedding, VectorIndex
from specter.vision.frames import FrameImage
from specter.vision.quality import ENROLLMENT_QUALITY_THRESHOLDS, assess_quality, score_quality

logger = logging.getLogger(__name__)

PERSON_OBJECT_CLASS = "person"
# A reference photo is posed, so a person the detector is unsure of is background, not the target.
MINIMUM_PERSON_CONFIDENCE_RATIO = 0.5
ENROLLMENT_JOB_ID_PREFIX = "enrollment"


@dataclass(frozen=True, slots=True)
class EnrollmentOutcome:
    """The embedding of one kind taken from a reference image, or why there is none."""

    embedding: Embedding | None = None
    quality: QualityReport | None = None
    rejection_reason: RejectionReason | None = None


@dataclass(frozen=True, slots=True)
class EnrollmentModels:
    """The model sessions and wrappers that enrollment runs."""

    object_detection_session: InferenceSession
    object_detector: ObjectDetector
    face_detection_session: InferenceSession
    face_detector: FaceDetector
    face_recognition_session: InferenceSession
    appearance_session: InferenceSession
    appearance_embedder: AppearanceEmbedder


def build_enrollment_job_id(
    reference_image_id: str, modality: EmbeddingModality, model_version: str
) -> str:
    """Returns the message id of an enrollment job, the same for every job of the same enrollment.

    JetStream stores a message id once within its duplicate window, so a detector that restarts
    quickly does not queue the same enrollment twice. The model version is part of the id, because
    enrolling again for a new model is a different job that must not be dropped as a duplicate.
    """
    return f"{ENROLLMENT_JOB_ID_PREFIX}_{reference_image_id}_{modality}_{model_version}"


def embed_face(models: EnrollmentModels, image: FrameImage) -> EnrollmentOutcome:
    """Embeds the single face of a reference image, if it passes the enrollment quality gate."""
    model_input, scale = models.face_detector.prepare(image)
    faces = models.face_detector.decode(models.face_detection_session.run(model_input)[0], scale)
    if not faces:
        return EnrollmentOutcome(rejection_reason=RejectionReason.NO_FACE_DETECTED)
    # Several faces leave it unclear who the target is, and enrolling the wrong one raises alerts
    # about someone else.
    if len(faces) > 1:
        return EnrollmentOutcome(rejection_reason=RejectionReason.MULTIPLE_FACES)
    try:
        aligned_face = FaceEmbedder.align(image, faces[0])
    except ValueError:
        return EnrollmentOutcome(rejection_reason=RejectionReason.EXTREME_POSE)
    quality = measure_face_quality(faces[0], aligned_face)
    verdict = assess_quality(quality, ENROLLMENT_QUALITY_THRESHOLDS)
    if not verdict.has_passed:
        return EnrollmentOutcome(quality=quality, rejection_reason=verdict.rejection_reasons[0])
    outputs = models.face_recognition_session.run(FaceEmbedder.build_input_tensor([aligned_face]))
    return EnrollmentOutcome(embedding=FaceEmbedder.read_embedding(outputs[0]), quality=quality)


def embed_appearance(models: EnrollmentModels, image: FrameImage) -> EnrollmentOutcome:
    """Embeds the appearance of the single person in a reference image."""
    letterboxed_image, transform = models.object_detector.letterbox(image)
    outputs = models.object_detection_session.run(
        models.object_detector.build_input_tensor([letterboxed_image])
    )
    people = [
        detection
        for detection in models.object_detector.decode(outputs[0], transform)
        if detection.object_class == PERSON_OBJECT_CLASS
        and detection.confidence_ratio >= MINIMUM_PERSON_CONFIDENCE_RATIO
    ]
    if not people:
        return EnrollmentOutcome(rejection_reason=RejectionReason.NO_PERSON_DETECTED)
    if len(people) > 1:
        return EnrollmentOutcome(rejection_reason=RejectionReason.MULTIPLE_PEOPLE)
    box: BoundingBox = people[0].bounding_box
    crop = image[box.y : box.bottom, box.x : box.right]
    embedding_outputs = models.appearance_session.run(
        models.appearance_embedder.build_input_tensor([crop])
    )
    return EnrollmentOutcome(embedding=AppearanceEmbedder.read_embedding(embedding_outputs[0]))


def read_reference_image(data_directory: Path, image_path: str) -> FrameImage | None:
    """Returns the decoded image, or None if it is missing, undecodable or outside the directory."""
    image_file = (data_directory / image_path).resolve()
    if not image_file.is_relative_to(data_directory.resolve()) or not image_file.is_file():
        return None
    image = cv2.imread(str(image_file), cv2.IMREAD_COLOR)
    return None if image is None else np.asarray(image, dtype=np.uint8)


class EnrollmentService:
    """Keeps the vector index in step with the reference images in the database.

    At startup it replaces collections whose model changed, marks embeddings of an older model for
    enrollment again, removes stale points and queues every pending embedding. Each job then
    embeds one kind of embedding of one image on the detector's model thread, stores it and
    publishes the outcome.
    """

    def __init__(
        self,
        *,
        models: EnrollmentModels,
        model_executor: ThreadPoolExecutor,
        data_directory: Path,
        database_thread: DatabaseThread,
        vector_index: VectorIndex,
        message_bus: MessageBus,
        model_versions_by_modality: Mapping[EmbeddingModality, str],
        embedding_sizes_by_modality: Mapping[EmbeddingModality, int],
    ) -> None:
        self._models = models
        self._model_executor = model_executor
        self._data_directory = data_directory
        self._database_thread = database_thread
        self._vector_index = vector_index
        self._message_bus = message_bus
        self._model_versions_by_modality = model_versions_by_modality
        self._embedding_sizes_by_modality = embedding_sizes_by_modality

    async def prepare(self) -> None:
        """Brings the vector index up to date with the database and queues pending enrollments."""
        replaced_modalities = await self._vector_index.ensure_collections(
            self._embedding_sizes_by_modality
        )
        reset_count = await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor,
            partial(reset_outdated_image_embeddings, self._model_versions_by_modality),
        )
        synchronization_report = await self._vector_index.synchronize(
            await asyncio.get_running_loop().run_in_executor(
                self._database_thread.executor, list_embedded_image_enabled_states
            )
        )
        pending_enrollments = await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor, list_pending_image_enrollments
        )
        for enrollment in pending_enrollments:
            await self._message_bus.publish(
                EnrollmentJobMessage(
                    message_id=build_enrollment_job_id(
                        enrollment.reference_image_id,
                        enrollment.embedding.modality,
                        self._model_versions_by_modality[enrollment.embedding.modality],
                    ),
                    occurred_at=datetime.now(UTC),
                    owner_id=enrollment.owner_id,
                    target_id=enrollment.target_id,
                    reference_image_id=enrollment.reference_image_id,
                    modality=enrollment.embedding.modality,
                )
            )
        logger.info(
            "enrollment prepared",
            extra={
                "replaced_collections": [str(modality) for modality in replaced_modalities],
                "outdated_embedding_count": reset_count,
                "removed_point_count": synchronization_report.removed_point_count,
                "updated_point_count": synchronization_report.updated_point_count,
                "queued_enrollment_count": len(pending_enrollments),
            },
        )

    async def handle_job(self, raw_job: bytes, delivery: JobDelivery) -> None:
        """Enrolls one kind of embedding of a reference image.

        A job whose embedding is gone or no longer pending was already handled and is skipped. When
        the last attempt fails, the embedding is recorded as failed so it does not wait forever.
        """
        job = EnrollmentJobMessage.model_validate_json(raw_job)
        enrollment = await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor,
            partial(find_image_enrollment, job.reference_image_id, job.modality),
        )
        if enrollment is None or enrollment.embedding.status is not ImageStatus.PENDING:
            return
        try:
            outcome = await asyncio.get_running_loop().run_in_executor(
                self._model_executor, self._embed, enrollment
            )
            await self._record(enrollment, outcome)
        except Exception:
            if delivery.is_last_attempt:
                await self._record(
                    enrollment,
                    EnrollmentOutcome(rejection_reason=RejectionReason.PROCESSING_FAILED),
                )
            raise

    def _embed(self, enrollment: ImageEnrollment) -> EnrollmentOutcome:
        image = read_reference_image(self._data_directory, enrollment.image_path)
        if image is None:
            return EnrollmentOutcome(rejection_reason=RejectionReason.UNREADABLE_IMAGE)
        match enrollment.embedding.modality:
            case EmbeddingModality.FACE:
                return embed_face(self._models, image)
            case EmbeddingModality.APPEARANCE:
                return embed_appearance(self._models, image)

    async def _record(self, enrollment: ImageEnrollment, outcome: EnrollmentOutcome) -> None:
        modality = enrollment.embedding.modality
        model_version = self._model_versions_by_modality[modality]
        if outcome.embedding is not None:
            await self._vector_index.upsert_embedding(
                StoredEmbedding(
                    owner_id=enrollment.owner_id,
                    watchlist_id=enrollment.watchlist_id,
                    target_id=enrollment.target_id,
                    reference_image_id=enrollment.reference_image_id,
                    modality=modality,
                    vector=outcome.embedding.tolist(),
                    model_version=model_version,
                    is_enabled=enrollment.is_target_enabled,
                )
            )
            updated_embedding = enrollment.embedding.mark_embedded(outcome.quality, model_version)
        else:
            updated_embedding = enrollment.embedding.mark_rejected(
                outcome.rejection_reason or RejectionReason.PROCESSING_FAILED, outcome.quality
            )
        is_saved = await asyncio.get_running_loop().run_in_executor(
            self._database_thread.executor,
            partial(save_image_embedding, enrollment.reference_image_id, updated_embedding),
        )
        if not is_saved:
            # The image was deleted while it was being embedded, so its point must not stay behind.
            await self._vector_index.delete_reference_image(enrollment.reference_image_id)
            return
        await self._publish_outcome(enrollment, updated_embedding.status, outcome)

    async def _publish_outcome(
        self, enrollment: ImageEnrollment, status: ImageStatus, outcome: EnrollmentOutcome
    ) -> None:
        message = EnrollmentStatusChangedMessage(
            occurred_at=datetime.now(UTC),
            owner_id=enrollment.owner_id,
            target_id=enrollment.target_id,
            reference_image_id=enrollment.reference_image_id,
            modality=enrollment.embedding.modality,
            status=status,
            rejection_reason=outcome.rejection_reason,
            quality_score_ratio=(
                None if outcome.quality is None else score_quality(outcome.quality)
            ),
        )
        try:
            await self._message_bus.publish(message)
        except NatsError:
            # The outcome is already stored, which is what clients read when they miss the event.
            logger.exception(
                "cannot publish an enrollment outcome",
                extra={"reference_image_id": enrollment.reference_image_id},
            )
