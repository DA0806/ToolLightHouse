from pathlib import Path

from toollighthouse.config import Settings


def test_default_calibration_cannot_resolve_to_project_userdata():
    project_root = Path(__file__).resolve().parents[1]
    project_background = (project_root / "data" / "calibration" / "background.png").resolve()
    configured_background = Path(Settings(mode="mock").background_path).resolve()
    assert configured_background != project_background
