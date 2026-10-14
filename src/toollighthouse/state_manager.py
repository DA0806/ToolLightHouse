from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _State:
    candidate: str | None = None
    count: int = 0
    confirmed: str | None = None


class StateManager:
    def __init__(self, confirmations: int = 2, photo_confirmations: int = 1):
        self.confirmations, self.photo_confirmations = confirmations, photo_confirmations
        self._states: dict[int, _State] = {}

    def reset(self) -> None:
        self._states.clear()

    def update(self, track_id: int, class_id: str | None, valid: bool, photo: bool = False) -> str | None:
        state = self._states.setdefault(track_id, _State())
        if not valid or class_id is None:
            # An invalid observation must not keep a stale LED asserted forever.
            state.candidate, state.count, state.confirmed = None, 0, None
            return None
        if state.confirmed is not None and state.confirmed != class_id:
            state.confirmed, state.candidate, state.count = None, class_id, 1
            return None
        if state.candidate == class_id:
            state.count += 1
        else:
            state.candidate, state.count = class_id, 1
        if state.count >= (self.photo_confirmations if photo else self.confirmations):
            state.confirmed = class_id
        return state.confirmed
