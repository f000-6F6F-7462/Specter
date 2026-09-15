import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.watchlists import TargetType, Watchlist


def test_watchlist_is_rejected_when_name_is_empty() -> None:
    with pytest.raises(InvalidEntityError, match="watchlist name"):
        Watchlist(id="watchlist_1", owner_id="owner_alice", name="", target_type=TargetType.PERSON)


@pytest.mark.parametrize("threshold_ratio", [-0.1, 1.1])
def test_watchlist_is_rejected_when_threshold_is_outside_zero_to_one(
    threshold_ratio: float,
) -> None:
    with pytest.raises(InvalidEntityError, match="match_threshold_ratio"):
        Watchlist(
            id="watchlist_1",
            owner_id="owner_alice",
            name="Visitors",
            target_type=TargetType.PERSON,
            match_threshold_ratio=threshold_ratio,
        )
