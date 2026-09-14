import numpy as np

from specter.application.pipeline.sampling import AdaptiveSampler, SampleOutcome
from specter.domain.streams import SamplingConfig
from specter.domain.vision import Frame


def _frame(seq: int, ts: float, *, fill: int = 128) -> Frame:
    return Frame(
        stream_id="s1",
        seq=seq,
        ts=ts,
        image=np.full((48, 48, 3), fill, dtype=np.uint8),
    )


def test_rate_cap_drops_frames_faster_than_target_fps() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=10.0, motion_gating=False))

    assert sampler.classify(_frame(0, 0.00)) is SampleOutcome.PROCESS
    assert sampler.classify(_frame(1, 0.05)) is SampleOutcome.DROP_RATE
    assert sampler.classify(_frame(2, 0.10)) is SampleOutcome.PROCESS
    assert sampler.stats.admitted == 2
    assert sampler.stats.dropped_rate == 1


def test_motion_gate_skips_still_frames() -> None:
    sampler = AdaptiveSampler(
        SamplingConfig(target_fps=100.0, motion_gating=True), motion_min_delta=2.0
    )

    assert sampler.classify(_frame(0, 0.0, fill=100)) is SampleOutcome.PROCESS
    assert sampler.classify(_frame(1, 1.0, fill=100)) is SampleOutcome.DROP_MOTION
    assert sampler.classify(_frame(2, 2.0, fill=100)) is SampleOutcome.DROP_MOTION
    # a big pixel change gets through
    assert sampler.classify(_frame(3, 3.0, fill=200)) is SampleOutcome.PROCESS
    assert sampler.stats.dropped_motion == 2
    assert sampler.stats.admitted == 2


def test_motion_gate_off_admits_still_frames() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=100.0, motion_gating=False))
    outcomes = [sampler.classify(_frame(i, i)) for i in range(4)]
    assert outcomes == [SampleOutcome.PROCESS] * 4


def test_adjust_for_latency_backs_off_multiplicatively_when_over_budget() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=10.0, min_fps=2.0, motion_gating=False))
    assert sampler.effective_fps == 10.0
    sampler.adjust_for_latency(150.0)  # budget at 10fps is 100ms; 150ms is over it
    assert sampler.effective_fps == 5.0  # default decrease_factor halves it


def test_adjust_for_latency_never_drops_below_min_fps() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=10.0, min_fps=2.0, motion_gating=False))
    for _ in range(10):
        sampler.adjust_for_latency(1_000.0)  # always over budget
    assert sampler.effective_fps == 2.0


def test_adjust_for_latency_recovers_additively_when_under_budget() -> None:
    sampler = AdaptiveSampler(
        SamplingConfig(target_fps=10.0, min_fps=2.0, motion_gating=False), increase_fps=1.0
    )
    sampler.adjust_for_latency(1_000.0)
    assert sampler.effective_fps == 5.0
    sampler.adjust_for_latency(1.0)  # comfortably under budget now
    assert sampler.effective_fps == 6.0


def test_adjust_for_latency_never_exceeds_target_fps() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=10.0, min_fps=2.0, motion_gating=False))
    for _ in range(50):
        sampler.adjust_for_latency(0.0)  # always comfortably under budget
    assert sampler.effective_fps == 10.0


def test_backing_off_lowers_the_rate_cap() -> None:
    sampler = AdaptiveSampler(SamplingConfig(target_fps=10.0, min_fps=2.0, motion_gating=False))
    sampler.adjust_for_latency(1_000.0)  # -> 5.0 effective fps, i.e. a 0.2s gap

    assert sampler.classify(_frame(0, 0.00)) is SampleOutcome.PROCESS
    assert sampler.classify(_frame(1, 0.10)) is SampleOutcome.DROP_RATE  # ok at 10fps, not at 5
    assert sampler.classify(_frame(2, 0.20)) is SampleOutcome.PROCESS
