"""Shared fixtures and small object factories for the unit suite."""

from datetime import UTC, datetime

import numpy as np
import pytest

from specter.contracts import (
    BBoxModel,
    Dedup,
    DetectionInfo,
    MatchEventMessage,
    MatchInfo,
    StreamRef,
)
from specter.domain.catalog import (
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
    WatchlistKind,
)
from specter.domain.matching import Candidate, TrackMatchState
from specter.domain.vision import BBox
from specter.platform.clock import FrozenClock


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(mono=1_000.0)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(1234)


def make_watchlist(**overrides: object) -> Watchlist:
    defaults: dict[str, object] = {
        "id": "wl_test",
        "owner_id": "o_test",
        "name": "Test list",
        "type": TargetType.PERSON,
        "kind": WatchlistKind.BLACKLIST,
        "match_threshold": 0.78,
    }
    defaults.update(overrides)
    return Watchlist(**defaults)  # type: ignore[arg-type]


def make_image(status: ImageStatus = ImageStatus.PENDING, **overrides: object) -> ReferenceImage:
    defaults: dict[str, object] = {
        "id": "img_test",
        "blob_key": "blobs/o_test/faces/2026/01/img_test.jpg",
        "status": status,
    }
    defaults.update(overrides)
    return ReferenceImage(**defaults)  # type: ignore[arg-type]


def make_target(image_statuses: list[ImageStatus] | None = None, **overrides: object) -> Target:
    images = [make_image(status=s, id=f"img_{i}") for i, s in enumerate(image_statuses or [])]
    defaults: dict[str, object] = {
        "id": "tgt_test",
        "watchlist_id": "wl_test",
        "label": "John Doe",
        "type": TargetType.PERSON,
        "images": images,
    }
    defaults.update(overrides)
    return Target(**defaults)  # type: ignore[arg-type]


def make_candidate(similarity: float, target_id: str = "tgt_42") -> Candidate:
    return Candidate(target_id=target_id, similarity=similarity)


def fresh_state() -> TrackMatchState:
    return TrackMatchState()


def make_match_event_message(**overrides: object) -> MatchEventMessage:
    now = datetime(2026, 8, 31, 12, 34, 56, tzinfo=UTC)
    defaults: dict[str, object] = {
        "event_id": "evt_0192",
        "owner_id": "o_9c1f",
        "occurred_at": now,
        "stream": StreamRef(id="st_9", name="Lobby Cam 1", camera_id="cam-lobby-1"),
        "match": MatchInfo(
            watchlist_id="wl_7",
            watchlist_name="VIP Blacklist",
            kind="blacklist",
            target_id="tgt_42",
            target_label="John Doe",
            target_type="person",
            similarity=0.83,
            threshold=0.78,
            calibrated_confidence=0.91,
        ),
        "detection": DetectionInfo(
            **{"class": "person"},
            confidence=0.94,
            bbox=BBoxModel(x=340, y=120, w=88, h=210),
            frame_ts=now,
            frame_id=190233,
            track_id=5561,
        ),
        "dedup": Dedup(correlation_id="trk_5561", hit_count=4, window_ms=1200, first_seen_at=now),
    }
    defaults.update(overrides)
    return MatchEventMessage(**defaults)  # type: ignore[arg-type]


__all__ = [
    "BBox",
    "make_watchlist",
    "make_image",
    "make_target",
    "make_candidate",
    "fresh_state",
    "make_match_event_message",
]
