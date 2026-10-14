from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass
class Track:
    track_id: int
    bbox: tuple[int, int, int, int]
    missed: int = 0
    class_id: str | None = None
    velocity: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))


def _center(box):
    x, y, w, h = box
    return (x + w / 2, y + h / 2)


def _iou(first, second) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def _hungarian(costs: list[list[float]]) -> list[int]:
    """Return the minimum-cost column for every row of a square matrix."""

    size = len(costs)
    if not size:
        return []
    rows = [0.0] * (size + 1)
    cols = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    predecessor = [0] * (size + 1)
    for row in range(1, size + 1):
        matching[0] = row
        column = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = matching[column]
            delta = math.inf
            next_column = 0
            for candidate in range(1, size + 1):
                if used[candidate]:
                    continue
                reduced = costs[current_row - 1][candidate - 1] - rows[current_row] - cols[candidate]
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    predecessor[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(size + 1):
                if used[candidate]:
                    rows[matching[candidate]] += delta
                    cols[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matching[column] == 0:
                break
        while True:
            previous = predecessor[column]
            matching[column] = matching[previous]
            column = previous
            if column == 0:
                break
    assignment = [-1] * size
    for column in range(1, size + 1):
        if matching[column]:
            assignment[matching[column] - 1] = column - 1
    return assignment


class CentroidTracker:
    def __init__(self, max_distance: float = 120, max_missed: int = 8):
        self.max_distance, self.max_missed = max_distance, max_missed
        self._next_id = 1
        self.tracks: dict[int, Track] = {}

    def reset(self) -> None:
        self.tracks.clear()

    def _predicted_box(self, track: Track) -> tuple[float, float, float, float]:
        x, y, width, height = track.bbox
        steps = track.missed + 1
        return (x + track.velocity[0] * steps, y + track.velocity[1] * steps, width, height)

    def _cost(self, track: Track, box: tuple[int, int, int, int]) -> float | None:
        predicted = self._predicted_box(track)
        distance = math.dist(_center(predicted), _center(box))
        overlap = _iou(predicted, box)
        if distance > self.max_distance and overlap < 0.05:
            return None
        return min(distance / self.max_distance, 2.0) * 0.8 + (1.0 - overlap) * 0.2

    def update(self, boxes: list[tuple[int, int, int, int]]) -> list[Track]:
        if not boxes:
            for track in list(self.tracks.values()):
                track.missed += 1
                if track.missed > self.max_missed:
                    del self.tracks[track.track_id]
            return []
        tracks = [self.tracks[track_id] for track_id in sorted(self.tracks)]
        track_count, box_count = len(tracks), len(boxes)
        if not tracks:
            assigned = []
            for box in boxes:
                track = Track(self._next_id, box)
                self._next_id += 1
                self.tracks[track.track_id] = track
                assigned.append(track)
            return assigned

        unmatched_cost, invalid_cost = 1.0, 10_000.0
        size = track_count + box_count
        costs = [[0.0] * size for _ in range(size)]
        for index, track in enumerate(tracks):
            for box_index, box in enumerate(boxes):
                cost = self._cost(track, box)
                costs[index][box_index] = invalid_cost if cost is None else cost
            for dummy_index in range(track_count):
                costs[index][box_count + dummy_index] = unmatched_cost if dummy_index == index else invalid_cost
        for dummy_index in range(box_count):
            row = track_count + dummy_index
            for box_index in range(box_count):
                costs[row][box_index] = unmatched_cost
            for track_dummy in range(track_count):
                costs[row][box_count + track_dummy] = 0.0

        assignment = _hungarian(costs)
        matched_boxes: set[int] = set()
        assigned_by_box: dict[int, Track] = {}
        for track_index, column in enumerate(assignment[:track_count]):
            track = tracks[track_index]
            if 0 <= column < box_count and costs[track_index][column] <= unmatched_cost:
                box = boxes[column]
                old_center = _center(track.bbox)
                new_center = _center(box)
                steps = track.missed + 1
                track.velocity = ((new_center[0] - old_center[0]) / steps, (new_center[1] - old_center[1]) / steps)
                track.bbox, track.missed = box, 0
                matched_boxes.add(column)
                assigned_by_box[column] = track
            else:
                track.missed += 1
                if track.missed > self.max_missed:
                    del self.tracks[track.track_id]

        for box_index, box in enumerate(boxes):
            if box_index not in matched_boxes:
                track = Track(self._next_id, box)
                self._next_id += 1
                self.tracks[track.track_id] = track
                assigned_by_box[box_index] = track
        return [assigned_by_box[index] for index in range(box_count)]
