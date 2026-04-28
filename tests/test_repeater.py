from waterbag_inspection.repeater import RepeatDefectTracker


def test_repeat_tracker_marks_second_same_box_as_repeat(tmp_path):
    tracker = RepeatDefectTracker(str(tmp_path / "history.json"), iou_threshold=0.5, max_entries_per_camera=10)
    boxes = [{"x1": 10, "y1": 10, "x2": 40, "y2": 40, "label": "anomaly", "confidence": 0.8}]

    assert tracker.is_repeated(1, boxes) is False
    assert tracker.is_repeated(1, boxes) is True


def test_repeat_tracker_isolated_by_camera(tmp_path):
    tracker = RepeatDefectTracker(str(tmp_path / "history.json"), iou_threshold=0.5, max_entries_per_camera=10)
    boxes = [{"x1": 10, "y1": 10, "x2": 40, "y2": 40, "label": "anomaly", "confidence": 0.8}]

    assert tracker.is_repeated(1, boxes) is False
    assert tracker.is_repeated(2, boxes) is False


def test_repeat_tracker_isolated_by_namespace_scope(tmp_path):
    tracker = RepeatDefectTracker(
        str(tmp_path / "history.json"),
        iou_threshold=0.5,
        max_entries_per_camera=10,
        namespace="line-a",
    )
    boxes = [{"x1": 10, "y1": 10, "x2": 40, "y2": 40, "label": "anomaly", "confidence": 0.8}]

    assert tracker.is_repeated(1, boxes, scope="runtime") is False
    assert tracker.is_repeated(1, boxes, scope="replay") is False
    assert tracker.is_repeated(1, boxes, scope="runtime") is True
