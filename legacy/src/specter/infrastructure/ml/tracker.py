"""Greedy IoU tracker — a dependency-free multi-object tracker.

Associates each frame's detections to existing tracks by best intersection-over-union,
spawns tracks for the unmatched, and evicts tracks that go unseen for ``max_age``
frames. One instance serves every stream; per-stream state is keyed by ``stream_id``.

Good enough for face / person / vehicle re-id gating at 5-15 fps. A motion-model
tracker (Kalman + Hungarian, e.g. ByteTrack) can replace it behind the same port.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import count

from specter.domain.vision import Detection, Track


@dataclass(slots=True)
class _Trk:
    track_id: int
    detection: Detection
    hits: int = 1
    misses: int = 0
    age: int = 0


@dataclass(slots=True)
class _StreamState:
    tracks: dict[int, _Trk] = field(default_factory=dict)


class IouTracker:
    def __init__(self, *, iou_threshold: float = 0.3, max_age: int = 30, min_hits: int = 1) -> None:
        self._iou_threshold = iou_threshold
        self._max_age = max_age
        self._min_hits = min_hits
        self._streams: dict[str, _StreamState] = {}
        self._ids = count(1)

    def update(self, stream_id: str, detections: Sequence[Detection]) -> list[Track]:
        state = self._streams.setdefault(stream_id, _StreamState())
        matched_trk, matched_det = self._associate(state, detections)

        for trk_id, det_idx in matched_trk.items():
            trk = state.tracks[trk_id]
            trk.detection = detections[det_idx]
            trk.hits += 1
            trk.misses = 0
            trk.age += 1

        for trk_id in list(state.tracks):
            if trk_id not in matched_trk:
                trk = state.tracks[trk_id]
                trk.misses += 1
                trk.age += 1
                if trk.misses > self._max_age:
                    del state.tracks[trk_id]

        for det_idx, det in enumerate(detections):
            if det_idx not in matched_det:
                trk_id = next(self._ids)
                state.tracks[trk_id] = _Trk(track_id=trk_id, detection=det)

        return [
            Track(track_id=t.track_id, detection=t.detection, age=t.age)
            for t in state.tracks.values()
            if t.misses == 0 and t.hits >= self._min_hits
        ]

    def forget(self, stream_id: str) -> None:
        """Drop a stream's state when its pipeline stops."""
        self._streams.pop(stream_id, None)

    def _associate(
        self, state: _StreamState, detections: Sequence[Detection]
    ) -> tuple[dict[int, int], set[int]]:
        pairs: list[tuple[float, int, int]] = []
        for trk_id, trk in state.tracks.items():
            for det_idx, det in enumerate(detections):
                if det.cls != trk.detection.cls:
                    continue
                iou = trk.detection.bbox.iou(det.bbox)
                if iou >= self._iou_threshold:
                    pairs.append((iou, trk_id, det_idx))

        pairs.sort(reverse=True)
        matched_trk: dict[int, int] = {}
        matched_det: set[int] = set()
        for _iou, trk_id, det_idx in pairs:
            if trk_id in matched_trk or det_idx in matched_det:
                continue
            matched_trk[trk_id] = det_idx
            matched_det.add(det_idx)
        return matched_trk, matched_det
