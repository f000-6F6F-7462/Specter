from datetime import UTC, datetime

import numpy as np

from specter.entities.cameras import SamplingMode, SamplingSettings
from specter.vision.frames import Frame
from specter.vision.sampling import FrameSampler, SamplingDecision

CAPTURED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
SOURCE_FPS = 30


def build_frame(presentation_time_seconds: float, *, intensity: int = 128) -> Frame:
    return Frame(
        camera_id="camera_1",
        sequence_number=round(presentation_time_seconds * SOURCE_FPS),
        presentation_time_seconds=presentation_time_seconds,
        captured_at=CAPTURED_AT,
        image=np.full((64, 64, 3), intensity, dtype=np.uint8),
    )


def test_admitted_frames_follow_target_rate_when_source_is_faster() -> None:
    sampler = FrameSampler(SamplingSettings(target_fps=10.0, is_motion_gating_enabled=False))

    decisions = [sampler.decide(build_frame(index / SOURCE_FPS)) for index in range(SOURCE_FPS)]

    assert decisions.count(SamplingDecision.PROCESS) == 10


def test_frame_is_skipped_when_nothing_moved_since_last_admitted_frame() -> None:
    sampler = FrameSampler(SamplingSettings(target_fps=10.0))

    first_decision = sampler.decide(build_frame(0.0))
    unchanged_decision = sampler.decide(build_frame(1.0))
    changed_decision = sampler.decide(build_frame(2.0, intensity=200))

    assert first_decision is SamplingDecision.PROCESS
    assert unchanged_decision is SamplingDecision.SKIP_WITHOUT_MOTION
    assert changed_decision is SamplingDecision.PROCESS


def test_adaptive_rate_backs_off_to_minimum_when_detection_is_too_slow() -> None:
    sampler = FrameSampler(SamplingSettings(target_fps=10.0, minimum_fps=3.0))

    sampler.adapt_to_detection_latency(detection_latency_p95_milliseconds=250.0)
    rate_after_first_backoff = sampler.effective_fps
    sampler.adapt_to_detection_latency(detection_latency_p95_milliseconds=250.0)

    assert rate_after_first_backoff == 5.0
    assert sampler.effective_fps == 3.0


def test_adaptive_rate_recovers_step_by_step_when_detection_is_fast_again() -> None:
    sampler = FrameSampler(SamplingSettings(target_fps=10.0, minimum_fps=3.0))
    sampler.adapt_to_detection_latency(detection_latency_p95_milliseconds=250.0)

    sampler.adapt_to_detection_latency(detection_latency_p95_milliseconds=20.0)

    assert sampler.effective_fps == 5.5


def test_fixed_rate_ignores_detection_latency_when_mode_is_fixed() -> None:
    sampler = FrameSampler(SamplingSettings(mode=SamplingMode.FIXED, target_fps=10.0))

    sampler.adapt_to_detection_latency(detection_latency_p95_milliseconds=500.0)

    assert sampler.effective_fps == 10.0


def test_frame_is_admitted_when_timestamps_start_over_after_reconnecting() -> None:
    sampler = FrameSampler(SamplingSettings(target_fps=10.0, is_motion_gating_enabled=False))

    before_reconnect = sampler.decide(build_frame(500.0))
    after_reconnect = sampler.decide(build_frame(0.2))

    assert before_reconnect is SamplingDecision.PROCESS
    assert after_reconnect is SamplingDecision.PROCESS
