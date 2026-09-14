"""Identifier generation.

Prefixed, time-ordered ids: ``tgt_0192f1c9...`` — the prefix is a human hint, the
body is a UUIDv7 hex so ids sort by creation time (good b-tree locality).
"""

import re
from uuid import uuid7

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")


def new_id(prefix: str) -> str:
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r} (want ^[a-z][a-z0-9]{{0,15}}$)")
    return f"{prefix}_{uuid7().hex}"
