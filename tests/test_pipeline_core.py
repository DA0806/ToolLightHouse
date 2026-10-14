import numpy as np

from toollighthouse.classifier import SimilarityClassifier
from toollighthouse.config import Inventory, Settings, ToolCategory
from toollighthouse.led_controller import mask_for_detections
from toollighthouse.segmentation import Segmenter
from toollighthouse.state_manager import StateManager
from toollighthouse.tracking import CentroidTracker


def scene(*rectangles):
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    for x, y, w, h in rectangles:
        image[y : y + h, x : x + w] = (220, 220, 220)
    return image


def test_segmenter_finds_multiple_separated_objects():
    detections = Segmenter(min_area=100).detect(scene((20, 30, 40, 50), (160, 80, 60, 70)))
    assert [d.bbox for d in detections] == [(20, 30, 40, 50), (160, 80, 60, 70)]


def test_tracker_keeps_ids_and_expires_missed_objects():
    tracker = CentroidTracker(max_missed=1, max_distance=80)
    first = tracker.update([(10, 10, 20, 20), (200, 10, 20, 20)])
    second = tracker.update([(15, 12, 20, 20)])
    assert second[0].track_id == first[0].track_id
    assert tracker.update([]) == []
    assert tracker.update([]) == []


def test_classifier_rejects_low_margin_and_state_hysteresis():
    classifier = SimilarityClassifier({"hammer": np.array([1.0, 0.0]), "saw": np.array([0.98, 0.2])}, threshold=0.9, margin=0.5)
    prediction = classifier.predict(np.array([1.0, 0.1]), track_id=1)
    assert prediction.class_id is None
    state = StateManager(confirmations=2, photo_confirmations=1)
    assert state.update(1, "hammer", valid=True, photo=False) is None
    assert state.update(1, "hammer", valid=True, photo=False) == "hammer"


def test_led_mask_deduplicates_categories_and_uses_complete_state():
    categories = [ToolCategory("hammer", "Hammer", "bench", 0, 2), ToolCategory("saw", "Saw", "bench", 1, 3)] + [ToolCategory(f"tool_{i}", f"Tool {i}", "bench", i, i + 2) for i in range(2, 9)]
    inventory = Inventory(categories)
    assert mask_for_detections([{"class_id": "hammer", "validation": "confirmed"}, {"class_id": "hammer", "validation": "confirmed"}, {"class_id": "saw", "validation": "confirmed"}], inventory) == 3


def test_real_segmentation_requires_matching_persisted_background():
    segmenter = Segmenter(min_area=10, require_background=True)
    try:
        segmenter.detect(scene((10, 10, 20, 20)))
    except RuntimeError as error:
        assert "background calibration" in str(error)
    else:
        raise AssertionError("real segmentation silently used an uncalibrated fallback")
    segmenter.set_background(np.zeros((20, 20, 3), dtype=np.uint8))
    try:
        segmenter.detect(scene((10, 10, 20, 20)))
    except ValueError as error:
        assert "dimensions" in str(error)
    else:
        raise AssertionError("mismatched calibration dimensions accepted")


def test_example_inventory_cannot_be_marked_as_hardware_ready():
    example = Inventory.example()
    assert example.example is True
    assert all(category.location == "unconfirmed" for category in example.categories)
    try:
        Inventory([ToolCategory(f"tool_{index}", "Tool", "confirmed", index, 10 - index) for index in range(9)])
    except ValueError as error:
        assert "pin" in str(error)
    else:
        raise AssertionError("non-bit-aligned inventory accepted")
