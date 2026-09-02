"""The match-decision core — pure functions over per-track state.

A ``MatchPolicy`` folds each new similarity into ``TrackMatchState`` (mutated in
place) and returns a ``MatchDecision``. ``now`` is monotonic seconds supplied by the
caller's ``Clock``; policies never read the wall clock.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from specter.platform.errors import RuleViolation


@dataclass(frozen=True, slots=True)
class Candidate:
    target_id: str
    similarity: float


@dataclass(frozen=True, slots=True)
class MatchDecision:
    fire: bool
    similarity: float = 0.0
    target_id: str | None = None


@dataclass(slots=True)
class TrackMatchState:
    recent: deque[float] = field(default_factory=deque)
    ema: float = 0.0
    best_target_id: str | None = None
    fired_at: float | None = None


@runtime_checkable
class MatchPolicy(Protocol):
    def evaluate(
        self, state: TrackMatchState, candidate: Candidate | None, now: float
    ) -> MatchDecision: ...


@dataclass(frozen=True, slots=True)
class NofMPolicy:
    """Fire when ``need`` of the last ``window`` similarities clear ``threshold`` AND a
    smoothed (EMA) score also clears it AND the track is not within ``cooldown_s`` of a
    previous alert."""

    threshold: float
    need: int = 3
    window: int = 5
    ema_alpha: float = 0.4
    cooldown_s: float = 45.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise RuleViolation(f"threshold out of range: {self.threshold}")
        if self.need < 1 or self.window < 1:
            raise RuleViolation("need and window must be >= 1")
        if self.need > self.window:
            raise RuleViolation("need cannot exceed window")
        if not 0.0 < self.ema_alpha <= 1.0:
            raise RuleViolation(f"ema_alpha out of range: {self.ema_alpha}")
        if self.cooldown_s < 0:
            raise RuleViolation("cooldown_s must be >= 0")

    def evaluate(
        self, state: TrackMatchState, candidate: Candidate | None, now: float
    ) -> MatchDecision:
        if candidate is None:
            return MatchDecision(fire=False, similarity=state.ema, target_id=state.best_target_id)

        state.ema = self.ema_alpha * candidate.similarity + (1.0 - self.ema_alpha) * state.ema
        state.best_target_id = candidate.target_id
        state.recent.append(candidate.similarity)
        while len(state.recent) > self.window:
            state.recent.popleft()

        hits = sum(1 for s in state.recent if s >= self.threshold)
        cooling = state.fired_at is not None and (now - state.fired_at) < self.cooldown_s
        fire = hits >= self.need and state.ema >= self.threshold and not cooling
        if fire:
            state.fired_at = now
        return MatchDecision(fire=fire, similarity=state.ema, target_id=candidate.target_id)


@dataclass(frozen=True, slots=True)
class SimpleThresholdPolicy:
    """Single-sample threshold with a cooldown. Useful for tests and low-stakes lists."""

    threshold: float
    cooldown_s: float = 30.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise RuleViolation(f"threshold out of range: {self.threshold}")
        if self.cooldown_s < 0:
            raise RuleViolation("cooldown_s must be >= 0")

    def evaluate(
        self, state: TrackMatchState, candidate: Candidate | None, now: float
    ) -> MatchDecision:
        if candidate is None:
            return MatchDecision(fire=False, similarity=state.ema, target_id=state.best_target_id)
        state.ema = candidate.similarity
        state.best_target_id = candidate.target_id
        cooling = state.fired_at is not None and (now - state.fired_at) < self.cooldown_s
        fire = candidate.similarity >= self.threshold and not cooling
        if fire:
            state.fired_at = now
        return MatchDecision(
            fire=fire, similarity=candidate.similarity, target_id=candidate.target_id
        )
