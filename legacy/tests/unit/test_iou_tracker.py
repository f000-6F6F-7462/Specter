from specter.domain.vision import BBox, Detection
from specter.infrastructure.ml.tracker import IouTracker


def _det(x: int, y: int, *, cls: str = "face", w: int = 20, h: int = 20) -> Detection:
    return Detection(cls=cls, confidence=0.9, bbox=BBox(x=x, y=y, w=w, h=h))


def test_stable_id_while_the_box_drifts() -> None:
    tracker = IouTracker(iou_threshold=0.2)

    first = tracker.update("s1", [_det(0, 0)])
    assert len(first) == 1
    track_id = first[0].track_id

    ids = [track_id]
    for step in range(1, 5):
        tracks = tracker.update("s1", [_det(step * 3, 0)])  # overlapping drift
        assert len(tracks) == 1
        ids.append(tracks[0].track_id)
    assert set(ids) == {track_id}
    assert tracker.update("s1", [_det(12, 0)])[0].age >= 4


def test_disjoint_box_gets_a_new_id() -> None:
    tracker = IouTracker(iou_threshold=0.3)
    a = tracker.update("s1", [_det(0, 0)])[0].track_id
    b = tracker.update("s1", [_det(500, 500)])[0].track_id
    assert a != b


def test_two_objects_keep_separate_ids() -> None:
    tracker = IouTracker(iou_threshold=0.2)
    tracks = tracker.update("s1", [_det(0, 0), _det(200, 200)])
    assert len({t.track_id for t in tracks}) == 2
    again = tracker.update("s1", [_det(2, 0), _det(202, 200)])
    assert {t.track_id for t in tracks} == {t.track_id for t in again}


def test_class_change_is_not_associated() -> None:
    tracker = IouTracker(iou_threshold=0.1)
    face = tracker.update("s1", [_det(0, 0, cls="face")])[0].track_id
    car = tracker.update("s1", [_det(0, 0, cls="car")])[0].track_id
    assert face != car


def test_track_is_evicted_after_max_age() -> None:
    tracker = IouTracker(iou_threshold=0.3, max_age=2)
    original = tracker.update("s1", [_det(0, 0)])[0].track_id
    for _ in range(3):  # misses > max_age
        tracker.update("s1", [])
    reborn = tracker.update("s1", [_det(0, 0)])[0].track_id
    assert reborn != original


def test_streams_are_isolated_and_forgettable() -> None:
    tracker = IouTracker()
    tracker.update("s1", [_det(0, 0)])
    s2_id = tracker.update("s2", [_det(0, 0)])[0].track_id
    tracker.forget("s1")
    # s2 keeps its track; s1 starts over on next use
    assert tracker.update("s2", [_det(1, 0)])[0].track_id == s2_id
    assert tracker.update("s1", [_det(0, 0)])[0].track_id not in {s2_id}
