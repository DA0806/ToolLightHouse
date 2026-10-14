from __future__ import annotations

import time
import uuid
import base64
import json
import hashlib
from pathlib import Path

import numpy as np
import cv2

from .classifier import MockClassifier, SimilarityClassifier
from .config import Settings
from .embeddings import EmbeddingEngine
from .gallery import load_validated_gallery
from .led_controller import mask_for_detections
from .metrics import Metrics
from .segmentation import Segmenter
from .serial_transport import SimulatedSerial
from .state_manager import StateManager
from .tracking import CentroidTracker

class StaleFrameError(ValueError):
    """Frame arrived too late for video inference."""


class Pipeline:
    def __init__(self, settings: Settings | None = None, serial=None, embedder=None, safe_startup: bool = False):
        self.settings = settings or Settings()
        self.preparation_reasons: list[str] = []
        self.mode = self.settings.mode

        self.segmenter = Segmenter(
            self.settings.segmentation_min_area,
            self.settings.segmentation_max_area,
            require_background=self.settings.require_background or (self.settings.mode == "real" and not safe_startup),
            roi=self.settings.roi,
        )

        if self.settings.roi is None and self.settings.roi_path and Path(self.settings.roi_path).exists():
            try:
                roi = tuple(int(v) for v in json.loads(Path(self.settings.roi_path).read_text(encoding="utf-8")))
                if len(roi) == 4 and roi[2] > 0 and roi[3] > 0:
                    self.segmenter.roi = roi
            except (ValueError, OSError, json.JSONDecodeError):
                raise ValueError(f"invalid ROI calibration file: {self.settings.roi_path}")

        if self.settings.background_path:
            background = cv2.imread(self.settings.background_path, cv2.IMREAD_COLOR)
            if background is None:
                if safe_startup:
                    self.preparation_reasons.append(f"background calibration not found: {self.settings.background_path}")
                elif self.settings.require_background:
                    raise FileNotFoundError(f"background calibration not found: {self.settings.background_path}")
            else:
                self.segmenter.set_background(background)
        elif self.settings.mode == "real" and safe_startup:
            self.preparation_reasons.append("background calibration path not configured")

        self.tracker = CentroidTracker(self.settings.tracking_max_distance, self.settings.tracking_max_missed)
        self.state = StateManager()
        self.metrics = Metrics(include_vram=self.settings.mode == "real")
        self.serial = serial or SimulatedSerial()
        self.embedder = embedder

        references = {}
        if self.settings.mode == "real":
            try:
                import torch
            except ImportError:
                if safe_startup:
                    self.preparation_reasons.append("PyTorch not installed")
                else:
                    raise RuntimeError("PyTorch is required for real mode")

            if safe_startup and not Path(self.settings.model_path).exists():
                self.preparation_reasons.append(f"model weights not found: {self.settings.model_path}")

            if self.settings.gallery_path and Path(self.settings.gallery_path).exists():
                try:
                    references = load_validated_gallery(
                        self.settings.gallery_path,
                        inventory=self.settings.inventory,
                        model_path=self.settings.model_path,
                        gallery_mode=self.settings.gallery_mode,
                        text_weight=self.settings.text_weight,
                    ).references
                except (FileNotFoundError, ValueError, KeyError) as exc:
                    if safe_startup:
                        self.preparation_reasons.append(f"gallery cache unavailable or stale: {exc}")
                        references = {}
                    else:
                        raise RuntimeError(f"real mode gallery unavailable or stale: {self.settings.gallery_path}; rebuild it before starting") from exc
            elif safe_startup:
                self.preparation_reasons.append("real mode requires valid gallery_path with a validated gallery cache")
            else:
                raise RuntimeError("real mode requires gallery_path with a validated gallery cache")

            if not references:
                if safe_startup:
                    if not any("gallery" in r.lower() for r in self.preparation_reasons):
                        self.preparation_reasons.append("gallery contains no valid class references")
                else:
                    raise RuntimeError("real mode gallery contains no references")

        # Determine effective mode
        if self.settings.mode == "real" and safe_startup and self.preparation_reasons:
            self.mode = "preparation"
        elif self.settings.mode in {"mock", "preparation", "real"}:
            self.mode = self.settings.mode
        else:
            self.mode = "mock"

        # Safely initialize embedder only if in real mode and not in preparation
        self.embedder = embedder
        if self.embedder is None and self.mode == "real" and not self.preparation_reasons:
            try:
                self.embedder = EmbeddingEngine(self.settings.model_path, self.settings.device if self.settings.device != "auto" else None)
            except Exception as exc:
                if safe_startup:
                    self.preparation_reasons.append(f"failed to initialize embedding engine: {exc}")
                    self.mode = "preparation"
                    self.embedder = None
                else:
                    raise

        threshold, margin = self.settings.classifier_parameters()
        if self.mode == "real" and references:
            self.classifier = SimilarityClassifier(references, threshold, margin, gallery_mode=self.settings.gallery_mode)
        else:
            self.classifier = MockClassifier([c.class_id for c in self.settings.inventory.categories])

        self._prediction_cache = {}

    def is_ready(self) -> bool:
        return self.mode in {"mock", "real"} and not self.preparation_reasons

    def update_classifier_parameters(self, threshold: float, margin: float):
        if isinstance(self.classifier, SimilarityClassifier):
            self.classifier.threshold = float(threshold)
            self.classifier.margin = float(margin)

    def reset_scene(self):
        self.tracker.reset()
        self.state.reset()
        self._prediction_cache.clear()

    def process(self, image: np.ndarray, timestamp: float | None = None, photo: bool = False, apply_leds: bool = True) -> dict:
        if self.mode == "preparation":
            reasons = "; ".join(self.preparation_reasons) if self.preparation_reasons else "calibration or gallery missing"
            raise RuntimeError(f"System is in preparation mode: {reasons}")
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("process expects a BGR HxWx3 image")
        if self.segmenter.background is not None and self.segmenter.background.size > 0:
            bg_h, bg_w = self.segmenter.background.shape[:2]
            if (image.shape[0], image.shape[1]) != (bg_h, bg_w):
                raise ValueError(f"image dimensions ({image.shape[1]}x{image.shape[0]}) do not match calibration background ({bg_w}x{bg_h})")
        started = time.perf_counter()
        now = time.time() if timestamp is None else timestamp
        if not photo and time.time() - now > self.settings.frame_max_age_seconds:
            raise StaleFrameError("stale frame discarded before inference")
        if photo:
            self.reset_scene()
        stage_started = time.perf_counter()
        segments = self.segmenter.detect(image)
        self.metrics.observe_stage("segment", time.perf_counter() - stage_started)
        stage_started = time.perf_counter()
        tracks = self.tracker.update([segment.bbox for segment in segments])
        self.metrics.observe_stage("track", time.perf_counter() - stage_started)
        real_embeddings = [None] * len(segments)
        due = []
        digests = []
        for index, (track, segment) in enumerate(zip(tracks, segments)):
            digest = hashlib.sha256(segment.crop.tobytes()).hexdigest()
            digests.append(digest)
            cached = self._prediction_cache.get(track.track_id)
            age = time.time() - cached["at"] if cached else float("inf")
            if self.embedder is not None and (not cached or cached["digest"] != digest or age > self.settings.prediction_max_age_seconds or (cached["prediction"].class_id is None and age > self.settings.prediction_interval_seconds)):
                due.append(index)
            elif cached:
                real_embeddings[index] = cached["prediction"]
        if self.embedder is not None and due:
            for start in range(0, len(due), self.settings.batch_size):
                batch = due[start:start + self.settings.batch_size]
                stage_started = time.perf_counter()
                values = self.embedder.encode_images([segments[i].crop for i in batch])
                self.metrics.observe_stage("embed", time.perf_counter() - stage_started)
                self.metrics.observe_inference()
                stage_started = time.perf_counter()
                for i, value in zip(batch, values):
                    real_embeddings[i] = value
                    self._prediction_cache[tracks[i].track_id] = {"digest": digests[i], "at": time.time(), "prediction": self.classifier.predict(value, tracks[i].track_id)}
                self.metrics.observe_stage("classify", time.perf_counter() - stage_started)
        if not photo and time.time() - now > self.settings.frame_max_age_seconds:
            raise StaleFrameError("stale frame discarded after inference")
        detections = []
        stage_started = time.perf_counter()
        inferred_tracks = set(due)
        for index, (track, segment) in enumerate(zip(tracks, segments)):
            if isinstance(self.classifier, MockClassifier):
                prediction = self.classifier.predict(segment.crop, track.track_id)
            elif self.embedder is not None:
                prediction = self._prediction_cache[track.track_id]["prediction"]
            else:
                prediction = self.classifier.predict(np.zeros(1, dtype=np.float32), track.track_id)
            if photo or index in inferred_tracks:
                confirmed = self.state.update(track.track_id, prediction.class_id, prediction.class_id is not None, photo=photo)
            else:
                cached_state = self.state._states.get(track.track_id)
                confirmed = cached_state.confirmed if cached_state else None
            category = self.settings.inventory.by_id.get(confirmed or prediction.class_id)
            detections.append({
                "track_id": track.track_id,
                "bbox": list(track.bbox),
                "center": [track.bbox[0] + track.bbox[2] / 2, track.bbox[1] + track.bbox[3] / 2],
                "class_id": confirmed,
                "name": category.effective_display_name if category else None,
                "location": category.location if category else None,
                "similarity_top1": prediction.similarity_top1,
                "similarity_top2": prediction.similarity_top2,
                "margin": prediction.margin,
                "validation": "simulated" if self.mode == "mock" else ("confirmed" if confirmed else prediction.validation),
            })
        mask = mask_for_detections(detections, self.settings.inventory)
        if isinstance(self.classifier, MockClassifier):
            self.metrics.observe_stage("classify", time.perf_counter() - stage_started)
        if apply_leds and hasattr(self.serial, "set_scene_deadline"):
            self.serial.set_scene_deadline(now + (self.settings.photo_retention_seconds if photo else self.settings.scene_ttl_seconds))
        serial_started = time.perf_counter()
        sent = self.serial.send_mask(mask) if apply_leds else False
        if apply_leds:
            self.metrics.observe_stage("serial", time.perf_counter() - serial_started)
            self.metrics.observe_ack(now, self.serial.last_ack_at, sent and self.serial.status().get("confirmed_mask") == mask, self.serial.simulated)
        self.metrics.observe(time.perf_counter() - started, now)
        status = self.serial.status()
        return {
            "frame_id": str(uuid.uuid4()),
            "timestamp": now,
            "mode": self.mode,
            "simulated": bool(self.serial.simulated or self.mode != "real"),
            "image": {"width": int(image.shape[1]), "height": int(image.shape[0]), "orientation": "normalized", "preview_data_url": "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()).decode("ascii")},
            "detections": detections,
            "arduino": {**status, "requested_mask": mask, "ack": bool(sent and status.get("confirmed_mask") == mask)},
            "metrics": self.metrics.as_dict(),
        }


def check_system_readiness(settings: Settings, pipeline: Pipeline | None = None) -> dict[str, Any]:
    import platform
    python_version = platform.python_version()
    torch_available = False
    cuda_available = False
    gpu_name = None
    vram_free_mb = None
    try:
        import torch
        torch_available = True
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
            free_bytes, _ = torch.cuda.mem_get_info(0)
            vram_free_mb = int(free_bytes // (1024 * 1024))
    except (ImportError, Exception):
        pass

    model_path = Path(settings.model_path) if settings.model_path else None
    model_present = False
    model_status = "missing"
    if not torch_available:
        model_status = "missing_dependency"
    elif pipeline is not None and pipeline.embedder is not None:
        model_present = True
        model_status = "ready"
    elif model_path and model_path.is_dir():
        has_config = (model_path / "config.json").exists()
        weight_candidates = ["model.safetensors", "pytorch_model.bin", "model.safetensors.index.json"]
        has_weights = any((model_path / c).exists() for c in weight_candidates)
        if has_config and has_weights:
            model_present = True
            model_status = "ready"
        else:
            model_status = "missing"
    else:
        model_status = "missing"

    inventory_ready = bool(
        len(settings.inventory.categories) == 9
        and settings.inventory_confirmed
        and not settings.inventory.example
    )

    gallery_ready = False
    if settings.gallery_path and Path(settings.gallery_path).exists():
        try:
            load_validated_gallery(
                settings.gallery_path,
                inventory=settings.inventory,
                model_path=settings.model_path,
                gallery_mode=settings.gallery_mode,
                text_weight=settings.text_weight,
            )
            gallery_ready = True
        except Exception:
            gallery_ready = False

    calibration_ready = False
    calibration_reason = None
    bg_img = None
    if pipeline is not None and pipeline.segmenter.background is not None and pipeline.segmenter.background.size > 0:
        bg_img = pipeline.segmenter.background
    elif settings.background_path and Path(settings.background_path).is_file():
        try:
            bg_img = cv2.imread(settings.background_path, cv2.IMREAD_COLOR)
        except Exception:
            bg_img = None

    if bg_img is not None and bg_img.size > 0:
        h, w = bg_img.shape[:2]
        if settings.roi is not None:
            rx, ry, rw, rh = settings.roi
            if rx < 0 or ry < 0 or rw <= 0 or rh <= 0 or rx + rw > w or ry + rh > h:
                calibration_ready = False
                calibration_reason = f"configured ROI {list(settings.roi)} exceeds background bounds ({w}x{h})"
            else:
                calibration_ready = True
        else:
            calibration_ready = True
    else:
        calibration_ready = False
        calibration_reason = "Background calibration missing or unreadable"

    arduino_ready = bool(
        pipeline is not None
        and (getattr(pipeline.serial, "connected", False) or getattr(pipeline.serial, "simulated", False))
    )

    active_mode = pipeline.mode if pipeline else settings.mode

    reasons = []
    if model_status != "ready":
        if model_status == "missing_dependency":
            reasons.append("PyTorch not installed (install optional AI dependencies via pip install -e '.[ai]')")
        else:
            reasons.append(f"Model weights missing at {settings.model_path}")
    if not inventory_ready:
        reasons.append("Inventory not confirmed (physical inventory setup required)")
    if not gallery_ready:
        reasons.append("Gallery cache not built or stale")
    if not calibration_ready:
        reasons.append(calibration_reason or "Background calibration missing")
    if not arduino_ready:
        reasons.append("Arduino hardware not connected and simulation not selected")

    can_activate_real = len(reasons) == 0

    return {
        "backend_ok": True,
        "python_version": python_version,
        "torch_available": torch_available,
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "vram_free_mb": vram_free_mb,
        "model_present": model_present,
        "model_status": model_status,
        "inventory_ready": inventory_ready,
        "gallery_ready": gallery_ready,
        "calibration_ready": calibration_ready,
        "arduino_ready": arduino_ready,
        "active_mode": active_mode,
        "can_activate_real": can_activate_real,
        "preparation_reasons": reasons,
    }
