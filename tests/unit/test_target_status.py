import pytest

from specter.domain.catalog import EnrollmentStatus, ImageStatus
from specter.platform.errors import RuleViolation
from tests.conftest import make_target

E, R, P = ImageStatus.EMBEDDED, ImageStatus.REJECTED, ImageStatus.PENDING


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], EnrollmentStatus.QUEUED),
        ([P], EnrollmentStatus.QUEUED),
        ([P, P], EnrollmentStatus.QUEUED),
        ([R], EnrollmentStatus.FAILED),
        ([R, R], EnrollmentStatus.FAILED),
        ([P, R], EnrollmentStatus.QUEUED),
        ([E], EnrollmentStatus.READY),
        ([E, E], EnrollmentStatus.READY),
        ([E, P], EnrollmentStatus.PARTIAL),
        ([E, R], EnrollmentStatus.PARTIAL),
        ([E, E, R], EnrollmentStatus.PARTIAL),
        ([E, R, R], EnrollmentStatus.PARTIAL),
    ],
)
def test_target_status_truth_table(statuses: list[ImageStatus], expected: EnrollmentStatus) -> None:
    assert make_target(image_statuses=statuses).status is expected


def test_empty_label_is_rejected() -> None:
    with pytest.raises(RuleViolation):
        make_target(label="   ")


def test_embedded_images_helper() -> None:
    target = make_target(image_statuses=[E, P, E, R])
    assert [i.id for i in target.embedded_images] == ["img_0", "img_2"]
