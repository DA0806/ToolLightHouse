"""Keep software tests independent from developer calibration artifacts."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_project_data(request, tmp_path, monkeypatch):
    """Run tests from an empty directory so Settings defaults stay hermetic.

    The opt-in EmbeddingGemma smoke is the only test that intentionally needs
    project model weights; it receives an absolute model path while keeping
    the repository's calibration files out of every ordinary test.
    """

    repo_root = Path(__file__).resolve().parents[1]
    is_real_smoke = request.node.name == "test_embeddinggemma_local_image_text_smoke" and os.getenv("TOOLLIGHTHOUSE_RUN_REAL_SMOKE") == "1"
    if is_real_smoke:
        configured_model = Path(os.getenv("TOOLLIGHTHOUSE_MODEL_PATH", "models/embeddinggemma-2"))
        if not configured_model.is_absolute():
            configured_model = repo_root / configured_model
        monkeypatch.setenv("TOOLLIGHTHOUSE_MODEL_PATH", str(configured_model))
        monkeypatch.chdir(repo_root)
    else:
        monkeypatch.chdir(tmp_path)
