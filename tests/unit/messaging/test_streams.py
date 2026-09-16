from collections import Counter

import pytest

from specter.messaging.streams import (
    ENROLLMENT_JOBS_CONSUMER,
    MATCH_COOLDOWNS_BUCKET_NAME,
    STREAM_DEFINITIONS,
    KeyValueBucketDefinition,
    build_key_value_bucket_definitions,
)

MATCH_COOLDOWN_SECONDS = 45.0


def test_no_subject_is_captured_by_two_streams_when_streams_are_defined() -> None:
    subject_counts = Counter(
        subject for stream in STREAM_DEFINITIONS for subject in stream.subjects
    )

    assert all(count == 1 for count in subject_counts.values())


def test_stream_and_bucket_names_are_unique_when_defined() -> None:
    stream_names = [stream.name for stream in STREAM_DEFINITIONS]
    bucket_names = [
        bucket.name for bucket in build_key_value_bucket_definitions(MATCH_COOLDOWN_SECONDS)
    ]

    assert len(set(stream_names)) == len(stream_names)
    assert len(set(bucket_names)) == len(bucket_names)


def test_match_cooldowns_expire_after_the_configured_time_when_buckets_are_built() -> None:
    buckets_by_name = {
        bucket.name: bucket for bucket in build_key_value_bucket_definitions(MATCH_COOLDOWN_SECONDS)
    }

    assert buckets_by_name[MATCH_COOLDOWNS_BUCKET_NAME].time_to_live_seconds == 45.0


def test_enrollment_job_is_delivered_five_times_when_it_keeps_failing() -> None:
    assert ENROLLMENT_JOBS_CONSUMER.max_deliveries == 5
    assert ENROLLMENT_JOBS_CONSUMER.to_consumer_config().max_deliver == 5


@pytest.mark.parametrize(
    ("attempt_number", "expected_delay_seconds"), [(1, 10.0), (2, 30.0), (3, 60.0), (4, 300.0)]
)
def test_redelivery_delay_grows_when_attempts_keep_failing(
    attempt_number: int, expected_delay_seconds: float
) -> None:
    assert ENROLLMENT_JOBS_CONSUMER.redelivery_delay_after(attempt_number) == expected_delay_seconds


@pytest.mark.parametrize(
    ("time_to_live_seconds", "expected_window_seconds"),
    [(None, 120.0), (30.0, 30.0), (600.0, 120.0)],
)
def test_duplicate_window_never_outlasts_bucket_values_when_derived(
    time_to_live_seconds: float | None, expected_window_seconds: float
) -> None:
    bucket = KeyValueBucketDefinition(name="test_bucket", time_to_live_seconds=time_to_live_seconds)

    assert bucket.duplicate_window_seconds == expected_window_seconds
