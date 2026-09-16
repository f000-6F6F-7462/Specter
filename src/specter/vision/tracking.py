"""Follows a camera's detected objects across frames under stable track ids."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import supervision
from trackers import ByteTrackTracker

from specter.vision.detections import Detection, Track

# ByteTrack counts its lost-track buffer in frames of a 30 fps video, even when it runs on
# timestamps.
BYTETRACK_REFERENCE_FPS = 30.0
# Long enough to carry a person through a brief occlusion, short enough that a new person at the
# same spot does not inherit the track.
DEFAULT_LOST_TRACK_SECONDS = 2.0
UNTRACKED_ID = -1
DETECTION_INDEX_KEY = "detection_index"


@dataclass(frozen=True, slots=True)
class TrackingUpdate:
    """The tracks seen in a frame, and the tracks that ended since the previous frame."""

    tracks: tuple[Track, ...]
    ended_track_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class _TrackHistory:
    first_seen_at: datetime
    last_seen_presentation_time_seconds: float
    age_frame_count: int


class ObjectTracker:
    """Assigns track ids to one camera's detections with ByteTrack.

    Timing follows the stream's presentation times, so an uneven frame rate keeps motion
    predictions right. A track ends once it has not been seen for the lost-track time; a stream
    whose times jump backwards, such as after a reconnect, ends every track and starts over.
    """

    def __init__(
        self,
        expected_fps: float,
        *,
        lost_track_seconds: float = DEFAULT_LOST_TRACK_SECONDS,
    ) -> None:
        self._expected_fps = expected_fps
        self._lost_track_seconds = lost_track_seconds
        self._tracker = self._create_tracker()
        self._histories_by_track_id: dict[int, _TrackHistory] = {}
        self._last_presentation_time_seconds: float | None = None

    def update(
        self,
        detections: Sequence[Detection],
        presentation_time_seconds: float,
        captured_at: datetime,
    ) -> TrackingUpdate:
        """Tracks the detections of the next frame."""
        ended_track_ids: set[int] = set()
        if (
            self._last_presentation_time_seconds is not None
            and presentation_time_seconds < self._last_presentation_time_seconds
        ):
            ended_track_ids.update(self._histories_by_track_id)
            self._histories_by_track_id.clear()
            self._tracker = self._create_tracker()
        self._last_presentation_time_seconds = presentation_time_seconds

        tracked_detections = self._tracker.update(
            self._to_tracker_detections(detections), timestamp=presentation_time_seconds
        )
        tracks: list[Track] = []
        # A frame without tracked detections comes back without the index data.
        detection_indices = np.asarray(
            tracked_detections.data.get(DETECTION_INDEX_KEY, ()), dtype=np.int64
        )
        track_ids = np.asarray(
            () if tracked_detections.tracker_id is None else tracked_detections.tracker_id,
            dtype=np.int64,
        )
        for track_id, detection_index in zip(
            track_ids.tolist(), detection_indices.tolist(), strict=True
        ):
            if track_id == UNTRACKED_ID:
                continue
            tracks.append(
                self._record_sighting(
                    track_id, detections[detection_index], presentation_time_seconds, captured_at
                )
            )

        for track_id, history in list(self._histories_by_track_id.items()):
            if (
                presentation_time_seconds - history.last_seen_presentation_time_seconds
                > self._lost_track_seconds
            ):
                ended_track_ids.add(track_id)
                del self._histories_by_track_id[track_id]
        return TrackingUpdate(tracks=tuple(tracks), ended_track_ids=frozenset(ended_track_ids))

    def _record_sighting(
        self,
        track_id: int,
        detection: Detection,
        presentation_time_seconds: float,
        captured_at: datetime,
    ) -> Track:
        previous_history = self._histories_by_track_id.get(track_id)
        history = _TrackHistory(
            first_seen_at=captured_at
            if previous_history is None
            else previous_history.first_seen_at,
            last_seen_presentation_time_seconds=presentation_time_seconds,
            age_frame_count=0 if previous_history is None else previous_history.age_frame_count + 1,
        )
        self._histories_by_track_id[track_id] = history
        return Track(
            track_id=track_id,
            detection=detection,
            first_seen_at=history.first_seen_at,
            age_frame_count=history.age_frame_count,
        )

    def _create_tracker(self) -> ByteTrackTracker:
        return ByteTrackTracker(
            lost_track_buffer=round(self._lost_track_seconds * BYTETRACK_REFERENCE_FPS),
            frame_rate=self._expected_fps,
        )

    @staticmethod
    def _to_tracker_detections(detections: Sequence[Detection]) -> supervision.Detections:
        if not detections:
            return supervision.Detections.empty()
        corner_boxes = np.array(
            [
                [
                    detection.bounding_box.x,
                    detection.bounding_box.y,
                    detection.bounding_box.right,
                    detection.bounding_box.bottom,
                ]
                for detection in detections
            ],
            dtype=np.float32,
        )
        # The detection's index travels through the tracker, which reorders and drops detections.
        return supervision.Detections(
            xyxy=corner_boxes,
            confidence=np.array(
                [detection.confidence_ratio for detection in detections], dtype=np.float32
            ),
            data={DETECTION_INDEX_KEY: np.arange(len(detections))},
        )
