from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Metrics:
    samples: deque = field(default_factory=lambda: deque(maxlen=256))
    frame_times: deque = field(default_factory=lambda: deque(maxlen=256))
    completion_times: deque = field(default_factory=lambda: deque(maxlen=256))
    display_times: deque = field(default_factory=lambda: deque(maxlen=256))
    stages: dict = field(default_factory=lambda: {name: deque(maxlen=256) for name in ("segment", "track", "embed", "classify", "total", "serial")})
    min_samples: int = 20
    include_vram: bool = False
    capture_to_ack_ms: float | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def observe(self, seconds: float, timestamp: float | None = None):
        with self._lock:
            self.samples.append(seconds)
            self.stages["total"].append(seconds)

    def observe_stage(self, stage: str, seconds: float):
        with self._lock:
            self.stages[stage].append(seconds)

    def observe_capture(self, timestamp: float | None = None):
        with self._lock:
            self.frame_times.append(time.time() if timestamp is None else timestamp)

    def observe_display(self, timestamp: float | None = None):
        with self._lock:
            self.display_times.append(time.time() if timestamp is None else timestamp)

    def observe_inference(self, timestamp: float | None = None):
        with self._lock:
            self.completion_times.append(time.time() if timestamp is None else timestamp)

    def observe_ack(self, captured_at: float, ack_at: float, matched: bool, simulated: bool):
        with self._lock:
            self.capture_to_ack_ms = round((ack_at - captured_at) * 1000, 2) if matched and not simulated and ack_at >= captured_at else None

    def _rate(self, times):
        if len(times) < self.min_samples or times[-1] <= times[0]:
            return None
        return round((len(times) - 1) / (times[-1] - times[0]), 2)

    def _percentiles(self, values):
        ordered = sorted(values)
        enough = len(ordered) >= self.min_samples
        return {"p50_ms": round(ordered[len(ordered) // 2] * 1000, 2) if enough else None,
                "p95_ms": round(ordered[max(0, int(len(ordered) * .95) - 1)] * 1000, 2) if enough else None,
                "sample_count": len(ordered)}

    def as_dict(self):
        vram_mb = None
        try:
            if not self.include_vram:
                raise ImportError
            import torch
            if torch.cuda.is_available():
                vram_mb = round(torch.cuda.memory_allocated() / 1_048_576, 2)
        except ImportError:
            pass
        with self._lock:
            embed = self._percentiles(self.stages["embed"])
            return {"inference_ms_p50": embed["p50_ms"], "inference_ms_p95": embed["p95_ms"],
                    "inference_hz": self._rate(self.completion_times), "capture_fps": self._rate(self.frame_times),
                    "display_fps": self._rate(self.display_times), "sample_count": len(self.samples),
                    "capture_to_ack_ms": self.capture_to_ack_ms, "vram_mb": vram_mb,
                    "stages": {name: self._percentiles(values) for name, values in self.stages.items()}}
