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

    def __post_init__(self):
        if not isinstance(self.class_id, str) or not self.class_id.strip():
            raise ValueError("class_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.descriptions, (list, tuple)):
            raise ValueError("descriptions must be a list or tuple of strings")
        if any(not isinstance(description, str) or not description.strip() for description in self.descriptions):
            raise ValueError("descriptions must contain non-empty strings")
        object.__setattr__(self, "descriptions", tuple(self.descriptions))


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
        return cls([ToolCategory(c["class_id"], c["name"], c.get("location", "unconfirmed"), c["led_index"], c["pin"], c.get("descriptions", ())) for c in raw["categories"]], example=bool(raw.get("example", False)))


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
    classifier_profiles: dict[str, dict[str, float]] = field(default_factory=dict)
    gallery_mode: str = "text-only"
    text_weight: float = 0.5
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
        if self.gallery_mode not in {"text-only", "image-only", "hybrid"}:
            raise ValueError("gallery_mode must be text-only, image-only, or hybrid")
        if not math.isfinite(float(self.classifier_threshold)) or not -1 <= self.classifier_threshold <= 1:
            raise ValueError("classifier threshold must be finite and between -1 and 1")
        if not math.isfinite(float(self.classifier_margin)) or not 0 <= self.classifier_margin <= 2:
            raise ValueError("classifier margin must be finite and between 0 and 2")
        if not math.isfinite(float(self.text_weight)) or not 0 <= self.text_weight <= 1:
            raise ValueError("text_weight must be finite and between 0 and 1")
        if self.gallery_mode == "hybrid" and not 0 < self.text_weight < 1:
            raise ValueError("hybrid text_weight must be strictly between 0 and 1")
        if not isinstance(self.classifier_profiles, dict):
            raise ValueError("classifier_profiles must be a mapping")
        for mode, profile in self.classifier_profiles.items():
            if mode not in {"text-only", "image-only", "hybrid"} or not isinstance(profile, dict):
                raise ValueError("classifier_profiles keys must be gallery modes and values must be mappings")
            if set(profile) - {"threshold", "margin"}:
                raise ValueError("classifier profiles support only threshold and margin")
            threshold = profile.get("threshold", self.classifier_threshold)
            margin = profile.get("margin", self.classifier_margin)
            if not math.isfinite(float(threshold)) or not -1 <= float(threshold) <= 1:
                raise ValueError("profile threshold must be finite and between -1 and 1")
            if not math.isfinite(float(margin)) or not 0 <= float(margin) <= 2:
                raise ValueError("profile margin must be finite and between 0 and 2")
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

    def classifier_parameters(self) -> tuple[float, float]:
        profile = self.classifier_profiles.get(self.gallery_mode, {})
        return (
            float(profile.get("threshold", self.classifier_threshold)),
            float(profile.get("margin", self.classifier_margin)),
        )
