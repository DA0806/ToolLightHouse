import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def test_embeddinggemma_local_image_text_smoke():
    if os.getenv("TOOLLIGHTHOUSE_RUN_REAL_SMOKE") != "1":
        pytest.skip("set TOOLLIGHTHOUSE_RUN_REAL_SMOKE=1 to run the opt-in model smoke")
    model_path = Path(os.getenv("TOOLLIGHTHOUSE_MODEL_PATH", "models/embeddinggemma-2"))
    if not model_path.exists():
        pytest.skip("local EmbeddingGemma weights are unavailable")
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable for the opt-in smoke")
    from toollighthouse.embeddings import EmbeddingEngine

    image = np.zeros((64, 64, 3), dtype=np.uint8)
    image[16:48, 16:48] = 255
    engine = EmbeddingEngine(str(model_path), "cuda")
    image_embedding = engine.encode_images([image])[0]
    text_embedding = engine.encode_text(["a white laboratory tool"])[0]
    assert image_embedding.shape == text_embedding.shape
    assert np.isfinite(image_embedding).all()
    assert np.isfinite(text_embedding).all()
