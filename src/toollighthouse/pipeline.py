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
    def __init__(self, settings: Settings | None = None, serial=None, embedder=None):
        self.settings = settings or Settings()
        self.segmenter = Segmenter(self.settings.segmentation_min_area, self.settings.segmentation_max_area, require_background=self.settings.require_background or self.settings.mode == "real", roi=self.settings.roi)
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
                if self.settings.require_background or self.settings.mode == "real":
                    raise FileNotFoundError(f"background calibration not found: {self.settings.background_path}")
            else:
                self.segmenter.set_background(background)
        self.tracker = CentroidTracker(self.settings.tracking_max_distance, self.settings.tracking_max_missed)
        self.state = StateManager()
        self.metrics = Metrics(include_vram=self.settings.mode == "real")
        self.serial = serial or SimulatedSerial()
        self.embedder = embedder or (EmbeddingEngine(self.settings.model_path, self.settings.device if self.settings.device != "auto" else None) if self.settings.mode == "real" else None)
        references = {}
        if self.settings.mode == "real" and self.settings.gallery_path:
            try:
                references = load_validated_gallery(
                    self.settings.gallery_path,
                    inventory=self.settings.inventory,
                    model_path=self.settings.model_path,
                    gallery_mode=self.settings.gallery_mode,
                    text_weight=self.settings.text_weight,
                ).references
            except (FileNotFoundError, ValueError, KeyError) as exc:
                if self.settings.mode == "real":
                    raise RuntimeError(f"real mode gallery unavailable or stale: {self.settings.gallery_path}; rebuild it before starting") from exc
                references = {}
        elif self.settings.mode == "real":
            raise RuntimeError("real mode requires gallery_path with a validated gallery cache")
        if self.settings.mode == "real" and not references:
            raise RuntimeError("real mode gallery contains no references")
        threshold, margin = self.settings.classifier_parameters()
        self.classifier = MockClassifier([c.class_id for c in self.settings.inventory.categories]) if self.settings.mode == "mock" else SimilarityClassifier(references, threshold, margin, gallery_mode=self.settings.gallery_mode)
        self._prediction_cache = {}

    def reset_scene(self):
        self.tracker.reset()
        self.state.reset()
        self._prediction_cache.clear()

    def process(self, image: np.ndarray, timestamp: float | None = None, photo: bool = False, apply_leds: bool = True) -> dict:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("process expects a BGR HxWx3 image")
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
                "name": category.name if category else None,
                "location": category.location if category else None,
                "similarity_top1": prediction.similarity_top1,
                "similarity_top2": prediction.similarity_top2,
                "margin": prediction.margin,
                "validation": "simulated" if self.settings.mode == "mock" else ("confirmed" if confirmed else prediction.validation),
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
            "mode": self.settings.mode,
            "simulated": bool(self.serial.simulated or self.settings.mode == "mock"),
            "image": {"width": int(image.shape[1]), "height": int(image.shape[0]), "orientation": "normalized", "preview_data_url": "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()).decode("ascii")},
            "detections": detections,
            "arduino": {**status, "requested_mask": mask, "ack": bool(sent and status.get("confirmed_mask") == mask)},
            "metrics": self.metrics.as_dict(),
        }
