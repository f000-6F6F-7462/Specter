"""Identifies a camera's tracked people against its watchlists, by face and by appearance."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from nats.errors import Error as NatsError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from specter.camera.detector_client import DetectorClient
from specter.entities.cameras import Camera
from specter.entities.targets import EmbeddingModality
from specter.entities.watchlists import Watchlist
from specter.frame_transport.detector_requests import (
    IdentificationResult,
    IdentificationTask,
    PixelBox,
)
from specter.messaging.shared_state import MatchCooldownBucket
from specter.storage.vector_index import VectorIndex
from specter.vision.best_shot import BestShotSelector
from specter.vision.detections import Track
from specter.vision.frames import Frame
from specter.vision.identity_matching import (
    IdentityMatchPolicy,
    MatchDecision,
    TrackMatchState,
    rank_best_candidate_per_target,
)
from specter.vision.tracking import TrackingUpdate

logger = logging.getLogger(__name__)

PERSON_OBJECT_CLASS = "person"
# Each reference image is one candidate, so this leaves room for several targets with many images.
CANDIDATE_LIMIT = 20
# A person this tall carries all the detail that the appearance model's 256-pixel input can use.
FULL_DETAIL_PERSON_HEIGHT_PIXELS = 256

type MatchStateKey = tuple[int, EmbeddingModality]
type MatchPolicyKey = tuple[str, EmbeddingModality]


@dataclass(frozen=True, slots=True)
class ConfirmedMatch:
    """A tracked person confirmed as a watchlist target."""

    track: Track
    modality: EmbeddingModality
    decision: MatchDecision
    threshold_ratio: float


def score_appearance_shot(track: Track) -> float:
    """Returns how useful the track's box is for an appearance embedding, from 0 to 1."""
    detection = track.detection
    size_ratio = min(detection.bounding_box.height / FULL_DETAIL_PERSON_HEIGHT_PIXELS, 1.0)
    return detection.confidence_ratio * size_ratio


