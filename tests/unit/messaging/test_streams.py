from collections import Counter

from specter.messaging.streams import KEY_VALUE_BUCKET_DEFINITIONS, STREAM_DEFINITIONS


def test_no_subject_is_captured_by_two_streams_when_streams_are_defined() -> None:
    subject_counts = Counter(
        subject for stream in STREAM_DEFINITIONS for subject in stream.subjects
    )

    assert all(count == 1 for count in subject_counts.values())


def test_stream_and_bucket_names_are_unique_when_defined() -> None:
    stream_names = [stream.name for stream in STREAM_DEFINITIONS]
    bucket_names = [bucket.name for bucket in KEY_VALUE_BUCKET_DEFINITIONS]

    assert len(set(stream_names)) == len(stream_names)
    assert len(set(bucket_names)) == len(bucket_names)
