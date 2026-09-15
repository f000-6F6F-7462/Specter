"""Checks that entity constructors share."""

from collections.abc import Sequence

from specter.core.errors import InvalidEntityError


def require_non_empty_text(value: str, field_name: str) -> None:
    """Raises InvalidEntityError when the text is empty or only whitespace."""
    if not value.strip():
        raise InvalidEntityError(f"{field_name} must not be empty")


def require_ratio(value: float, field_name: str) -> None:
    """Raises InvalidEntityError when the value is outside 0 to 1."""
    if not 0.0 <= value <= 1.0:
        raise InvalidEntityError(f"{field_name} must be between 0 and 1, got {value}")


def require_positive(value: float, field_name: str) -> None:
    """Raises InvalidEntityError when the value is zero or negative."""
    if value <= 0:
        raise InvalidEntityError(f"{field_name} must be positive, got {value}")


def require_non_negative(value: float, field_name: str) -> None:
    """Raises InvalidEntityError when the value is negative."""
    if value < 0:
        raise InvalidEntityError(f"{field_name} must not be negative, got {value}")


def require_unique(values: Sequence[str], field_name: str) -> None:
    """Raises InvalidEntityError when a value appears more than once."""
    if len(set(values)) != len(values):
        raise InvalidEntityError(f"{field_name} must not contain duplicates")
