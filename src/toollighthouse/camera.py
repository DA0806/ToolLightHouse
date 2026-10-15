from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import cv2
import numpy as np


class LatestFrameWorker:
    """Size-one frame slot: submitting a new frame evicts an older pending frame."""

    def __init__(self, processor):
        self.processor, self._pending, self._lock = processor, None, threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, frame, timestamp: float | None = None):
        with self._lock:
            self._pending = (frame, timestamp or time.time())

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="toollighthouse-inference", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                item, self._pending = self._pending, None
            if item is None:
                self._stop.wait(0.01)
                continue
            frame, timestamp = item
            self.processor(frame, timestamp)


class LatestInferenceWorker:
    """One inference thread and one replaceable pending request."""

    def __init__(self, processor):
        self.processor = processor
        self._condition = threading.Condition()
        self._pending: tuple[tuple, dict, Future] | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="toollighthouse-inference", daemon=True)
        self._thread.start()

    def submit(self, *args, **kwargs) -> Future:
        future = Future()
        with self._condition:
            if self._stop:
                future.cancel()
                return future
            if self._pending is not None:
                self._pending[2].cancel()
            self._pending = (args, kwargs, future)
            self._condition.notify()
        return future

    def close(self, timeout=2):
        with self._condition:
            self._stop = True
            if self._pending:
                self._pending[2].cancel()
            self._condition.notify()
        self._thread.join(timeout=timeout)

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._stop:
                    self._condition.wait()
                if self._stop:
                    return
                args, kwargs, future = self._pending
                self._pending = None
            if future.cancelled():
                continue
            try:
                result = self.processor(*args, **kwargs)
                if not future.cancelled():
                    future.set_result(result)
            except Exception as exc:
                if not future.cancelled():
                    future.set_exception(exc)


class OpenCVCamera:
    """Reconnectable OpenCV source for camera indices, URLs and video files."""

    def __init__(self, source=0, reconnect_seconds: float = 1.0):
        self.source = int(source) if str(source).isdigit() else source
        self.reconnect_seconds = reconnect_seconds
        self.capture = None

    def open(self):
        self.capture = cv2.VideoCapture(self.source)
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = None
            raise RuntimeError(f"unable to open camera/video source: {self.source}")
        return self

    def read(self):
        if self.capture is None:
            self.open()
        ok, frame = self.capture.read()
        if not ok:
            if isinstance(self.source, str) and not self.source.startswith(("http://", "https://", "rtsp://")):
                return None
            self.close()
            time.sleep(self.reconnect_seconds)
            self.open()
            ok, frame = self.capture.read()
        return frame if ok else None

    def close(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None


def enumerate_opencv_cameras(active_index: int | None = None, max_probe: int = 4) -> list[dict]:
    """
    Safely probes for available local OpenCV camera indices.
    Skips opening the active_index to prevent DirectShow capture stalls on Windows.
    """
    devices = []
    for i in range(max_probe):
        if active_index is not None and i == active_index:
            devices.append({
                "index": i,
                "name": f"USB Video Device #{i}" if i > 0 else "Integrated / Primary Camera",
                "is_active": True,
            })
            continue

        try:
            # Try CAP_DSHOW on Windows, fallback to default backend
            backend = getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
                cap.release()
                devices.append({
                    "index": i,
                    "name": f"USB Video Device #{i}" if i > 0 else "Integrated / Primary Camera",
                    "width": w,
                    "height": h,
                    "fps": fps,
                    "is_active": False,
                })
            else:
                cap.release()
        except Exception:
            pass
    return devices


class OpenCVCameraService:
    """
    Managed background acquisition service for local physical webcams.
    Acquires single-reader frames, handles reconnections, and produces
    binary JPEG preview frames at controlled resolutions and FPS.
    """

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720, fps: float = 30.0):
        self.index = index
        self.requested_width = width
        self.requested_height = height
        self.requested_fps = fps
        self.actual_width = width
        self.actual_height = height
        self.actual_fps = fps

        self._capture = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._latest_raw_frame = None
        self._latest_preview_jpeg: bytes | None = None
        self._latest_timestamp: float = 0.0
        self._latest_mono: float = 0.0

        self.is_running = False
        self.status = "standby"
        self.last_error: str | None = None

    def start(self):
        with self._lock:
            if self.is_running:
                return
            self._stop.clear()
            self.is_running = True
            self.status = "active"
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name=f"opencv-cam-{self.index}", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            self.is_running = False
            self.status = "standby"

    def get_latest_frame(self):
        """Returns (raw_bgr_frame, wall_clock_time, mono_time) or None."""
        with self._lock:
            if self._latest_raw_frame is None:
                return None
            return self._latest_raw_frame.copy(), self._latest_timestamp, self._latest_mono

    def get_latest_preview_jpeg(self, width: int | None = None, height: int | None = None) -> bytes | None:
        with self._lock:
            jpeg = self._latest_preview_jpeg
        if jpeg is None or width is None or height is None or (width, height) == (640, 360):
            return jpeg
        try:
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return jpeg
            resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 70])
            return buf.tobytes() if ok else jpeg
        except Exception:
            return jpeg

    def _open_capture(self):
        backend = getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            # Fallback to default backend
            cap = cv2.VideoCapture(self.index)
            if not cap.isOpened():
                cap.release()
                raise RuntimeError(f"Unable to open OpenCV camera index {self.index}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
        cap.set(cv2.CAP_PROP_FPS, self.requested_fps)

        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.requested_width)
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.requested_height)
        self.actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or self.requested_fps)
        return cap

    def _run(self):
        try:
            self._capture = self._open_capture()
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.last_error = str(exc)
                self.is_running = False
            return

        consecutive_errors = 0
        target_interval = 1.0 / max(1.0, self.requested_fps)

        try:
            while not self._stop.is_set():
                loop_start = time.monotonic()
                ok, frame = self._capture.read()
                if not ok:
                    consecutive_errors += 1
                    if consecutive_errors <= 3:
                        time.sleep(0.5)
                        try:
                            if self._capture:
                                self._capture.release()
                            self._capture = self._open_capture()
                        except Exception:
                            pass
                        continue
                    else:
                        with self._lock:
                            self.status = "error"
                            self.last_error = f"Camera read failed after {consecutive_errors} attempts"
                        break

                consecutive_errors = 0
                now_wall = time.time()
                now_mono = time.monotonic()

                # Generate downscaled 640x360 preview JPEG
                try:
                    preview_frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
                    ok_enc, buf = cv2.imencode(".jpg", preview_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    preview_jpeg = buf.tobytes() if ok_enc else None
                except Exception:
                    preview_jpeg = None

                with self._lock:
                    self._latest_raw_frame = frame
                    self._latest_timestamp = now_wall
                    self._latest_mono = now_mono
                    self._latest_preview_jpeg = preview_jpeg

                elapsed = time.monotonic() - loop_start
                sleep_time = target_interval - elapsed
                if sleep_time > 0.005:
                    time.sleep(sleep_time)

        finally:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception:
                    pass
                self._capture = None
            with self._lock:
                self.is_running = False
                if self.status == "active":
                    self.status = "standby"
