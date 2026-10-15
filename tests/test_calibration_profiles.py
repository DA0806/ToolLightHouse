import numpy as np
import pytest
from toollighthouse.config import CalibrationProfile, Settings, apply_canonical_transform


def test_calibration_profile_serialization():
    profile = CalibrationProfile(
        source_id="remote_phone",
        resolution={"width": 1920, "height": 1080},
        orientation="normal",
        background_filename="bg_phone.jpg",
        roi=(100, 100, 800, 600),
        status="ready",
    )
    d = profile.to_dict()
    assert d["source_id"] == "remote_phone"
    assert d["resolution"] == {"width": 1920, "height": 1080}
    assert d["roi"] == [100, 100, 800, 600]
    assert d["status"] == "ready"

    restored = CalibrationProfile.from_dict(d)
    assert restored.source_id == profile.source_id
    assert restored.resolution == profile.resolution
    assert restored.roi == profile.roi
    assert restored.status == profile.status


def test_legacy_calibration_unbound_migration():
    # Legacy config has background_path and roi, but no calibration_profiles
    raw = {
        "mode": "mock",
        "background_path": "data/calibration/background.png",
        "roi": [50, 50, 500, 400],
    }
    settings = Settings.from_dict(raw)
    assert "unbound_legacy" in settings.calibration_profiles
    legacy_profile = settings.calibration_profiles["unbound_legacy"]
    assert legacy_profile.status == "needs_confirmation"
    assert legacy_profile.roi == (50, 50, 500, 400)
    assert legacy_profile.background_filename == "background.png"


def test_apply_canonical_transform():
    # Test image 400x600 (H=400, W=600)
    img = np.zeros((400, 600, 3), dtype=np.uint8)

    # Rotation 90 degrees clockwise -> H=600, W=400
    r90 = apply_canonical_transform(img, orientation="rotate_90")
    assert r90.shape == (600, 400, 3)

    # Rotation 180 degrees -> H=400, W=600
    r180 = apply_canonical_transform(img, orientation="rotate_180")
    assert r180.shape == (400, 600, 3)

    # Crop (x, y, w, h)
    cropped = apply_canonical_transform(img, orientation="normal", crop=(50, 50, 200, 150))
    assert cropped.shape == (150, 200, 3)
