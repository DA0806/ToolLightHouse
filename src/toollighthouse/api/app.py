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


from .sessions import Session, UnifiedSessionStore, PhysicalControlLease
from ..camera_registry import CameraSourceRegistry, CameraSourceDescriptor
from .signaling import CameraInviteStore, WebRTCSignalingHub
from ..camera import OpenCVCameraService, enumerate_opencv_cameras
from ..config import CalibrationProfile, apply_canonical_transform

# Backward compatibility alias
SessionStore = UnifiedSessionStore



async def _read_image(upload, limit: int, max_pixels: int):
    data = await upload.read(limit + 1)
    return _decode_image_bytes(data, limit, max_pixels)


def _decode_image_bytes(data: bytes, limit: int, max_pixels: int):
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
        from fastapi import Body, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import HTMLResponse, Response as RawResponse
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
    active_stream_runs: dict[str, int] = {}
    stopped_stream_runs: dict[str, int] = {}
    last_stream_mono = 0.0
    lease_watchdog: asyncio.Task | None = None
    camera_registry = CameraSourceRegistry(initial_active="remote_phone")
    if hasattr(active_settings, "camera_sources") and active_settings.camera_sources:
        for s_id, s_conf in active_settings.camera_sources.items():
            if isinstance(s_conf, dict):
                camera_registry.update_source_status(
                    s_id,
                    "active" if s_id == camera_registry.active_source_id else "standby",
                    resolution=s_conf.get("resolution"),
                    fps=s_conf.get("fps"),
                )
    invite_store = CameraInviteStore()
    signaling_hub = WebRTCSignalingHub(invite_store, allowed_origins=base_settings.cors_origins)
    opencv_service: OpenCVCameraService | None = None
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

    def capture_context() -> tuple[str, int, int, str | None, int]:
        source_id, source_version = camera_registry.snapshot_active()
        with state_lock:
            return source_id, source_version, store.lease_generation, store.controller_id, scene_generation

    def normalize_for_source(image, source_id: str):
        profile = active_settings.calibration_profiles.get(source_id)
        if profile is not None:
            expected = profile.resolution
            if profile.status == "ready" and (
                int(expected.get("width", image.shape[1])) != int(image.shape[1])
                or int(expected.get("height", image.shape[0])) != int(image.shape[0])
            ):
                raise ValueError("calibration profile is incompatible with the active source resolution")
            image = apply_canonical_transform(image, orientation=profile.orientation, crop=profile.crop)
        return image

    def context_current(source_id: str, source_version: int, config_version: int) -> bool:
        current_source, current_version = camera_registry.snapshot_active()
        return current_source == source_id and current_version == source_version and active_version == config_version

    def auth(session_id: str | None, ticket: str | None, role: str | None = None):
        if not base_settings.auth_required:
            return None
        with state_lock:
            session = store.sessions.get(session_id or "")
            now = time.time()
            if not session or session.expires_at <= now:
                raise HTTPException(status_code=401, detail="session expired or invalid")
            if not ticket or not secrets.compare_digest(session.ticket, ticket or ""):
                raise HTTPException(status_code=401, detail="invalid session ticket")

            if session.role in {"controller", "admin"} and store.controller_id == session.session_id and session.lease_expires_at <= now:
                store.controller_id = None
                store.lease_generation += 1
                if role == "controller":
                    raise HTTPException(status_code=409, detail="controller lease expired")
            if session.role == "controller" and role == "controller" and store.controller_id != session.session_id:
                if store.controller_id == session.session_id:
                    store.controller_id = None
                    store.lease_generation += 1
                raise HTTPException(status_code=409, detail="controller lease expired")

            # Requests must observe an expired *other* controller immediately;
            # relying only on the background watchdog leaves a stale lease
            # blocking the next admin/controller during lightweight ASGI tests
            # and brief event-loop stalls. Ticket validation comes first so an
            # invalid request cannot mutate lease ownership.
            store.expire_controller()
            if session.role == "controller" and store.controller_id == session.session_id:
                session.lease_expires_at = now + base_settings.lease_ttl_seconds
                if store.lease and store.lease.holder_session_id == session.session_id:
                    store.lease.lease_expires_at = now + base_settings.lease_ttl_seconds

            if role == "admin":
                if session.role != "admin":
                    raise HTTPException(status_code=403, detail="admin role required")
            elif role == "controller":
                if session.role not in {"controller", "admin"}:
                    raise HTTPException(status_code=403, detail="controller or admin role required")
                if store.controller_id not in {None, session.session_id}:
                    raise HTTPException(status_code=409, detail="another controller session is active")
                if store.controller_id is None:
                    store.controller_id = session.session_id
                    store.lease_generation += 1
                session.lease_expires_at = now + base_settings.lease_ttl_seconds
                if store.lease and store.lease.holder_session_id == session.session_id:
                    store.lease.lease_expires_at = now + base_settings.lease_ttl_seconds
        return session

    async def broadcast(event: dict[str, Any]):
        stale = []
        for client in tuple(clients):
            try:
                await asyncio.wait_for(client.send_json(event), timeout=1.0)
            except Exception:
                stale.append(client)
        clients.difference_update(stale)

    def current(generation, controller, scene, source_id: str | None = None, source_version: int | None = None, config_version: int | None = None):
        session = store.sessions.get(controller or "")
        return (
            not closed
            and generation == store.lease_generation
            and scene == scene_generation
            and (source_id is None or context_current(source_id, source_version or -1, config_version if config_version is not None else active_version))
            and (
                not base_settings.auth_required
                or (
                    controller == store.controller_id
                    and session is not None
                    and session.expires_at > time.time()
                    and session.lease_expires_at > time.time()
                )
            )
        )

    def inference_rejection_detail(generation, controller):
        """Distinguish a lost lease from a deliberately superseded result."""
        with state_lock:
            session = store.sessions.get(controller or "")
            lease_lost = (
                closed
                or generation != store.lease_generation
                or controller != store.controller_id
                or (
                    base_settings.auth_required
                    and (
                        session is None
                        or session.expires_at <= time.time()
                        or session.lease_expires_at <= time.time()
                    )
                )
            )
        if lease_lost:
            return {"code": "lease_lost", "message": "physical control lease lost during inference"}
        return {
            "code": "inference_discarded",
            "message": "inference result discarded because the scene, source, configuration, or maintenance state changed",
        }

    def hardware_command(mask, generation, controller, scene, deadline=0.0, captured_at=None, clearing=False):
        expected_transport_generation = None
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
                expected_transport_generation = pipeline.serial.set_scene_deadline(transport_deadline)
        started = time.perf_counter()
        if expected_transport_generation is None:
            sent = pipeline.serial.send_mask(mask)
        else:
            sent = pipeline.serial.send_mask(mask, expected_generation=expected_transport_generation)
        pipeline.metrics.observe_stage("serial", time.perf_counter() - started)
        status = pipeline.serial.status()
        matched = bool(sent and status.get("confirmed_mask") == mask)
        if captured_at is not None:
            pipeline.metrics.observe_ack(captured_at, getattr(pipeline.serial, "last_ack_at", 0), matched, pipeline.serial.simulated)
        with state_lock:
            if closed or generation != store.lease_generation or scene != scene_generation:
                return None
        return {**status, "ack": matched}

    def maintenance_hardware_command(mask: int, generation: int, controller: str, scene: int, test_gen: int, target_serial_ref: Any, deadline: float):
        expected_transport_generation = None
        with state_lock:
            if closed or not maintenance_mode or generation != store.lease_generation or scene != scene_generation:
                return None
            if store.controller_id != controller or test_generation != test_gen or pipeline.serial is not target_serial_ref:
                return None
            if base_settings.auth_required:
                session = store.sessions.get(controller)
                if not session or session.expires_at <= time.time() or session.lease_expires_at <= time.time():
                    return None
            if hasattr(target_serial_ref, "set_scene_deadline"):
                transport_deadline = deadline
                if base_settings.auth_required:
                    transport_deadline = min(transport_deadline, session.expires_at, session.lease_expires_at)
                expected_transport_generation = target_serial_ref.set_scene_deadline(transport_deadline)
        started = time.perf_counter()
        if expected_transport_generation is None:
            sent = target_serial_ref.send_mask(mask)
        else:
            sent = target_serial_ref.send_mask(mask, expected_generation=expected_transport_generation)
        pipeline.metrics.observe_stage("serial", time.perf_counter() - started)
        status = target_serial_ref.status()
        matched = bool(sent and status.get("confirmed_mask") == mask)
        with state_lock:
            if closed or not maintenance_mode or generation != store.lease_generation or scene != scene_generation:
                return None
            if store.controller_id != controller or test_generation != test_gen or pipeline.serial is not target_serial_ref:
                return None
        return {**status, "ack": matched}

    async def command(*args, **kwargs):
        try:
            return await asyncio.wrap_future(hardware_worker.submit(lambda: hardware_command(*args, **kwargs)))
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return None

    async def maintenance_command(*args, **kwargs):
        try:
            return await asyncio.wrap_future(hardware_worker.submit(lambda: maintenance_hardware_command(*args, **kwargs)))
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return None

    async def clear_scene(event: str, announce_before_reset: bool = False):
        nonlocal scene_generation, scene_deadline, scene_mask, last_stream_mono
        if announce_before_reset:
            await broadcast({"type": "system", "event": event, "timestamp": time.time()})
        with state_lock:
            last_stream_mono = 0.0
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

    async def on_transmitter_disconnect(_owner_session_id: str):
        invite_store.clear_fallback_frame()
        await clear_scene("transmitter_disconnected")

    def validate_transmitter(cred):
        source_id, source_version = camera_registry.snapshot_active()
        with state_lock:
            if invite_store.transmitters.get(cred.transmitter_id) is not cred:
                return False
            if cred.owner_lease_generation != store.lease_generation or cred.source_version != source_version or cred.source_id != source_id:
                return False
            if base_settings.auth_required and store.controller_id != cred.owner_session_id:
                return False
            return True

    signaling_hub.on_transmitter_disconnect = on_transmitter_disconnect
    signaling_hub.validate_transmitter = validate_transmitter

    async def commit_result(result, generation, controller, scene, photo, source_id: str | None = None, source_version: int | None = None, config_version: int | None = None):
        nonlocal scene_deadline, scene_mask
        deadline = result["timestamp"] + (active_settings.photo_retention_seconds if photo else active_settings.scene_ttl_seconds)
        with state_lock:
            if maintenance_mode or not current(generation, controller, scene, source_id, source_version, config_version) or deadline <= time.time() or scene != scene_generation:
                return None
            scene_deadline = deadline
            scene_mask = result["arduino"]["requested_mask"]
        status = await command(scene_mask, generation, controller, scene, deadline, result["timestamp"])
        with state_lock:
            if maintenance_mode or status is None or not current(generation, controller, scene, source_id, source_version, config_version) or deadline <= time.time() or scene != scene_generation:
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
        nonlocal opencv_service
        if opencv_service is not None:
            opencv_service.stop()
            opencv_service = None
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
                    expired_owner = store.controller_id
                    expired = store.expire_controller()
                    lease_changed = last_lease != store.lease_generation
                    last_lease = store.lease_generation
                    if scene_deadline and hasattr(pipeline.serial, "set_scene_deadline"):
                        deadline = scene_deadline
                        session = store.sessions.get(store.controller_id or "")
                        if base_settings.auth_required:
                            deadline = min(deadline, session.expires_at, session.lease_expires_at) if session else 0
                        pipeline.serial.set_scene_deadline(deadline)
                if expired:
                    if expired_owner:
                        await invite_store.revoke_all_for_owner(expired_owner)
                    await clear_scene("lease_expired")
                elif lease_changed and store.controller_id is None and scene_deadline:
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
            active_stream_runs.pop(session_id, None)
            stopped_stream_runs.pop(session_id, None)
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
            "active_source_id": camera_registry.active_source_id,
            "source_version": camera_registry.source_version,
            "arduino": pipeline.serial.status(),
            "maintenance_mode": maintenance_mode,
            "pipeline_mode": pipeline.mode,
        }

    @app.post("/v1/sessions/lease/takeover")
    async def takeover_lease(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        session = auth(x_session_id, x_session_ticket, "admin")

        async def takeover_teardown(new_gen: int, evicted_controller_id: str | None = None):
            nonlocal maintenance_mode, led_test_timer, test_generation
            with state_lock:
                test_generation += 1
                if led_test_timer is not None:
                    led_test_timer.cancel()
                    led_test_timer = None
                maintenance_mode = False
            await clear_scene("admin_takeover")
            if evicted_controller_id:
                await invite_store.revoke_all_for_owner(evicted_controller_id)

        try:
            lease = await store.takeover_control_lease(session.session_id, takeover_teardown)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        await broadcast({
            "type": "system",
            "event": "control_takeover",
            "lease_generation": lease.lease_generation,
            "holder": session.session_id,
        })
        return {
            "success": True,
            "lease_generation": lease.lease_generation,
            "holder": session.session_id,
        }

    @app.post("/v1/sessions/heartbeat")
    async def session_heartbeat(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if not x_session_id or not x_session_ticket:
            raise HTTPException(status_code=401, detail="Missing session headers")
        with state_lock:
            session = store.sessions.get(x_session_id)
            now_wall = time.time()
            if not session or session.expires_at <= now_wall or not secrets.compare_digest(session.ticket, x_session_ticket):
                raise HTTPException(status_code=401, detail="session expired or invalid")
            ok = store.heartbeat(x_session_id, x_session_ticket)
            session.expires_at = now_wall + store.settings.session_ttl_seconds
            lease_held = bool(ok and store.lease and store.lease.holder_session_id == x_session_id and store.lease.lease_expires_at > now_wall)
            expires_in = max(
                0.0,
                (store.lease.lease_expires_at - now_wall)
                if lease_held and store.lease
                else (session.expires_at - now_wall),
            )
            return {"alive": True, "lease_held": lease_held, "expires_in": expires_in}

    # -------------------------------------------------------------------------
    # Multicamera Registry & WebRTC Signaling Endpoints
    # -------------------------------------------------------------------------

    @app.get("/v1/camera/sources")
    async def get_camera_sources(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket)
        return {
            "sources": camera_registry.list_sources(),
            "active_source_id": camera_registry.active_source_id,
            "source_version": camera_registry.source_version,
        }

    @app.api_route("/v1/camera/sources/active", methods=["POST", "PUT"])
    async def set_active_camera_source(
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        session = auth(x_session_id, x_session_ticket, "controller")
        source_id = str(payload.get("source_id", "")).strip()
        if not source_id:
            raise HTTPException(status_code=422, detail="source_id is required")

        nonlocal opencv_service

        async def source_teardown(old_src: str, new_src: str, new_ver: int):
            await invite_store.revoke_all_for_source(camera_registry.source_version)
            await clear_scene("camera_source_changed")
            invite_store.clear_fallback_frame()
            nonlocal opencv_service
            if opencv_service is not None:
                opencv_service.stop()
                opencv_service = None

            if new_src in {"laptop_builtin", "usb_webcam"}:
                src_cfg = getattr(active_settings, "camera_sources", {}).get(new_src) if hasattr(active_settings, "camera_sources") else None
                default_idx = 0 if new_src == "laptop_builtin" else 1
                idx = default_idx
                res = {"width": 1280, "height": 720}
                target_fps = 15.0
                if src_cfg:
                    if isinstance(src_cfg, dict):
                        settings_dict = src_cfg.get("settings", src_cfg)
                        idx = int(settings_dict.get("device_index", default_idx))
                        res = settings_dict.get("resolution", res)
                        target_fps = float(settings_dict.get("fps", target_fps))
                    elif hasattr(src_cfg, "settings"):
                        idx = int(src_cfg.settings.get("device_index", default_idx))
                        res = src_cfg.settings.get("resolution", res)
                        target_fps = float(src_cfg.settings.get("fps", target_fps))
                opencv_service = OpenCVCameraService(
                    index=idx,
                    width=res.get("width", 1280),
                    height=res.get("height", 720),
                    fps=target_fps,
                )
                opencv_service.start()

            # Install destination source calibration profile into segmenter
            cal_prof = active_settings.calibration_profiles.get(new_src)
            if cal_prof and cal_prof.background_filename:
                bg_p = Path("data/calibration") / cal_prof.background_filename
                if bg_p.exists():
                    bg_img = cv2.imread(str(bg_p))
                    if bg_img is not None:
                        pipeline.segmenter.set_background(bg_img)
                        pipeline.segmenter.roi = cal_prof.roi
                    else:
                        pipeline.segmenter.background = None
                        pipeline.segmenter.roi = None
                else:
                    pipeline.segmenter.background = None
                    pipeline.segmenter.roi = None
            else:
                pipeline.segmenter.background = None
                pipeline.segmenter.roi = None

        try:
            async with lifecycle_lock:
                desc = await camera_registry.switch_active_source(source_id, source_teardown)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        cal_prof = active_settings.calibration_profiles.get(source_id)
        cal_status = cal_prof.status if cal_prof else "needs_calibration"
        camera_registry.update_source_status(
            source_id, desc.status, calibration_available=(cal_status == "ready")
        )

        await broadcast({
            "type": "camera_source_changed",
            "active_source_id": source_id,
            "source_version": camera_registry.source_version,
            "calibration_status": cal_status,
        })

        return {
            "success": True,
            "active_source_id": source_id,
            "source_version": camera_registry.source_version,
            "calibration_status": cal_status,
        }

    @app.get("/v1/camera/webcams")
    async def get_webcams(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket)
        active_idx = opencv_service.index if opencv_service and opencv_service.is_running else None
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(None, lambda: enumerate_opencv_cameras(active_index=active_idx))
        return {"devices": devices}

    @app.post("/v1/camera/sources/{source_id}/settings")
    async def update_camera_source_settings(
        source_id: str,
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        session = auth(x_session_id, x_session_ticket, "admin")
        if store.controller_id != session.session_id:
            raise HTTPException(
                status_code=409,
                detail="Current active physical control lease required to alter camera settings",
            )

        desc = camera_registry.get_source(source_id)
        if not desc:
            raise HTTPException(status_code=404, detail=f"Source {source_id} not found")

        w = int(payload.get("width", desc.current_resolution["width"]))
        h = int(payload.get("height", desc.current_resolution["height"]))
        fps = float(payload.get("fps", desc.current_fps))
        if not 160 <= w <= 3840 or not 90 <= h <= 2160 or not 1 <= fps <= 60:
            raise HTTPException(status_code=422, detail="camera dimensions/fps are outside supported bounds")
        device_index = payload.get("device_index")
        if source_id in {"laptop_builtin", "usb_webcam"} and device_index is not None:
            if isinstance(device_index, bool):
                raise HTTPException(status_code=422, detail="device_index must be a non-negative integer")
            try:
                device_index = int(device_index)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="device_index must be a non-negative integer") from exc
            if device_index < 0:
                raise HTTPException(status_code=422, detail="device_index must be a non-negative integer")
            discovered = await asyncio.get_running_loop().run_in_executor(
                None, lambda: enumerate_opencv_cameras(active_index=device_index)
            )
            if not any(int(item.get("index", -1)) == device_index for item in discovered):
                raise HTTPException(status_code=422, detail="device_index was not found by webcam enumeration")
        camera_registry.update_source_status(
            source_id, desc.status, resolution={"width": w, "height": h}, fps=fps
        )

        prof = active_settings.calibration_profiles.get(source_id)
        if prof and (prof.resolution.get("width") != w or prof.resolution.get("height") != h):
            prof.status = "needs_calibration"

        source_settings = dict(active_settings.camera_sources or {})
        current_settings = dict(source_settings.get(source_id, {}))
        current_settings.update({"width": w, "height": h, "resolution": {"width": w, "height": h}, "fps": fps})
        if device_index is not None:
            current_settings["device_index"] = device_index
        source_settings[source_id] = current_settings
        active_settings.camera_sources = dict(source_settings)
        draft_settings.camera_sources = dict(source_settings)
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)
        active_version = saved_version
        config_store.save_active_atomic(active_settings, active_inventory, active_version)

        return {
            "success": True,
            "profile_status": prof.status if prof else "needs_calibration",
            "device_index": device_index,
            "width": w,
            "height": h,
            "fps": fps,
        }

    @app.post("/v1/camera/invite")
    async def create_camera_invite(
        request: Request,
        payload: dict = Body(default={}),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        session = auth(x_session_id, x_session_ticket, "controller")
        expires_in = int(payload.get("expires_in_seconds", 180))
        if expires_in < 30 or expires_in > 600:
            raise HTTPException(status_code=422, detail="expires_in_seconds must be between 30 and 600")
        invite = await invite_store.create_invite(
            session.session_id if session is not None else "mock-owner",
            store.lease_generation,
            camera_registry.source_version,
            source_id=camera_registry.active_source_id,
            expires_in_seconds=expires_in,
        )
        host = request.headers.get("host", "localhost:8000")
        invite_url = f"https://{host}/camera#token={invite.invite_token}"
        return {
            "invite_id": invite.invite_id,
            "invite_token": invite.invite_token,
            "invite_url": invite_url,
            "expires_at": invite.expires_at,
        }

    @app.delete("/v1/camera/invite/{invite_id}")
    async def cancel_camera_invite(
        invite_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        session = auth(x_session_id, x_session_ticket, "controller")
        ok = await invite_store.cancel_invite(invite_id, session.session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Invite not found or already cancelled")
        return {"cancelled": True}

    @app.post("/v1/camera/revoke")
    async def revoke_camera_transmitters(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        session = auth(x_session_id, x_session_ticket, "controller")
        count = await invite_store.revoke_all_for_owner(session.session_id if session is not None else "mock-owner")
        await clear_scene("camera_revoked")
        return {"revoked": True, "disconnected_transmitters": count}

    @app.websocket("/v1/camera/signal")
    async def camera_signal_ws(ws: WebSocket):
        def validate_operator(session_id: str, ticket: str) -> bool:
            with store.state_lock:
                if not store.valid(session_id, ticket):
                    return False
                session = store.sessions.get(session_id)
                if not session or session.role not in {"controller", "admin"}:
                    return False
                if not store.lease or store.lease.holder_session_id != session_id or store.lease.lease_expires_at <= time.time():
                    return False
                return True

        await signaling_hub.handle_connection(ws, validate_operator)

    @app.post("/v1/camera/frame-upload")
    async def camera_frame_upload(
        file: UploadFile = File(...),
        x_transmitter_id: str | None = Header(default=None),
        x_transmitter_token: str | None = Header(default=None),
        x_capture_sequence: str | None = Header(default=None),
        x_source_id: str | None = Header(default=None),
        x_source_version: str | None = Header(default=None),
    ):
        if not x_transmitter_id or not x_transmitter_token:
            raise HTTPException(status_code=401, detail="Missing transmitter authentication headers")
        try:
            cred = await invite_store.validate_transmitter_for_upload(
                x_transmitter_id,
                x_transmitter_token,
                store.lease_generation,
                camera_registry.source_version,
                camera_registry.active_source_id,
                require_connected=True,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        data = await file.read(350 * 1024 + 1)
        if len(data) > 350 * 1024:
            raise HTTPException(status_code=400, detail="Frame exceeds 350KB limit")

        try:
            image = _decode_image_bytes(data, 350 * 1024, 1280 * 720)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if image.shape[1] > 1280 or image.shape[0] > 720:
            raise HTTPException(status_code=400, detail="Frame dimensions exceed 1280x720")
        sequence_header = x_capture_sequence
        try:
            sequence = int(sequence_header) if sequence_header is not None else -1
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="X-Capture-Sequence must be an integer") from exc
        source_id = x_source_id or camera_registry.active_source_id
        source_version_header = x_source_version
        try:
            source_version = int(source_version_header) if source_version_header is not None else camera_registry.source_version
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="X-Source-Version must be an integer") from exc
        if source_id != camera_registry.active_source_id or source_version != camera_registry.source_version:
            raise HTTPException(status_code=409, detail="Frame source is stale")

        now_mono = time.monotonic()
        if cred.last_upload_mono and now_mono - cred.last_upload_mono < 0.5:
            raise HTTPException(status_code=429, detail="Fallback upload rate exceeded")
        if sequence >= 0 and sequence <= cred.last_sequence:
            raise HTTPException(status_code=409, detail="Fallback frame sequence is stale")
        cred.last_upload_mono = now_mono
        if sequence >= 0:
            cred.last_sequence = sequence
        invite_store.save_fallback_frame(
            data,
            source_id=source_id,
            source_version=source_version,
            sequence=sequence,
            transmitter_id=cred.transmitter_id,
        )

        return {"accepted": True, "server_monotonic": now_mono}

    @app.websocket("/v1/camera/preview-ws")
    async def camera_preview_ws(ws: WebSocket):
        if not signaling_hub._origin_allowed(ws):
            await ws.close(code=1008, reason="Origin not allowed")
            return
        await ws.accept()
        try:
            first_msg = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
            if not isinstance(first_msg, dict) or first_msg.get("type") != "auth":
                await ws.close(code=1008, reason="Authentication failed")
                return
            s_id = first_msg.get("session_id")
            s_ticket = first_msg.get("ticket")
            if base_settings.auth_required and (not isinstance(s_id, str) or not isinstance(s_ticket, str) or not store.valid(s_id, s_ticket)):
                await ws.close(code=1008, reason="Authentication failed")
                return
            fps = min(max(float(first_msg.get("fps", 15.0)), 1.0), 30.0)
            width = min(max(int(first_msg.get("width", 640)), 160), 1280)
            height = min(max(int(first_msg.get("height", 360)), 90), 720)
            await ws.send_json({"type": "auth_ok"})

            while True:
                await asyncio.sleep(1.0 / fps)
                if base_settings.auth_required and not store.valid(s_id, s_ticket):
                    await ws.close(code=1008, reason="Session expired")
                    return
                source_id, source_version = camera_registry.snapshot_active()
                if source_id in {"laptop_builtin", "usb_webcam"} and opencv_service is not None and opencv_service.is_running:
                    jpeg = opencv_service.get_latest_preview_jpeg(width, height)
                    if jpeg is not None:
                        await ws.send_bytes(jpeg)
                elif source_id == "remote_phone":
                    fb = invite_store.get_latest_fallback_frame(max_age_seconds=5.0)
                    if fb is not None:
                        await ws.send_bytes(fb)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            pass

    @app.post("/v1/analyze/scan")
    async def analyze_scan(
        file: UploadFile | None = None,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            session = auth(x_session_id, x_session_ticket, "controller")
        else:
            session = None
        if maintenance_mode:
            raise HTTPException(status_code=409, detail="hardware is in maintenance mode")
        if pipeline.mode == "preparation":
            reasons = "; ".join(pipeline.preparation_reasons) if pipeline.preparation_reasons else "calibration or gallery missing"
            raise HTTPException(status_code=409, detail=f"system is in preparation mode: {reasons}")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")

        image = None
        current_source, source_version, generation, controller_id, scene = capture_context()
        config_version = active_version

        if current_source in {"laptop_builtin", "usb_webcam"}:
            if opencv_service is None or not opencv_service.is_running:
                raise HTTPException(status_code=400, detail="Backend camera is not running")
            latest = opencv_service.get_latest_frame()
            if latest is None:
                raise HTTPException(status_code=400, detail="No frame captured from camera yet")
            raw_img, _frame_wall_ts, frame_mono_ts = latest
            if time.monotonic() - frame_mono_ts > 5.0:
                raise HTTPException(status_code=409, detail="Captured frame from webcam is stale (>5s)")
            image = raw_img
        elif file is not None:
            image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        else:
            fb_bytes = invite_store.get_latest_fallback_frame(max_age_seconds=5.0)
            if fb_bytes is not None:
                nparr = np.frombuffer(fb_bytes, np.uint8)
                image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            raise HTTPException(status_code=400, detail="No fresh image available to scan")

        if not context_current(current_source, source_version, config_version):
            raise HTTPException(status_code=409, detail="source changed during intake")
        if closed:
            raise HTTPException(status_code=503, detail="service unavailable")
        try:
            image = normalize_for_source(image, current_source)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if opencv_service is not None and current_source in {"laptop_builtin", "usb_webcam"}:
            opencv_service.set_frozen_frame(image)

        ok_enc, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        preview_data_url = (
            f"data:image/jpeg;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"
            if ok_enc
            else None
        )

        captured_at = time.time()
        try:
            result = await asyncio.wait_for(
                asyncio.wrap_future(
                    inference_worker.submit(lambda: process_frame(image, captured_at, True, scene))
                ),
                base_settings.inference_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            if generation == store.lease_generation and current(generation, controller_id, scene):
                await clear_scene("inference_timeout")
            raise HTTPException(status_code=504, detail="inference timed out") from exc
        except StaleFrameError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            if "preparation mode" in str(exc).lower():
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncio.CancelledError:
            raise HTTPException(status_code=409, detail="scan superseded by a newer frame")

        result["image"]["preview_data_url"] = preview_data_url
        result["source_id"] = current_source
        result["source_version"] = source_version
        result["lease_generation"] = generation
        result["scene_generation"] = scene

        result = await commit_result(result, generation, controller_id, scene, True, current_source, source_version, config_version)
        if result is None:
            raise HTTPException(status_code=409, detail=inference_rejection_detail(generation, controller_id))

        await broadcast({"type": "detection", "result": result})
        await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})
        return result

    # -------------------------------------------------------------------------
    # Multi-Profile Calibration Endpoints
    # -------------------------------------------------------------------------

    @app.get("/v1/calibration/profiles")
    async def get_calibration_profiles(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket)
        return {
            "profiles": {
                k: p.to_dict() if hasattr(p, "to_dict") else p
                for k, p in active_settings.calibration_profiles.items()
            }
        }

    @app.get("/v1/calibration/profiles/{source_id}")
    async def get_single_calibration_profile(
        source_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket)
        prof = active_settings.calibration_profiles.get(source_id)
        if not prof:
            raise HTTPException(status_code=404, detail=f"No profile found for source {source_id}")
        return prof.to_dict() if hasattr(prof, "to_dict") else prof

    @app.post("/v1/calibration/profiles/{source_id}/capture")
    @app.post("/v1/calibration/profiles/{source_id}/capture-background")
    async def capture_profile_background(
        source_id: str,
        file: UploadFile | None = None,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        session = auth(x_session_id, x_session_ticket, "controller")
        image = None

        if source_id in {"laptop_builtin", "usb_webcam"} and camera_registry.active_source_id != source_id:
            raise HTTPException(status_code=409, detail="select the source before capturing its background")
        if source_id in {"laptop_builtin", "usb_webcam"} and opencv_service is not None and opencv_service.is_running:
            latest = opencv_service.get_latest_frame()
            if latest is not None:
                raw_frame, _, _ = latest
                image = raw_frame
        elif file is not None:
            image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        elif source_id == "remote_phone" and invite_store.latest_fallback_frame is not None:
            nparr = np.frombuffer(invite_store.latest_fallback_frame, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            raise HTTPException(status_code=400, detail="No frame available from source to capture as background")

        raw_resolution = {"width": int(image.shape[1]), "height": int(image.shape[0])}
        try:
            image = normalize_for_source(image, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        cal_dir = Path("data/calibration")
        cal_dir.mkdir(parents=True, exist_ok=True)
        bg_filename = f"bg_{source_id}_{image.shape[1]}x{image.shape[0]}.jpg"
        bg_path = cal_dir / bg_filename
        # Keep a JPEG suffix so OpenCV selects an encoder for the atomic temp
        # write; a trailing `.tmp` is not a recognized image format.
        tmp_path = bg_path.with_name(f"{bg_path.name}.tmp.jpg")
        if not cv2.imwrite(str(tmp_path), image):
            raise HTTPException(status_code=500, detail="failed to write calibration background")
        os.replace(tmp_path, bg_path)

        prof = active_settings.calibration_profiles.get(source_id)
        if not prof:
            prof = CalibrationProfile(
                source_id=source_id,
                resolution=raw_resolution,
                orientation="normal",
                background_filename=bg_filename,
                roi=None,
                calibrated_at=time.time(),
                status="needs_calibration",
            )
            active_settings.calibration_profiles[source_id] = prof
            draft_settings.calibration_profiles[source_id] = prof
        else:
            prof.resolution = raw_resolution
            prof.background_filename = bg_filename
            prof.calibrated_at = time.time()
            if prof.roi is None:
                prof.status = "needs_calibration"

        draft_settings.calibration_profiles[source_id] = CalibrationProfile.from_dict(prof.to_dict())
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)
        active_version = saved_version
        config_store.save_active_atomic(active_settings, active_inventory, active_version)

        if camera_registry.active_source_id == source_id:
            pipeline.segmenter.set_background(image)

        return prof.to_dict()

    @app.put("/v1/calibration/profiles/{source_id}/roi")
    async def update_profile_roi(
        source_id: str,
        payload: dict,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        session = auth(x_session_id, x_session_ticket, "controller")
        raw_roi = payload.get("roi")
        if not raw_roi or len(raw_roi) != 4:
            raise HTTPException(status_code=422, detail="roi must be [x, y, width, height]")
        roi_tuple = tuple(int(v) for v in raw_roi)
        if roi_tuple[0] < 0 or roi_tuple[1] < 0 or roi_tuple[2] <= 0 or roi_tuple[3] <= 0:
            raise HTTPException(status_code=422, detail="roi coordinates must be non-negative with positive dimensions")

        prof = active_settings.calibration_profiles.get(source_id)
        source_desc = camera_registry.get_source(source_id)
        profile_resolution = prof.resolution if prof else (source_desc.current_resolution if source_desc else {"width": 0, "height": 0})
        if (
            roi_tuple[0] + roi_tuple[2] > int(profile_resolution.get("width", 0))
            or roi_tuple[1] + roi_tuple[3] > int(profile_resolution.get("height", 0))
        ):
            raise HTTPException(status_code=422, detail="roi exceeds calibration profile resolution")
        if not prof:
            prof = CalibrationProfile(
                source_id=source_id,
                roi=roi_tuple,
                status="ready",
                calibrated_at=time.time(),
            )
            active_settings.calibration_profiles[source_id] = prof
            draft_settings.calibration_profiles[source_id] = prof
        else:
            prof.roi = roi_tuple
            prof.status = "ready"
            prof.calibrated_at = time.time()

        draft_settings.calibration_profiles[source_id] = CalibrationProfile.from_dict(prof.to_dict())
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)
        active_version = saved_version
        config_store.save_active_atomic(active_settings, active_inventory, active_version)

        if camera_registry.active_source_id == source_id:
            pipeline.segmenter.roi = roi_tuple

        camera_registry.update_source_status(
            source_id,
            "active" if camera_registry.active_source_id == source_id else "standby",
            calibration_available=True,
        )
        return prof.to_dict()

    @app.post("/v1/calibration/profiles/{source_id}/confirm-legacy")
    async def confirm_legacy_profile(
        source_id: str,
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
    ):
        nonlocal saved_version, active_version
        session = auth(x_session_id, x_session_ticket, "admin")
        legacy = active_settings.calibration_profiles.pop("unbound_legacy", None)
        if not legacy:
            raise HTTPException(status_code=404, detail="No unbound legacy calibration profile found")
        legacy.source_id = source_id
        legacy.status = "ready"
        legacy.calibrated_at = time.time()
        active_settings.calibration_profiles[source_id] = legacy
        draft_settings.calibration_profiles[source_id] = legacy
        saved_version = config_store.save_atomic(draft_settings, draft_inventory)
        active_version = saved_version
        config_store.save_active_atomic(active_settings, active_inventory, active_version)
        if camera_registry.active_source_id == source_id and legacy.roi:
            pipeline.segmenter.roi = legacy.roi
        return legacy.to_dict()

    @app.get("/camera")
    async def camera_page():
        index_html = Path("frontend/dist/index.html")
        if index_html.exists():
            return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
        return HTMLResponse(
            content="<!DOCTYPE html><html><body><h1>ToolLighthouse Camera</h1><p>Frontend compilado no disponible aún. Ejecute npm run build en frontend/.</p></body></html>"
        )

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
                await invite_store.revoke_all_for_source(camera_registry.source_version)
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
                        await invite_store.revoke_all_for_source(camera_registry.source_version)
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
                source_id, source_version = camera_registry.snapshot_active()
                background = normalize_for_source(background, source_id)
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
        session = auth(x_session_id, x_session_ticket, "admin")
        with state_lock:
            if store.controller_id != session.session_id:
                raise HTTPException(status_code=409, detail="physical control lease required for maintenance operations")
        async with lifecycle_lock:
            with state_lock:
                store.expire_controller()
                if store.controller_id != session.session_id:
                    raise HTTPException(status_code=409, detail="physical control lease required for maintenance operations")
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
            with state_lock:
                test_generation += 1
                if led_test_timer is not None:
                    led_test_timer.cancel()
                    led_test_timer = None
                maintenance_mode = False
                generation = store.lease_generation
                ctrl = store.controller_id
                scene = scene_generation

            await command(0, generation, ctrl, scene, clearing=True)
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
        session = auth(x_session_id, x_session_ticket, "admin")

        # TestClient and lightweight ASGI hosts may serve requests without
        # running the optional startup hook. Capture the request loop here so
        # the timer can still marshal its ACK event safely.
        request_loop = asyncio.get_running_loop()
        if app_loop is None or not app_loop.is_running():
            app_loop = request_loop

        with state_lock:
            if store.controller_id != session.session_id:
                raise HTTPException(status_code=409, detail="physical control lease required for maintenance operations")
            if not maintenance_mode:
                raise HTTPException(status_code=409, detail="Must enter maintenance mode before testing LEDs")
            if not 0 <= idx <= 8:
                raise HTTPException(status_code=400, detail="led_index must be between 0 and 8")

            test_generation += 1
            current_gen = test_generation
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None

            generation = store.lease_generation
            controller = store.controller_id
            scene = scene_generation
            target_serial = pipeline.serial

        mask = 1 << idx
        effective_duration = min(max(duration, 0.1), 3.0)
        deadline = time.time() + effective_duration

        cmd_result = await maintenance_command(
            mask, generation, controller, scene, current_gen, target_serial, deadline
        )
        if cmd_result is None:
            raise HTTPException(status_code=409, detail="physical control lease or maintenance mode lost")
        status_info = cmd_result

        def do_auto_off(gen: int, s_ref: Any, scene_at_start: int):
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
                if not maintenance_mode or gen != test_generation or s_ref is not pipeline.serial or scene_at_start != scene_generation:
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

        led_test_timer = threading.Timer(effective_duration, do_auto_off, args=[current_gen, target_serial, scene])
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
        session = auth(x_session_id, x_session_ticket, "admin")

        with state_lock:
            if store.controller_id != session.session_id:
                raise HTTPException(status_code=409, detail="physical control lease required for maintenance operations")
            if not maintenance_mode:
                raise HTTPException(status_code=409, detail="Must enter maintenance mode before issuing maintenance commands")
            test_generation += 1
            if led_test_timer is not None:
                led_test_timer.cancel()
                led_test_timer = None
            generation = store.lease_generation
            controller = store.controller_id
            scene = scene_generation

        status_info = await command(0, generation, controller, scene, clearing=True)
        if status_info is None:
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
        x_source_id: str | None = Header(default=None),
        x_source_version: str | None = Header(default=None),
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

        source_id, source_version, generation, controller_id, scene = capture_context()
        config_version = active_version
        if source_id in {"laptop_builtin", "usb_webcam"} and x_source_id != source_id:
            raise HTTPException(status_code=409, detail="legacy photo intake requires the active source metadata")
        if x_source_id is not None and x_source_id != source_id:
            raise HTTPException(status_code=409, detail="photo source is not active")
        if x_source_version is not None and str(source_version) != str(x_source_version):
            raise HTTPException(status_code=409, detail="photo source version is stale")
        try:
            image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            image = normalize_for_source(image, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not context_current(source_id, source_version, config_version):
            raise HTTPException(status_code=409, detail="photo source changed during intake")
        try:
            captured_at = time.time()
            result = await asyncio.wait_for(
                asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, True, scene))),
                base_settings.inference_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            if current(generation, controller_id, scene, source_id, source_version, config_version):
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

        result["source_id"] = source_id
        result["source_version"] = source_version
        result["lease_generation"] = generation
        result["scene_generation"] = scene
        result = await commit_result(result, generation, controller_id, scene, True, source_id, source_version, config_version)
        if result is None:
            raise HTTPException(status_code=409, detail=inference_rejection_detail(generation, controller_id))
        await broadcast({"type": "detection", "result": result})
        await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})
        return result

    @app.post("/v1/stream/frame")
    async def stream_frame(
        file: UploadFile | None = File(default=None),
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
        x_source_id: str | None = Header(default=None),
        x_source_version: str | None = Header(default=None),
        x_stream_run: str | None = Header(default=None),
    ):
        nonlocal stream_task, stream_generation, last_stream_mono
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        stream_run = None
        stream_session = x_session_id or ""
        if x_stream_run is not None:
            try:
                stream_run = int(x_stream_run)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "invalid stream run"}) from exc
            if stream_run <= 0:
                raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "invalid stream run"})
            with state_lock:
                if stream_run <= stopped_stream_runs.get(stream_session, 0):
                    raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "stream run was stopped"})
                active_run = active_stream_runs.get(stream_session)
                if active_run is not None and active_run != stream_run:
                    raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "a newer stream run is active"})
        if maintenance_mode:
            raise HTTPException(status_code=409, detail="hardware is in maintenance mode")
        if pipeline.mode == "preparation":
            reasons = "; ".join(pipeline.preparation_reasons) if pipeline.preparation_reasons else "calibration or gallery missing"
            raise HTTPException(status_code=409, detail=f"system is in preparation mode: {reasons}")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")

        source_id, source_version, lease_generation, controller_id, scene = capture_context()
        config_version = active_version

        # Enforce current source id and source version
        if source_id in {"laptop_builtin", "usb_webcam"}:
            if x_source_id != source_id:
                raise HTTPException(status_code=409, detail="stream source is not active")
            if x_source_version is None or str(source_version) != str(x_source_version):
                raise HTTPException(status_code=409, detail="stream source version is stale")
        else:
            if x_source_id is not None and x_source_id != source_id:
                raise HTTPException(status_code=409, detail="stream source is not active")
            if x_source_version is not None and str(source_version) != str(x_source_version):
                raise HTTPException(status_code=409, detail="stream source version is stale")

        # Backend FPS limit enforcement
        now_mono = time.monotonic()
        desc = camera_registry.get_source(source_id)
        target_fps = float(desc.current_fps) if desc and desc.current_fps > 0 else 30.0
        min_interval = 1.0 / max(1.0, min(target_fps, 60.0))
        with state_lock:
            if last_stream_mono > 0 and (now_mono - last_stream_mono) < (min_interval * 0.9):
                raise HTTPException(status_code=429, detail="Stream rate limit exceeded")
            last_stream_mono = now_mono

        # Retrieve pristine buffer for backend source, or decode upload file for browser source
        if source_id in {"laptop_builtin", "usb_webcam"}:
            if file is not None:
                try:
                    await file.close()
                except Exception:
                    pass
            if opencv_service is None or not opencv_service.is_running:
                raise HTTPException(status_code=400, detail="Backend camera is not running")
            latest = opencv_service.get_latest_frame()
            if latest is None:
                raise HTTPException(status_code=400, detail="No frame captured from camera yet")
            raw_img, frame_wall_ts, frame_mono_ts = latest
            if time.monotonic() - frame_mono_ts > base_settings.frame_max_age_seconds:
                raise HTTPException(status_code=409, detail="Captured frame from webcam is stale")
            image = raw_img
            captured_mono = frame_mono_ts
            captured_at = frame_wall_ts
        else:
            if file is None:
                raise HTTPException(status_code=400, detail="file is required for stream upload")
            try:
                image = await _read_image(file, base_settings.max_upload_bytes, base_settings.max_pixels)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            captured_mono = time.monotonic()
            captured_at = time.time()

        try:
            image = normalize_for_source(image, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not context_current(source_id, source_version, config_version):
            raise HTTPException(status_code=409, detail="stream source changed during intake")

        if time.monotonic() - captured_mono > base_settings.frame_max_age_seconds:
            raise HTTPException(status_code=409, detail="frame became stale before queueing")

        frame_id = str(uuid.uuid4())

        async def process_latest():
            try:
                if time.monotonic() - captured_mono > base_settings.frame_max_age_seconds:
                    return
                result = await asyncio.wait_for(
                    asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, False, scene))),
                    base_settings.inference_timeout_seconds,
                )
            except asyncio.TimeoutError:
                if generation == stream_generation and current(lease_generation, controller_id, scene, source_id, source_version, config_version):
                    await clear_scene("inference_timeout")
                return
            except (asyncio.CancelledError, StaleFrameError):
                return
            except (ValueError, RuntimeError) as exc:
                await broadcast({"type": "system", "event": "frame_rejected", "detail": str(exc)})
                return

            if time.monotonic() - captured_mono > base_settings.frame_max_age_seconds:
                return

            result["frame_id"] = frame_id
            if generation != stream_generation or not current(lease_generation, controller_id, scene, source_id, source_version, config_version):
                return
            result["source_id"] = source_id
            result["source_version"] = source_version
            result["lease_generation"] = lease_generation
            result["scene_generation"] = scene
            result = await commit_result(result, lease_generation, controller_id, scene, False, source_id, source_version, config_version)
            if result is None:
                return
            await broadcast({"type": "detection", "result": result})
            await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})

        with state_lock:
            if stream_run is not None:
                if stream_run <= stopped_stream_runs.get(stream_session, 0):
                    raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "stream run was stopped during intake"})
                active_run = active_stream_runs.get(stream_session)
                if active_run is not None and active_run != stream_run:
                    raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "a newer stream run is active"})
                active_stream_runs[stream_session] = stream_run
            stream_generation += 1
            generation = stream_generation
            if stream_task and not stream_task.done():
                stream_task.cancel()
            stream_task = asyncio.create_task(process_latest())
        return {"accepted": True, "latest_only": True, "generation": generation, "frame_id": frame_id, "timestamp": time.time()}

    @app.post("/v1/stream/stop")
    async def stop_stream(
        x_session_id: str | None = Header(default=None),
        x_session_ticket: str | None = Header(default=None),
        x_source_id: str | None = Header(default=None),
        x_source_version: str | None = Header(default=None),
        x_stream_run: str | None = Header(default=None),
    ):
        nonlocal stream_task, stream_generation, last_stream_mono
        if base_settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        stream_run = None
        stream_session = x_session_id or ""
        if x_stream_run is not None:
            try:
                stream_run = int(x_stream_run)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "invalid stream run"}) from exc
            if stream_run <= 0:
                raise HTTPException(status_code=409, detail={"code": "stream_stale", "message": "invalid stream run"})
        current_source, current_source_version = camera_registry.snapshot_active()
        if x_source_id is not None and x_source_id != current_source:
            raise HTTPException(status_code=409, detail={"code": "source_stale", "message": "stream stop source is no longer active"})
        if x_source_version is not None and str(current_source_version) != str(x_source_version):
            raise HTTPException(status_code=409, detail={"code": "source_stale", "message": "stream stop source version is stale"})
        with state_lock:
            if stream_run is not None:
                active_run = active_stream_runs.get(stream_session)
                if active_run is not None and active_run != stream_run:
                    return {"stopped": False, "stream_generation": stream_generation}
                if stream_run <= stopped_stream_runs.get(stream_session, 0):
                    return {"stopped": False, "stream_generation": stream_generation}
                stopped_stream_runs[stream_session] = stream_run
                active_stream_runs.pop(stream_session, None)
            stream_generation += 1
            last_stream_mono = 0.0
            if stream_task and not stream_task.done():
                stream_task.cancel()
        await clear_scene("stream_stopped")
        return {"stopped": True, "stream_generation": stream_generation}

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
        from fastapi.responses import FileResponse

        @app.get("/camera")
        @app.get("/camera/")
        async def serve_camera_spa():
            return FileResponse(frontend_dist / "index.html")

        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app
