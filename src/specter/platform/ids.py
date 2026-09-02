"""Identifier generation.

Prefixed, time-ordered ids: ``tgt_0192f1c9...`` — the prefix is a human hint, the
body is a UUIDv7 hex so ids sort by creation time (good b-tree locality).
"""

import re
import secrets
import time
from uuid import UUID

try:  # Python 3.14+
    from uuid import uuid7  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - dev fallback on 3.13

    def uuid7() -> UUID:
        ms = time.time_ns() // 1_000_000
        rand_a = secrets.randbits(12)
        rand_b = secrets.randbits(62)
        value = (ms & 0xFFFF_FFFF_FFFF) << 80
        value |= 0x7 << 76
        value |= rand_a << 64
        value |= 0b10 << 62
        value |= rand_b
        return UUID(int=value)


_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")


def new_id(prefix: str) -> str:
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r} (want ^[a-z][a-z0-9]{{0,15}}$)")
    return f"{prefix}_{uuid7().hex}"
