import time

from toollighthouse.camera import LatestFrameWorker
from toollighthouse.serial_transport import SimulatedSerial, parse_ack, validate_mask


def test_mask_protocol_accepts_complete_nine_bit_range():
    assert validate_mask(0) == 0
    assert validate_mask(511) == 511
    try:
        validate_mask(512)
    except ValueError:
        pass
    else:
        raise AssertionError("out of range mask accepted")


def test_simulated_serial_ack_is_exact():
    serial = SimulatedSerial()
    assert serial.send_mask(7) is True
    assert serial.confirmed_mask == 7
    assert parse_ack("ACK:7") == 7
    assert parse_ack("ACK:8") == 8


def test_simulated_serial_watchdog_fails_safe_without_inference():
    serial = SimulatedSerial(watchdog_seconds=0.01)
    serial.send_mask(7)
    time.sleep(0.02)
    assert serial.status()["confirmed_mask"] == 0


def test_latest_frame_worker_does_not_queue_old_frames():
    seen = []
    worker = LatestFrameWorker(lambda frame, timestamp: seen.append(frame))
    worker.submit("old")
    worker.submit("new")
    worker.start()
    time.sleep(0.05)
    worker.stop()
    assert seen == ["new"]
