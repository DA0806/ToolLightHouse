from __future__ import annotations

import asyncio
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from ..config import Settings


@dataclass
class Session:
    session_id: str
    device_id: str
    role: str                       # 'admin' | 'controller' | 'reader' | 'camera_transmitter'
    ticket: str
    expires_at: float               # Wall-clock time (time.time())
    lease_expires_at: float         # Wall-clock time (time.time())
    ws_tickets: dict[str, float] = field(default_factory=dict)


@dataclass
class PhysicalControlLease:
    holder_session_id: str
    role: str                       # 'admin' | 'controller'
    device_id: str
    acquired_at: float              # Wall-clock time (time.time())
    lease_expires_at: float         # Wall-clock time (time.time())
    lease_generation: int


class UnifiedSessionStore:
    """
    Unified Control Lease store managing physical control exclusivity,
    explicit administrative takeover, heartbeats, and roles.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.lease: PhysicalControlLease | None = None
        self.lease_generation: int = 0
        self.transition_state: str = "idle"       # "idle" | "transitioning" | "failed"
        self.current_transition_id: str | None = None
        self.transition_mutex = asyncio.Lock()
        self.state_lock = threading.RLock()

    @property
    def controller_id(self) -> str | None:
        """Backward-compatible property for existing call sites."""
        with self.state_lock:
            if self.lease is not None and self.lease.lease_expires_at > time.time():
                return self.lease.holder_session_id
            return None

    @controller_id.setter
    def controller_id(self, value: str | None):
        with self.state_lock:
            if value is None:
                self.lease = None
            else:
                session = self.sessions.get(value)
                if session:
                    now = time.time()
                    self.lease = PhysicalControlLease(
                        holder_session_id=session.session_id,
                        role=session.role,
                        device_id=session.device_id,
                        acquired_at=now,
                        lease_expires_at=now + self.settings.lease_ttl_seconds,
                        lease_generation=self.lease_generation,
                    )

    def create(
        self,
        device_id: str,
        role: str,
        key: str | None = None,
        admin_key: str | None = None,
    ) -> Session:
        if role not in {"controller", "reader", "admin", "camera_transmitter"}:
            raise ValueError("role must be controller, reader, admin, or camera_transmitter")

        if role == "admin":
            effective_admin = self.settings.admin_key or ""
            supplied = admin_key or key
            if self.settings.auth_required:
                if not effective_admin or not secrets.compare_digest(supplied or "", effective_admin):
                    raise PermissionError("invalid admin key")
        elif role in {"controller", "reader"}:
            if self.settings.auth_required and (
                not self.settings.pairing_key or not secrets.compare_digest(key or "", self.settings.pairing_key)
            ):
                raise PermissionError("invalid pairing key")
            if role == "controller":
                with self.state_lock:
                    now = time.time()
                    if self.lease is not None and self.lease.lease_expires_at > now:
                        current = self.sessions.get(self.lease.holder_session_id)
                        if current and current.expires_at > now:
                            raise PermissionError("another controller session is active")
                    self.lease = None
        elif role == "camera_transmitter":
            raise PermissionError("Las credenciales de camera_transmitter solo pueden obtenerse canjeando una invitación válida mediante /v1/camera/signal")

        now_wall = time.time()
        session = Session(
            session_id=str(uuid.uuid4()),
            device_id=device_id,
            role=role,
            ticket=secrets.token_urlsafe(24),
            expires_at=now_wall + self.settings.session_ttl_seconds,
            lease_expires_at=now_wall + self.settings.lease_ttl_seconds,
            ws_tickets={},
        )
        with self.state_lock:
            self.sessions[session.session_id] = session
            if role == "controller":
                self.lease_generation += 1
                self.lease = PhysicalControlLease(
                    holder_session_id=session.session_id,
                    role=session.role,
                    device_id=session.device_id,
                    acquired_at=now_wall,
                    lease_expires_at=now_wall + self.settings.lease_ttl_seconds,
                    lease_generation=self.lease_generation,
                )
        return session

    def _get_valid_session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if not session or session.expires_at <= time.time():
            raise PermissionError("session expired or invalid")
        return session

    def acquire_vacant_lease(self, session_id: str) -> PhysicalControlLease:
        """Adquiere el lease ÚNICAMENTE si está vacante o expirado y no hay transición pendiente."""
        with self.state_lock:
            if self.transition_state == "transitioning":
                raise PermissionError("Transición de control en curso; adquisición bloqueada temporalmente.")

            session = self._get_valid_session(session_id)
            if session.role not in {"controller", "admin"}:
                raise PermissionError(f"El rol '{session.role}' no tiene permiso para adquirir el control físico.")

            now = time.time()
            if self.lease is not None and self.lease.lease_expires_at > now:
                if self.lease.holder_session_id == session_id:
                    self.lease.lease_expires_at = now + self.settings.lease_ttl_seconds
                    session.lease_expires_at = now + self.settings.lease_ttl_seconds
                    return self.lease
                raise PermissionError("El control físico está ocupado por otro operador activo.")

            self.lease_generation += 1
            self.lease = PhysicalControlLease(
                holder_session_id=session.session_id,
                role=session.role,
                device_id=session.device_id,
                acquired_at=now,
                lease_expires_at=now + self.settings.lease_ttl_seconds,
                lease_generation=self.lease_generation,
            )
            session.lease_expires_at = now + self.settings.lease_ttl_seconds
            self.transition_state = "idle"
            return self.lease

    async def takeover_control_lease(
        self,
        admin_session_id: str,
        teardown_callback: Callable[[int], Coroutine[Any, Any, None]] | None = None,
    ) -> PhysicalControlLease:
        """
        Expropiación EXPLÍCITA ejecutada con exclusión mutua de transición:
        1. Serializada por transition_mutex (asyncio.Lock).
        2. Bajo state_lock: valida rol 'admin', rechaza si transition_state == 'transitioning',
           asigna transition_id, marca transition_state = 'transitioning', incrementa lease_generation,
           desasigna el lease actual.
        3. Teardown I/O ejecutado FUERA de state_lock.
        4. Re-adquisición de state_lock: revalida transition_id, generación y validez de la sesión admin.
           Si exitoso, asigna nuevo lease. Si falla o es revocada, deja lease = None y transition_state = 'failed'.
        """
        async with self.transition_mutex:
            # --- FASE 1: Validación y bloqueo atómico de transición ---
            with self.state_lock:
                session = self._get_valid_session(admin_session_id)
                if session.role != "admin":
                    raise PermissionError("Únicamente una sesión administrativa puede forzar la toma de control.")
                if self.transition_state == "transitioning":
                    raise PermissionError("Otra transición de control ya se encuentra en curso.")

                transition_id = str(uuid.uuid4())
                self.current_transition_id = transition_id
                self.transition_state = "transitioning"
                self.lease_generation += 1
                captured_generation = self.lease_generation

                # Captura el titular anterior antes de desasignar
                previous_controller_id = self.lease.holder_session_id if self.lease else None
                # Desasigna de inmediato al titular anterior
                self.lease = None

            # --- FASE 2: Teardown I/O y apagado FUERA de state_lock ---
            teardown_ok = False
            try:
                if teardown_callback is not None:
                    try:
                        await teardown_callback(captured_generation, previous_controller_id)
                    except TypeError:
                        await teardown_callback(captured_generation)
                teardown_ok = True
            except Exception:
                teardown_ok = False

            # --- FASE 3: Revalidación y compromiso bajo state_lock ---
            with self.state_lock:
                now_mono = time.monotonic()
                now_wall = time.time()
                session_still_valid = (
                    admin_session_id in self.sessions
                    and self.sessions[admin_session_id].expires_at > now_wall
                    and self.sessions[admin_session_id].role == "admin"
                )

                if (
                    teardown_ok
                    and self.current_transition_id == transition_id
                    and self.lease_generation == captured_generation
                    and session_still_valid
                ):
                    self.lease = PhysicalControlLease(
                        holder_session_id=admin_session_id,
                        role="admin",
                        device_id=self.sessions[admin_session_id].device_id,
                        acquired_at=now_wall,
                        lease_expires_at=now_wall + self.settings.lease_ttl_seconds,
                        lease_generation=captured_generation,
                    )
                    self.sessions[admin_session_id].lease_expires_at = now_wall + self.settings.lease_ttl_seconds
                    self.transition_state = "idle"
                    self.current_transition_id = None
                    return self.lease
                else:
                    self.lease = None
                    self.transition_state = "failed"
                    self.current_transition_id = None
                    raise RuntimeError("Fallo durante la expropiación o sesión de administrador invalidada.")

    def heartbeat(self, session_id: str, ticket: str) -> bool:
        """
        Renovación periódica del lease activo (heartbeat).
        REVALIDA que:
        1. No haya transición pendiente (transition_state != 'transitioning').
        2. La sesión autenticada exista y no esté expirada en el dominio de tiempo de pared.
        3. El ticket coincida criptográficamente.
        4. El solicitante sea el titular actual del lease físico.
        """
        with self.state_lock:
            if self.transition_state == "transitioning":
                return False

            now_wall = time.time()
            session = self.sessions.get(session_id)
            if not session or session.expires_at <= now_wall or session.role not in {"controller", "admin"}:
                return False
            if not secrets.compare_digest(session.ticket, ticket or ""):
                return False
            if self.lease and self.lease.holder_session_id == session_id:
                if self.lease.lease_expires_at > now_wall:
                    self.lease.lease_expires_at = now_wall + self.settings.lease_ttl_seconds
                    session.lease_expires_at = now_wall + self.settings.lease_ttl_seconds
                    session.expires_at = now_wall + self.settings.session_ttl_seconds
                    return True
            return False

    def valid(self, session_id: str | None, ticket: str | None, role: str | None = None) -> bool:
        with self.state_lock:
            session = self.sessions.get(session_id or "")
            now_wall = time.time()
            if not session:
                return False
            # camera_transmitter sessions cannot access general endpoints without specifying role
            if session.role == "camera_transmitter" and role != "camera_transmitter":
                return False
            owns_lease = self.lease and self.lease.holder_session_id == session.session_id
            if (
                session.expires_at <= now_wall
                or (owns_lease and self.lease.lease_expires_at <= now_wall)
                or (session.role == "controller" and (not self.lease or self.lease.holder_session_id != session.session_id))
            ):
                if session and self.lease and self.lease.holder_session_id == session.session_id:
                    self.lease = None
                    self.lease_generation += 1
                return False
            valid = bool(ticket) and secrets.compare_digest(session.ticket, ticket or "") and (role is None or session.role == role)
            if valid and owns_lease:
                self.lease.lease_expires_at = now_wall + self.settings.lease_ttl_seconds
                session.lease_expires_at = now_wall + self.settings.lease_ttl_seconds
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
        with self.state_lock:
            session = self.sessions.get(session_id)
            if session and self.lease and self.lease.holder_session_id == session_id:
                self.lease = None
                self.lease_generation += 1
            if session:
                session.expires_at = 0
                session.lease_expires_at = 0
                session.ws_tickets.clear()

    def expire_controller(self) -> bool:
        with self.state_lock:
            if not self.lease:
                return False
            now_wall = time.time()
            session = self.sessions.get(self.lease.holder_session_id)
            if session and session.expires_at > now_wall and self.lease.lease_expires_at > now_wall:
                return False
            self.lease = None
            self.lease_generation += 1
            return True
