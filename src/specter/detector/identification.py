"""Embeds the faces and appearance of tracked people, for every camera."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from specter.entities.geometry import BoundingBox
from specter.frame_transport.detector_requests import (
    FaceSample,
    IdentificationReply,
    IdentificationRequest,
    IdentificationResult,
    IdentificationTask,
    PixelBox,
)
from specter.frame_transport.shared_frames import SharedFrameReaders
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import Embedding, InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.face_embedder import FaceEmbedder
from specter.inference.face_quality import measure_face_quality
from specter.vision.detections import FaceDetection
from specter.vision.frames import FrameImage
from specter.vision.quality import RUNTIME_QUALITY_THRESHOLDS, assess_quality, score_quality

IDENTIFICATION_THREAD_NAME = "specter-identification"
EMPTY_REPLY = IdentificationReply(results=())
# A person's box can cut through the head, so the face is searched with a margin around it.
PERSON_BOX_MARGIN_RATIO = 0.1
# Only a face in the upper half of the box belongs to the person rather than to someone behind.
FACE_SEARCH_HEIGHT_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class _FoundFace:
    aligned_face: FrameImage
    quality_score_ratio: float
    has_passed_quality_gate: bool


@dataclass(frozen=True, slots=True)
class _Models:
    face_detection_session: InferenceSession
    face_detector: FaceDetector
    face_recognition_session: InferenceSession
    appearance_session: InferenceSession
    appearance_embedder: AppearanceEmbedder


class IdentificationService:
    """Finds and embeds the faces and appearance of the people that cameras track.

    Requests run one at a time on a dedicated thread, so identification never competes with itself
    for the CPU, and each request's faces and crops run as one batch per model.
    """

    def __init__(
        self,
        *,
        face_detection_session: InferenceSession,
        face_detector: FaceDetector,
        face_recognition_session: InferenceSession,
        appearance_session: InferenceSession,
        appearance_embedder: AppearanceEmbedder,
    ) -> None:
        self._models = _Models(
            face_detection_session=face_detection_session,
            face_detector=face_detector,
            face_recognition_session=face_recognition_session,
            appearance_session=appearance_session,
            appearance_embedder=appearance_embedder,
        )
        self._frame_readers = SharedFrameReaders()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=IDENTIFICATION_THREAD_NAME
        )

    async def answer(self, raw_request: bytes) -> bytes:
        """Answers a serialized identification request with the serialized reply."""
        request = IdentificationRequest.model_validate_json(raw_request)
        reply = await asyncio.get_running_loop().run_in_executor(
            self._executor, self.identify, request
        )
        return reply.model_dump_json().encode()

    def identify(self, request: IdentificationRequest) -> IdentificationReply:
        """Returns the embeddings of every task, blocking while the models run."""
        frame = request.frame
        image = self._frame_readers.read(
            frame.shared_memory_name,
            frame.frame_width_pixels,
            frame.frame_height_pixels,
            frame.frame_sequence_number,
        )
        if image is None:
            return EMPTY_REPLY

        found_faces_by_track_id = {
            task.track_id: self._find_face(image, task.person_box)
            for task in request.tasks
            if task.minimum_face_quality_score_ratio is not None
        }
        faces_to_embed_by_track_id = self._select_faces_to_embed(
            request.tasks, found_faces_by_track_id
        )
        face_embeddings_by_track_id = dict(
            zip(
                faces_to_embed_by_track_id,
                self._embed_faces(list(faces_to_embed_by_track_id.values())),
                strict=True,
            )
        )
        appearance_tasks = [task for task in request.tasks if task.embeds_appearance]
        appearance_embeddings_by_track_id = dict(
            zip(
                [task.track_id for task in appearance_tasks],
                self._embed_appearances(
                    [self._crop(image, task.person_box) for task in appearance_tasks]
                ),
                strict=True,
            )
        )
        return IdentificationReply(
            results=tuple(
                self._build_result(
                    task,
                    found_faces_by_track_id.get(task.track_id),
                    face_embeddings_by_track_id.get(task.track_id),
                    appearance_embeddings_by_track_id.get(task.track_id),
                )
                for task in request.tasks
            )
        )

    def close(self) -> None:
        """Waits for the running request, then detaches from every camera's region."""
        self._executor.submit(self._frame_readers.close).result()
        self._executor.shutdown(wait=True)

    def _find_face(self, image: FrameImage, person_box: PixelBox) -> _FoundFace | None:
        frame_height, frame_width = image.shape[:2]
        margin_pixels = round(person_box.width * PERSON_BOX_MARGIN_RATIO)
        search_box = BoundingBox(
            x=person_box.x - margin_pixels,
            y=person_box.y - margin_pixels,
            width=person_box.width + 2 * margin_pixels,
            height=round(person_box.height * FACE_SEARCH_HEIGHT_RATIO) + margin_pixels,
        ).clip_to_frame(frame_width, frame_height)
        search_image = image[search_box.y : search_box.bottom, search_box.x : search_box.right]
        model_input, scale = self._models.face_detector.prepare(search_image)
        faces = self._models.face_detector.decode(
            self._models.face_detection_session.run(model_input)[0], scale
        )
        if not faces:
            return None
        best_face = self._move_to_frame(
            max(faces, key=lambda face: face.confidence_ratio), search_box
        )
        try:
            aligned_face = FaceEmbedder.align(image, best_face)
        except ValueError:
            return None
        quality_report = measure_face_quality(best_face, aligned_face)
        return _FoundFace(
            aligned_face=aligned_face,
            quality_score_ratio=score_quality(quality_report),
            has_passed_quality_gate=assess_quality(
                quality_report, RUNTIME_QUALITY_THRESHOLDS
            ).has_passed,
        )

    def _embed_faces(self, aligned_faces: list[FrameImage]) -> list[Embedding]:
        if not aligned_faces:
            return []
        all_outputs = self._models.face_recognition_session.run(
            FaceEmbedder.build_input_tensor(aligned_faces)
        )
        return [FaceEmbedder.read_embedding(outputs) for outputs in all_outputs]

    def _embed_appearances(self, crops: list[FrameImage]) -> list[Embedding]:
        if not crops:
            return []
        all_outputs = self._models.appearance_session.run(
            self._models.appearance_embedder.build_input_tensor(crops)
        )
        return [AppearanceEmbedder.read_embedding(outputs) for outputs in all_outputs]

    @staticmethod
    def _select_faces_to_embed(
        tasks: tuple[IdentificationTask, ...],
        found_faces_by_track_id: dict[int, _FoundFace | None],
    ) -> dict[int, FrameImage]:
        # Embedding runs only for faces good enough to match and better than what the camera has.
        aligned_faces_by_track_id: dict[int, FrameImage] = {}
        for task in tasks:
            found_face = found_faces_by_track_id.get(task.track_id)
            if (
                found_face is not None
                and task.minimum_face_quality_score_ratio is not None
                and found_face.has_passed_quality_gate
                and found_face.quality_score_ratio > task.minimum_face_quality_score_ratio
            ):
                aligned_faces_by_track_id[task.track_id] = found_face.aligned_face
        return aligned_faces_by_track_id

    @staticmethod
    def _build_result(
        task: IdentificationTask,
        found_face: _FoundFace | None,
        face_embedding: Embedding | None,
        appearance_embedding: Embedding | None,
    ) -> IdentificationResult:
        face_sample = (
            None
            if found_face is None
            else FaceSample(
                quality_score_ratio=found_face.quality_score_ratio,
                has_passed_quality_gate=found_face.has_passed_quality_gate,
                embedding=None if face_embedding is None else tuple(face_embedding.tolist()),
            )
        )
        return IdentificationResult(
            track_id=task.track_id,
            face=face_sample,
            appearance_embedding=(
                None if appearance_embedding is None else tuple(appearance_embedding.tolist())
            ),
        )

    @staticmethod
    def _crop(image: FrameImage, box: PixelBox) -> FrameImage:
        frame_height, frame_width = image.shape[:2]
        clipped_box = BoundingBox(
            x=box.x, y=box.y, width=box.width, height=box.height
        ).clip_to_frame(frame_width, frame_height)
        return image[clipped_box.y : clipped_box.bottom, clipped_box.x : clipped_box.right]

    @staticmethod
    def _move_to_frame(face: FaceDetection, search_box: BoundingBox) -> FaceDetection:
        return replace(
            face,
            bounding_box=replace(
                face.bounding_box,
                x=face.bounding_box.x + search_box.x,
                y=face.bounding_box.y + search_box.y,
            ),
            landmarks=tuple(
                (point_x + search_box.x, point_y + search_box.y)
                for point_x, point_y in face.landmarks
            ),
        )
