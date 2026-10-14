
import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

print("GPU:", torch.cuda.get_device_name(0))

model = SentenceTransformer(
    "models/embeddinggemma-2",
    device="cuda",
    config_kwargs={"audio_config": None},
)

image = Image.open("samples/objeto.jpg").convert("RGB")

embedding = model.encode({"image": image})

print("Forma:", embedding.shape)
print("Valores válidos:", np.isfinite(embedding).all())
print("Modelo cargado correctamente")
