from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import cv2


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
