from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


@dataclass
class CameraInvite:
    invite_id: str
    invite_token: str
    created_at: float
    expires_at: float
    owner_session_id: str
    owner_lease_generation: int
    source_version: int
    source_id: str = "remote_phone"
    used: bool = False
    revoked: bool = False


@dataclass
class CameraTransmitterCredential:
    transmitter_id: str
    ticket: str
    owner_session_id: str
    owner_lease_generation: int
    source_version: int
    created_at: float
    expires_at: float
    source_id: str = "remote_phone"
    ws: WebSocket | None = None
    last_upload_mono: float = 0.0
    last_sequence: int = -1


class CameraInviteStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.invites: dict[str, CameraInvite] = {}              # invite_id -> CameraInvite
        self.invites_by_token: dict[str, CameraInvite] = {}     # invite_token -> CameraInvite
        self.transmitters: dict[str, CameraTransmitterCredential] = {}  # transmitter_id -> Credential
        self.latest_fallback_frame: bytes | None = None
        self.latest_fallback_timestamp: float = 0.0
        self.latest_fallback_arrival_mono: float = 0.0
        self.latest_fallback_source_id: str | None = None
        self.latest_fallback_source_version: int | None = None
        self.latest_fallback_sequence: int = -1
        self.latest_fallback_transmitter_id: str | None = None

    def save_fallback_frame(
        self,
        frame_bytes: bytes,
        capture_timestamp: float | None = None,
        *,
        source_id: str = "remote_phone",
        source_version: int = 0,
        sequence: int = -1,
        transmitter_id: str | None = None,
    ):
        self.latest_fallback_frame = frame_bytes
        self.latest_fallback_timestamp = capture_timestamp or time.time()
        self.latest_fallback_arrival_mono = time.monotonic()
        self.latest_fallback_source_id = source_id
        self.latest_fallback_source_version = source_version
        self.latest_fallback_sequence = sequence
        self.latest_fallback_transmitter_id = transmitter_id

    def get_latest_fallback_frame(self, max_age_seconds: float = 5.0) -> bytes | None:
        if self.latest_fallback_frame is None:
            return None
        if time.monotonic() - self.latest_fallback_arrival_mono > max_age_seconds:
            self.latest_fallback_frame = None
            self.latest_fallback_arrival_mono = 0.0
            return None
        return self.latest_fallback_frame

    def clear_fallback_frame(self):
        self.latest_fallback_frame = None
        self.latest_fallback_arrival_mono = 0.0
        self.latest_fallback_source_id = None
        self.latest_fallback_source_version = None
        self.latest_fallback_sequence = -1
        self.latest_fallback_transmitter_id = None

    async def create_invite(
        self,
        owner_session_id: str,
        owner_lease_generation: int,
        source_version: int,
        expires_in_seconds: int = 180,
        *,
        source_id: str = "remote_phone",
    ) -> CameraInvite:
        async with self._lock:
            invite_id = f"inv_{uuid.uuid4().hex[:12]}"
            invite_token = secrets.token_urlsafe(32)
            now = time.time()
            invite = CameraInvite(
                invite_id=invite_id,
                invite_token=invite_token,
                created_at=now,
                expires_at=now + expires_in_seconds,
                owner_session_id=owner_session_id,
                owner_lease_generation=owner_lease_generation,
                source_version=source_version,
                source_id=source_id,
            )
            self.invites[invite_id] = invite
            self.invites_by_token[invite_token] = invite
            return invite

    async def cancel_invite(self, invite_id: str, owner_session_id: str) -> bool:
        async with self._lock:
            invite = self.invites.get(invite_id)
            if not invite or invite.owner_session_id != owner_session_id:
                return False
            invite.revoked = True
            return True

    async def revoke_all_for_owner(self, owner_session_id: str) -> int:
        sockets: list[WebSocket] = []
        async with self._lock:
            count = 0
            for inv in self.invites.values():
                if inv.owner_session_id == owner_session_id and not inv.revoked:
                    inv.revoked = True
                    count += 1
            for tid, trans in list(self.transmitters.items()):
                if trans.owner_session_id == owner_session_id:
                    if trans.ws:
                        sockets.append(trans.ws)
                    del self.transmitters[tid]
                    count += 1
            self.clear_fallback_frame()
        for socket in sockets:
            try:
                await socket.close(code=1008, reason="Owner revoked camera permission")
            except Exception:
                pass
        return count

    async def revoke_all_for_source(self, source_version: int) -> int:
        sockets: list[WebSocket] = []
        async with self._lock:
            count = 0
            for invite in self.invites.values():
                if invite.source_version == source_version and not invite.revoked:
                    invite.revoked = True
                    count += 1
            for tid, trans in list(self.transmitters.items()):
                if trans.source_version == source_version:
                    if trans.ws:
                        sockets.append(trans.ws)
                    del self.transmitters[tid]
                    count += 1
            self.clear_fallback_frame()
        for socket in sockets:
            try:
                await socket.close(code=1008, reason="Camera source changed")
            except Exception:
                pass
        return count

    async def consume_invite(
        self,
        invite_token: str,
        current_lease_gen: int | None = None,
        current_source_ver: int | None = None,
        current_source_id: str | None = None,
    ) -> CameraTransmitterCredential:
        async with self._lock:
            invite = self.invites_by_token.get(invite_token)
            now = time.time()
            if not invite:
                raise PermissionError("Invitación no encontrada.")
            if invite.revoked:
                raise PermissionError("Esta invitación ha sido revocada.")
            if invite.used:
                raise PermissionError("Esta invitación ya fue consumida previamente.")
            if invite.expires_at <= now:
                raise PermissionError("La invitación ha expirado.")

            if (
                current_lease_gen is not None
                and current_source_ver is not None
                and (
                    invite.owner_lease_generation != current_lease_gen
                    or invite.source_version != current_source_ver
                    or (current_source_id is not None and invite.source_id != current_source_id)
                )
            ):
                invite.revoked = True
                raise PermissionError("invite is no longer bound to the active source")

            # Mark used atomically (single-use)
            invite.used = True

            transmitter_id = f"trans_{uuid.uuid4().hex[:12]}"
            ticket = secrets.token_urlsafe(32)
            cred = CameraTransmitterCredential(
                transmitter_id=transmitter_id,
                ticket=ticket,
                owner_session_id=invite.owner_session_id,
                owner_lease_generation=invite.owner_lease_generation,
                source_version=invite.source_version,
                source_id=invite.source_id,
                created_at=now,
                expires_at=now + 3600.0, # 1 hour session
            )
            self.transmitters[transmitter_id] = cred
            return cred

    async def reconnect_transmitter(
        self,
        transmitter_id: str,
        ticket: str,
        current_lease_gen: int | None = None,
        current_source_ver: int | None = None,
        current_source_id: str | None = None,
    ) -> CameraTransmitterCredential:
        async with self._lock:
            cred = self.transmitters.get(transmitter_id)
            now = time.time()
            if not cred or not secrets.compare_digest(cred.ticket, ticket or ""):
                raise PermissionError("Credencial de transmisor inválida.")
            if cred.expires_at <= now:
                raise PermissionError("Credencial de transmisor expirada.")
            if (
                current_lease_gen is not None
                and current_source_ver is not None
                and (
                    cred.owner_lease_generation != current_lease_gen
                    or cred.source_version != current_source_ver
                    or (current_source_id is not None and cred.source_id != current_source_id)
                )
            ):
                del self.transmitters[transmitter_id]
                raise PermissionError("Credencial de transmisor obsoleta para la fuente activa.")
            return cred

    async def validate_transmitter_for_upload(
        self,
        transmitter_id: str,
        ticket: str,
        current_lease_gen: int,
        current_source_ver: int,
        current_source_id: str = "remote_phone",
        *,
        require_connected: bool = False,
    ) -> CameraTransmitterCredential:
        async with self._lock:
            cred = self.transmitters.get(transmitter_id)
            now = time.time()
            if not cred or not secrets.compare_digest(cred.ticket, ticket or ""):
                raise PermissionError("Credencial de transmisor inválida.")
            if cred.expires_at <= now:
                raise PermissionError("Credencial expirada.")
            if (
                cred.owner_lease_generation != current_lease_gen
                or cred.source_version != current_source_ver
                or cred.source_id != current_source_id
            ):
                raise PermissionError("Generación de sesión o versión de fuente obsoleta.")
            if require_connected and cred.ws is None:
                raise PermissionError("transmitter connection is not active")
            return cred


