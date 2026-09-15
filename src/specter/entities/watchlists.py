"""Watchlists: named lists of targets that cameras look for."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from specter.entities.targets import TargetType
from specter.entities.validation import require_non_empty_text, require_ratio

DEFAULT_MATCH_THRESHOLD_RATIO = 0.78


class WatchlistKind(StrEnum):
    """What a match against the watchlist means to its owner."""

    WATCHLIST = "watchlist"
    BLACKLIST = "blacklist"


@dataclass(frozen=True, slots=True)
class Watchlist:
    """A named list of targets matched against every camera that uses it."""

    id: str
    owner_id: str
    name: str
    target_type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    match_threshold_ratio: float = DEFAULT_MATCH_THRESHOLD_RATIO
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_text(self.name, "watchlist name")
        require_ratio(self.match_threshold_ratio, "match_threshold_ratio")
