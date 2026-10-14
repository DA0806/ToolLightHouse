from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolCategory:
    class_id: str
    name: str
    location: str
    led_index: int
    pin: int
    descriptions: tuple[str, ...] = ()


class Inventory:
    def __init__(self, categories: list[ToolCategory], example: bool = False):
        if len(categories) != 9:
            raise ValueError("inventory must contain exactly nine categories")
        ids = [c.class_id for c in categories]
        indexes = [c.led_index for c in categories]
        pins = [c.pin for c in categories]
        if len(set(ids)) != 9 or sorted(indexes) != list(range(9)) or sorted(pins) != list(range(2, 11)) or any(pin != index + 2 for pin, index in zip(pins, indexes)):
            raise ValueError("inventory requires unique class IDs, LED indices 0-8 and pin D2+DLED index")
        self.categories = tuple(categories)
        self.by_id = {c.class_id: c for c in categories}
        self.example = example

    @classmethod
    def example(cls) -> "Inventory":
        return cls([ToolCategory(f"tool_{i+1}", f"Tool {i+1}", "unconfirmed", i, i + 2) for i in range(9)], example=True)

    @classmethod
    def from_file(cls, path: str | Path) -> "Inventory":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([ToolCategory(c["class_id"], c["name"], c.get("location", "unconfirmed"), c["led_index"], c["pin"], tuple(c.get("descriptions", ()))) for c in raw["categories"]], example=bool(raw.get("example", False)))


@dataclass
class Settings:
    mode: str = "mock"
    pairing_key: str = ""
    auth_required: bool = True
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    session_ttl_seconds: float = 900.0
    lease_ttl_seconds: float = 10.0
    max_upload_bytes: int = 12 * 1024 * 1024
    max_pixels: int = 20_000_000
    inference_timeout_seconds: float = 30.0
    frame_max_age_seconds: float = 1.0
    scene_ttl_seconds: float = 2.0
    photo_retention_seconds: float = 30.0
    prediction_max_age_seconds: float = 1.0
    prediction_interval_seconds: float = 0.0
    batch_size: int = 9
    segmentation_min_area: int = 150
    segmentation_max_area: int = 1_000_000
    tracking_max_distance: float = 120.0
    tracking_max_missed: int = 8
    classifier_threshold: float = 0.72
    classifier_margin: float = 0.08
    text_weight: float = 0.0
    background_path: str | None = "data/calibration/background.png"
    roi_path: str | None = "data/calibration/roi.json"
    roi: tuple[int, int, int, int] | None = None
    inventory_path: str | None = None
    inventory_confirmed: bool = False
    gallery_path: str | None = None
    device: str = "auto"
    require_background: bool = False
    model_path: str = "models/embeddinggemma-2"
    inventory: Inventory = field(default_factory=Inventory.example)

    def __post_init__(self):
        if any(not math.isfinite(value) or value <= 0 for value in (self.scene_ttl_seconds, self.photo_retention_seconds)):
            raise ValueError("scene TTL and photo retention must be finite and positive")
        if self.mode not in {"mock", "real"}:
            raise ValueError("mode must be 'mock' or 'real'")
        if self.device not in {"auto", "cpu", "cuda"} and not self.device.startswith("cuda:"):
            raise ValueError("device must be auto, cpu, cuda, or cuda:N")
        if not 0 <= self.classifier_threshold <= 1 or not 0 <= self.classifier_margin <= 1:
            raise ValueError("classifier threshold and margin must be between 0 and 1")
        if self.prediction_max_age_seconds <= 0 or self.prediction_interval_seconds < 0 or self.batch_size <= 0:
            raise ValueError("prediction ages/interval and batch_size must be positive")
        if self.roi is not None and (len(self.roi) != 4 or any(int(v) < 0 for v in self.roi) or int(self.roi[2]) <= 0 or int(self.roi[3]) <= 0):
            raise ValueError("roi must be [x, y, width, height] with positive dimensions")

    @classmethod
    def from_file(cls, path: str | Path) -> "Settings":
        config_path = Path(path)
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        allowed = {field.name for field in __import__("dataclasses").fields(cls)} | {"inventory"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown settings fields: {sorted(unknown)}")
        data = {k: v for k, v in raw.items() if k != "inventory"}
        if "inventory" in raw:
            categories = [ToolCategory(**c) for c in raw["inventory"]["categories"]]
            data["inventory"] = Inventory(categories, example=bool(raw["inventory"].get("example", False)))
        settings = cls(**data)
        if settings.session_ttl_seconds <= 0 or settings.lease_ttl_seconds <= 0 or settings.max_upload_bytes <= 0 or settings.max_pixels <= 0 or not all(math.isfinite(float(value)) for value in (settings.classifier_threshold, settings.classifier_margin, settings.text_weight)):
            raise ValueError("session, lease, upload, pixel and classifier settings must be finite and positive where applicable")
        if settings.inventory_path:
            inventory_path = Path(settings.inventory_path)
            settings.inventory = Inventory.from_file(inventory_path if inventory_path.is_absolute() else config_path.parent / inventory_path)
        return settings
