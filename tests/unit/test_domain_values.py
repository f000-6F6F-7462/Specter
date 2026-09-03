from datetime import UTC, datetime

import pytest

from specter.core.errors import RuleViolation
from specter.domain.alerts import Alert, Disposition, MatchEvidence
from specter.domain.streams import (
    RegionOfInterest,
    SamplingConfig,
    StreamConfig,
    StreamProtocol,
    StreamSource,
)
from specter.domain.vision import BBox


class TestBBox:
    def test_derived_geometry(self) -> None:
        box = BBox(x=10, y=20, w=30, h=40)
        assert box.xyxy() == (10, 20, 40, 60)
        assert box.area == 1200

    def test_zero_size_is_rejected(self) -> None:
        with pytest.raises(RuleViolation):
            BBox(x=0, y=0, w=0, h=5)

    def test_clip_keeps_box_inside_frame(self) -> None:
        clipped = BBox(x=-5, y=-5, w=100, h=100).clip(width=50, height=40)
        assert clipped.x >= 0 and clipped.y >= 0
        assert clipped.x2 <= 50 and clipped.y2 <= 40

    def test_iou(self) -> None:
        a = BBox(0, 0, 10, 10)
        assert a.iou(a) == pytest.approx(1.0)
        assert a.iou(BBox(20, 20, 5, 5)) == 0.0
        assert a.iou(BBox(5, 0, 10, 10)) == pytest.approx(1 / 3)


class TestStreams:
    def test_valid_config_builds(self) -> None:
        cfg = StreamConfig(
            id="st_1",
            owner_id="o_1",
            name="Lobby",
            source=StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://x/y"),
            roi=[RegionOfInterest(0.1, 0.1, 0.8, 0.8)],
        )
        assert cfg.sampling.mode.value == "adaptive"

    @pytest.mark.parametrize(
        "roi",
        [(-0.1, 0, 0.5, 0.5), (0, 0, 0, 0.5), (0.6, 0.0, 0.5, 0.5), (0.0, 0.6, 0.5, 0.5)],
    )
    def test_bad_roi_is_rejected(self, roi: tuple[float, float, float, float]) -> None:
        with pytest.raises(RuleViolation):
            RegionOfInterest(*roi)

    def test_min_fps_cannot_exceed_target(self) -> None:
        with pytest.raises(RuleViolation):
            SamplingConfig(target_fps=5.0, min_fps=10.0)

    def test_empty_url_is_rejected(self) -> None:
        with pytest.raises(RuleViolation):
            StreamSource(protocol=StreamProtocol.RTSP, url="  ")


class TestAlert:
    def _alert(self) -> Alert:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        return Alert(
            id="evt_1",
            owner_id="o_1",
            stream_id="st_1",
            watchlist_id="wl_1",
            target_id="tgt_1",
            similarity=0.9,
            bbox=BBox(1, 1, 2, 2),
            track_id=7,
            frame_ts=now,
            created_at=now,
        )

    def test_resolve_sets_disposition_and_note(self) -> None:
        alert = self._alert()
        alert.resolve(Disposition.FALSE_POSITIVE, note="lighting")
        assert alert.disposition is Disposition.FALSE_POSITIVE
        assert alert.note == "lighting"

    def test_cannot_resolve_to_unreviewed(self) -> None:
        with pytest.raises(RuleViolation):
            self._alert().resolve(Disposition.UNREVIEWED)

    def test_default_evidence_is_empty(self) -> None:
        assert self._alert().evidence == MatchEvidence()
