import numpy as np

from specter.application.pipeline.stages import filter_detections
from specter.domain.streams import (
    RegionOfInterest,
    StreamConfig,
    StreamProtocol,
    StreamSource,
)
from specter.domain.vision import BBox, Detection, Frame


def _stream(roi: list[RegionOfInterest]) -> StreamConfig:
    return StreamConfig(
        id="st_1",
        owner_id="o_1",
        name="cam1",
        source=StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://x"),
        roi=roi,
    )


def _frame(width: int = 100, height: int = 100) -> Frame:
    return Frame(
        stream_id="st_1", seq=1, ts=0.0, image=np.zeros((height, width, 3), dtype=np.uint8)
    )


def _detection(x: int, y: int, w: int = 10, h: int = 10) -> Detection:
    return Detection(cls="person", confidence=0.9, bbox=BBox(x=x, y=y, w=w, h=h))


class TestRoiFiltering:
    def test_no_roi_keeps_everything(self) -> None:
        stream = _stream(roi=[])
        dets = [_detection(0, 0), _detection(90, 90)]
        out = filter_detections(dets, stream, _frame(), min_confidence=0.5)
        assert out == dets

    def test_detection_inside_roi_is_kept(self) -> None:
        # ROI covers the left half of the frame
        stream = _stream(roi=[RegionOfInterest(x=0.0, y=0.0, w=0.5, h=1.0)])
        inside = _detection(x=5, y=5, w=10, h=10)  # center (10, 10) -> in [0, 50]
        out = filter_detections([inside], stream, _frame(), min_confidence=0.5)
        assert out == [inside]

    def test_detection_outside_roi_is_dropped(self) -> None:
        stream = _stream(roi=[RegionOfInterest(x=0.0, y=0.0, w=0.5, h=1.0)])
        outside = _detection(x=80, y=80, w=10, h=10)  # center (85, 85) -> outside [0, 50]
        out = filter_detections([outside], stream, _frame(), min_confidence=0.5)
        assert out == []

    def test_detection_kept_if_inside_any_of_multiple_rois(self) -> None:
        stream = _stream(
            roi=[
                RegionOfInterest(x=0.0, y=0.0, w=0.2, h=0.2),
                RegionOfInterest(x=0.8, y=0.8, w=0.2, h=0.2),
            ]
        )
        top_left = _detection(x=5, y=5, w=4, h=4)
        bottom_right = _detection(x=85, y=85, w=4, h=4)
        middle = _detection(x=45, y=45, w=4, h=4)
        out = filter_detections(
            [top_left, bottom_right, middle], stream, _frame(), min_confidence=0.5
        )
        assert out == [top_left, bottom_right]

    def test_confidence_and_class_filters_still_apply_with_roi(self) -> None:
        stream = _stream(roi=[RegionOfInterest(x=0.0, y=0.0, w=1.0, h=1.0)])
        stream.detect_classes.append("person")
        low_conf = Detection(cls="person", confidence=0.1, bbox=BBox(x=5, y=5, w=10, h=10))
        wrong_class = Detection(cls="car", confidence=0.9, bbox=BBox(x=5, y=5, w=10, h=10))
        good = _detection(5, 5)
        out = filter_detections([low_conf, wrong_class, good], stream, _frame(), min_confidence=0.5)
        assert out == [good]
