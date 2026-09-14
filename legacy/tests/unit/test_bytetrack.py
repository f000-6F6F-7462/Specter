"""ByteTrackAdapter — same contract shape as IouTracker's own tests, adjusted for
ByteTrack's real behaviour: a track only gets a real id after
``minimum_consecutive_frames`` (2 by default), so the very first update for a new
object returns nothing yet.
"""

from specter.domain.vision import BBox, Detection
from specter.infrastructure.ml.bytetrack import ByteTrackAdapter


def _det(x: int, y: int, *, cls: str = "face", w: int = 20, h: int = 20) -> Detection:
    return Detection(cls=cls, confidence=0.9, bbox=BBox(x=x, y=y, w=w, h=h))


def test_first_update_has_no_activated_track_yet() -> None:
    tracker = ByteTrackAdapter()
    assert tracker.update("s1", [_det(0, 0)]) == []


def test_stable_id_while_the_box_drifts() -> None:
    tracker = ByteTrackAdapter()
    tracker.update("s1", [_det(0, 0)])  # frame 1: pending activation
    second = tracker.update("s1", [_det(2, 0)])  # frame 2: activates
    assert len(second) == 1
    track_id = second[0].track_id

    ids = [track_id]
    for step in range(2, 6):
        tracks = tracker.update("s1", [_det(step * 2, 0)])
        assert len(tracks) == 1
        ids.append(tracks[0].track_id)
    assert set(ids) == {track_id}


def test_two_objects_keep_separate_ids() -> None:
    tracker = ByteTrackAdapter()
    tracker.update("s1", [_det(0, 0), _det(200, 200)])
    tracks = tracker.update("s1", [_det(2, 0), _det(202, 200)])
    assert len(tracks) == 2
    assert len({t.track_id for t in tracks}) == 2


def test_class_name_round_trips_through_the_integer_mapping() -> None:
    tracker = ByteTrackAdapter()
    tracker.update("s1", [_det(0, 0, cls="face")])
    tracks = tracker.update("s1", [_det(2, 0, cls="face")])
    assert len(tracks) == 1
    assert tracks[0].detection.cls == "face"


def test_age_increments_across_activated_updates() -> None:
    tracker = ByteTrackAdapter()
    tracker.update("s1", [_det(0, 0)])
    first = tracker.update("s1", [_det(2, 0)])[0].age
    second = tracker.update("s1", [_det(4, 0)])[0].age
    assert second > first


def test_streams_are_isolated_and_forgettable() -> None:
    tracker = ByteTrackAdapter()
    tracker.update("s1", [_det(0, 0)])
    tracker.update("s2", [_det(0, 0)])
    s2_id = tracker.update("s2", [_det(2, 0)])[0].track_id

    tracker.forget("s1")

    # s2 is unaffected by forgetting s1
    assert tracker.update("s2", [_det(4, 0)])[0].track_id == s2_id
    # s1 starts over: its new track needs two frames to activate again
    assert tracker.update("s1", [_det(2, 0)]) == []


def test_empty_detections_returns_empty() -> None:
    tracker = ByteTrackAdapter()
    assert tracker.update("s1", []) == []
