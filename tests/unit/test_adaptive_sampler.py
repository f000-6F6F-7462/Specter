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
