from __future__ import annotations

import re
import threading
import time


def validate_mask(mask: int) -> int:
    if isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask <= 511:
        raise ValueError("LED mask must be an integer from 0 to 511")
    return mask


def parse_ack(line: str) -> int:
    match = re.fullmatch(r"ACK:(\d+)", line.strip())
    if not match:
        raise ValueError("invalid ACK")
    return validate_mask(int(match.group(1)))


class SimulatedSerial:
    simulated = True

    def __init__(self, watchdog_seconds: float = 2.5):
        self._state_lock = threading.RLock()
        self.connected = True
        self.watchdog_seconds = watchdog_seconds
        self.requested_mask = 0
        self.confirmed_mask: int | None = 0
        self.last_ack_at = time.time()

    def send_mask(self, mask: int, retries: int = 2) -> bool:
        validate_mask(mask)
        with self._state_lock:
            self.requested_mask = mask
            if not self.connected:
                self.confirmed_mask = None
                return False
            self.confirmed_mask = mask
            self.last_ack_at = time.time()
            return True

    def status(self):
        with self._state_lock:
            if self.confirmed_mask and time.time() - self.last_ack_at > self.watchdog_seconds:
                self.requested_mask = 0
                self.confirmed_mask = 0 if self.connected else None
            return {"connected": self.connected, "simulated": True, "requested_mask": self.requested_mask, "confirmed_mask": self.confirmed_mask, "ack": self.confirmed_mask is not None, "last_ack_at": self.last_ack_at}


class SerialTransport:
    simulated = False

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.4, retries: int = 2, watchdog_seconds: float = 2.5):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for hardware mode") from exc
        self.serial = serial
        self.port, self.baudrate, self.timeout, self.retries = port, baudrate, timeout, retries
        self._lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._serial = None
        self.requested_mask, self.confirmed_mask = 0, None
        self.watchdog_seconds = watchdog_seconds
        self.last_ack_at = 0.0
        self._scene_deadline = 0.0
        self._explicit_deadline = False
        self._closed = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    def open(self):
        with self._lock:
            self._closed = False
            self._connect_locked()
        self._heartbeat_stop.clear()
        if self._heartbeat is None or not self._heartbeat.is_alive():
            self._heartbeat = threading.Thread(target=self._heartbeat_loop, name="toollighthouse-serial", daemon=True)
            self._heartbeat.start()

    def set_scene_deadline(self, deadline: float):
        with self._state_lock:
            self._explicit_deadline = True
            self._scene_deadline = deadline
            if deadline <= time.time():
                self.requested_mask = 0

    def _disconnect_locked(self):
        with self._state_lock:
            port, self._serial = self._serial, None
            self.confirmed_mask = None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def _connect_locked(self):
        self._disconnect_locked()
        try:
            port = self.serial.Serial(self.port, self.baudrate, timeout=self.timeout, write_timeout=self.timeout)
            with self._state_lock:
                self._serial = port
            time.sleep(1.8)
            if not self._exchange_locked(0, 1):
                self._disconnect_locked()
                return False
            return True
        except Exception:
            self._disconnect_locked()
            return False

    def _exchange_locked(self, mask, retries):
        with self._state_lock:
            self.confirmed_mask = None
            port = self._serial
        if port is None:
            return False
        for _ in range(retries + 1):
            try:
                port.write(f"MASK:{mask}\n".encode("ascii"))
                line = port.readline().decode("ascii", "replace")
            except Exception:
                self._disconnect_locked()
                return False
            try:
                ack = parse_ack(line)
            except ValueError:
                continue
            if ack == mask:
                with self._state_lock:
                    self.confirmed_mask = ack
                    self.last_ack_at = time.time()
                return True
        return False

    def send_mask(self, mask: int, retries: int | None = None) -> bool:
        validate_mask(mask)
        with self._lock:
            with self._state_lock:
                if not self._explicit_deadline:
                    self._scene_deadline = time.time() + self.watchdog_seconds
                if self._closed or self._scene_deadline <= time.time():
                    mask = 0
                self.requested_mask = mask
            return self._exchange_locked(mask, self.retries if retries is None else retries)

    def status(self):
        with self._state_lock:
            if self.confirmed_mask is not None and time.time() - self.last_ack_at > self.watchdog_seconds:
                self.confirmed_mask = None
            return {"connected": self._serial is not None, "simulated": False, "requested_mask": self.requested_mask, "confirmed_mask": self.confirmed_mask, "ack": self.confirmed_mask is not None, "last_ack_at": self.last_ack_at}

    def _refresh(self):
        with self._lock:
            if self._closed:
                return
            if self._serial is None and not self._connect_locked():
                return
            with self._state_lock:
                if self._scene_deadline <= time.time():
                    self.requested_mask = 0
                mask = self.requested_mask
            self._exchange_locked(mask, 0)

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(min(1.0, self.watchdog_seconds / 2)):
            self._refresh()

    def close(self):
        self._heartbeat_stop.set()
        with self._lock:
            self._closed = True
            with self._state_lock:
                self.requested_mask = 0
                self._scene_deadline = 0
            self._exchange_locked(0, 0)
            self._disconnect_locked()
        if self._heartbeat:
            self._heartbeat.join(timeout=2)