class PersonIdentifier:
    """Embeds the best shots of a camera's tracked people and confirms who they are.

    Each embedding is searched in the watchlists the camera uses, and a track is confirmed as a
    target by the matching policy of the best candidate's watchlist for that kind of embedding. A
    confirmed target raises one alert per cooldown on the camera, even across process restarts.
    """

    def __init__(
        self,
        camera: Camera,
        detector_client: DetectorClient,
        vector_index: VectorIndex,
        cooldown_bucket: MatchCooldownBucket,
        cooldown_seconds: float,
    ) -> None:
        self._camera = camera
        self._detector_client = detector_client
        self._vector_index = vector_index
        self._cooldown_bucket = cooldown_bucket
        self._cooldown_seconds = cooldown_seconds
        self._watchlist_ids: tuple[str, ...] = ()
        self._policies: dict[MatchPolicyKey, IdentityMatchPolicy] = {}
        self._selectors_by_modality = {
            modality: BestShotSelector() for modality in EmbeddingModality
        }
        self._match_states: dict[MatchStateKey, TrackMatchState] = {}

    def configure(self, watchlists: Sequence[Watchlist]) -> None:
        """Replaces the watchlists the camera matches against."""
        self._watchlist_ids = tuple(watchlist.id for watchlist in watchlists)
        self._policies = {
            (watchlist.id, modality): IdentityMatchPolicy(
                threshold_ratio=watchlist.match_threshold_ratio(modality),
                cooldown_seconds=self._cooldown_seconds,
            )
            for watchlist in watchlists
            for modality in EmbeddingModality
        }

    async def identify(
        self, frame: Frame, tracking_update: TrackingUpdate, now_seconds: float
    ) -> list[ConfirmedMatch]:
        """Samples the frame's tracked people and returns those confirmed in this frame."""
        for selector in self._selectors_by_modality.values():
            selector.forget_tracks(tracking_update.ended_track_ids)
        for key in [key for key in self._match_states if key[0] in tracking_update.ended_track_ids]:
            del self._match_states[key]
        if not self._watchlist_ids:
            return []

        tasks = self._plan_tasks(tracking_update.tracks, now_seconds)
        if not tasks:
            return []
        results = await self._detector_client.identify(frame, tasks)
        if results is None:
            return []

        tracks_by_id = {track.track_id: track for track in tracking_update.tracks}
        tasks_by_track_id = {task.track_id: task for task in tasks}
        confirmed_matches: list[ConfirmedMatch] = []
        for result in results:
            track = tracks_by_id[result.track_id]
            for modality, embedding in self._record_samples(
                track, tasks_by_track_id[result.track_id], result, now_seconds
            ):
                confirmed_matches.extend(await self._match(track, modality, embedding, now_seconds))
        return confirmed_matches

    def _plan_tasks(self, tracks: Sequence[Track], now_seconds: float) -> list[IdentificationTask]:
        tasks: list[IdentificationTask] = []
        for track in tracks:
            if track.detection.object_class != PERSON_OBJECT_CLASS:
                continue
            minimum_face_quality_score = self._selectors_by_modality[
                EmbeddingModality.FACE
            ].minimum_quality_score(track.track_id, now_seconds)
            minimum_appearance_quality_score = self._selectors_by_modality[
                EmbeddingModality.APPEARANCE
            ].minimum_quality_score(track.track_id, now_seconds)
            # The appearance shot is scored here from the box alone, so a worse shot costs nothing.
            embeds_appearance = (
                minimum_appearance_quality_score is not None
                and score_appearance_shot(track) > minimum_appearance_quality_score
            )
            if minimum_face_quality_score is None and not embeds_appearance:
                continue
            box = track.detection.bounding_box
            tasks.append(
                IdentificationTask(
                    track_id=track.track_id,
                    person_box=PixelBox(x=box.x, y=box.y, width=box.width, height=box.height),
                    minimum_face_quality_score_ratio=minimum_face_quality_score,
                    embeds_appearance=embeds_appearance,
                )
            )
        return tasks

    def _record_samples(
        self,
        track: Track,
        task: IdentificationTask,
        result: IdentificationResult,
        now_seconds: float,
    ) -> list[tuple[EmbeddingModality, tuple[float, ...]]]:
        embeddings: list[tuple[EmbeddingModality, tuple[float, ...]]] = []
        if task.minimum_face_quality_score_ratio is not None:
            face_embedding = None if result.face is None else result.face.embedding
            self._selectors_by_modality[EmbeddingModality.FACE].record_sample(
                track.track_id,
                None if result.face is None else result.face.quality_score_ratio,
                was_embedded=face_embedding is not None,
                now_seconds=now_seconds,
            )
            if face_embedding is not None:
                embeddings.append((EmbeddingModality.FACE, face_embedding))
        if task.embeds_appearance:
            self._selectors_by_modality[EmbeddingModality.APPEARANCE].record_sample(
                track.track_id,
                score_appearance_shot(track),
                was_embedded=result.appearance_embedding is not None,
                now_seconds=now_seconds,
            )
            if result.appearance_embedding is not None:
                embeddings.append((EmbeddingModality.APPEARANCE, result.appearance_embedding))
        return embeddings

    async def _match(
        self,
        track: Track,
        modality: EmbeddingModality,
        embedding: Sequence[float],
        now_seconds: float,
    ) -> list[ConfirmedMatch]:
        try:
            candidates = await self._vector_index.search(
                modality,
                embedding,
                owner_id=self._camera.owner_id,
                watchlist_ids=self._watchlist_ids,
                limit=CANDIDATE_LIMIT,
            )
        except (UnexpectedResponse, ResponseHandlingException) as error:
            logger.warning(
                "cannot search watchlists: %s", error, extra={"camera_id": self._camera.id}
            )
            return []
        ranked_candidates = rank_best_candidate_per_target(candidates)
        if not ranked_candidates:
            return []
        policy = self._policies[(ranked_candidates[0].watchlist_id, modality)]
        key = (track.track_id, modality)
        self._match_states[key], decision = policy.evaluate(
            self._match_states.get(key, TrackMatchState()), candidates, now_seconds
        )
        if not decision.is_confirmed or decision.target_id is None:
            return []
        self._selectors_by_modality[modality].finish(track.track_id)
        try:
            is_first_alert = await self._cooldown_bucket.claim(
                self._camera.owner_id, self._camera.id, decision.target_id
            )
        except NatsError as error:
            logger.warning(
                "cannot claim a match cooldown: %s", error, extra={"camera_id": self._camera.id}
            )
            return []
        if not is_first_alert:
            return []
        return [
            ConfirmedMatch(
                track=track,
                modality=modality,
                decision=decision,
                threshold_ratio=policy.threshold_ratio,
            )
        ]
