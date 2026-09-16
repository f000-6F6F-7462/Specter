from datetime import UTC, datetime, timedelta

from specter.entities.geometry import BoundingBox
from specter.vision.detections import Detection
from specter.vision.tracking import ObjectTracker

EXPECTED_FPS = 5.0
FRAME_INTERVAL_SECONDS = 1 / EXPECTED_FPS
STARTED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def build_person(x_pixels: int) -> Detection:
    return Detection(
        object_class="person",
        confidence_ratio=0.9,
        bounding_box=BoundingBox(x=x_pixels, y=100, width=60, height=160),
    )


def captured_at(frame_index: int) -> datetime:
    return STARTED_AT + timedelta(seconds=frame_index * FRAME_INTERVAL_SECONDS)


def test_moving_person_keeps_one_track_id_when_seen_in_consecutive_frames() -> None:
    tracker = ObjectTracker(EXPECTED_FPS)

    track_ids = [
        [
            track.track_id
            for track in tracker.update(
                [build_person(100 + frame_index * 4)],
                frame_index * FRAME_INTERVAL_SECONDS,
                captured_at(frame_index),
            ).tracks
        ]
        for frame_index in range(6)
    ]

    confirmed_track_ids = {
        track_id for frame_track_ids in track_ids for track_id in frame_track_ids
    }
    assert len(confirmed_track_ids) == 1
    assert all(frame_track_ids for frame_track_ids in track_ids[1:])


def test_track_keeps_its_first_sighting_time_when_it_ages() -> None:
    tracker = ObjectTracker(EXPECTED_FPS)

    updates = [
        tracker.update(
            [build_person(100)], frame_index * FRAME_INTERVAL_SECONDS, captured_at(frame_index)
        )
        for frame_index in range(4)
    ]

    last_track = updates[-1].tracks[0]
    assert last_track.first_seen_at == updates[1].tracks[0].first_seen_at
    assert last_track.age_frame_count == 2


def test_track_ends_when_unseen_longer_than_the_lost_track_time() -> None:
    tracker = ObjectTracker(EXPECTED_FPS, lost_track_seconds=1.0)
    for frame_index in range(3):
        tracker.update(
            [build_person(100)], frame_index * FRAME_INTERVAL_SECONDS, captured_at(frame_index)
        )

    still_lost = tracker.update([], 1.2, captured_at(6))
    ended = tracker.update([], 1.6, captured_at(8))

    assert still_lost.ended_track_ids == frozenset()
    assert len(ended.ended_track_ids) == 1


def test_every_track_ends_when_stream_time_jumps_backwards() -> None:
    tracker = ObjectTracker(EXPECTED_FPS)
    for frame_index in range(3):
        tracker.update(
            [build_person(100)], 10 + frame_index * FRAME_INTERVAL_SECONDS, captured_at(frame_index)
        )

    update = tracker.update([build_person(100)], 0.0, captured_at(3))

    assert len(update.ended_track_ids) == 1
    assert update.is_timeline_restarted
