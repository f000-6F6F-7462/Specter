"""Confirms a track's identity from repeated similarities to watchlist targets."""

from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    """A watchlist target that vector search returned for one embedding of a track."""

    target_id: str
    watchlist_id: str
    similarity_ratio: float


@dataclass(frozen=True, slots=True)
class TrackMatchState:
    """What a track's recent samples said about its identity."""

    candidate_target_id: str | None = None
    recent_hit_flags: tuple[bool, ...] = ()
    smoothed_similarity_ratio: float = 0.0
    last_confirmed_monotonic_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """The outcome of evaluating one sample of a track."""

    is_confirmed: bool
    target_id: str | None = None
    watchlist_id: str | None = None
    similarity_ratio: float = 0.0
    margin_ratio: float = 0.0
    hit_count: int = 0


@dataclass(frozen=True, slots=True)
class IdentityMatchPolicy:
    """Decides when repeated similarities confirm a track as a watchlist target.

    A sample is a hit when its best target clears the threshold and beats the second-best target
    by at least the minimum margin. A match is confirmed once enough recent samples are hits and the
    smoothed similarity also clears the threshold, unless the track was confirmed within the
    cooldown.
    """

    threshold_ratio: float
    required_hit_count: int = 3
    window_sample_count: int = 5
    smoothing_factor: float = 0.4
    minimum_margin_ratio: float = 0.05
    cooldown_seconds: float = 45.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold_ratio <= 1.0:
            raise ValueError(f"threshold_ratio must be between 0 and 1, got {self.threshold_ratio}")
        if not 1 <= self.required_hit_count <= self.window_sample_count:
            raise ValueError("required_hit_count must be between 1 and window_sample_count")
        if not 0.0 < self.smoothing_factor <= 1.0:
            raise ValueError(f"smoothing_factor must be in (0, 1], got {self.smoothing_factor}")
        if not 0.0 <= self.minimum_margin_ratio <= 1.0:
            raise ValueError("minimum_margin_ratio must be between 0 and 1")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")

    def evaluate(
        self,
        state: TrackMatchState,
        candidates: Sequence[Candidate],
        now_monotonic_seconds: float,
    ) -> tuple[TrackMatchState, MatchDecision]:
        """Returns the updated track state and whether a match is confirmed."""
        ranked_candidates = rank_best_candidate_per_target(candidates)
        if not ranked_candidates:
            return state, MatchDecision(
                is_confirmed=False, similarity_ratio=state.smoothed_similarity_ratio
            )

        best_candidate = ranked_candidates[0]
        runner_up_similarity_ratio = (
            max(ranked_candidates[1].similarity_ratio, 0.0) if len(ranked_candidates) > 1 else 0.0
        )
        margin_ratio = max(best_candidate.similarity_ratio - runner_up_similarity_ratio, 0.0)

        # Similarities to different targets must not add up, so a new best target restarts the
        # history while the cooldown carries over.
        is_same_target = state.candidate_target_id == best_candidate.target_id
        previous_hit_flags = state.recent_hit_flags if is_same_target else ()
        is_hit = (
            best_candidate.similarity_ratio >= self.threshold_ratio
            and margin_ratio >= self.minimum_margin_ratio
        )
        recent_hit_flags = (*previous_hit_flags, is_hit)[-self.window_sample_count :]
        smoothed_similarity_ratio = (
            self.smoothing_factor * best_candidate.similarity_ratio
            + (1.0 - self.smoothing_factor) * state.smoothed_similarity_ratio
            if previous_hit_flags
            else best_candidate.similarity_ratio
        )
        hit_count = sum(recent_hit_flags)
        is_cooling_down = (
            state.last_confirmed_monotonic_seconds is not None
            and now_monotonic_seconds - state.last_confirmed_monotonic_seconds
            < self.cooldown_seconds
        )
        is_confirmed = (
            hit_count >= self.required_hit_count
            and smoothed_similarity_ratio >= self.threshold_ratio
            and not is_cooling_down
        )

        updated_state = TrackMatchState(
            candidate_target_id=best_candidate.target_id,
            recent_hit_flags=recent_hit_flags,
            smoothed_similarity_ratio=smoothed_similarity_ratio,
            last_confirmed_monotonic_seconds=(
                now_monotonic_seconds if is_confirmed else state.last_confirmed_monotonic_seconds
            ),
        )
        decision = MatchDecision(
            is_confirmed=is_confirmed,
            target_id=best_candidate.target_id,
            watchlist_id=best_candidate.watchlist_id,
            similarity_ratio=smoothed_similarity_ratio,
            margin_ratio=margin_ratio,
            hit_count=hit_count,
        )
        return updated_state, decision


def rank_best_candidate_per_target(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Returns each target's most similar candidate, most similar first.

    Vector search returns one candidate per reference image, so without this a target with several
    images would compete against itself for the margin.
    """
    best_candidates_by_target_id: dict[str, Candidate] = {}
    for candidate in candidates:
        current_best_candidate = best_candidates_by_target_id.get(candidate.target_id)
        if (
            current_best_candidate is None
            or candidate.similarity_ratio > current_best_candidate.similarity_ratio
        ):
            best_candidates_by_target_id[candidate.target_id] = candidate
    return sorted(
        best_candidates_by_target_id.values(),
        key=lambda candidate: candidate.similarity_ratio,
        reverse=True,
    )


def remove_ended_track_states(
    states_by_track_id: Mapping[int, TrackMatchState], active_track_ids: AbstractSet[int]
) -> dict[int, TrackMatchState]:
    """Returns the states of tracks that are still active, dropping those of ended tracks."""
    return {
        track_id: state
        for track_id, state in states_by_track_id.items()
        if track_id in active_track_ids
    }
