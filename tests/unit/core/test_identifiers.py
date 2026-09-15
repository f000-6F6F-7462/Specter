import re

import pytest

from specter.core.identifiers import new_identifier

UNIQUENESS_SAMPLE_COUNT = 10_000


def test_identifier_starts_with_prefix_when_created() -> None:
    identifier = new_identifier("reference_image")

    assert re.fullmatch(r"reference_image_[0-9a-f]{32}", identifier)


def test_identifiers_differ_when_created_repeatedly() -> None:
    identifiers = {new_identifier("camera") for _ in range(UNIQUENESS_SAMPLE_COUNT)}

    assert len(identifiers) == UNIQUENESS_SAMPLE_COUNT


@pytest.mark.parametrize(
    "prefix", ["", "Camera", "camera-id", "_camera", "camera_", "reference__image", "camera1"]
)
def test_identifier_is_rejected_when_prefix_is_not_lowercase_words(prefix: str) -> None:
    with pytest.raises(ValueError, match="identifier prefix"):
        new_identifier(prefix)
