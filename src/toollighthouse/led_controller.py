from __future__ import annotations

from .config import Inventory


def mask_for_detections(detections: list[dict], inventory: Inventory) -> int:
    mask = 0
    for detection in detections:
        category = inventory.by_id.get(detection.get("class_id"))
        if category and detection.get("validation") in {"confirmed", "simulated"}:
            mask |= 1 << category.led_index
    return mask
