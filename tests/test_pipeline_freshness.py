import time

import numpy as np
import pytest

from toollighthouse.classifier import SimilarityClassifier
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline, StaleFrameError
from toollighthouse.serial_transport import SimulatedSerial


def _image(value=220):
    image = np.zeros((180, 240, 3), dtype=np.uint8)
    image[20:80, 30:90] = value
    return image


class CountingEmbedder:
    def __init__(self, vector=(1.0, 0.0), delay=0.0):
        self.calls = []
        self.vector = np.asarray(vector, dtype=np.float32)
        self.delay = delay

    def encode_images(self, images):
        self.calls.append(len(images))
        if self.delay:
            time.sleep(self.delay)
        return np.repeat(self.vector[None, :], len(images), axis=0)


class RecordingSerial(SimulatedSerial):
    def __init__(self):
        super().__init__()
        self.sent = []

    def send_mask(self, mask, retries=2, expected_generation=None):
        self.sent.append(mask)
        return super().send_mask(mask, retries, expected_generation)


def _pipeline(embedder, *, frame_max_age=1.0, prediction_max_age=10.0, prediction_interval=0.0, batch_size=9, confirmations=2):
    settings = Settings(mode="mock", background_path=None, roi_path=None, frame_max_age_seconds=frame_max_age, prediction_max_age_seconds=prediction_max_age, prediction_interval_seconds=prediction_interval, batch_size=batch_size)
    pipeline = Pipeline(settings, serial=RecordingSerial(), embedder=embedder)
    pipeline.classifier = SimilarityClassifier({"tool_1": np.array([1.0, 0.0], dtype=np.float32)}, threshold=0.1, margin=0.0)
    pipeline.state.confirmations = confirmations
    return pipeline


def test_nonphoto_old_frame_is_rejected_before_embedder_and_led_commit():
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder)
    with pytest.raises(StaleFrameError, match="before inference"):
        pipeline.process(_image(), timestamp=time.time() - 2, photo=False)
    assert embedder.calls == []
    assert pipeline.serial.sent == []


def test_slow_encode_becomes_stale_and_cannot_send_leds_after_inference():
    embedder = CountingEmbedder(delay=0.03)
    pipeline = _pipeline(embedder, frame_max_age=0.01)
    with pytest.raises(StaleFrameError, match="after inference"):
        pipeline.process(_image(), timestamp=time.time(), photo=False)
    assert embedder.calls == [1]
    assert pipeline.serial.sent == []


def test_photo_timestamp_is_not_subject_to_video_age_rejection():
    pipeline = _pipeline(CountingEmbedder(), frame_max_age=0.01)
    result = pipeline.process(_image(), timestamp=time.time() - 60, photo=True)
    assert result["image"]["width"] == 240


def test_identical_stable_crop_is_encoded_once_but_changed_crop_is_reencoded():
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder, confirmations=1)
    pipeline.process(_image(220), timestamp=time.time(), photo=False)
    pipeline.process(_image(220), timestamp=time.time(), photo=False)
    assert embedder.calls == [1]
    pipeline.process(_image(150), timestamp=time.time(), photo=False)
    assert embedder.calls == [1, 1]


def test_prediction_cache_expires_and_reencodes_crop():
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder, prediction_max_age=0.01)
    pipeline.process(_image(), timestamp=time.time(), photo=False)
    time.sleep(0.02)
    pipeline.process(_image(), timestamp=time.time(), photo=False)
    assert embedder.calls == [1, 1]


def test_cached_prediction_does_not_count_as_new_temporal_confirmation():
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder, confirmations=2)
    first = pipeline.process(_image(), timestamp=time.time(), photo=False)
    second = pipeline.process(_image(), timestamp=time.time(), photo=False)
    assert first["detections"][0]["class_id"] is None
    assert second["detections"][0]["class_id"] is None
    assert embedder.calls == [1]


def test_embedding_batches_obey_configured_batch_size():
    image = np.zeros((360, 520, 3), dtype=np.uint8)
    for index in range(5):
        x = 10 + (index % 5) * 100
        image[30:90, x:x + 60] = 220
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder, batch_size=2)
    pipeline.process(image, timestamp=time.time(), photo=True)
    assert embedder.calls == [2, 2, 1]


def test_reset_scene_invalidates_prediction_cache():
    embedder = CountingEmbedder()
    pipeline = _pipeline(embedder)
    pipeline.process(_image(), timestamp=time.time(), photo=False)
    pipeline.reset_scene()
    pipeline.process(_image(), timestamp=time.time(), photo=False)
    assert embedder.calls == [1, 1]
