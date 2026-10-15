import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from toollighthouse.camera import OpenCVCameraService, enumerate_opencv_cameras


def test_enumerate_opencv_cameras():
    # Calling enumerate without real hardware should not crash and should return a list
    devices = enumerate_opencv_cameras(active_index=0, max_probe=2)
    assert isinstance(devices, list)
    assert len(devices) >= 1
    # Device 0 is marked active
    assert devices[0]["is_active"] is True


def test_opencv_camera_service_lifecycle_with_mock():
    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, mock_frame)
    mock_cap.get.side_effect = lambda prop: 640.0 if prop == 3 else (480.0 if prop == 4 else 30.0)

    service = OpenCVCameraService(index=0, width=640, height=480, fps=30.0)

    with patch.object(service, "_open_capture", return_value=mock_cap):
        service.start()
        assert service.is_running is True

        import time
        time.sleep(0.05)

        frame_data = service.get_latest_frame()
        assert frame_data is not None
        frame, wall_ts, mono_ts = frame_data
        assert frame.shape == (480, 640, 3)
        assert wall_ts > 0
        assert mono_ts > 0

        preview_jpeg = service.get_latest_preview_jpeg()
        assert preview_jpeg is not None
        assert isinstance(preview_jpeg, bytes)

        service.stop()
        assert service.is_running is False
        mock_cap.release.assert_called()
