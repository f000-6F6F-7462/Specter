"""Pure pre/post-processing shared by YoloDetector and OnnxDetector — no ultralytics
import needed to exercise it."""

from types import SimpleNamespace

from specter.infrastructure.ml._ultralytics_common import detections_from_result, overrides


class TestOverrides:
    def test_both_none_is_empty(self) -> None:
        assert overrides(None, None) == {}

    def test_only_set_values_are_kept(self) -> None:
        assert overrides(0.5, None) == {"conf": 0.5}
        assert overrides(None, 0.4) == {"iou": 0.4}

    def test_both_set(self) -> None:
        assert overrides(0.5, 0.4) == {"conf": 0.5, "iou": 0.4}


def _fake_result(boxes_xyxy, confs, cls_ids, names) -> SimpleNamespace:
    boxes = SimpleNamespace(
        xyxy=SimpleNamespace(tolist=lambda: boxes_xyxy),
        conf=SimpleNamespace(tolist=lambda: confs),
        cls=SimpleNamespace(tolist=lambda: cls_ids),
    )
    return SimpleNamespace(boxes=boxes, names=names)


class TestDetectionsFromResult:
    def test_no_boxes_attribute_is_empty(self) -> None:
        assert detections_from_result(SimpleNamespace(boxes=None)) == []

    def test_converts_xyxy_to_bbox_xywh(self) -> None:
        result = _fake_result([[10.0, 20.0, 30.0, 50.0]], [0.9], [0.0], {0: "person"})
        [detection] = detections_from_result(result)
        assert detection.cls == "person"
        assert detection.confidence == 0.9
        assert (detection.bbox.x, detection.bbox.y, detection.bbox.w, detection.bbox.h) == (
            10,
            20,
            20,
            30,
        )

    def test_zero_area_box_still_has_positive_size(self) -> None:
        result = _fake_result([[5.0, 5.0, 5.0, 5.0]], [0.5], [0.0], {0: "person"})
        [detection] = detections_from_result(result)
        assert detection.bbox.w >= 1 and detection.bbox.h >= 1

    def test_multiple_detections_map_to_correct_class_names(self) -> None:
        result = _fake_result(
            [[0.0, 0.0, 10.0, 10.0], [0.0, 0.0, 10.0, 10.0]],
            [0.9, 0.8],
            [0.0, 1.0],
            {0: "person", 1: "car"},
        )
        classes = [d.cls for d in detections_from_result(result)]
        assert classes == ["person", "car"]
