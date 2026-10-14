from __future__ import annotations

import cv2


def draw_overlay(image, detections, metrics=None, arduino=None):
    """Draw pixel-contract boxes for the optional local OpenCV window."""
    output = image.copy()
    for detection in detections:
        x, y, width, height = detection["bbox"]
        color = (0, 220, 0) if detection.get("validation") in {"confirmed", "simulated"} else (0, 180, 220)
        cv2.rectangle(output, (int(x), int(y)), (int(x + width), int(y + height)), color, 2)
        label = f"#{detection['track_id']} {detection.get('name') or detection.get('validation', 'unknown')}"
        cv2.putText(output, label, (int(x), max(18, int(y) - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    if metrics is not None or arduino is not None:
        metrics, arduino = metrics or {}, arduino or {}
        def number(key):
            value = metrics.get(key)
            return "n/a" if value is None else f"{value:.1f}"
        present = ", ".join(dict.fromkeys(d.get("name") or d["class_id"] for d in detections if d.get("class_id"))) or "none"
        requested = arduino.get("requested_mask")
        confirmed = arduino.get("confirmed_mask") if arduino.get("ack") else None
        requested_bits = f"{requested:09b}" if requested is not None else "?????????"
        confirmed_bits = f"{confirmed:09b}" if confirmed is not None else "?????????"
        mode = "SIM" if arduino.get("simulated") else "REAL" if arduino.get("simulated") is False else "n/a"
        connection = "online" if arduino.get("connected") else "offline"
        ack = "yes" if confirmed is not None else "n/a"
        ack_ms = number("capture_to_ack_ms") if arduino.get("simulated") is False else "n/a"
        samples = metrics.get("stages", {}).get("embed", {}).get("sample_count", 0)
        lines = [f"Present: {present}",
                 f"{mode} {connection} | ACK {ack} | LED req {requested_bits} ack {confirmed_bits}",
                 f"FPS cap {number('capture_fps')} / draw {number('display_fps')} | infer {number('inference_hz')} Hz",
                 f"ms p50 {number('inference_ms_p50')} p95 {number('inference_ms_p95')} n={samples} | toACK {ack_ms} | VRAM {number('vram_mb')} MB"]
        cv2.rectangle(output, (0, 0), (output.shape[1] - 1, min(69, output.shape[0] - 1)), (24, 24, 24), -1)
        for index, line in enumerate(lines):
            width = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, .45, 1)[0][0]
            scale = .45 * min(1, max(1, output.shape[1] - 12) / max(1, width))
            cv2.putText(output, line, (6, 15 + index * 16), cv2.FONT_HERSHEY_SIMPLEX, scale, (235, 235, 235), 1, cv2.LINE_AA)
    return output
