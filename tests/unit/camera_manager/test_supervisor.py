import pytest

from specter.camera_manager.supervisor import next_restart_delay_seconds


@pytest.mark.parametrize(
    ("previous_delay_seconds", "run_duration_seconds", "expected_delay_seconds"),
    [
        (None, 0.0, 1.0),
        (1.0, 5.0, 2.0),
        (32.0, 5.0, 60.0),
        (60.0, 5.0, 60.0),
        (60.0, 300.0, 1.0),
    ],
)
def test_restart_delay_doubles_up_to_a_limit_and_resets_after_a_stable_run(
    previous_delay_seconds: float | None,
    run_duration_seconds: float,
    expected_delay_seconds: float,
) -> None:
    delay_seconds = next_restart_delay_seconds(previous_delay_seconds, run_duration_seconds)

    assert delay_seconds == expected_delay_seconds
