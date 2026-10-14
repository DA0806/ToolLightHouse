import asyncio
import io
import secrets
import threading
import time
import uuid
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import Settings
from ..camera import LatestInferenceWorker
from ..pipeline import Pipeline, StaleFrameError


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

    def create(self, device_id: str, role: str, key: str | None) -> Session:
        if self.settings.auth_required and (not self.settings.pairing_key or not secrets.compare_digest(key or "", self.settings.pairing_key)):
            raise PermissionError("invalid pairing key")
        if role not in {"controller", "reader"}:
            raise ValueError("role must be controller or reader")
        if role == "controller" and self.controller_id is not None:
            current = self.sessions.get(self.controller_id)
            if current and current.expires_at > time.time() and current.lease_expires_at > time.time():
                raise PermissionError("another controller session is active")
            self.controller_id = None
        now = time.time()
        session = Session(str(uuid.uuid4()), device_id, role, secrets.token_urlsafe(24), now + self.settings.session_ttl_seconds, now + self.settings.lease_ttl_seconds, {})
        self.sessions[session.session_id] = session
        if role == "controller":
            self.controller_id = session.session_id
            self.lease_generation += 1
        return session

    def valid(self, session_id: str | None, ticket: str | None, role: str | None = None) -> bool:
        session = self.sessions.get(session_id or "")
        now = time.time()
        if not session or session.expires_at <= now or (session.role == "controller" and (self.controller_id != session.session_id or session.lease_expires_at <= now)):
            if session and self.controller_id == session.session_id:
                self.controller_id = None
                self.lease_generation += 1
            return False
        valid = bool(ticket) and secrets.compare_digest(session.ticket, ticket or "") and (role is None or session.role == role)
        if valid and session.role == "controller":
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
            if source.width * source.height > max_pixels:
                raise ValueError("decoded image exceeds configured pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("invalid image file") from exc
    if image.width * image.height > max_pixels:
        raise ValueError("decoded image exceeds configured pixel limit")
    return np.asarray(image)[:, :, ::-1].copy()


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None):
    try:
        from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as exc:
        raise RuntimeError("FastAPI dependencies are required; install the default project dependencies") from exc

    settings = settings or Settings(pairing_key=os.getenv("TOOLLIGHTHOUSE_PAIRING_KEY", os.getenv("TOOL_LIGHTHOUSE_PAIRING_KEY", "")))
    pipeline = pipeline or Pipeline(settings)
    store = SessionStore(settings)
    app = FastAPI(title="ToolLighthouse API", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    clients: set[WebSocket] = set()
    inference_worker = LatestInferenceWorker(lambda operation: operation())
    hardware_worker = LatestInferenceWorker(lambda operation: operation())
    stream_task: asyncio.Task | None = None
    stream_generation = 0
    lease_watchdog: asyncio.Task | None = None
    state_lock = threading.RLock()
    scene_generation = 0
    scene_deadline = 0.0
    scene_mask = 0
    closed = False
    calibrating = False
    processed_scene = -1

    def process_frame(image, captured_at, photo, scene):
        nonlocal processed_scene
        if processed_scene != scene:
            pipeline.reset_scene()
            processed_scene = scene
        return pipeline.process(image, captured_at, photo, False)

    def auth(session_id: str | None, ticket: str | None, role: str | None = None):
        try:
            with state_lock:
                return store.get(session_id or "", ticket, role)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    async def broadcast(event):
        stale = []
        for client in tuple(clients):
            try:
                await asyncio.wait_for(client.send_json(event), timeout=1.0)
            except Exception:
                stale.append(client)
        clients.difference_update(stale)

    def current(generation, controller, scene):
        session = store.sessions.get(controller or "")
        return (not closed and generation == store.lease_generation and scene == scene_generation
                and (not settings.auth_required or (controller == store.controller_id and session is not None
                     and min(session.expires_at, session.lease_expires_at) > time.time())))

    def hardware_command(mask, generation, controller, scene, deadline=0.0, captured_at=None, clearing=False):
        with state_lock:
            if closed or generation != store.lease_generation or scene != scene_generation:
                return None
            if not clearing and (not current(generation, controller, scene) or time.time() >= deadline):
                return None
            if hasattr(pipeline.serial, "set_scene_deadline"):
                transport_deadline = deadline
                if not clearing and settings.auth_required:
                    session = store.sessions[controller]
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

    async def clear_scene(event):
        nonlocal scene_generation, scene_deadline, scene_mask
        with state_lock:
            scene_generation += 1
            scene_deadline, scene_mask = 0.0, 0
            generation, scene = store.lease_generation, scene_generation
            if hasattr(pipeline.serial, "set_scene_deadline"):
                pipeline.serial.set_scene_deadline(0)
        inference_worker.submit(pipeline.reset_scene)
        status = await command(0, generation, store.controller_id, scene, clearing=True)
        if status is not None and generation == store.lease_generation and scene == scene_generation:
            await broadcast({"type": "system", "event": event, "timestamp": time.time()})
            await broadcast({"type": "arduino_ack", "arduino": status})

    async def commit_result(result, generation, controller, scene, photo):
        nonlocal scene_deadline, scene_mask
        deadline = result["timestamp"] + (settings.photo_retention_seconds if photo else settings.scene_ttl_seconds)
        with state_lock:
            if not current(generation, controller, scene) or deadline <= time.time():
                return None
            scene_deadline = deadline
            scene_mask = result["arduino"]["requested_mask"]
        status = await command(scene_mask, generation, controller, scene, deadline, result["timestamp"])
        with state_lock:
            if status is None or not current(generation, controller, scene) or deadline <= time.time():
                return None
        result["arduino"] = status
        result["metrics"] = pipeline.metrics.as_dict()
        return result

    @app.on_event("shutdown")
    async def shutdown_worker():
        nonlocal closed, scene_generation
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
        nonlocal lease_watchdog

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
                        if settings.auth_required:
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
                state = tuple(status.get(key) for key in ("connected", "requested_mask", "confirmed_mask", "ack"))
                if last_status is not None and state != last_status:
                    await broadcast({"type": "system", "event": "arduino_state_changed", "timestamp": time.time()})
                    await broadcast({"type": "arduino_ack", "arduino": status})
                last_status = state

        lease_watchdog = asyncio.create_task(watch())

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": settings.mode, "simulated": bool(pipeline.serial.simulated)}

    @app.post("/v1/sessions")
    async def create_session(payload: dict, x_pairing_key: str | None = Header(default=None)):
        try:
            with state_lock:
                session = store.create(str(payload.get("device_id", "unknown")), str(payload.get("role", "reader")), x_pairing_key)
        except PermissionError as exc:
            raise HTTPException(status_code=409 if "another" in str(exc) else 401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if session.role == "controller":
            await clear_scene("controller_acquired")
        return {"session_id": session.session_id, "ticket": session.ticket, "role": session.role, "expires_at": session.expires_at, "lease_expires_at": session.lease_expires_at, "lease_generation": store.lease_generation}

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
        if session.role == "controller":
            await clear_scene("controller_released")
        else:
            await broadcast({"type": "system", "event": "reader_released"})
        return {"released": True}

    @app.get("/v1/status")
    async def status(x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        session = auth(x_session_id, x_session_ticket) if settings.auth_required else None
        return {"session": {"device_id": session.device_id, "role": session.role} if session else None, "lease_generation": store.lease_generation, "controller_active": store.controller_id is not None, "arduino": pipeline.serial.status()}

    @app.get("/v1/inventory")
    async def inventory():
        return {"example": settings.inventory.example, "categories": [c.__dict__ for c in settings.inventory.categories]}

    @app.post("/v1/analyze/photo")
    async def analyze_photo(file: UploadFile = File(...), x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        if settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")
        generation, scene = store.lease_generation, scene_generation
        try:
            image = await _read_image(file, settings.max_upload_bytes, settings.max_pixels)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            captured_at = time.time()
            result = await asyncio.wait_for(asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, True, scene))), settings.inference_timeout_seconds)
        except asyncio.TimeoutError as exc:
            if current(generation, x_session_id, scene):
                await clear_scene("inference_timeout")
            raise HTTPException(status_code=504, detail="inference timed out") from exc
        except StaleFrameError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncio.CancelledError:
            raise HTTPException(status_code=409, detail="photo superseded by a newer frame")
        result = await commit_result(result, generation, x_session_id, scene, True)
        if result is None:
            raise HTTPException(status_code=409, detail="controller lease expired during inference")
        await broadcast({"type": "detection", "result": result})
        await broadcast({"type": "arduino_ack", "arduino": result["arduino"]})
        return result

    @app.post("/v1/calibration/background")
    async def set_background(file: UploadFile = File(...), x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None), x_roi: str | None = Header(default=None)):
        nonlocal calibrating
        if settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")
        try:
            import cv2
            background = await _read_image(file, settings.max_upload_bytes, settings.max_pixels)
            pending_roi = pipeline.segmenter.roi
            if x_roi:
                import json
                roi = tuple(int(v) for v in json.loads(x_roi))
                if len(roi) != 4 or any(v < 0 for v in roi) or roi[2] <= 0 or roi[3] <= 0 or roi[0] + roi[2] > background.shape[1] or roi[1] + roi[3] > background.shape[0]:
                    raise ValueError("ROI must be [x,y,width,height] inside the calibration image")
                pending_roi = roi
            if pending_roi and (pending_roi[0] + pending_roi[2] > background.shape[1] or pending_roi[1] + pending_roi[3] > background.shape[0]):
                raise ValueError("configured ROI exceeds calibration image dimensions")
            generation = store.lease_generation
            calibrating = True
            await clear_scene("calibration_started")
            scene = scene_generation
            def calibrate():
                with state_lock:
                    if not current(generation, x_session_id, scene):
                        raise ValueError("controller changed during calibration")
                pipeline.segmenter.set_background(background)
                pipeline.segmenter.roi = pending_roi
                settings.roi = pending_roi
                pipeline.reset_scene()
                if settings.roi_path and pending_roi:
                    from pathlib import Path
                    Path(settings.roi_path).parent.mkdir(parents=True, exist_ok=True)
                    import json
                    Path(settings.roi_path).write_text(json.dumps(list(pending_roi)), encoding="utf-8")
                if settings.background_path:
                    from pathlib import Path
                    Path(settings.background_path).parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(settings.background_path, background):
                        raise ValueError("unable to save calibration image")
            await asyncio.wrap_future(inference_worker.submit(calibrate))
        except asyncio.CancelledError as exc:
            raise HTTPException(status_code=409, detail="calibration superseded") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            calibrating = False
        return {"calibrated": True, "roi": list(pipeline.segmenter.roi) if pipeline.segmenter.roi else None, "image": {"width": int(pipeline.segmenter.background.shape[1]), "height": int(pipeline.segmenter.background.shape[0]), "orientation": "normalized"}}

    @app.post("/v1/stream/frame")
    async def stream_frame(file: UploadFile = File(...), x_session_id: str | None = Header(default=None), x_session_ticket: str | None = Header(default=None)):
        nonlocal stream_task, stream_generation
        if settings.auth_required:
            auth(x_session_id, x_session_ticket, "controller")
        if calibrating:
            raise HTTPException(status_code=409, detail="background calibration is in progress")
        try:
            image = await _read_image(file, settings.max_upload_bytes, settings.max_pixels)
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
                result = await asyncio.wait_for(asyncio.wrap_future(inference_worker.submit(lambda: process_frame(image, captured_at, False, scene))), settings.inference_timeout_seconds)
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
        with state_lock:
            authorized = not settings.auth_required or store.consume_ws_ticket(session_id or "", ticket or "")
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
                if settings.auth_required and (not session or session.expires_at <= time.time() or (session.role == "controller" and (session.lease_expires_at <= time.time() or store.controller_id != session.session_id))):
                    await websocket.close(code=1008)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(websocket)

    # The API remains usable without a build; when the Vite bundle exists this
    # gives the phone one local origin for dashboard and API.
    from pathlib import Path
    frontend_dist = Path("frontend/dist")
    if frontend_dist.is_dir() and (frontend_dist / "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app
