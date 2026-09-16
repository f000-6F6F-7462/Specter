from specter.vision.best_shot import BestShotSelector

TRACK_ID = 7


def build_selector() -> BestShotSelector:
    return BestShotSelector(
        sample_interval_seconds=0.5,
        refresh_interval_seconds=2.0,
        improvement_ratio=0.1,
        maximum_embedding_count=3,
    )


def test_any_shot_is_accepted_when_track_was_never_embedded() -> None:
    assert build_selector().minimum_quality_score(TRACK_ID, now_seconds=0.0) == 0.0


def test_track_is_skipped_when_sampled_within_the_interval() -> None:
    selector = build_selector()
    selector.record_sample(TRACK_ID, None, was_embedded=False, now_seconds=0.0)

    assert selector.minimum_quality_score(TRACK_ID, now_seconds=0.3) is None


def test_shot_must_beat_the_best_one_when_track_was_embedded_recently() -> None:
    selector = build_selector()
    selector.record_sample(TRACK_ID, 0.5, was_embedded=True, now_seconds=0.0)

    minimum_score = selector.minimum_quality_score(TRACK_ID, now_seconds=1.0)

    assert minimum_score is not None
    assert round(minimum_score, 6) == 0.55


def test_any_shot_is_accepted_again_when_refresh_interval_passed() -> None:
    selector = build_selector()
    selector.record_sample(TRACK_ID, 0.9, was_embedded=True, now_seconds=0.0)

    assert selector.minimum_quality_score(TRACK_ID, now_seconds=2.0) == 0.0


def test_track_is_skipped_when_it_reached_the_maximum_embedding_count() -> None:
    selector = build_selector()
    for now_seconds in (0.0, 2.0, 4.0):
        selector.record_sample(TRACK_ID, 0.5, was_embedded=True, now_seconds=now_seconds)

    assert selector.minimum_quality_score(TRACK_ID, now_seconds=10.0) is None


def test_track_is_skipped_when_finished() -> None:
    selector = build_selector()
    selector.finish(TRACK_ID)

    assert selector.minimum_quality_score(TRACK_ID, now_seconds=0.0) is None


def test_forgotten_track_starts_over_when_seen_again() -> None:
    selector = build_selector()
    selector.finish(TRACK_ID)

    selector.forget_tracks(frozenset({TRACK_ID}))

    assert selector.minimum_quality_score(TRACK_ID, now_seconds=0.0) == 0.0