class WebRTCSignalingHub:
    """Authenticated, bounded relay for one operator and one transmitter."""

    MAX_MESSAGE_BYTES = 65536
    MAX_MESSAGES_PER_MINUTE = 120

    def __init__(
        self,
        invite_store: CameraInviteStore,
        *,
        allowed_origins: tuple[str, ...] = (),
        on_transmitter_disconnect: Callable[[str], Awaitable[None]] | None = None,
        validate_transmitter: Callable[[CameraTransmitterCredential], bool] | None = None,
    ):
        self.invite_store = invite_store
        self.operator_ws: WebSocket | None = None
        self.operator_session_id: str | None = None
        self.transmitter_ws: WebSocket | None = None
        self.active_transmitter: CameraTransmitterCredential | None = None
        self._lock = asyncio.Lock()
        self.allowed_origins = tuple(allowed_origins)
        self.on_transmitter_disconnect = on_transmitter_disconnect
        self.validate_transmitter = validate_transmitter

    def _origin_allowed(self, websocket: WebSocket) -> bool:
        origin = websocket.headers.get("origin")
        if not origin:
            return True
        host = websocket.headers.get("host")
        return origin in self.allowed_origins or (host is not None and origin in {f"http://{host}", f"https://{host}"})

    async def _reject(self, websocket: WebSocket, message: str, code: int = 1008):
        try:
            await websocket.send_json({"type": "error", "message": message})
        except Exception:
            pass
        await websocket.close(code=code, reason=message)

    async def handle_connection(self, websocket: WebSocket, session_validator):
        if not self._origin_allowed(websocket):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        await websocket.accept()
        role: str | None = None
        session_id: str | None = None
        operator_ticket: str | None = None
        try:
            raw_first = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            if len(raw_first.encode("utf-8")) > self.MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                return
            try:
                auth_data = json.loads(raw_first)
            except Exception:
                await self._reject(websocket, "Invalid JSON", 1003)
                return
            if not isinstance(auth_data, dict) or len(auth_data) > 8 or not isinstance(auth_data.get("type"), str):
                await self._reject(websocket, "Invalid auth schema", 1003)
                return

            msg_type = auth_data["type"]
            peer: WebSocket | None = None
            old_transmitter: WebSocket | None = None
            if msg_type == "auth_operator":
                s_id = auth_data.get("session_id")
                ticket = auth_data.get("ticket")
                if not isinstance(s_id, str) or not isinstance(ticket, str) or not session_validator(s_id, ticket):
                    await self._reject(websocket, "Authentication failed")
                    return
                async with self._lock:
                    old_operator = self.operator_ws
                    self.operator_ws = websocket
                    self.operator_session_id = s_id
                    peer = self.transmitter_ws
                if old_operator is not None and old_operator is not websocket:
                    try:
                        await old_operator.close(code=1000, reason="Replaced by operator")
                    except Exception:
                        pass
                role, session_id, operator_ticket = "operator", s_id, ticket
                await websocket.send_json({"type": "auth_ok", "role": "operator"})
                if peer is not None:
                    try:
                        await websocket.send_json({"type": "peer_connected", "role": "transmitter"})
                    except Exception:
                        pass
            elif msg_type == "auth_transmitter":
                invite_token = auth_data.get("invite_token")
                if not isinstance(invite_token, str) or not invite_token:
                    await self._reject(websocket, "Invalid transmitter auth")
                    return
                try:
                    cred = await self.invite_store.consume_invite(invite_token)
                except Exception as exc:
                    await self._reject(websocket, str(exc))
                    return
                if self.validate_transmitter and not self.validate_transmitter(cred):
                    await self._reject(websocket, "Transmitter credential is stale")
                    return
                async with self._lock:
                    old_transmitter = self.transmitter_ws
                    if self.active_transmitter is not None and self.active_transmitter is not cred:
                        self.active_transmitter.ws = None
                    self.transmitter_ws = websocket
                    self.active_transmitter = cred
                    cred.ws = websocket
                    peer = self.operator_ws
                if old_transmitter is not None:
                    try:
                        await old_transmitter.close(code=1000, reason="Replaced by new transmitter")
                    except Exception:
                        pass
                role = "transmitter"
                await websocket.send_json({"type": "auth_ok", "role": "transmitter", "transmitter_id": cred.transmitter_id, "transmitter_ticket": cred.ticket})
                if peer is not None:
                    try:
                        await peer.send_json({"type": "peer_connected", "role": "transmitter"})
                    except Exception:
                        pass
            elif msg_type == "reconnect_transmitter":
                t_id = auth_data.get("transmitter_id")
                t_ticket = auth_data.get("transmitter_ticket")
                if not isinstance(t_id, str) or not isinstance(t_ticket, str):
                    await self._reject(websocket, "Invalid transmitter auth")
                    return
                try:
                    cred = await self.invite_store.reconnect_transmitter(t_id, t_ticket)
                except Exception as exc:
                    await self._reject(websocket, str(exc))
                    return
                if self.validate_transmitter and not self.validate_transmitter(cred):
                    await self._reject(websocket, "Transmitter credential is stale")
                    return
                async with self._lock:
                    old_transmitter = self.transmitter_ws
                    if self.active_transmitter is not None and self.active_transmitter is not cred:
                        self.active_transmitter.ws = None
                    self.transmitter_ws = websocket
                    self.active_transmitter = cred
                    cred.ws = websocket
                    peer = self.operator_ws
                if old_transmitter is not None and old_transmitter is not websocket:
                    try:
                        await old_transmitter.close(code=1000, reason="Replaced by transmitter reconnect")
                    except Exception:
                        pass
                role = "transmitter"
                await websocket.send_json({"type": "auth_ok", "role": "transmitter", "reconnected": True})
                if peer is not None:
                    try:
                        await peer.send_json({"type": "peer_connected", "role": "transmitter"})
                    except Exception:
                        pass
            else:
                await self._reject(websocket, "First message must authenticate", 1008)
                return

            msg_count = 0
            start_window = time.monotonic()
            while True:
                msg_text = await websocket.receive_text()
                now_m = time.monotonic()
                if now_m - start_window >= 60.0:
                    msg_count, start_window = 0, now_m
                msg_count += 1
                if msg_count > self.MAX_MESSAGES_PER_MINUTE:
                    await websocket.close(code=1008, reason="Rate limit exceeded")
                    return
                if len(msg_text.encode("utf-8")) > self.MAX_MESSAGE_BYTES:
                    await websocket.close(code=1009, reason="Message too large")
                    return
                try:
                    msg = json.loads(msg_text)
                except Exception:
                    continue
                if not isinstance(msg, dict) or len(msg) > 12:
                    continue
                if role == "operator":
                    if not session_validator(session_id, operator_ticket):
                        await websocket.close(code=1008, reason="Operator session or lease expired")
                        return
                else:
                    async with self._lock:
                        current_ws = self.transmitter_ws is websocket
                        cred = self.active_transmitter
                    if not current_ws or cred is None or (self.validate_transmitter and not self.validate_transmitter(cred)):
                        await websocket.close(code=1008, reason="Transmitter credential expired")
                        return
                if msg.get("type") not in {"offer", "answer", "candidate", "disconnect", "ice_failed", "capabilities"}:
                    continue
                allowed_fields = {
                    "offer": {"type", "sdp"},
                    "answer": {"type", "sdp"},
                    "candidate": {"type", "candidate"},
                    "disconnect": {"type"},
                    "ice_failed": {"type", "reason"},
                    "capabilities": {"type", "capabilities"},
                }[msg["type"]]
                if set(msg) - allowed_fields:
                    continue
                async with self._lock:
                    target = self.transmitter_ws if role == "operator" else self.operator_ws
                if target is not None:
                    try:
                        await target.send_json(msg)
                    except Exception:
                        pass
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception as exc:
            logger.warning("Error in WebRTC signaling: %s", exc)
        finally:
            peer = None
            disconnect_owner = None
            should_cleanup = False
            async with self._lock:
                if role == "operator" and self.operator_ws is websocket:
                    self.operator_ws = None
                    self.operator_session_id = None
                    peer = self.transmitter_ws
                elif role == "transmitter" and self.transmitter_ws is websocket:
                    self.transmitter_ws = None
                    disconnect_owner = self.active_transmitter.owner_session_id if self.active_transmitter else None
                    should_cleanup = True
                    if self.active_transmitter:
                        self.active_transmitter.ws = None
                    peer = self.operator_ws
            if peer is not None:
                try:
                    await peer.send_json({"type": "peer_disconnected", "role": "operator" if role == "operator" else "transmitter"})
                except Exception:
                    pass
            if should_cleanup and disconnect_owner and self.on_transmitter_disconnect:
                try:
                    await self.on_transmitter_disconnect(disconnect_owner)
                except Exception:
                    logger.exception("transmitter disconnect cleanup failed")
