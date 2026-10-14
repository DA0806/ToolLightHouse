import asyncio
import base64
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException

from ..camera import LatestInferenceWorker
from ..classifier import MockClassifier, SimilarityClassifier
from ..config import ConfigStore, Inventory, Settings, ToolCategory, SAFE_CLASS_ID_REGEX, PORTABLE_SETTINGS_FIELDS
from ..embeddings import EmbeddingEngine, references_fingerprint
from ..gallery import build_gallery, load_validated_gallery
from ..pipeline import Pipeline, StaleFrameError, check_system_readiness
from ..serial_transport import SerialTransport, SimulatedSerial, list_com_ports


@dataclass
class Session:
    session_id: str
    device_id: str
    role: str
    ticket: str
    expires_at: float
    lease_expires_at: float
    ws_tickets: dict[str, float]


class SessionStore:
    def __init__(self, settings: Settings):
        self.settings, self.sessions = settings, {}
        self.controller_id: str | None = None
        self.lease_generation = 0

    def create(self, device_id: str, role: str, key: str | None = None, admin_key: str | None = None) -> Session:
        if role not in {"controller", "reader", "admin"}:
            raise ValueError("role must be controller, reader, or admin")

        if role == "admin":
            # Administrative authorization is deliberately separate from the
            # pairing secret.  Never derive an admin credential from a value
            # that readers/controllers already receive.
            effective_admin = self.settings.admin_key or ""
            supplied = admin_key or key
            if self.settings.auth_required:
                if not effective_admin or not secrets.compare_digest(supplied or "", effective_admin):
                    raise PermissionError("invalid admin key")
        else:
            if self.settings.auth_required and (not self.settings.pairing_key or not secrets.compare_digest(key or "", self.settings.pairing_key)):
                raise PermissionError("invalid pairing key")
            if role == "controller" and self.controller_id is not None:
                current = self.sessions.get(self.controller_id)
                if current and current.expires_at > time.time() and current.lease_expires_at > time.time():
                    raise PermissionError("another controller session is active")
                self.controller_id = None

        now = time.time()
        session = Session(
            str(uuid.uuid4()),
            device_id,
            role,
            secrets.token_urlsafe(24),
            now + self.settings.session_ttl_seconds,
            now + self.settings.lease_ttl_seconds,
            {},
        )
        self.sessions[session.session_id] = session
        if role == "controller":
            self.controller_id = session.session_id
            self.lease_generation += 1
        return session

    def valid(self, session_id: str | None, ticket: str | None, role: str | None = None) -> bool:
        session = self.sessions.get(session_id or "")
        now = time.time()
        owns_controller_lease = session and self.controller_id == session.session_id
        if not session or session.expires_at <= now or (owns_controller_lease and session.lease_expires_at <= now) or (session.role == "controller" and self.controller_id != session.session_id):
            if session and self.controller_id == session.session_id:
                self.controller_id = None
                self.lease_generation += 1
            return False
        valid = bool(ticket) and secrets.compare_digest(session.ticket, ticket or "") and (role is None or session.role == role)
        if valid and owns_controller_lease:
            session.lease_expires_at = now + self.settings.lease_ttl_seconds
        return valid

    def get(self, session_id: str, ticket: str | None = None, role: str | None = None) -> Session:
        if not self.valid(session_id, ticket, role):
            raise PermissionError("session expired or invalid")
        return self.sessions[session_id]

    def issue_ws_ticket(self, session_id: str, ticket: str) -> str:
        session = self.get(session_id, ticket)
        value = secrets.token_urlsafe(24)
        session.ws_tickets[value] = min(session.expires_at, time.time() + 60)
        return value

    def consume_ws_ticket(self, session_id: str, value: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or not self.valid(session_id, session.ticket):
            return False
        expires = session.ws_tickets.pop(value, None)
        return bool(expires and expires > time.time())

    def release(self, session_id: str):
        session = self.sessions.get(session_id)
        if session and self.controller_id == session_id:
            self.controller_id = None
            self.lease_generation += 1
        if session:
            session.expires_at = 0
            session.lease_expires_at = 0
            session.ws_tickets.clear()

    def expire_controller(self) -> bool:
        if not self.controller_id:
            return False
        session = self.sessions.get(self.controller_id)
        if session and session.expires_at > time.time() and session.lease_expires_at > time.time():
            return False
        self.controller_id = None
        self.lease_generation += 1
        return True


async def _read_image(upload, limit: int, max_pixels: int):
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ValueError("uploaded file exceeds configured limit")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError(f"unsupported image format: {source.format}; must be JPEG, PNG, or WebP")
            if source.width * source.height > max_pixels:
                raise ValueError("decoded image exceeds configured pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid image file") from exc
    if image.width * image.height > max_pixels:
        raise ValueError("decoded image exceeds configured pixel limit")
    return np.asarray(image)[:, :, ::-1].copy()


def _generate_thumbnail_data_url(image_array: np.ndarray, max_size=(96, 96)) -> str:
    rgb = image_array[..., ::-1]
    pil_img = Image.fromarray(rgb)
    pil_img.thumbnail(max_size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _clean_portable_settings(s: Settings) -> dict[str, Any]:
    data = {}
    for key in PORTABLE_SETTINGS_FIELDS:
        val = getattr(s, key)
        if key == "roi":
            data[key] = list(val) if val else None
        elif key in {
            "classifier_threshold", "classifier_margin", "text_weight",
            "frame_max_age_seconds", "scene_ttl_seconds", "photo_retention_seconds",
            "prediction_max_age_seconds", "prediction_interval_seconds", "tracking_max_distance"
        }:
            data[key] = float(val)
        elif key in {"segmentation_min_area", "segmentation_max_area", "tracking_max_missed", "batch_size"}:
            data[key] = int(val)
        elif key == "classifier_profiles":
            data[key] = dict(val)
        elif key == "inventory_confirmed":
            data[key] = bool(val)
        else:
            data[key] = val
    return data


def _resolve_contained_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal / symlink escape detected")
    return target


def create_app(
    settings: Settings | None = None,
    pipeline: Pipeline | None = None,
    config_path: Path | str | None = None,
    embedder: Any | None = None,
):
    try:
        from fastapi import FastAPI, File, Header, HTTPException, Query, Response, UploadFile, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import Response as RawResponse
    except ImportError as exc:
        raise RuntimeError("FastAPI dependencies are required; install the default project dependencies") from exc

    pairing_key = os.getenv("TOOLLIGHTHOUSE_PAIRING_KEY", os.getenv("TOOL_LIGHTHOUSE_PAIRING_KEY", ""))
    admin_key = os.getenv("TOOLLIGHTHOUSE_ADMIN_KEY", "")
    base_settings = settings or Settings(pairing_key=pairing_key, admin_key=admin_key)
    store = SessionStore(base_settings)

    effective_config_path = Path(config_path) if config_path else Path("config/app_config.json")
    config_store = ConfigStore(effective_config_path)

    config_loaded_from_file = False
    try:
        draft_settings, draft_inventory, loaded_version = config_store.load(base_settings=base_settings)
        saved_version = loaded_version
        if config_store.active_snapshot is not None:
            persisted_active_settings, persisted_active_inventory, persisted_active_version = config_store.active_snapshot
            active_settings = persisted_active_settings
            active_inventory = persisted_active_inventory
            active_version = persisted_active_version
        else:
            active_settings = Settings.from_dict(draft_settings.to_dict())
            active_inventory = Inventory.from_dict(draft_inventory.to_dict())
            active_settings.inventory = active_inventory
            active_version = loaded_version
        config_loaded_from_file = True
    except (FileNotFoundError, ValueError, KeyError):
        saved_version = 1
        active_version = 1
        draft_settings = Settings.from_dict(base_settings.to_dict())
        draft_inventory = Inventory.from_dict(base_settings.inventory.to_dict())
        draft_settings.inventory = draft_inventory
        active_settings = Settings.from_dict(draft_settings.to_dict())
        active_inventory = Inventory.from_dict(draft_inventory.to_dict())
        active_settings.inventory = active_inventory

    # Gallery caches are local runtime artifacts. Keep one controlled default so
    # a preparation install can build it and recover it after restart.
    storage_root = (effective_config_path.parent / "data") if (config_path is not None and effective_config_path.is_absolute()) else Path("data")
    default_gallery = storage_root / "cache/gallery.json"
    reference_root = storage_root / "references"
    if not draft_settings.gallery_path:
        draft_settings.gallery_path = str(default_gallery)
    if not active_settings.gallery_path:
        active_settings.gallery_path = str(default_gallery)
    draft_settings.inventory = draft_inventory
    active_settings.inventory = active_inventory
    config_store.active_snapshot = (
        Settings.from_dict(active_settings.to_dict()),
        Inventory.from_dict(active_inventory.to_dict()),
        active_version,
    )

    if pipeline is not None:
        pipeline.settings = active_settings
        if embedder is not None:
            pipeline.embedder = embedder
        pipeline.segmenter.roi = active_settings.roi
        pipeline.segmenter.min_area = active_settings.segmentation_min_area
        pipeline.segmenter.max_area = active_settings.segmentation_max_area
        pipeline.tracker.max_distance = active_settings.tracking_max_distance
        pipeline.tracker.max_missed = active_settings.tracking_max_missed
        if active_settings.mode == "mock":
            pipeline.mode = "mock"
            pipeline.classifier = MockClassifier([c.class_id for c in active_inventory.categories])
            pipeline.segmenter.require_background = False
        elif active_settings.mode == "preparation":
            pipeline.mode = "preparation"
            pipeline.classifier = MockClassifier([c.class_id for c in active_inventory.categories])
        elif pipeline.embedder is not None and active_settings.gallery_path:
            try:
                startup_gallery = load_validated_gallery(
                    active_settings.gallery_path,
                    inventory=active_inventory,
                    model_path=active_settings.model_path,
                    gallery_mode=active_settings.gallery_mode,
                    text_weight=active_settings.text_weight,
                )
                threshold, margin = active_settings.classifier_parameters()
                pipeline.classifier = SimilarityClassifier(startup_gallery.references, threshold, margin, gallery_mode=active_settings.gallery_mode)
                pipeline.mode = "real"
                pipeline.preparation_reasons.clear()
                pipeline.segmenter.require_background = True
            except Exception as exc:
                pipeline.mode = "preparation"
                pipeline.preparation_reasons = [f"active gallery unavailable or stale: {exc}"]
                pipeline.classifier = MockClassifier([c.class_id for c in active_inventory.categories])
        else:
            pipeline.mode = "preparation"
            pipeline.classifier = MockClassifier([c.class_id for c in active_inventory.categories])
    else:
        pipeline = Pipeline(active_settings, embedder=embedder, safe_startup=True)

    app = FastAPI(title="ToolLighthouse API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(base_settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    clients: set[WebSocket] = set()
    app_loop: asyncio.AbstractEventLoop | None = None
    inference_worker = LatestInferenceWorker(lambda operation: operation())
    hardware_worker = LatestInferenceWorker(lambda operation: operation())
    stream_task: asyncio.Task | None = None
    stream_generation = 0
    lease_watchdog: asyncio.Task | None = None
    state_lock = threading.RLock()
    lifecycle_lock = asyncio.Lock()
    task_lock = asyncio.Lock()
    scene_generation = 0
    scene_deadline = 0.0
    scene_mask = 0
    closed = False
    calibrating = False
    maintenance_mode = False
    led_test_timer: threading.Timer | None = None
    test_generation = 0
    processed_scene = -1
    references_dirty = any(
        draft_inventory.by_id[class_id].reference_count != active_inventory.by_id[class_id].reference_count
        for class_id in draft_inventory.by_id
    )

    def _draft_matches_active() -> bool:
        draft_payload = draft_settings.to_dict()
        draft_payload["inventory"] = draft_inventory.to_dict()
        active_payload = active_settings.to_dict()
        active_payload["inventory"] = active_inventory.to_dict()
        return draft_payload == active_payload

    prep_task_state = {
        "state": "idle",
        "stage": "idle",
        "progress": 0.0,
        "error": None,
    }

    def process_frame(image, captured_at, photo, scene):
        nonlocal processed_scene
        if processed_scene != scene:
            pipeline.reset_scene()
            processed_scene = scene
        return pipeline.process(image, captured_at, photo, False)

    def auth(session_id: str | None, ticket: str | None, role: str | None = None):
        if not base_settings.auth_required:
            return None
        with state_lock:
            session = store.sessions.get(session_id or "")
            now = time.time()
            if not session or session.expires_at <= now:
                raise HTTPException(status_code=401, detail="session expired or invalid")
            if session.role in {"controller", "admin"} and store.controller_id == session.session_id and session.lease_expires_at <= now:
                store.controller_id = None
                store.lease_generation += 1
                if role == "controller":
                    raise HTTPException(status_code=401, detail="controller lease expired")
            if session.role == "controller" and role == "controller" and store.controller_id != session.session_id:
                if store.controller_id == session.session_id:
                    store.controller_id = None
                    store.lease_generation += 1
                raise HTTPException(status_code=401, detail="controller lease expired")
            if not ticket or not secrets.compare_digest(session.ticket, ticket or ""):
                raise HTTPException(status_code=401, detail="invalid session ticket")
            # Requests must observe an expired *other* controller immediately;
            # relying only on the background watchdog leaves a stale lease
            # blocking the next admin/controller during lightweight ASGI tests
            # and brief event-loop stalls. Ticket validation comes first so an
            # invalid request cannot mutate lease ownership.
            store.expire_controller()
            if session.role == "controller" and store.controller_id == session.session_id:
                session.lease_expires_at = now + base_settings.lease_ttl_seconds

            if role == "admin":
                if session.role != "admin":
                    raise HTTPException(status_code=403, detail="admin role required")
            elif role == "controller":
                if session.role == "reader":
                    raise HTTPException(status_code=401, detail="controller role required")
                if session.role not in {"controller", "admin"}:
                    raise HTTPException(status_code=403, detail="controller or admin role required")
                if store.controller_id not in {None, session.session_id}:
                    raise HTTPException(status_code=409, detail="another controller session is active")
                if store.controller_id is None:
                    store.controller_id = session.session_id
                    store.lease_generation += 1
                session.lease_expires_at = now + base_settings.lease_ttl_seconds
        return session

    async def broadcast(event: dict[str, Any]):
        stale = []
        for client in tuple(clients):
            try:
                await asyncio.wait_for(client.send_json(event), timeout=1.0)
            except Exception:
                stale.append(client)
        clients.difference_update(stale)

    def current(generation, controller, scene):
        session = store.sessions.get(controller or "")
        return (
            not closed
            and generation == store.lease_generation
            and scene == scene_generation
            and (
                not base_settings.auth_required
                or (
                    controller == store.controller_id
                    and session is not None
                    and min(session.expires_at, session.lease_expires_at) > time.time()
                )
            )
        )

    def hardware_command(mask, generation, controller, scene, deadline=0.0, captured_at=None, clearing=False):
        with state_lock:
            if closed or generation != store.lease_generation or scene != scene_generation:
                return None
            if not clearing and maintenance_mode:
                return None
            if not clearing and (not current(generation, controller, scene) or time.time() >= deadline):
                return None
            if hasattr(pipeline.serial, "set_scene_deadline"):
                transport_deadline = deadline
                if not clearing and base_settings.auth_required:
                    session = store.sessions.get(controller)
                    if session:
                        transport_deadline = min(deadline, session.expires_at, session.lease_expires_at)
                pipeline.serial.set_scene_deadline(transport_deadline)
        started = time.perf_counter()
        sent = pipeline.serial.send_mask(mask)
        pipeline.metrics.observe_stage("serial", time.perf_counter() - started)
        status = pipeline.serial.status()
        matched = bool(sent and status.get("confirmed_mask") == mask)
        if captured_at is not None:
            pipeline.metrics.observe_ack(captured_at, getattr(pipeline.serial, "last_ack_at", 0), matched, pipeline.serial.simulated)
        with state_lock:
            if closed or generation != store.lease_generation or scene != scene_generation:
                return None
        return {**status, "ack": matched}

    async def command(*args, **kwargs):
        try:
            return await asyncio.wrap_future(hardware_worker.submit(lambda: hardware_command(*args, **kwargs)))
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return None

    async def clear_scene(event: str, announce_before_reset: bool = False):
        nonlocal scene_generation, scene_deadline, scene_mask
        if announce_before_reset:
            await broadcast({"type": "system", "event": event, "timestamp": time.time()})
        with state_lock:
            scene_generation += 1
            scene_deadline, scene_mask = 0.0, 0
            generation, scene = store.lease_generation, scene_generation
            if hasattr(pipeline.serial, "set_scene_deadline"):
                pipeline.serial.set_scene_deadline(0)
            pipeline.reset_scene()
        # Drain the worker before promoting a new scene/runtime. A queued reset
        # alone can otherwise let a late frame repopulate the new tracker.
        reset_future = inference_worker.submit(pipeline.reset_scene)
        await asyncio.wrap_future(reset_future)
        status = await command(0, generation, store.controller_id, scene, clearing=True)
        if status is not None and generation == store.lease_generation and scene == scene_generation:
            if not announce_before_reset:
                await broadcast({"type": "system", "event": event, "timestamp": time.time()})
            await broadcast({"type": "arduino_ack", "arduino": status})

    async def commit_result(result, generation, controller, scene, photo):
        nonlocal scene_deadline, scene_mask
        deadline = result["timestamp"] + (active_settings.photo_retention_seconds if photo else active_settings.scene_ttl_seconds)
        with state_lock:
            if maintenance_mode or not current(generation, controller, scene) or deadline <= time.time() or scene != scene_generation:
                return None
            scene_deadline = deadline
            scene_mask = result["arduino"]["requested_mask"]
        status = await command(scene_mask, generation, controller, scene, deadline, result["timestamp"])
        with state_lock:
            if maintenance_mode or status is None or not current(generation, controller, scene) or deadline <= time.time() or scene != scene_generation:
                return None
        result["arduino"] = status
        result["metrics"] = pipeline.metrics.as_dict()
        return result
        return result

    @app.on_event("shutdown")
    async def shutdown_worker():
        nonlocal closed, scene_generation, led_test_timer
        if led_test_timer is not None:
            led_test_timer.cancel()
            led_test_timer = None
        if lease_watchdog:
            lease_watchdog.cancel()
            await asyncio.gather(lease_watchdog, return_exceptions=True)
        if stream_task:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        with state_lock:
            closed = True
            scene_generation += 1
            if hasattr(pipeline.serial, "set_scene_deadline"):
                pipeline.serial.set_scene_deadline(0)
        await asyncio.to_thread(inference_worker.close)
        await asyncio.to_thread(hardware_worker.close, None)

        def finish():
            pipeline.serial.send_mask(0)
            close = getattr(pipeline.serial, "close", None)
            if close:
                close()

        await asyncio.to_thread(finish)

    @app.on_event("startup")
    async def start_lease_watchdog():
        nonlocal lease_watchdog, app_loop
        request_loop = asyncio.get_running_loop()
        if app_loop is None or not app_loop.is_running():
            app_loop = request_loop

        async def watch():
            last_status = None
            last_refresh = 0.0
            last_lease = store.lease_generation
            while True:
                await asyncio.sleep(0.05)
                with state_lock:
                    expired = store.expire_controller()
                    lease_changed = last_lease != store.lease_generation
                    last_lease = store.lease_generation
                    if scene_deadline and hasattr(pipeline.serial, "set_scene_deadline"):
                        deadline = scene_deadline
                        session = store.sessions.get(store.controller_id or "")
                        if base_settings.auth_required:
                            deadline = min(deadline, session.expires_at, session.lease_expires_at) if session else 0
                        pipeline.serial.set_scene_deadline(deadline)
                if expired or (lease_changed and store.controller_id is None and scene_deadline):
                    await clear_scene("lease_expired")
                elif scene_deadline and time.time() >= scene_deadline:
                    await clear_scene("scene_expired")
                elif pipeline.serial.simulated and scene_deadline and time.time() - last_refresh >= 1:
                    last_refresh = time.time()
                    await command(scene_mask, store.lease_generation, store.controller_id, scene_generation, scene_deadline)
                status = pipeline.serial.status()
                if "requested_mask" in status and "confirmed_mask" in status:
                    status = {
                        **status,
                        "ack": bool(
                            status.get("confirmed_mask") is not None
                            and status.get("confirmed_mask") == status.get("requested_mask")
                        ),
                    }
                state = tuple(status.get(key) for key in ("connected", "requested_mask", "confirmed_mask", "ack"))
                if last_status is not None and state != last_status:
                    await broadcast({"type": "system", "event": "arduino_state_changed", "timestamp": time.time()})
                    await broadcast({"type": "arduino_ack", "arduino": status})
                last_status = state

        lease_watchdog = asyncio.create_task(watch())

    # -------------------------------------------------------------------------
    # Baseline Endpoints & Sessions
    # -------------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": pipeline.mode, "simulated": bool(pipeline.serial.simulated)}

    @app.post("/v1/sessions")
    async def create_session(
        payload: dict,
        x_pairing_key: str | None = Header(default=None),
        x_admin_key: str | None = Header(default=None),
    ):
        try:
            with state_lock:
                role = str(payload.get("role", "reader"))
                device_id = str(payload.get("device_id", "unknown"))
                session = store.create(device_id, role, key=x_pairing_key, admin_key=x_admin_key)
        except PermissionError as exc:
            raise HTTPException(status_code=409 if "another" in str(exc) else 401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if session.role == "controller":
            await clear_scene("controller_acquired")
        return {
            "session_id": session.session_id,
            "ticket": session.ticket,
            "role": session.role,
            "expires_at": session.expires_at,
            "lease_expires_at": session.lease_expires_at,
            "lease_generation": store.lease_generation,
        }

    @app.post("/v1/sessions/{session_id}/ws-ticket")
    async def create_ws_ticket(session_id: str, x_session_ticket: str | None = Header(default=None)):
        try:
            with state_lock:
                return {"ticket": store.issue_ws_ticket(session_id, x_session_ticket or ""), "expires_in": 60}
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.delete("/v1/sessions/{session_id}")
    async def release_session(session_id: str, x_session_ticket: str | None = Header(default=None)):
        session = auth(session_id, x_session_ticket)
        with state_lock:
            store.release(session_id)
        if session and session.role == "controller":
            await clear_scene("controller_released")
        else:
            await broadcast({"type": "system", "event": "reader_released"})
        return {"released": True}

    @app.get("/v1/status")
    async def status(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        session = auth(x_session_id, x_session_ticket) if base_settings.auth_required else None
        return {
            "session": {"device_id": session.device_id, "role": session.role} if session else None,
            "lease_generation": store.lease_generation,
            "controller_active": store.controller_id is not None,
            "arduino": pipeline.serial.status(),
            "maintenance_mode": maintenance_mode,
            "pipeline_mode": pipeline.mode,
        }

    @app.get("/v1/inventory")
    async def get_inventory():
        return {
            "example": active_settings.inventory.example,
            "categories": [c.to_dict() for c in active_settings.inventory.categories],
        }

    # -------------------------------------------------------------------------
    # System Readiness & Mode Activation
    # -------------------------------------------------------------------------

    @app.get("/v1/system/readiness")
    async def get_readiness(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket)
        return check_system_readiness(active_settings, pipeline)

    @app.post("/v1/system/mode")
    async def set_system_mode(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        auth(x_session_id, x_session_ticket, "admin")
        target_mode = str(payload.get("mode", "")).strip().lower()
        if target_mode not in {"mock", "preparation", "real"}:
            raise HTTPException(status_code=422, detail="mode must be mock, preparation, or real")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        async with lifecycle_lock:
            if target_mode == "real":
                current_readiness = check_system_readiness(active_settings, pipeline)
                if not current_readiness["can_activate_real"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Cannot activate real mode: {'; '.join(current_readiness['preparation_reasons'])}",
                    )
                if pipeline.embedder is None:
                    def init_embedder():
                        return EmbeddingEngine(active_settings.model_path, active_settings.device if active_settings.device != "auto" else None)
                    pipeline.embedder = await asyncio.to_thread(init_embedder)

                def init_gallery():
                    return load_validated_gallery(
                        active_settings.gallery_path,
                        inventory=active_settings.inventory,
                        model_path=active_settings.model_path,
                        gallery_mode=active_settings.gallery_mode,
                        text_weight=active_settings.text_weight,
                    )
                gallery = await asyncio.to_thread(init_gallery)

                await clear_scene("mode_changed")
                with state_lock:
                    draft_settings.mode = "real"
                    active_settings.mode = "real"
                    pipeline.settings.mode = "real"
                    pipeline.mode = "real"
                    pipeline.preparation_reasons.clear()
                    pipeline.segmenter.require_background = True
                    threshold, margin = active_settings.classifier_parameters()
                    pipeline.classifier = SimilarityClassifier(gallery.references, threshold, margin, gallery_mode=active_settings.gallery_mode)
            elif target_mode == "mock":
                await clear_scene("mode_changed")
                with state_lock:
                    draft_settings.mode = "mock"
                    active_settings.mode = "mock"
                    pipeline.settings.mode = "mock"
                    pipeline.mode = "mock"
                    pipeline.preparation_reasons.clear()
                    pipeline.segmenter.require_background = False
                    pipeline.classifier = MockClassifier([c.class_id for c in active_settings.inventory.categories])
            else:
                await clear_scene("mode_changed")
                with state_lock:
                    draft_settings.mode = "preparation"
                    active_settings.mode = "preparation"
                    pipeline.settings.mode = "preparation"
                    pipeline.mode = "preparation"
                    pipeline.classifier = MockClassifier([c.class_id for c in active_settings.inventory.categories])

            saved_version = config_store.save_atomic(draft_settings, draft_inventory)
            if _draft_matches_active():
                active_version = saved_version
            config_store.save_active_atomic(active_settings, active_inventory, active_version)
            await broadcast({"type": "system", "event": "mode_changed", "mode": pipeline.mode})
            return {"mode": pipeline.mode, "readiness": check_system_readiness(active_settings, pipeline)}

    @app.post("/v1/system/download-model")
    async def download_model(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        auth(x_session_id, x_session_ticket, "admin")
        model_path = Path(draft_settings.model_path)
        if model_path.exists():
            return {"status": "present", "path": str(model_path), "detail": "EmbeddingGemma 2 weights already present."}
        return {
            "status": "instructions",
            "detail": (
                f"EmbeddingGemma 2 weights must be placed at '{draft_settings.model_path}'. "
                "Run the repository downloader on the laptop terminal:\n"
                f"  python -m toollighthouse download-model --model-path {draft_settings.model_path}"
            ),
        }

    # -------------------------------------------------------------------------
    # Configuration Management (Persistence, Versioning, Hot-Apply, Rollback)
    # -------------------------------------------------------------------------

    def _needs_gallery_rebuild(act_inv: Inventory, act_sett: Settings, drf_inv: Inventory, drf_sett: Settings, target_gal_path: Path) -> bool:
        # Mock classification is deterministic and does not consume the
        # embedding gallery.  Keep metadata, thresholds, gallery mode and
        # reference edits hot-applicable even when the model/cache is absent;
        # switching to preparation or real mode below still performs the full
        # readiness and cache validation.
        if drf_sett.mode == "mock":
            return False

        active_names = {c.class_id: c.name for c in act_inv.categories}
        draft_names = {c.class_id: c.name for c in drf_inv.categories}
        if active_names != draft_names:
            return True

        active_descs = {c.class_id: list(c.descriptions) for c in act_inv.categories}
        draft_descs = {c.class_id: list(c.descriptions) for c in drf_inv.categories}
        if active_descs != draft_descs:
            return True

        if act_sett.gallery_mode != drf_sett.gallery_mode:
            return True

        if float(act_sett.text_weight) != float(drf_sett.text_weight):
            return True

        # A real runtime must have an initialized encoder. Switching from
        # preparation/mock cannot safely reuse a mock classifier.
        if drf_sett.mode == "real" and pipeline.embedder is None:
            return True

        if not target_gal_path.exists():
            return True
        try:
            load_validated_gallery(
                target_gal_path,
                inventory=drf_inv,
                model_path=drf_sett.model_path,
                gallery_mode=drf_sett.gallery_mode,
                text_weight=drf_sett.text_weight,
            )
        except Exception:
            return True

        return False

    @app.get("/v1/config/status")
    async def get_config_status(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        auth(x_session_id, x_session_ticket)
        return {
            "saved_version": saved_version,
            "active_version": active_version,
            "is_dirty": (saved_version != active_version),
            "task": dict(prep_task_state),
        }

    @app.get("/v1/config")
    async def get_config(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket)
        return {
            "saved_version": saved_version,
            "active_version": active_version,
            "is_dirty": (saved_version != active_version),
            "settings": _clean_portable_settings(draft_settings),
            "inventory": draft_inventory.to_dict(),
        }

    @app.put("/v1/config")
    async def update_config(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, draft_settings, draft_inventory
        auth(x_session_id, x_session_ticket, "admin")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        expected_version = payload.get("expected_version")
        if expected_version is not None:
            if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                raise HTTPException(status_code=422, detail="expected_version must be an integer")
            exp_v = expected_version
            if exp_v != saved_version:
                raise HTTPException(
                    status_code=409,
                    detail=f"Configuration version mismatch: expected {expected_version}, current {saved_version}",
                )

        raw_settings = payload.get("settings")
        raw_inventory = payload.get("inventory")
        if not isinstance(raw_settings, dict) or not isinstance(raw_inventory, dict):
            raise HTTPException(status_code=422, detail="settings and inventory objects are required")

        unknown_settings = set(raw_settings) - PORTABLE_SETTINGS_FIELDS
        if unknown_settings:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown or non-portable settings fields: {sorted(unknown_settings)}",
            )

        try:
            if "categories" not in raw_inventory or not isinstance(raw_inventory["categories"], list):
                raise HTTPException(status_code=422, detail="inventory must contain a list of categories")

            new_cats = [ToolCategory.from_dict(c) for c in raw_inventory["categories"]]
            new_inv = Inventory(new_cats, example=bool(raw_inventory.get("example", False)))

            # Require stable class_ids on ordinary edit
            existing_ids = set(draft_inventory.by_id.keys())
            new_ids = {c.class_id for c in new_cats}
            if new_ids != existing_ids:
                raise HTTPException(
                    status_code=422,
                    detail=f"class_ids cannot be altered on ordinary edit: expected {sorted(existing_ids)}, got {sorted(new_ids)}",
                )

            merged = draft_settings.to_dict()
            for k in PORTABLE_SETTINGS_FIELDS:
                if k in raw_settings:
                    merged[k] = raw_settings[k]
            merged["inventory"] = new_inv.to_dict()

            new_settings = Settings.from_dict(merged)
            new_settings.inventory = new_inv

            saved_version = config_store.save_atomic(new_settings, new_inv)
            draft_settings = new_settings
            draft_inventory = new_inv
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        await broadcast({
            "type": "system",
            "event": "config_changed",
            "saved_version": saved_version,
            "active_version": active_version,
            "is_dirty": (saved_version != active_version),
        })
        return await get_config(x_session_id, x_session_ticket)

    @app.post("/v1/config/apply")
    async def apply_config(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal active_version, saved_version, active_settings, active_inventory, references_dirty
        auth(x_session_id, x_session_ticket, "admin")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is already running")

        target_gal = Path(draft_settings.gallery_path or "data/cache/gallery.json")
        needs_rebuild = draft_settings.mode != "mock" and (
            references_dirty
            or _needs_gallery_rebuild(active_inventory, active_settings, draft_inventory, draft_settings, target_gal)
        )

        if not needs_rebuild:
            # Cheap update path: no embeddings needed
            async with lifecycle_lock:
                await clear_scene("config_applied")
                with state_lock:
                    active_settings = Settings.from_dict(draft_settings.to_dict())
                    active_inventory = Inventory.from_dict(draft_inventory.to_dict())
                    active_settings.inventory = active_inventory
                    pipeline.settings = active_settings
                    pipeline.segmenter.roi = active_settings.roi
                    pipeline.segmenter.min_area = active_settings.segmentation_min_area
                    pipeline.segmenter.max_area = active_settings.segmentation_max_area
                    pipeline.tracker.max_distance = active_settings.tracking_max_distance
                    pipeline.tracker.max_missed = active_settings.tracking_max_missed
                    threshold, margin = active_settings.classifier_parameters()
                    if active_settings.mode == "real":
                        try:
                            candidate_gallery = load_validated_gallery(
                                active_settings.gallery_path,
                                inventory=active_inventory,
                                model_path=active_settings.model_path,
                                gallery_mode=active_settings.gallery_mode,
                                text_weight=active_settings.text_weight,
                            )
                        except Exception as exc:
                            raise HTTPException(status_code=409, detail=f"Cannot activate real mode: gallery is not valid ({exc})") from exc
                        pipeline.classifier = SimilarityClassifier(
                            candidate_gallery.references,
                            threshold,
                            margin,
                            gallery_mode=active_settings.gallery_mode,
                        )
                        pipeline.mode = "real"
                        pipeline.preparation_reasons.clear()
                        pipeline.segmenter.require_background = True
                    else:
                        pipeline.classifier = MockClassifier([c.class_id for c in active_inventory.categories])
                        pipeline.mode = active_settings.mode
                        pipeline.segmenter.require_background = False
                    pipeline.update_classifier_parameters(threshold, margin)
                    active_version = saved_version
                    references_dirty = False
                    config_store.save_active_atomic(active_settings, active_inventory, active_version)

            await broadcast({"type": "system", "event": "config_applied", "active_version": active_version})
            return await get_config(x_session_id, x_session_ticket)

        # Full candidate rebuild path
        async with task_lock:
            prep_task_state["state"] = "running"
            prep_task_state["stage"] = "collecting_references"
            prep_task_state["progress"] = 0.1
            prep_task_state["error"] = None
            await broadcast({"type": "system", "event": "task_status", "task": dict(prep_task_state)})
            await broadcast({"type": "system", "event": "gallery_building", "progress": 0.1, "stage": "collecting_references"})

            try:
                # 1. Ensure embedder exists
                if pipeline.embedder is None:
                    try:
                        import torch
                    except ImportError as exc:
                        raise RuntimeError("PyTorch is required for building candidate gallery embeddings") from exc
                    model_p = Path(draft_settings.model_path) if draft_settings.model_path else None
                    if not model_p or not model_p.is_dir():
                        raise RuntimeError(f"Embedding model weights not found at: {draft_settings.model_path}")
                    def init_emb():
                        return EmbeddingEngine(draft_settings.model_path, draft_settings.device if draft_settings.device != "auto" else None)
                    pipeline.embedder = await asyncio.to_thread(init_emb)

                # 2. Snapshot references and draft config
                ref_root = reference_root
                ref_paths = []
                if ref_root.exists():
                    for c in draft_inventory.categories:
                        cat_dir = ref_root / c.class_id
                        if cat_dir.is_dir():
                            for p in sorted(cat_dir.iterdir()):
                                if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                                    ref_paths.append(p)

                cand_inv = Inventory.from_dict(draft_inventory.to_dict())
                cand_mode = draft_settings.gallery_mode
                cand_weight = float(draft_settings.text_weight)
                cand_model_path = draft_settings.model_path

                prep_task_state["stage"] = "encoding_embeddings"
                prep_task_state["progress"] = 0.4
                await broadcast({"type": "system", "event": "task_status", "task": dict(prep_task_state)})
                await broadcast({"type": "system", "event": "gallery_building", "progress": 0.4, "stage": "encoding_embeddings"})

                def build_candidate_off_loop():
                    image_paths_for_build = ref_paths if cand_mode in {"image-only", "hybrid"} else []
                    cand_gallery, cand_manifest = build_gallery(
                        pipeline.embedder,
                        cand_inv,
                        mode=cand_mode,
                        image_paths=image_paths_for_build,
                        text_weight=cand_weight,
                    )
                    cand_manifest["paths"] = [str(p) for p in image_paths_for_build]
                    cand_manifest["references_root"] = str(ref_root)
                    descriptions = {c.class_id: list(c.descriptions) for c in cand_inv.categories}
                    names = {c.class_id: c.name for c in cand_inv.categories}
                    expected_ids = {c.class_id for c in cand_inv.categories}
                    fp = references_fingerprint(
                        image_paths_for_build,
                        cand_model_path,
                        descriptions=descriptions,
                        names=names,
                        text_weight=cand_weight,
                        model_identity=str(Path(cand_model_path).resolve()),
                        preprocessing="name-description-mean-normalized-v1",
                        gallery_mode=cand_mode,
                        inventory_ids=expected_ids,
                    )
                    tmp_gallery = target_gal.parent / f"{target_gal.name}.candidate.{uuid.uuid4().hex}.json"
                    tmp_gallery.parent.mkdir(parents=True, exist_ok=True)
                    cand_gallery.save(tmp_gallery, fp, cand_manifest)

                    # Validate candidate
                    load_validated_gallery(
                        tmp_gallery,
                        inventory=cand_inv,
                        model_path=cand_model_path,
                        gallery_mode=cand_mode,
                        text_weight=cand_weight,
                    )
                    return cand_gallery, tmp_gallery

                cand_gallery, tmp_gallery = await asyncio.to_thread(build_candidate_off_loop)

                prep_task_state["stage"] = "promoting_gallery"
                prep_task_state["progress"] = 0.9
                await broadcast({"type": "system", "event": "task_status", "task": dict(prep_task_state)})
                await broadcast({"type": "system", "event": "gallery_building", "progress": 0.9, "stage": "promoting_gallery"})

                # Atomic promotion with rollback preservation
                target_gal.parent.mkdir(parents=True, exist_ok=True)
                backup_gal = target_gal.with_name(f"{target_gal.name}.bak")
                if target_gal.exists():
                    shutil.copy2(target_gal, backup_gal)

                try:
                    os.replace(tmp_gallery, target_gal)
                except OSError:
                    tmp_gallery.unlink(missing_ok=True)
                    raise
                draft_settings.gallery_path = str(target_gal)
                persisted_gallery_path = None
                if config_store.target_path.exists():
                    try:
                        persisted_gallery_path = json.loads(config_store.target_path.read_text(encoding="utf-8")).get("settings", {}).get("gallery_path")
                    except (OSError, ValueError, TypeError):
                        persisted_gallery_path = None
                if persisted_gallery_path != str(target_gal):
                    try:
                        saved_version = config_store.save_atomic(draft_settings, draft_inventory)
                    except Exception:
                        if backup_gal.exists():
                            os.replace(backup_gal, target_gal)
                        raise

                # Swap runtime under lifecycle lock
                previous_active_settings = active_settings
                previous_active_inventory = active_inventory
                previous_active_version = active_version
                previous_classifier = pipeline.classifier
                previous_mode = pipeline.mode
                previous_require_background = pipeline.segmenter.require_background
                previous_roi = pipeline.segmenter.roi
                previous_min_area = pipeline.segmenter.min_area
                previous_max_area = pipeline.segmenter.max_area
                previous_max_distance = pipeline.tracker.max_distance
                previous_max_missed = pipeline.tracker.max_missed
                previous_references_dirty = references_dirty
                async with lifecycle_lock:
                    try:
                        await clear_scene("config_applied")
                        with state_lock:
                            if draft_settings.mode == "real":
                                threshold, margin = draft_settings.classifier_parameters()
                                pipeline.classifier = SimilarityClassifier(
                                    cand_gallery.references,
                                    threshold,
                                    margin,
                                    gallery_mode=cand_mode,
                                )
                                pipeline.mode = "real"
                                pipeline.segmenter.require_background = True
                            elif draft_settings.mode == "mock":
                                pipeline.classifier = MockClassifier([c.class_id for c in draft_inventory.categories])
                                pipeline.mode = "mock"
                                pipeline.segmenter.require_background = False
                            else:
                                pipeline.classifier = MockClassifier([c.class_id for c in draft_inventory.categories])
                                pipeline.mode = "preparation"
                            active_settings = Settings.from_dict(draft_settings.to_dict())
                            active_inventory = Inventory.from_dict(draft_inventory.to_dict())
                            active_settings.inventory = active_inventory
                            pipeline.settings = active_settings
                            pipeline.segmenter.roi = active_settings.roi
                            pipeline.segmenter.min_area = active_settings.segmentation_min_area
                            pipeline.segmenter.max_area = active_settings.segmentation_max_area
                            pipeline.tracker.max_distance = active_settings.tracking_max_distance
                            pipeline.tracker.max_missed = active_settings.tracking_max_missed
                            active_version = saved_version
                            references_dirty = False
                        # Promote the known-good runtime in the same restart-safe envelope.
                        active_version = saved_version
                        config_store.save_active_atomic(active_settings, active_inventory, active_version)
                        backup_gal.unlink(missing_ok=True)
                    except Exception:
                        active_settings = previous_active_settings
                        active_inventory = previous_active_inventory
                        active_version = previous_active_version
                        pipeline.classifier = previous_classifier
                        pipeline.mode = previous_mode
                        pipeline.segmenter.require_background = previous_require_background
                        pipeline.segmenter.roi = previous_roi
                        pipeline.segmenter.min_area = previous_min_area
                        pipeline.segmenter.max_area = previous_max_area
                        pipeline.tracker.max_distance = previous_max_distance
                        pipeline.tracker.max_missed = previous_max_missed
                        references_dirty = previous_references_dirty
                        pipeline.settings = previous_active_settings
                        if backup_gal.exists():
                            try:
                                os.replace(backup_gal, target_gal)
                            except OSError:
                                pass
                        else:
                            target_gal.unlink(missing_ok=True)
                        raise

                prep_task_state["state"] = "completed"
                prep_task_state["stage"] = "completed"
                prep_task_state["progress"] = 1.0
                prep_task_state["error"] = None
                await broadcast({"type": "system", "event": "task_status", "task": dict(prep_task_state)})
                await broadcast({"type": "system", "event": "gallery_ready", "active_version": active_version})
                await broadcast({"type": "system", "event": "config_applied", "active_version": active_version})
                return await get_config(x_session_id, x_session_ticket)

            except Exception as exc:
                prep_task_state["state"] = "failed"
                prep_task_state["stage"] = "failed"
                prep_task_state["error"] = str(exc)
                await broadcast({"type": "system", "event": "task_status", "task": dict(prep_task_state)})
                await broadcast({"type": "system", "event": "gallery_error", "detail": str(exc)})
                raise HTTPException(status_code=400, detail=f"Failed to rebuild gallery: {exc}") from exc

    @app.post("/v1/config/rollback")
    async def rollback_config(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version, draft_settings, draft_inventory, active_settings, active_inventory
        auth(x_session_id, x_session_ticket, "admin")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        async with lifecycle_lock:
            try:
                rb_settings, rb_inv, rb_version = config_store.rollback(base_settings=base_settings)
                saved_version = rb_version
                draft_settings = rb_settings
                draft_inventory = rb_inv

                # Check gallery compatibility
                gallery_compatible = False
                if rb_settings.gallery_path and Path(rb_settings.gallery_path).exists():
                    try:
                        load_validated_gallery(
                            rb_settings.gallery_path,
                            inventory=rb_inv,
                            model_path=rb_settings.model_path,
                            gallery_mode=rb_settings.gallery_mode,
                            text_weight=rb_settings.text_weight,
                        )
                        gallery_compatible = True
                    except Exception:
                        gallery_compatible = False

                if pipeline.mode == "real" and not gallery_compatible:
                    pipeline.mode = "preparation"
                    pipeline.preparation_reasons.append("Gallery incompatible with rolled-back inventory; apply config to rebuild")
                    pipeline.classifier = MockClassifier([c.class_id for c in rb_inv.categories])

                await clear_scene("config_rollback")
                with state_lock:
                    active_settings = Settings.from_dict(rb_settings.to_dict())
                    active_inventory = Inventory.from_dict(rb_inv.to_dict())
                    active_settings.inventory = active_inventory
                    pipeline.settings = active_settings
                    pipeline.segmenter.roi = rb_settings.roi
                    pipeline.segmenter.min_area = active_settings.segmentation_min_area
                    pipeline.segmenter.max_area = active_settings.segmentation_max_area
                    pipeline.tracker.max_distance = active_settings.tracking_max_distance
                    pipeline.tracker.max_missed = active_settings.tracking_max_missed
                    threshold, margin = rb_settings.classifier_parameters()
                    pipeline.update_classifier_parameters(threshold, margin)
                    active_version = saved_version
                    config_store.save_active_atomic(active_settings, active_inventory, active_version)

                await broadcast({"type": "system", "event": "config_rolled_back", "saved_version": saved_version, "active_version": active_version})
                return await get_config(x_session_id, x_session_ticket)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="No backup configuration found to rollback to") from exc

    @app.get("/v1/config/export")
    async def export_config(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket, "admin")
        return config_store.export_portable(draft_settings, draft_inventory)

    @app.post("/v1/config/import")
    async def import_config(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, draft_settings, draft_inventory
        auth(x_session_id, x_session_ticket, "admin")
        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="portable configuration must be a JSON object")
        expected_version = payload.get("expected_version")
        if expected_version is not None:
            if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                raise HTTPException(status_code=422, detail="expected_version must be an integer")
            if expected_version != saved_version:
                raise HTTPException(status_code=409, detail=f"Configuration version mismatch: expected {expected_version}, current {saved_version}")
        try:
            async with lifecycle_lock:
                if task_lock.locked():
                    raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")
                new_settings, new_inv = config_store.import_portable(payload, draft_settings)
                saved_version = config_store.save_atomic(new_settings, new_inv)
                draft_settings = new_settings
                draft_inventory = new_inv
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await broadcast({
            "type": "system",
            "event": "config_changed",
            "saved_version": saved_version,
            "active_version": active_version,
            "is_dirty": (saved_version != active_version),
        })
        return await get_config(x_session_id, x_session_ticket)

    # -------------------------------------------------------------------------
    # Visual References Management
    # -------------------------------------------------------------------------

    def _validate_safe_class_id(class_id: str):
        if not SAFE_CLASS_ID_REGEX.fullmatch(class_id) or class_id not in draft_inventory.by_id:
            raise HTTPException(status_code=400, detail=f"invalid or unknown class_id: {class_id!r}")
        ref_root = reference_root.resolve()
        return _resolve_contained_path(ref_root, class_id)

    @app.post("/v1/tools/{class_id}/references")
    async def upload_reference(
        class_id: str,
        file: UploadFile = File(...),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal references_dirty, draft_inventory, saved_version
        auth(x_session_id, x_session_ticket, "admin")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        cat_dir = _validate_safe_class_id(class_id)
        cat_dir.mkdir(parents=True, exist_ok=True)

        try:
            image_array = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        image_id = str(uuid.uuid4())
        target_file = _resolve_contained_path(cat_dir, f"{image_id}.jpg")
        cv2.imwrite(str(target_file), image_array, [cv2.IMWRITE_JPEG_QUALITY, 90])

        thumbnail_data_url = _generate_thumbnail_data_url(image_array)
        references_dirty = True

        # Update reference count in draft inventory
        updated_cats = []
        for c in draft_inventory.categories:
            if c.class_id == class_id:
                count = len([p for p in cat_dir.glob("*.jpg") if p.is_file()])
                updated_cats.append(ToolCategory(
                    c.class_id, c.name, c.location, c.led_index, c.pin,
                    c.descriptions, c.display_name, reference_count=count
                ))
            else:
                updated_cats.append(c)
        draft_inventory = Inventory(updated_cats, example=draft_inventory.example)
        draft_settings.inventory = draft_inventory
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)

        await broadcast({"type": "system", "event": "references_updated", "class_id": class_id})
        return {
            "image_id": image_id,
            "class_id": class_id,
            "thumbnail_data_url": thumbnail_data_url,
            "size_bytes": target_file.stat().st_size,
            "created_at": time.time(),
        }

    @app.get("/v1/tools/{class_id}/references")
    async def list_references(
        class_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        auth(x_session_id, x_session_ticket)
        cat_dir = _validate_safe_class_id(class_id)
        def scan_references():
            results = []
            if cat_dir.is_dir():
                for p in sorted(cat_dir.glob("*.jpg")):
                    if not p.is_file():
                        continue
                    resolved_p = _resolve_contained_path(cat_dir, p.name)
                    try:
                        with Image.open(resolved_p) as source:
                            if source.width * source.height > base_settings.max_pixels:
                                continue
                            img = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))[:, :, ::-1].copy()
                    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
                        continue
                    results.append({
                        "image_id": resolved_p.stem,
                        "filename": resolved_p.name,
                        "size_bytes": resolved_p.stat().st_size,
                        "created_at": resolved_p.stat().st_mtime,
                        "thumbnail_data_url": _generate_thumbnail_data_url(img),
                    })
            return results
        results = await asyncio.to_thread(scan_references)
        return {"class_id": class_id, "references": results}

    @app.get("/v1/tools/{class_id}/references/{image_id}")
    async def get_reference_content(
        class_id: str,
        image_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        auth(x_session_id, x_session_ticket)
        cat_dir = _validate_safe_class_id(class_id)
        if not re.fullmatch(r"^[a-zA-Z0-9_-]{1,64}$", image_id):
            raise HTTPException(status_code=400, detail="invalid image_id")
        target_file = _resolve_contained_path(cat_dir, f"{image_id}.jpg")
        if not target_file.is_file():
            raise HTTPException(status_code=404, detail="reference image not found")
        return RawResponse(content=target_file.read_bytes(), media_type="image/jpeg")

    @app.delete("/v1/tools/{class_id}/references/{image_id}")
    async def delete_reference(
        class_id: str,
        image_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal references_dirty, draft_inventory, saved_version
        auth(x_session_id, x_session_ticket, "admin")

        if task_lock.locked():
            raise HTTPException(status_code=409, detail="A configuration preparation or apply task is currently running; retry after completion")

        cat_dir = _validate_safe_class_id(class_id)
        if not re.fullmatch(r"^[a-zA-Z0-9_-]{1,64}$", image_id):
            raise HTTPException(status_code=400, detail="invalid image_id")
        target_file = _resolve_contained_path(cat_dir, f"{image_id}.jpg")
        if not target_file.is_file():
            raise HTTPException(status_code=404, detail="reference image not found")
        target_file.unlink()
        references_dirty = True

        updated_cats = []
        for c in draft_inventory.categories:
            if c.class_id == class_id:
                count = len([p for p in cat_dir.glob("*.jpg") if p.is_file()])
                updated_cats.append(ToolCategory(
                    c.class_id, c.name, c.location, c.led_index, c.pin,
                    c.descriptions, c.display_name, reference_count=count
                ))
            else:
                updated_cats.append(c)
        draft_inventory = Inventory(updated_cats, example=draft_inventory.example)
        draft_settings.inventory = draft_inventory
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)

        await broadcast({"type": "system", "event": "references_updated", "class_id": class_id})
        return {"deleted": True, "image_id": image_id, "class_id": class_id}

    # -------------------------------------------------------------------------
    # Tray Calibration & ROI
    # -------------------------------------------------------------------------

    @app.get("/v1/calibration")
    async def get_calibration(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket)
        has_bg = pipeline.segmenter.background is not None and pipeline.segmenter.background.size > 0
        return {
            "calibrated": has_bg,
            "status": "ready" if has_bg else "missing",
            "image": {
                "width": int(pipeline.segmenter.background.shape[1]),
                "height": int(pipeline.segmenter.background.shape[0]),
                "orientation": "normalized",
            } if has_bg else None,
            "roi": list(pipeline.segmenter.roi) if pipeline.segmenter.roi else None,
        }

    @app.get("/v1/calibration/background.jpg")
    async def get_calibration_background(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket)
        if pipeline.segmenter.background is None or pipeline.segmenter.background.size == 0:
            raise HTTPException(status_code=404, detail="No calibration background available")
        ok, buf = cv2.imencode(".jpg", pipeline.segmenter.background, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to encode background image")
        return RawResponse(content=buf.tobytes(), media_type="image/jpeg")

    @app.put("/v1/calibration/roi")
    async def set_roi(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        auth(x_session_id, x_session_ticket, "admin")
        roi = payload.get("roi")
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            raise HTTPException(status_code=400, detail="roi must be [x, y, width, height]")
        try:
            x, y, w, h = [int(v) for v in roi]
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="roi coordinates must be integers")
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            raise HTTPException(status_code=400, detail="roi dimensions must be positive non-negative values")

        if pipeline.segmenter.background is not None and pipeline.segmenter.background.size > 0:
            bg_h, bg_w = pipeline.segmenter.background.shape[:2]
            if x + w > bg_w or y + h > bg_h:
                raise HTTPException(status_code=400, detail=f"ROI exceeds background dimensions ({bg_w}x{bg_h})")

        async with lifecycle_lock:
            with state_lock:
                pipeline.segmenter.roi = (x, y, w, h)
                active_settings.roi = (x, y, w, h)
                draft_settings.roi = (x, y, w, h)
                if active_settings.roi_path:
                    roi_p = Path(active_settings.roi_path)
                    roi_p.parent.mkdir(parents=True, exist_ok=True)
                    tmp_roi = roi_p.with_name(f"{roi_p.name}.tmp.{uuid.uuid4().hex}")
                    tmp_roi.write_text(json.dumps([x, y, w, h]), encoding="utf-8")
                    os.replace(tmp_roi, roi_p)
                saved_version = config_store.save_atomic(draft_settings, draft_inventory)
                if _draft_matches_active():
                    active_version = saved_version
                config_store.save_active_atomic(active_settings, active_inventory, active_version)

        await broadcast({"type": "system", "event": "roi_updated", "roi": [x, y, w, h]})
        return {"calibrated": True, "roi": [x, y, w, h]}

    @app.post("/v1/calibration/background")
    async def set_background(
        file: UploadFile = File(...),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
        x_roi: str | None = Header(default=None),
    ):
        nonlocal calibrating, saved_version, active_version
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket, "admin")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")
        async with lifecycle_lock:
            try:
                background = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
                pending_roi = pipeline.segmenter.roi
                if x_roi:
                    roi = tuple(int(v) for v in json.loads(x_roi))
                    if len(roi) != 4 or any(v < 0 for v in roi) or roi[2] <= 0 or roi[3] <= 0 or roi[0] + roi[2] > background.shape[1] or roi[1] + roi[3] > background.shape[0]:
                        raise ValueError("ROI must be [x,y,width,height] inside the calibration image")
                    pending_roi = roi
                if pending_roi and (pending_roi[0] + pending_roi[2] > background.shape[1] or pending_roi[1] + pending_roi[3] > background.shape[0]):
                    raise ValueError("configured ROI exceeds calibration image dimensions")
                generation = store.lease_generation
                calibrating = True
                await clear_scene("calibration_started", announce_before_reset=True)
                scene = scene_generation

                def calibrate():
                    nonlocal saved_version, active_version
                    with state_lock:
                        if closed or scene != scene_generation:
                            raise ValueError("scene changed during calibration")
                        if base_settings.auth_required:
                            session = store.sessions.get(x_session_id or "")
                            if not session or session.expires_at <= time.time():
                                raise ValueError("session expired during calibration")
                            if session.role != "admin" and not current(generation, x_session_id, scene):
                                raise ValueError("controller changed during calibration")
                    pipeline.segmenter.set_background(background)
                    pipeline.segmenter.roi = pending_roi
                    active_settings.roi = pending_roi
                    draft_settings.roi = pending_roi
                    pipeline.reset_scene()
                    if active_settings.roi_path and pending_roi:
                        roi_p = Path(active_settings.roi_path)
                        roi_p.parent.mkdir(parents=True, exist_ok=True)
                        tmp_roi = roi_p.with_name(f"{roi_p.name}.tmp.{uuid.uuid4().hex}")
                        tmp_roi.write_text(json.dumps(list(pending_roi)), encoding="utf-8")
                        os.replace(tmp_roi, roi_p)
                    if active_settings.background_path:
                        bg_p = Path(active_settings.background_path)
                        bg_p.parent.mkdir(parents=True, exist_ok=True)
                        tmp_bg = bg_p.with_name(f"{bg_p.name}.tmp.{uuid.uuid4().hex}.png")
                        if not cv2.imwrite(str(tmp_bg), background):
                            raise ValueError("unable to save calibration image")
                        os.replace(tmp_bg, bg_p)
                    saved_version = config_store.save_atomic(draft_settings, draft_inventory)
                    if _draft_matches_active():
                        active_version = saved_version
                    config_store.save_active_atomic(active_settings, active_inventory, active_version)

                await asyncio.wrap_future(inference_worker.submit(calibrate))
            except asyncio.CancelledError as exc:
                raise HTTPException(status_code=409, detail="calibration superseded") from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                calibrating = False
        return {
            "calibrated": True,
            "roi": list(pipeline.segmenter.roi) if pipeline.segmenter.roi else None,
            "image": {
                "width": int(pipeline.segmenter.background.shape[1]),
                "height": int(pipeline.segmenter.background.shape[0]),
                "orientation": "normalized",
            },
        }

    # -------------------------------------------------------------------------
    # Arduino Nano Hardware & Maintenance Mode
    # -------------------------------------------------------------------------

    @app.get("/v1/arduino/ports")
    async def get_arduino_ports(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        auth(x_session_id, x_session_ticket, "admin")
        return {"ports": list_com_ports()}

    @app.post("/v1/arduino/connect")
    async def connect_arduino(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        auth(x_session_id, x_session_ticket, "admin")
        async with lifecycle_lock:
            port = str(payload.get("port", "")).strip()
            simulated = bool(payload.get("simulated", False))
            if not simulated:
                if not port:
                    raise HTTPException(status_code=422, detail="port is required for physical connection")
                enumerated = [p["port"] for p in list_com_ports()]
                if port not in enumerated:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Port {port!r} is not an enumerated COM port. Available: {enumerated}",
                    )

            # Close previous transport safely
            old_serial = pipeline.serial
            if hasattr(old_serial, "close"):
                try:
                    await asyncio.to_thread(old_serial.close)
                except Exception:
                    pass
            elif hasattr(old_serial, "disconnect"):
                try:
                    await asyncio.to_thread(old_serial.disconnect)
                except Exception:
                    pass

            if simulated:
                pipeline.serial = SimulatedSerial()
            else:
                def open_serial():
                    transport = SerialTransport(port)
                    transport.open()
                    return transport
                pipeline.serial = await asyncio.to_thread(open_serial)

            await clear_scene("arduino_connected")
            status_info = pipeline.serial.status()
            await broadcast({"type": "system", "event": "arduino_state_changed", "arduino": status_info})
            return {"connected": status_info["connected"], "arduino": status_info}

    @app.post("/v1/arduino/disconnect")
    async def disconnect_arduino(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        nonlocal led_test_timer, test_generation
        auth(x_session_id, x_session_ticket, "admin")
        async with lifecycle_lock:
            test_generation += 1
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None

            await clear_scene("arduino_disconnected")

            def close_serial():
                if hasattr(pipeline.serial, "disconnect"):
                    pipeline.serial.disconnect()
                elif hasattr(pipeline.serial, "close"):
                    pipeline.serial.close()

            await asyncio.to_thread(close_serial)
            status_info = pipeline.serial.status()
            await broadcast({"type": "system", "event": "arduino_state_changed", "arduino": status_info})
            return {"arduino": status_info}

    @app.post("/v1/arduino/maintenance/enter")
    async def enter_maintenance(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        nonlocal maintenance_mode
        auth(x_session_id, x_session_ticket, "admin")
        async with lifecycle_lock:
            maintenance_mode = True
            await clear_scene("maintenance_entered")
            status_info = pipeline.serial.status()
            await broadcast({"type": "system", "event": "maintenance_mode", "maintenance_mode": True, "arduino": status_info})
            return {"maintenance_mode": True, "arduino": status_info}

    @app.post("/v1/arduino/maintenance/leave")
    async def leave_maintenance(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        nonlocal maintenance_mode, led_test_timer, test_generation
        auth(x_session_id, x_session_ticket, "admin")
        async with lifecycle_lock:
            test_generation += 1
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None

            await asyncio.to_thread(pipeline.serial.send_mask, 0)
            maintenance_mode = False
            await clear_scene("maintenance_left")

            status_info = pipeline.serial.status()
            await broadcast({"type": "system", "event": "maintenance_mode", "maintenance_mode": False, "arduino": status_info})
            return {"maintenance_mode": False, "arduino": status_info}

    @app.post("/v1/arduino/maintenance/test-led/{idx}")
    async def test_led(
        idx: int,
        duration: float = Query(default=2.0, ge=0.1, le=3.0),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal app_loop, led_test_timer, test_generation
        auth(x_session_id, x_session_ticket, "admin")

        # TestClient and lightweight ASGI hosts may serve requests without
        # running the optional startup hook. Capture the request loop here so
        # the timer can still marshal its ACK event safely.
        request_loop = asyncio.get_running_loop()
        if app_loop is None or not app_loop.is_running():
            app_loop = request_loop

        with state_lock:
            if not maintenance_mode:
                raise HTTPException(status_code=409, detail="Must enter maintenance mode before testing LEDs")
            if not 0 <= idx <= 8:
                raise HTTPException(status_code=400, detail="led_index must be between 0 and 8")

            test_generation += 1
            current_gen = test_generation
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None

        mask = 1 << idx
        effective_duration = min(max(duration, 0.1), 3.0)
        target_serial = pipeline.serial

        if hasattr(target_serial, "set_scene_deadline"):
            target_serial.set_scene_deadline(time.time() + effective_duration)
        await asyncio.to_thread(target_serial.send_mask, mask)
        status_info = target_serial.status()

        def do_auto_off(gen: int, s_ref: Any):
            with state_lock:
                if not maintenance_mode or gen != test_generation or s_ref is not pipeline.serial:
                    return
            sent = False
            try:
                if hasattr(s_ref, "set_scene_deadline"):
                    s_ref.set_scene_deadline(0)
                sent = bool(s_ref.send_mask(0))
            except Exception:
                pass
            with state_lock:
                if not maintenance_mode or gen != test_generation or s_ref is not pipeline.serial:
                    return
                raw_status = s_ref.status()
                status = {**raw_status, "ack": bool(sent and raw_status.get("confirmed_mask") == 0)}
                loop = app_loop
            # Timer callbacks run on a worker thread. Marshal the ACK event onto
            # FastAPI's loop so websocket clients immediately observe the
            # physical result, including an explicit negative ACK.
            if loop is not None and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(
                        broadcast({"type": "arduino_ack", "arduino": status}),
                        loop,
                    )
                except RuntimeError:
                    pass

        led_test_timer = threading.Timer(effective_duration, do_auto_off, args=[current_gen, target_serial])
        led_test_timer.daemon = True
        led_test_timer.start()

        await broadcast({"type": "arduino_ack", "arduino": status_info})
        return {
            "led_index": idx,
            "mask": mask,
            "duration_seconds": effective_duration,
            "arduino": status_info,
        }

    @app.post("/v1/arduino/maintenance/all-off")
    async def all_off(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        nonlocal led_test_timer, test_generation
        auth(x_session_id, x_session_ticket, "admin")

        with state_lock:
            if not maintenance_mode:
                raise HTTPException(status_code=409, detail="Must enter maintenance mode before issuing maintenance commands")
            test_generation += 1
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None

        await asyncio.to_thread(pipeline.serial.send_mask, 0)
        status_info = pipeline.serial.status()
        await broadcast({"type": "arduino_ack", "arduino": status_info})
        return {"arduino": status_info}

    # -------------------------------------------------------------------------
    # Analysis & Inference Endpoints
    # -------------------------------------------------------------------------

    @app.post("/v1/analyze/photo")
    async def analyze_photo(
        file: UploadFile = File(...),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        if maintenance_mode:
            raise HTTPException(status_code=409, detail="hardware is in maintenance mode")
        if pipeline.mode == "preparation":
            reasons = "; ".join(pipeline.preparation_reasons) if pipeline.preparation_reasons else "calibration or gallery missing"
            raise HTTPException(status_code=409, detail=f"system is in preparation mode: {reasons}")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")

        generation, scene = store.lease_generation, scene_generation
        try:
            image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            captured_at = time.time()
            result = await asyncio.wait_for(
                asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, True, scene))),
                base_settings.inference_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            if current(generation, x_session_id, scene):
                await clear_scene("inference_timeout")
            raise HTTPException(status_code=504, detail="inference timed out") from exc
        except StaleFrameError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            if "preparation mode" in str(exc).lower():
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncio.CancelledError:
            raise HTTPException(status_code=409, detail="photo superseded by a newer frame")

        result = await commit_result(result, generation, x_session_id, scene, True)
        if result is None:
            raise HTTPException(status_code=409, detail="controller lease expired during inference")
        await broadcast({"type": "detection", "result": result})
        await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})
        return result

    @app.post("/v1/stream/frame")
    async def stream_frame(
        file: UploadFile = File(...),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal stream_task, stream_generation
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        if maintenance_mode:
            raise HTTPException(status_code=409, detail="hardware is in maintenance mode")
        if pipeline.mode == "preparation":
            reasons = "; ".join(pipeline.preparation_reasons) if pipeline.preparation_reasons else "calibration or gallery missing"
            raise HTTPException(status_code=409, detail=f"system is in preparation mode: {reasons}")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")

        try:
            image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        stream_generation += 1
        generation = stream_generation
        lease_generation = store.lease_generation
        scene = scene_generation
        controller_id = x_session_id
        frame_id = str(uuid.uuid4())
        if stream_task and not stream_task.done():
            stream_task.cancel()

        async def process_latest():
            try:
                captured_at = time.time()
                result = await asyncio.wait_for(
                    asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, False, scene))),
                    base_settings.inference_timeout_seconds,
                )
            except asyncio.TimeoutError:
                if generation == stream_generation and current(lease_generation, controller_id, scene):
                    await clear_scene("inference_timeout")
                return
            except asyncio.CancelledError:
                return
            except (ValueError, RuntimeError) as exc:
                await broadcast({"type": "system", "event": "frame_rejected", "detail": str(exc)})
                return

            result["frame_id"] = frame_id
            if generation != stream_generation or not current(lease_generation, controller_id, scene):
                return
            result = await commit_result(result, lease_generation, controller_id, scene, False)
            if result is None:
                return
            await broadcast({"type": "detection", "result": result})
            await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})

        stream_task = asyncio.create_task(process_latest())
        return {"accepted": True, "latest_only": True, "generation": generation, "frame_id": frame_id, "timestamp": time.time()}

    @app.websocket("/v1/ws")
    async def websocket(websocket: WebSocket, session_id: str | None = None, ticket: str | None = None):
        nonlocal app_loop
        app_loop = asyncio.get_running_loop()
        with state_lock:
            authorized = not base_settings.auth_required or store.consume_ws_ticket(session_id or "", ticket or "")
        if not authorized:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        clients.add(websocket)
        try:
            await websocket.send_json({"type": "system", "event": "connected", "lease_generation": store.lease_generation})
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                session = store.sessions.get(session_id or "")
                if base_settings.auth_required and (
                    not session
                    or session.expires_at <= time.time()
                    or (session.role == "controller" and (session.lease_expires_at <= time.time() or store.controller_id != session.session_id))
                ):
                    await websocket.close(code=1008)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(websocket)

    frontend_dist = Path("frontend/dist")
    if frontend_dist.is_dir() and (frontend_dist / "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app
