"""ByteTrack — a real motion-model tracker (Kalman filter + Hungarian assignment,
matched in two passes so low-confidence detections can still recover a track through
brief occlusion) behind the same ``Tracker`` port ``IouTracker`` implements.

Built on the ``trackers`` package (``pip install trackers``), Roboflow's actively
maintained successor to ``supervision.ByteTrack`` — that class is deprecated as of
supervision 0.28 and removed in 0.31, with `ByteTrackTracker` from `trackers` as its
named replacement.

One ``ByteTrackTracker`` per stream (its internal Kalman state must not leak between
streams, same as ``IouTracker``'s per-stream dict). Class names are an open,
small-cardinality vocabulary here ("person"/"face"/"vehicle"/...) so they're mapped to
stable small integers for `supervision.Detections.class_id`, and back, purely for this
adapter's own bookkeeping — nothing downstream sees the integer form.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np

from specter.domain.vision import BBox, Detection, Track


class ByteTrackAdapter:
    def __init__(
        self,
        *,
        track_activation_threshold: float = 0.25,
        lost_track_buffer: int = 30,
        minimum_consecutive_frames: int = 2,
        minimum_iou_threshold: float = 0.3,
        frame_rate: float = 30.0,
    ) -> None:
        self._kwargs: dict[str, Any] = {
            "track_activation_threshold": track_activation_threshold,
            "lost_track_buffer": lost_track_buffer,
            "minimum_consecutive_frames": minimum_consecutive_frames,
            "minimum_iou_threshold": minimum_iou_threshold,
            "frame_rate": frame_rate,
        }
        self._by_stream: dict[str, Any] = {}
        self._ages: dict[str, dict[int, int]] = {}
        self._class_ids: dict[str, int] = {}
        self._class_names: dict[int, str] = {}

    def update(self, stream_id: str, detections: Sequence[Detection]) -> list[Track]:
        tracker = self._tracker_for(stream_id)
        tracked = tracker.update(self._to_sv(detections))
        return self._to_tracks(stream_id, tracked)

    def forget(self, stream_id: str) -> None:
        self._by_stream.pop(stream_id, None)
        self._ages.pop(stream_id, None)

    def _tracker_for(self, stream_id: str) -> Any:
        tracker = self._by_stream.get(stream_id)
        if tracker is None:
            from trackers import ByteTrackTracker  # pylint: disable=import-outside-toplevel

            tracker = ByteTrackTracker(**self._kwargs)
            self._by_stream[stream_id] = tracker
        return tracker

    def _class_id(self, cls: str) -> int:
        cid = self._class_ids.get(cls)
        if cid is None:
            cid = len(self._class_ids)
            self._class_ids[cls] = cid
            self._class_names[cid] = cls
        return cid

    def _to_sv(self, detections: Sequence[Detection]) -> Any:
        import supervision as sv  # pylint: disable=import-outside-toplevel

        if not detections:
            return sv.Detections.empty()
        xyxy = np.array(
            [[d.bbox.x, d.bbox.y, d.bbox.x2, d.bbox.y2] for d in detections], dtype=np.float32
        )
        confidence = np.array([d.confidence for d in detections], dtype=np.float32)
        class_id = np.array([self._class_id(d.cls) for d in detections], dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)

    def _to_tracks(self, stream_id: str, tracked: Any) -> list[Track]:
        if tracked.tracker_id is None or len(tracked) == 0:
            return []
        ages = self._ages.setdefault(stream_id, {})
        tracks: list[Track] = []
        for i in range(len(tracked)):
            raw_id = int(tracked.tracker_id[i])
            if raw_id < 0:
                continue  # not yet activated (< minimum_consecutive_frames)
            x1, y1, x2, y2 = (float(v) for v in tracked.xyxy[i])
            cls_id = int(tracked.class_id[i]) if tracked.class_id is not None else -1
            confidence = float(tracked.confidence[i]) if tracked.confidence is not None else 0.0
            bbox = BBox(
                x=int(round(x1)),
                y=int(round(y1)),
                w=max(int(round(x2 - x1)), 1),
                h=max(int(round(y2 - y1)), 1),
            )
            detection = Detection(
                cls=self._class_names.get(cls_id, "unknown"), confidence=confidence, bbox=bbox
            )
            age = ages.get(raw_id, 0) + 1
            ages[raw_id] = age
            tracks.append(Track(track_id=raw_id, detection=detection, age=age))
        return tracks
