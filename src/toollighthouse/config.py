from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAFE_CLASS_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
PORTABLE_SETTINGS_FIELDS = {
    "mode",
    "gallery_mode",
    "text_weight",
    "classifier_threshold",
    "classifier_margin",
    "classifier_profiles",
    "roi",
    "inventory_confirmed",
    "segmentation_min_area",
    "segmentation_max_area",
    "tracking_max_distance",
    "tracking_max_missed",
    "batch_size",
    "frame_max_age_seconds",
    "scene_ttl_seconds",
    "photo_retention_seconds",
    "prediction_max_age_seconds",
    "prediction_interval_seconds",
    "calibration_profiles",
    "camera_sources",
}


@dataclass
class CalibrationProfile:
    source_id: str
    resolution: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 720})
    orientation: str = "normal"          # "normal" | "rotate_90" | "rotate_180" | "rotate_270"
    crop: tuple[int, int, int, int] | None = None
    processing_version: int = 2
    background_filename: str | None = None
    roi: tuple[int, int, int, int] | None = None
    calibrated_at: float = 0.0
    status: str = "needs_calibration"   # "ready" | "needs_calibration" | "incompatible" | "needs_confirmation"

    def __post_init__(self):
        if self.orientation not in {"normal", "rotate_90", "rotate_180", "rotate_270"}:
            raise ValueError("calibration orientation is invalid")
        if not isinstance(self.resolution, dict) or any(
            isinstance(self.resolution.get(key), bool) or not isinstance(self.resolution.get(key), int) or self.resolution.get(key) <= 0
            for key in ("width", "height")
        ):
            raise ValueError("calibration resolution must contain positive width and height")
        for name, value in (("crop", self.crop), ("roi", self.roi)):
            if value is not None and (len(value) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in value) or value[2] <= 0 or value[3] <= 0):
                raise ValueError(f"calibration {name} must be [x, y, width, height]")
        if self.processing_version <= 0:
            raise ValueError("calibration processing_version must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "resolution": dict(self.resolution),
            "orientation": self.orientation,
            "crop": list(self.crop) if self.crop else None,
            "processing_version": self.processing_version,
            "background_filename": self.background_filename,
            "roi": list(self.roi) if self.roi else None,
            "calibrated_at": self.calibrated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationProfile":
        crop_raw = data.get("crop")
        crop = tuple(int(v) for v in crop_raw) if crop_raw and len(crop_raw) == 4 else None
        roi_raw = data.get("roi")
        roi = tuple(int(v) for v in roi_raw) if roi_raw and len(roi_raw) == 4 else None
        return cls(
            source_id=str(data.get("source_id", "unknown")),
            resolution=dict(data.get("resolution", {"width": 1280, "height": 720})),
            orientation=str(data.get("orientation", "normal")),
            crop=crop,
            processing_version=int(data.get("processing_version", 2)),
            background_filename=data.get("background_filename"),
            roi=roi,
            calibrated_at=float(data.get("calibrated_at", 0.0)),
            status=str(data.get("status", "needs_calibration")),
        )


def apply_canonical_transform(
    raw_frame: Any,
    orientation: str = "normal",
    crop: tuple[int, int, int, int] | None = None,
) -> Any:
    """
    Applies identical spatial normalization (rotation + crop) to both
    empty tray calibration backgrounds and live/photo inference frames.
    """
    if raw_frame is None:
        return None
    import cv2
    frame = raw_frame
    if orientation == "rotate_90":
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif orientation == "rotate_180":
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif orientation == "rotate_270":
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if crop is not None and len(crop) == 4:
        x, y, w, h = crop
        h_img, w_img = frame.shape[:2]
        x1 = max(0, min(x, w_img - 1))
        y1 = max(0, min(y, h_img - 1))
        x2 = max(x1 + 1, min(x + w, w_img))
        y2 = max(y1 + 1, min(y + h, h_img))
        frame = frame[y1:y2, x1:x2]
    return frame



@dataclass(frozen=True)
class ToolCategory:
    class_id: str
    name: str
    location: str = "unconfirmed"
    led_index: int = 0
    pin: int = 2
    descriptions: tuple[str, ...] = ()
    display_name: str | None = None
    reference_count: int = 0

    def __post_init__(self):
        if not isinstance(self.class_id, str) or not SAFE_CLASS_ID_REGEX.fullmatch(self.class_id.strip()):
            raise ValueError(f"class_id must be a filesystem-safe identifier (1-64 alphanumeric, underscore or hyphen): got {self.class_id!r}")
        if self.class_id in {".", ".."}:
            raise ValueError("class_id cannot be '.' or '..'")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if self.display_name is not None and (not isinstance(self.display_name, str) or not self.display_name.strip()):
            raise ValueError("display_name when provided must be a non-empty string")
        if not isinstance(self.location, str):
            raise ValueError("location must be a string")
        if isinstance(self.led_index, bool) or not isinstance(self.led_index, int):
            raise ValueError("led_index must be an integer")
        if isinstance(self.pin, bool) or not isinstance(self.pin, int):
            raise ValueError("pin must be an integer")
        if not isinstance(self.descriptions, (list, tuple)):
            raise ValueError("descriptions must be a list or tuple of strings")
        if any(not isinstance(description, str) or not description.strip() for description in self.descriptions):
            raise ValueError("descriptions must contain non-empty strings")
        if isinstance(self.reference_count, bool) or not isinstance(self.reference_count, int) or self.reference_count < 0:
            raise ValueError("reference_count must be a non-negative integer")
        object.__setattr__(self, "descriptions", tuple(str(d).strip() for d in self.descriptions))

    @property
    def effective_display_name(self) -> str:
        if self.display_name is not None and self.display_name.strip():
            return self.display_name.strip()
        return self.name

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "class_id": self.class_id,
            "name": self.name,
            "location": self.location,
            "led_index": self.led_index,
            "pin": self.pin,
            "descriptions": list(self.descriptions),
        }
        if self.display_name is not None:
            data["display_name"] = self.display_name
        if self.reference_count:
            data["reference_count"] = self.reference_count
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCategory":
        if not isinstance(data, dict):
            raise ValueError("ToolCategory data must be a JSON object")
        allowed = {"class_id", "name", "location", "led_index", "pin", "descriptions", "display_name", "reference_count"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown ToolCategory fields: {sorted(unknown)}")
        if "class_id" not in data or not isinstance(data["class_id"], str):
            raise ValueError("class_id is required and must be a string")
        if "name" not in data or not isinstance(data["name"], str):
            raise ValueError("name is required and must be a string")
        if "led_index" not in data or isinstance(data["led_index"], bool) or not isinstance(data["led_index"], int):
            raise ValueError("led_index is required and must be an integer")
        if "pin" not in data or isinstance(data["pin"], bool) or not isinstance(data["pin"], int):
            raise ValueError("pin is required and must be an integer")
        return cls(
            class_id=data["class_id"],
            name=data["name"],
            location=data.get("location", "unconfirmed"),
            led_index=data["led_index"],
            pin=data["pin"],
            descriptions=data.get("descriptions", ()),
            display_name=data.get("display_name"),
            reference_count=data.get("reference_count", 0),
        )


class Inventory:
    def __init__(self, categories: list[ToolCategory] | tuple[ToolCategory, ...], example: bool = False):
        if len(categories) != 9:
            raise ValueError("inventory must contain exactly nine categories")
        ids = [c.class_id for c in categories]
        indexes = [c.led_index for c in categories]
        pins = [c.pin for c in categories]
        if (
            len(set(ids)) != 9
            or sorted(indexes) != list(range(9))
            or sorted(pins) != list(range(2, 11))
            or any(pin != index + 2 for pin, index in zip(pins, indexes))
        ):
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
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Inventory":
        if "categories" not in raw or not isinstance(raw["categories"], list):
            raise ValueError("inventory must contain a categories list")
        categories = [ToolCategory.from_dict(c) for c in raw["categories"]]
        return cls(categories, example=bool(raw.get("example", False)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "example": self.example,
            "categories": [c.to_dict() for c in self.categories],
        }


@dataclass
class Settings:
    mode: str = "mock"
    pairing_key: str = ""
    admin_key: str = ""
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
    calibration_profiles: dict[str, CalibrationProfile] = field(default_factory=dict)
    camera_sources: dict[str, Any] = field(default_factory=dict)
    inventory: Inventory = field(default_factory=Inventory.example)

    def __post_init__(self):
        for name, value in (
            ("scene_ttl_seconds", self.scene_ttl_seconds),
            ("photo_retention_seconds", self.photo_retention_seconds),
            ("session_ttl_seconds", self.session_ttl_seconds),
            ("lease_ttl_seconds", self.lease_ttl_seconds),
            ("inference_timeout_seconds", self.inference_timeout_seconds),
            ("frame_max_age_seconds", self.frame_max_age_seconds),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.mode not in {"mock", "real", "preparation"}:
            raise ValueError("mode must be 'mock', 'real', or 'preparation'")
        if self.device not in {"auto", "cpu", "cuda"} and not self.device.startswith("cuda:"):
            raise ValueError("device must be auto, cpu, cuda, or cuda:N")
        if self.gallery_mode not in {"text-only", "image-only", "hybrid"}:
            raise ValueError("gallery_mode must be text-only, image-only, or hybrid")
        if not isinstance(self.classifier_threshold, (int, float)) or isinstance(self.classifier_threshold, bool) or not math.isfinite(float(self.classifier_threshold)) or not -1 <= self.classifier_threshold <= 1:
            raise ValueError("classifier threshold must be finite and between -1 and 1")
        if not isinstance(self.classifier_margin, (int, float)) or isinstance(self.classifier_margin, bool) or not math.isfinite(float(self.classifier_margin)) or not 0 <= self.classifier_margin <= 2:
            raise ValueError("classifier margin must be finite and between 0 and 2")
        if not isinstance(self.text_weight, (int, float)) or isinstance(self.text_weight, bool) or not math.isfinite(float(self.text_weight)) or not 0 <= self.text_weight <= 1:
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
            if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not math.isfinite(float(threshold)) or not -1 <= float(threshold) <= 1:
                raise ValueError("profile threshold must be finite and between -1 and 1")
            if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not math.isfinite(float(margin)) or not 0 <= float(margin) <= 2:
                raise ValueError("profile margin must be finite and between 0 and 2")
        if self.prediction_max_age_seconds <= 0 or self.prediction_interval_seconds < 0 or self.batch_size <= 0:
            raise ValueError("prediction ages/interval and batch_size must be positive")
        for name, value in (
            ("max_upload_bytes", self.max_upload_bytes),
            ("max_pixels", self.max_pixels),
            ("batch_size", self.batch_size),
            ("segmentation_min_area", self.segmentation_min_area),
            ("segmentation_max_area", self.segmentation_max_area),
            ("tracking_max_missed", self.tracking_max_missed),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.segmentation_min_area > self.segmentation_max_area:
            raise ValueError("segmentation_min_area must not exceed segmentation_max_area")
        if isinstance(self.tracking_max_distance, bool) or not isinstance(self.tracking_max_distance, (int, float)) or not math.isfinite(float(self.tracking_max_distance)) or self.tracking_max_distance <= 0:
            raise ValueError("tracking_max_distance must be a finite positive number")
        if self.roi is not None:
            if len(self.roi) != 4 or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in self.roi) or int(self.roi[2]) <= 0 or int(self.roi[3]) <= 0:
                raise ValueError("roi must be [x, y, width, height] with positive integer dimensions")
            self.roi = tuple(int(v) for v in self.roi)
        if isinstance(self.cors_origins, list):
            self.cors_origins = tuple(self.cors_origins)

    @classmethod
    def from_file(cls, path: str | Path) -> "Settings":
        config_path = Path(path)
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        if "schema_version" in raw and "settings" in raw:
            envelope_settings = raw["settings"]
            envelope_inv = raw.get("inventory")
            settings = cls.from_dict(envelope_settings)
            if envelope_inv:
                settings.inventory = Inventory.from_dict(envelope_inv)
            return settings
        return cls.from_dict(raw, base_path=config_path.parent)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], base_path: Path | None = None) -> "Settings":
        allowed = {f.name for f in __import__("dataclasses").fields(cls)} | {"inventory"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown settings fields: {sorted(unknown)}")
        data = {k: v for k, v in raw.items() if k != "inventory"}
        if "inventory" in raw and isinstance(raw["inventory"], dict):
            categories = [ToolCategory.from_dict(c) for c in raw["inventory"]["categories"]]
            data["inventory"] = Inventory(categories, example=bool(raw["inventory"].get("example", False)))

        if "calibration_profiles" in raw and isinstance(raw["calibration_profiles"], dict):
            data["calibration_profiles"] = {
                k: v if isinstance(v, CalibrationProfile) else CalibrationProfile.from_dict(v)
                for k, v in raw["calibration_profiles"].items()
            }
        elif ("background_path" in raw and raw["background_path"]) or ("roi" in raw and raw["roi"]):
            bg_p = raw.get("background_path")
            bg_fn = Path(bg_p).name if bg_p else None
            roi_raw = raw.get("roi")
            roi_val = tuple(int(v) for v in roi_raw) if roi_raw and len(roi_raw) == 4 else None
            data["calibration_profiles"] = {
                "unbound_legacy": CalibrationProfile(
                    source_id="unbound_legacy",
                    resolution={"width": 1280, "height": 720},
                    orientation="normal",
                    background_filename=bg_fn,
                    roi=roi_val,
                    status="needs_confirmation",
                )
            }

        settings = cls(**data)
        if settings.session_ttl_seconds <= 0 or settings.lease_ttl_seconds <= 0 or settings.max_upload_bytes <= 0 or settings.max_pixels <= 0 or not all(math.isfinite(float(value)) for value in (settings.classifier_threshold, settings.classifier_margin, settings.text_weight)):
            raise ValueError("session, lease, upload, pixel and classifier settings must be finite and positive where applicable")
        if settings.inventory_path:
            inv_p = Path(settings.inventory_path)
            resolved = inv_p if inv_p.is_absolute() or base_path is None else base_path / inv_p
            if resolved.exists():
                settings.inventory = Inventory.from_file(resolved)
        return settings

    def classifier_parameters(self) -> tuple[float, float]:
        profile = self.classifier_profiles.get(self.gallery_mode, {})
        return (
            float(profile.get("threshold", self.classifier_threshold)),
            float(profile.get("margin", self.classifier_margin)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "pairing_key": self.pairing_key,
            "admin_key": self.admin_key,
            "auth_required": self.auth_required,
            "cors_origins": list(self.cors_origins),
            "session_ttl_seconds": self.session_ttl_seconds,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "max_upload_bytes": self.max_upload_bytes,
            "max_pixels": self.max_pixels,
            "inference_timeout_seconds": self.inference_timeout_seconds,
            "frame_max_age_seconds": self.frame_max_age_seconds,
            "scene_ttl_seconds": self.scene_ttl_seconds,
            "photo_retention_seconds": self.photo_retention_seconds,
            "prediction_max_age_seconds": self.prediction_max_age_seconds,
            "prediction_interval_seconds": self.prediction_interval_seconds,
            "batch_size": self.batch_size,
            "segmentation_min_area": self.segmentation_min_area,
            "segmentation_max_area": self.segmentation_max_area,
            "tracking_max_distance": self.tracking_max_distance,
            "tracking_max_missed": self.tracking_max_missed,
            "classifier_threshold": self.classifier_threshold,
            "classifier_margin": self.classifier_margin,
            "classifier_profiles": self.classifier_profiles,
            "gallery_mode": self.gallery_mode,
            "text_weight": self.text_weight,
            "background_path": self.background_path,
            "roi_path": self.roi_path,
            "roi": list(self.roi) if self.roi else None,
            "inventory_path": self.inventory_path,
            "inventory_confirmed": self.inventory_confirmed,
            "gallery_path": self.gallery_path,
            "device": self.device,
            "require_background": self.require_background,
            "model_path": self.model_path,
            "calibration_profiles": {
                k: v.to_dict() if hasattr(v, "to_dict") else v
                for k, v in self.calibration_profiles.items()
            },
            "camera_sources": self.camera_sources,
            "inventory": self.inventory.to_dict(),
        }


class ConfigStore:
    """Manages an atomic, versioned configuration envelope on disk with backup and recovery."""

    def __init__(self, target_path: str | Path):
        self.target_path = Path(target_path)
        self.backup_path = self.target_path.with_name(f"{self.target_path.stem}.backup.json")
        self.version = 1
        self.active_snapshot: tuple[Settings, Inventory, int] | None = None

    @staticmethod
    def _copy_settings(settings: Settings, inventory: Inventory) -> tuple[Settings, Inventory]:
        copied_inventory = Inventory.from_dict(inventory.to_dict())
        copied_settings = Settings.from_dict(settings.to_dict())
        copied_settings.inventory = copied_inventory
        return copied_settings, copied_inventory

    def save_atomic(self, settings: Settings, inventory: Inventory) -> int:
        next_version = int(self.version) + 1
        self.target_path.parent.mkdir(parents=True, exist_ok=True)

        envelope = {
            "schema_version": 1,
            "config_version": next_version,
            "updated_at": time.time(),
            "settings": settings.to_dict(),
            "inventory": inventory.to_dict(),
        }
        if self.active_snapshot is not None:
            active_settings, active_inventory, active_version = self.active_snapshot
            envelope.update({
                "active_settings": active_settings.to_dict(),
                "active_inventory": active_inventory.to_dict(),
                "active_version": int(active_version),
            })

        # Prepare a known-good backup before replacing the primary.  On the
        # first edit there is no primary yet, so the active runtime snapshot is
        # the rollback baseline rather than the new draft being saved.
        backup_bytes: bytes | None = None
        if self.target_path.exists():
            try:
                current_bytes = self.target_path.read_bytes()
                current_raw = json.loads(current_bytes.decode("utf-8"))
                if not isinstance(current_raw, dict) or not current_raw.get("settings"):
                    raise ValueError("primary configuration is not a valid envelope")
                backup_bytes = current_bytes
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                # Never replace an existing known-good backup with a corrupt
                # primary.  A pre-existing backup remains the recovery source.
                if not self.backup_path.exists():
                    raise ValueError("cannot save configuration while the primary file is corrupt and no backup exists")
        elif not self.backup_path.exists():
            baseline_settings, baseline_inventory, baseline_version = self.active_snapshot or self._copy_settings(settings, inventory) + (int(self.version),)
            baseline_envelope = {
                "schema_version": 1,
                "config_version": int(baseline_version),
                "updated_at": time.time(),
                "settings": baseline_settings.to_dict(),
                "inventory": baseline_inventory.to_dict(),
            }
            backup_bytes = json.dumps(baseline_envelope, indent=2, allow_nan=False).encode("utf-8")

        backup_tmp: Path | None = None
        if backup_bytes is not None:
            backup_tmp = self.backup_path.with_name(f"{self.backup_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
            try:
                with open(backup_tmp, "wb") as handle:
                    handle.write(backup_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(backup_tmp, self.backup_path)
            except Exception:
                backup_tmp.unlink(missing_ok=True)
                raise

        tmp_name = f"{self.target_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        tmp_file = self.target_path.parent / tmp_name
        try:
            with open(tmp_file, "w", encoding="utf-8") as handle:
                json.dump(envelope, handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(tmp_file, self.target_path)
        except Exception:
            tmp_file.unlink(missing_ok=True)
            raise

        self.version = next_version
        return next_version

    def save_active_atomic(self, settings: Settings, inventory: Inventory, version: int) -> None:
        """Persist the last known-good runtime without changing the draft version."""
        active_settings, active_inventory = self._copy_settings(settings, inventory)
        if not self.target_path.exists():
            return
        tmp_file: Path | None = None
        try:
            payload = json.loads(self.target_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("configuration file must be a JSON object")
            payload.update({
                "active_settings": active_settings.to_dict(),
                "active_inventory": active_inventory.to_dict(),
                "active_version": int(version),
            })
            tmp_file = self.target_path.with_name(f"{self.target_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
            with open(tmp_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_file, self.target_path)
            self.active_snapshot = (active_settings, active_inventory, int(version))
        except Exception:
            if tmp_file is not None:
                tmp_file.unlink(missing_ok=True)
            raise

    def _load_file(self, path: Path, base_settings: Settings | None = None) -> tuple[Settings, Inventory, int]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration file must be a JSON object")
        version = int(raw.get("config_version", 1))
        settings_data = raw.get("settings", raw)
        inventory_data = raw.get("inventory") or settings_data.get("inventory")
        if not inventory_data:
            raise ValueError("configuration file contains no inventory")
        inventory = Inventory.from_dict(inventory_data)
        if base_settings is not None:
            merged = base_settings.to_dict()
            for key in PORTABLE_SETTINGS_FIELDS:
                if key in settings_data:
                    merged[key] = settings_data[key]
            if isinstance(settings_data.get("gallery_path"), str):
                merged["gallery_path"] = settings_data["gallery_path"]
            merged["inventory"] = inventory.to_dict()
            settings = Settings.from_dict(merged, base_path=path.parent)
        else:
            settings = Settings.from_dict(settings_data, base_path=path.parent)
        settings.inventory = inventory
        active_settings_data = raw.get("active_settings")
        active_inventory_data = raw.get("active_inventory")
        active_version = raw.get("active_version")
        if isinstance(active_settings_data, dict) and isinstance(active_inventory_data, dict):
            try:
                active_inventory = Inventory.from_dict(active_inventory_data)
                if base_settings is not None:
                    active_merged = base_settings.to_dict()
                    for key in PORTABLE_SETTINGS_FIELDS:
                        if key in active_settings_data:
                            active_merged[key] = active_settings_data[key]
                    # gallery_path is generated by the local app and is needed to
                    # recover the known-good cache after a process restart.
                    if isinstance(active_settings_data.get("gallery_path"), str):
                        active_merged["gallery_path"] = active_settings_data["gallery_path"]
                    active_merged["inventory"] = active_inventory.to_dict()
                    active_settings = Settings.from_dict(active_merged, base_path=path.parent)
                else:
                    active_settings = Settings.from_dict(active_settings_data, base_path=path.parent)
                active_settings.inventory = active_inventory
                self.active_snapshot = (active_settings, active_inventory, int(active_version or version))
            except (ValueError, TypeError, KeyError):
                self.active_snapshot = None
        self.version = version
        return settings, inventory, version

    def load(self, base_settings: Settings | None = None) -> tuple[Settings, Inventory, int]:
        if not self.target_path.exists():
            if self.backup_path.exists():
                return self._load_file(self.backup_path, base_settings=base_settings)
            raise FileNotFoundError(f"config file not found: {self.target_path}")

        try:
            return self._load_file(self.target_path, base_settings=base_settings)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            # Primary file corrupt: attempt recovery from backup
            if self.backup_path.exists():
                try:
                    recovered = self._load_file(self.backup_path, base_settings=base_settings)
                    # Restore the recovered draft to primary atomically.  The
                    # new envelope version is the one callers must continue
                    # from; the known-good backup remains untouched because
                    # the corrupt primary is not copied over it.
                    restored_version = self.save_atomic(recovered[0], recovered[1])
                    return recovered[0], recovered[1], restored_version
                except Exception:
                    pass
            raise ValueError(f"failed to load configuration; primary corrupt and backup unavailable: {exc}") from exc

    def rollback(self, base_settings: Settings | None = None) -> tuple[Settings, Inventory, int]:
        if not self.backup_path.exists():
            raise FileNotFoundError(f"backup configuration not found: {self.backup_path}")
        settings, inventory, _ = self._load_file(self.backup_path, base_settings=base_settings)
        new_version = self.save_atomic(settings, inventory)
        return settings, inventory, new_version

    def export_portable(self, settings: Settings, inventory: Inventory) -> dict[str, Any]:
        """Produce a clean, portable configuration export free of machine-local credentials and paths."""
        return {
            "schema_version": 1,
            "type": "portable_configuration",
            "exported_at": time.time(),
            "notice": (
                "Credentials, model weights, and machine-specific absolute paths are excluded. "
                "Reference photographs stored in data/references must be migrated separately if moving to another device."
            ),
            "settings": {
                "mode": settings.mode,
                "gallery_mode": settings.gallery_mode,
                "text_weight": float(settings.text_weight),
                "classifier_threshold": float(settings.classifier_threshold),
                "classifier_margin": float(settings.classifier_margin),
                "classifier_profiles": dict(settings.classifier_profiles),
                "roi": list(settings.roi) if settings.roi else None,
                "inventory_confirmed": bool(settings.inventory_confirmed),
                "segmentation_min_area": int(settings.segmentation_min_area),
                "segmentation_max_area": int(settings.segmentation_max_area),
                "tracking_max_distance": float(settings.tracking_max_distance),
                "tracking_max_missed": int(settings.tracking_max_missed),
                "batch_size": int(settings.batch_size),
                "frame_max_age_seconds": float(settings.frame_max_age_seconds),
                "scene_ttl_seconds": float(settings.scene_ttl_seconds),
                "photo_retention_seconds": float(settings.photo_retention_seconds),
                "prediction_max_age_seconds": float(settings.prediction_max_age_seconds),
                "prediction_interval_seconds": float(settings.prediction_interval_seconds),
            },
            "inventory": inventory.to_dict(),
        }

    def import_portable(self, data: dict[str, Any], current_settings: Settings) -> tuple[Settings, Inventory]:
        """Validate and import a portable configuration, preserving current machine credentials and paths."""
        if not isinstance(data, dict) or "settings" not in data or "inventory" not in data:
            raise ValueError("invalid portable configuration: must contain settings and inventory")
        raw_settings = data["settings"]
        if not isinstance(raw_settings, dict):
            raise ValueError("settings must be a JSON object")

        unknown = set(raw_settings) - PORTABLE_SETTINGS_FIELDS
        if unknown:
            raise ValueError(f"unknown settings fields in portable configuration: {sorted(unknown)}")

        inventory = Inventory.from_dict(data["inventory"])

        # Merge portable fields into a copy of current machine settings
        merged_dict = current_settings.to_dict()
        for key in PORTABLE_SETTINGS_FIELDS:
            if key in raw_settings:
                merged_dict[key] = raw_settings[key]
        merged_dict["inventory"] = inventory.to_dict()

        new_settings = Settings.from_dict(merged_dict)
        new_settings.inventory = inventory
        return new_settings, inventory
