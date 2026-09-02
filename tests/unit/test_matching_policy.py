from collections.abc import Sequence

import pytest

from specter.domain.matching import (
    MatchPolicy,
    NofMPolicy,
    SimpleThresholdPolicy,
    TrackMatchState,
)
from specter.platform.errors import RuleViolation
from tests.conftest import make_candidate


def run(
    policy: MatchPolicy,
    sims: Sequence[float | None],
    nows: Sequence[float] | None = None,
) -> list[bool]:
    state = TrackMatchState()
    nows = list(nows) if nows is not None else [float(i) for i in range(len(sims))]
    fired: list[bool] = []
    for sim, now in zip(sims, nows, strict=True):
        cand = None if sim is None else make_candidate(sim)
        fired.append(policy.evaluate(state, cand, now).fire)
    return fired


class TestNofMPolicyValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"threshold": 1.5},
            {"threshold": -0.1},
            {"threshold": 0.8, "need": 0},
            {"threshold": 0.8, "window": 0},
            {"threshold": 0.8, "need": 6, "window": 5},
            {"threshold": 0.8, "ema_alpha": 0.0},
            {"threshold": 0.8, "ema_alpha": 1.1},
            {"threshold": 0.8, "cooldown_s": -1.0},
        ],
    )
    def test_rejects_bad_config(self, kwargs: dict[str, float | int]) -> None:
        with pytest.raises(RuleViolation):
            NofMPolicy(**kwargs)  # type: ignore[arg-type]


class TestNofMPolicyBehaviour:
    def test_all_below_threshold_never_fires(self) -> None:
        policy = NofMPolicy(threshold=0.8, need=3, window=5)
        assert run(policy, [0.1] * 12) == [False] * 12

    def test_ema_gate_blocks_early_burst(self) -> None:
        # Three 0.9 hits satisfy N-of-M but the EMA has not caught up yet.
        policy = NofMPolicy(threshold=0.8, need=3, window=5, ema_alpha=0.4)
        assert run(policy, [0.9, 0.9, 0.9]) == [False, False, False]

    def test_fires_once_when_ema_and_count_both_clear(self) -> None:
        policy = NofMPolicy(threshold=0.8, need=3, window=5, ema_alpha=0.4)
        fired = run(policy, [0.9] * 6)
        assert fired == [False, False, False, False, True, False]
        assert sum(fired) == 1

    def test_cooldown_suppresses_then_allows(self) -> None:
        policy = NofMPolicy(threshold=0.8, need=3, window=5, ema_alpha=0.4, cooldown_s=45.0)
        sims = [0.9] * 12
        nows = [0, 1, 2, 3, 4, 10, 20, 30, 40, 48, 48.5, 100.0]
        fired = run(policy, sims, nows)
        assert fired[4] is True  # first fire at t=4
        assert not any(fired[5:11])  # every later sample is within 45s of t=4
        assert fired[11] is True  # t=100 is clear of cooldown

    def test_none_candidate_is_a_noop(self) -> None:
        policy = NofMPolicy(threshold=0.8, need=1, window=1)
        state = TrackMatchState()
        decision = policy.evaluate(state, None, now=0.0)
        assert decision.fire is False
        assert state.ema == 0.0
        assert len(state.recent) == 0

    def test_window_slides(self) -> None:
        # need=3 window=3: once an early low value slides out, three highs in a row fire.
        policy = NofMPolicy(threshold=0.8, need=3, window=3, ema_alpha=0.9)
        fired = run(policy, [0.1, 0.95, 0.95, 0.95, 0.95])
        assert fired == [False, False, False, True, False]


class TestSimpleThresholdPolicy:
    def test_fires_on_first_clearing_sample(self) -> None:
        policy = SimpleThresholdPolicy(threshold=0.7, cooldown_s=10.0)
        fired = run(policy, [0.5, 0.75, 0.8, 0.9], nows=[0, 1, 2, 20.0])
        assert fired == [False, True, False, True]

    def test_validation(self) -> None:
        with pytest.raises(RuleViolation):
            SimpleThresholdPolicy(threshold=2.0)
