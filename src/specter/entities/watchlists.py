"""Watchlists: named lists of targets that cameras look for."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from specter.entities.targets import EmbeddingModality, TargetType
from specter.entities.validation import require_non_empty_text, require_ratio

# ArcFace scores different photos of one person lower than OSNet scores two views of one outfit, so
# each modality needs its own threshold.
DEFAULT_FACE_MATCH_THRESHOLD_RATIO = 0.45
DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO = 0.75


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
    face_match_threshold_ratio: float = DEFAULT_FACE_MATCH_THRESHOLD_RATIO
    appearance_match_threshold_ratio: float = DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty_text(self.name, "watchlist name")
        require_ratio(self.face_match_threshold_ratio, "face_match_threshold_ratio")
        require_ratio(self.appearance_match_threshold_ratio, "appearance_match_threshold_ratio")

    def match_threshold_ratio(self, modality: EmbeddingModality) -> float:
        """Returns the similarity that confirms a match by the given kind of embedding."""
        match modality:
            case EmbeddingModality.FACE:
                return self.face_match_threshold_ratio
            case EmbeddingModality.APPEARANCE:
                return self.appearance_match_threshold_ratio
