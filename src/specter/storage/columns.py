"""Conversions between Python values and the text stored in database columns."""

import json
from datetime import UTC, datetime
from typing import Any


def format_utc_timestamp(moment: datetime) -> str:
    """Returns the moment as fixed-width ISO 8601 text in UTC, which sorts in time order.

    Raises:
        ValueError: The moment has no timezone.
    """
    if moment.tzinfo is None:
        raise ValueError("stored timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def parse_utc_timestamp(text: str) -> datetime:
    """Returns the moment stored by ``format_utc_timestamp``."""
    return datetime.fromisoformat(text)


def dump_json(value: object) -> str:
    """Returns the value as compact JSON text."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def load_json(text: str) -> Any:
    """Returns the value stored by ``dump_json``."""
    return json.loads(text)
