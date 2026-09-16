import pytest

from specter.vision.identity_matching import (
    Candidate,
    IdentityMatchPolicy,
    MatchDecision,
    TrackMatchState,
    rank_best_candidate_per_target,
)

POLICY = IdentityMatchPolicy(threshold_ratio=0.78, cooldown_seconds=45.0)


def build_candidate(target_id: str, similarity_ratio: float) -> Candidate:
    return Candidate(
        target_id=target_id, watchlist_id="watchlist_visitors", similarity_ratio=similarity_ratio
    )


def evaluate_samples(
    samples: list[list[Candidate]],
    *,
    state: TrackMatchState | None = None,
    start_monotonic_seconds: float = 0.0,
) -> tuple[TrackMatchState, list[MatchDecision]]:
    current_state = state or TrackMatchState()
    decisions: list[MatchDecision] = []
    for sample_index, candidates in enumerate(samples):
        current_state, decision = POLICY.evaluate(
            current_state, candidates, start_monotonic_seconds + sample_index
        )
        decisions.append(decision)
    return current_state, decisions


def test_match_is_confirmed_on_third_hit_when_similarity_is_steady() -> None:
    samples = [[build_candidate("target_jane", 0.9), build_candidate("target_bob", 0.4)]] * 3

    _, decisions = evaluate_samples(samples)

    assert [decision.is_confirmed for decision in decisions] == [False, False, True]
    assert decisions[-1].target_id == "target_jane"
    assert decisions[-1].margin_ratio == pytest.approx(0.5)


def test_match_is_never_confirmed_when_second_target_is_within_margin() -> None:
    samples = [[build_candidate("target_jane", 0.9), build_candidate("target_twin", 0.88)]] * 5

    _, decisions = evaluate_samples(samples)

    assert not any(decision.is_confirmed for decision in decisions)


def test_images_of_the_same_target_do_not_compete_when_ranked() -> None:
    candidates = [
        build_candidate("target_jane", 0.9),
        build_candidate("target_jane", 0.89),
        build_candidate("target_bob", 0.5),
    ]

    ranked_candidates = rank_best_candidate_per_target(candidates)

    assert [candidate.target_id for candidate in ranked_candidates] == ["target_jane", "target_bob"]
    assert ranked_candidates[0].similarity_ratio == 0.9


def test_history_restarts_when_best_target_changes() -> None:
    jane_samples = [[build_candidate("target_jane", 0.9)]] * 2
    bob_samples = [[build_candidate("target_bob", 0.9)]] * 3

    _, decisions = evaluate_samples(jane_samples + bob_samples)

    assert [decision.is_confirmed for decision in decisions] == [False, False, False, False, True]
    assert decisions[-1].target_id == "target_bob"


def test_second_confirmation_waits_for_cooldown_when_track_keeps_matching() -> None:
    samples = [[build_candidate("target_jane", 0.9)]] * 3
    confirmed_state, _ = evaluate_samples(samples)

    _, decisions_during_cooldown = evaluate_samples(
        [[build_candidate("target_jane", 0.9)]], state=confirmed_state, start_monotonic_seconds=10.0
    )
    _, decisions_after_cooldown = evaluate_samples(
        [[build_candidate("target_jane", 0.9)]], state=confirmed_state, start_monotonic_seconds=60.0
    )

    assert not decisions_during_cooldown[0].is_confirmed
    assert decisions_after_cooldown[0].is_confirmed


def test_state_is_unchanged_when_there_are_no_candidates() -> None:
    state = TrackMatchState(candidate_target_id="target_jane", recent_hit_flags=(True,))

    updated_state, decision = POLICY.evaluate(state, [], now_monotonic_seconds=5.0)

    assert updated_state == state
    assert not decision.is_confirmed


def test_policy_is_rejected_when_required_hits_exceed_window() -> None:
    with pytest.raises(ValueError, match="required_hit_count"):
        IdentityMatchPolicy(threshold_ratio=0.78, required_hit_count=6, window_sample_count=5)
