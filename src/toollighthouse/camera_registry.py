from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


@dataclass
class CameraSourceDescriptor:
    source_id: str                      # e.g. "remote_phone", "laptop_builtin", "usb_webcam", "operator_device", "manual_upload"
    display_name: str                   # Friendly name
    kind: str                           # "webrtc_remote" | "opencv_builtin" | "opencv_usb" | "browser_local" | "manual_upload"
    connection_type: str                # "webrtc" | "builtin" | "usb" | "browser" | "file"
    status: str                         # "active" | "standby" | "disconnected" | "error" | "needs_calibration"
    current_resolution: dict[str, int]  # {"width": 1920, "height": 1080}
    current_fps: float                  # FPS measured or reported
    is_active: bool                     # True if currently selected
    calibration_profile_available: bool # True if confirmed calibration profile exists
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "connection_type": self.connection_type,
            "status": self.status,
            "current_resolution": dict(self.current_resolution),
            "current_fps": self.current_fps,
            "is_active": self.is_active,
            "calibration_profile_available": self.calibration_profile_available,
            "last_error": self.last_error,
        }


class CameraSourceRegistry:
    """
    Unified Camera Source Registry managing the 5 primary sources,
    source switching transitions, fencing versions, and device states.
    """

    CANONICAL_SOURCES = {
        "remote_phone": {
            "display_name": "Teléfono Remoto (WebRTC)",
            "kind": "webrtc_remote",
            "connection_type": "webrtc",
            "default_res": {"width": 1920, "height": 1080},
        },
        "laptop_builtin": {
            "display_name": "Cámara Integrada de Laptop",
            "kind": "opencv_builtin",
            "connection_type": "builtin",
            "default_res": {"width": 1280, "height": 720},
        },
        "usb_webcam": {
            "display_name": "Webcam USB Externa",
            "kind": "opencv_usb",
            "connection_type": "usb",
            "default_res": {"width": 1280, "height": 720},
        },
        "operator_device": {
            "display_name": "Cámara de este Navegador",
            "kind": "browser_local",
            "connection_type": "browser",
            "default_res": {"width": 1280, "height": 720},
        },
        "manual_upload": {
            "display_name": "Fotografía Manual",
            "kind": "manual_upload",
            "connection_type": "file",
            "default_res": {"width": 1920, "height": 1080},
        },
    }

    def __init__(self, initial_active: str = "remote_phone"):
        self._lock = threading.RLock()
        self.source_version: int = 1
        self.active_source_id: str = initial_active
        self._sources: dict[str, CameraSourceDescriptor] = {}

        for sid, meta in self.CANONICAL_SOURCES.items():
            is_active = (sid == initial_active)
            self._sources[sid] = CameraSourceDescriptor(
                source_id=sid,
                display_name=meta["display_name"],
                kind=meta["kind"],
                connection_type=meta["connection_type"],
                status="active" if is_active else "standby",
                current_resolution=meta["default_res"],
                current_fps=30.0 if meta["connection_type"] != "file" else 0.0,
                is_active=is_active,
                calibration_profile_available=False,
                last_error=None,
            )

    def list_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [desc.to_dict() for desc in self._sources.values()]

    def get_source(self, source_id: str) -> CameraSourceDescriptor | None:
        with self._lock:
            return self._sources.get(source_id)

    def snapshot_active(self) -> tuple[str, int]:
        with self._lock:
            return self.active_source_id, self.source_version

    def update_source_status(
        self,
        source_id: str,
        status: str,
        error: str | None = None,
        resolution: dict[str, int] | None = None,
        fps: float | None = None,
        calibration_available: bool | None = None,
    ):
        with self._lock:
            desc = self._sources.get(source_id)
            if not desc:
                return
            desc.status = status
            if error is not None:
                desc.last_error = error
            if resolution is not None:
                desc.current_resolution = dict(resolution)
            if fps is not None:
                desc.current_fps = fps
            if calibration_available is not None:
                desc.calibration_profile_available = calibration_available

    async def switch_active_source(
        self,
        source_id: str,
        teardown_callback: Callable[[str, str, int], Coroutine[Any, Any, None]] | None = None,
    ) -> CameraSourceDescriptor:
        """
        Switches the active inspection camera source safely:
        1. Validates destination source.
        2. Executes teardown_callback (stops continuous inference, clears pending results, safe MASK:0).
        3. Updates active status and increments source_version under lock.
        """
        with self._lock:
            if source_id not in self._sources:
                raise KeyError(f"Camera source '{source_id}' does not exist.")
            if source_id == self.active_source_id:
                return self._sources[source_id]

            old_source_id = self.active_source_id
            new_version = self.source_version + 1

        # Run teardown I/O outside lock
        if teardown_callback is not None:
            await teardown_callback(old_source_id, source_id, new_version)

        with self._lock:
            # Recheck destination exists and apply new active state
            for sid, desc in self._sources.items():
                if sid == source_id:
                    desc.is_active = True
                    if desc.status != "error":
                        desc.status = "active"
                elif desc.is_active:
                    desc.is_active = False
                    if desc.status == "active":
                        desc.status = "standby"

            self.active_source_id = source_id
            self.source_version = new_version
            return self._sources[source_id]
