"""Identifiers for entities and messages."""

import re
from uuid import uuid4

IDENTIFIER_PREFIX_PATTERN = re.compile(r"[a-z]+(_[a-z]+)*")


def new_identifier(prefix: str) -> str:
    """Returns a random identifier that starts with the prefix, such as ``camera_4f9c…``.

    Identifiers contain only lowercase letters, digits and underscores, so they are also valid
    NATS subject tokens.

    Raises:
        ValueError: The prefix is not lowercase words joined by single underscores.
    """
    if not IDENTIFIER_PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError(f"identifier prefix {prefix!r} must be lowercase words joined by '_'")
    return f"{prefix}_{uuid4().hex}"
