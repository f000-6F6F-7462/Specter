import pytest

from specter.entities.geometry import BoundingBox, NormalizedBoundingBox, NormalizedPoint


def test_clip_keeps_only_the_part_inside_the_frame_when_box_extends_past_it() -> None:
    clipped_box = BoundingBox(x=-20, y=300, width=100, height=100).clip_to_frame(640, 360)

    assert clipped_box == BoundingBox(x=0, y=300, width=80, height=60)


def test_bounding_box_is_rejected_when_size_is_not_positive() -> None:
    with pytest.raises(ValueError, match="positive size"):
        BoundingBox(x=0, y=0, width=0, height=10)


def test_normalized_point_is_rejected_when_outside_zero_to_one() -> None:
    with pytest.raises(ValueError, match="within 0 to 1"):
        NormalizedPoint(x=1.2, y=0.5)


def test_normalized_box_is_relative_to_frame_when_converted_from_pixels() -> None:
    normalized_box = NormalizedBoundingBox.from_pixels(
        BoundingBox(x=160, y=90, width=320, height=180), frame_width=640, frame_height=360
    )

    assert normalized_box == NormalizedBoundingBox(x=0.25, y=0.25, width=0.5, height=0.5)
