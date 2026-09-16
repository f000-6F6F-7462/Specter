import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.targets import EmbeddingModality, TargetType
from specter.entities.watchlists import Watchlist


def test_watchlist_is_rejected_when_name_is_empty() -> None:
    with pytest.raises(InvalidEntityError, match="watchlist name"):
        Watchlist(id="watchlist_1", owner_id="owner_alice", name="", target_type=TargetType.PERSON)


@pytest.mark.parametrize("threshold_ratio", [-0.1, 1.1])
def test_watchlist_is_rejected_when_threshold_is_outside_zero_to_one(
    threshold_ratio: float,
) -> None:
    with pytest.raises(InvalidEntityError, match="appearance_match_threshold_ratio"):
        Watchlist(
            id="watchlist_1",
            owner_id="owner_alice",
            name="Visitors",
            target_type=TargetType.PERSON,
            appearance_match_threshold_ratio=threshold_ratio,
        )


def test_each_modality_is_matched_by_its_own_threshold() -> None:
    watchlist = Watchlist(
        id="watchlist_1",
        owner_id="owner_alice",
        name="Wanted",
        target_type=TargetType.PERSON,
        face_match_threshold_ratio=0.4,
        appearance_match_threshold_ratio=0.8,
    )

    assert watchlist.match_threshold_ratio(EmbeddingModality.FACE) == 0.4
    assert watchlist.match_threshold_ratio(EmbeddingModality.APPEARANCE) == 0.8
