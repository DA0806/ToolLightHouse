from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Segment:
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]
    area: float
    crop: np.ndarray | None = None


class Segmenter:
    def __init__(self, min_area: int = 150, max_area: int = 1_000_000, threshold: int = 24, require_background: bool = False, roi=None):
        self.min_area, self.max_area, self.threshold = min_area, max_area, threshold
        self.require_background = require_background
        self.roi = tuple(roi) if roi else None
        self.background: np.ndarray | None = None

    def set_background(self, image: np.ndarray) -> None:
        self.background = image.copy()

    def detect(self, image: np.ndarray) -> list[Segment]:
        if image.ndim != 3:
            raise ValueError("image must be HxWx3")
        if self.roi:
            rx, ry, rw, rh = self.roi
            height, width = image.shape[:2]
            if rx + rw > width or ry + rh > height:
                raise ValueError("configured ROI exceeds image dimensions")
        if self.require_background and self.background is None:
            raise RuntimeError("background calibration is required before real segmentation")
        if self.background is not None and self.background.shape != image.shape:
            raise ValueError("image dimensions do not match calibrated background")
        if self.background is not None and self.background.shape == image.shape:
            delta = cv2.absdiff(image, self.background)
            gray = cv2.cvtColor(delta, cv2.COLOR_BGR2GRAY)
            mask = (gray > self.threshold).astype(np.uint8) * 255
        else:
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, (0, 0, self.threshold), (180, 255, 255))
        if self.roi:
            rx, ry, rw, rh = self.roi
            roi_mask = np.zeros_like(mask)
            roi_mask[ry:ry + rh, rx:rx + rw] = 255
            mask = cv2.bitwise_and(mask, roi_mask)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        found: list[Segment] = []
        height, width = image.shape[:2]
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if not self.min_area <= area <= self.max_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            moments = cv2.moments(contour)
            cx = (moments["m10"] / moments["m00"]) if moments["m00"] else x + w / 2
            cy = (moments["m01"] / moments["m00"]) if moments["m00"] else y + h / 2
            x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
            crop = image[y0:y1, x0:x1].copy()
            local = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
            cv2.drawContours(local, [contour - np.array([[[x0, y0]]])], -1, 255, -1)
            crop[local == 0] = 0
            found.append(Segment((x, y, w, h), (float(cx), float(cy)), area, crop))
        return sorted(found, key=lambda item: (item.bbox[1], item.bbox[0]))
