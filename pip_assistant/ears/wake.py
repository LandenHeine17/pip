"""Wake-word detection on the Pi ("Pippletron", "Hey Pip"), using openWakeWord ONNX models (D-74).

Models are looked up in ~/pip-data/models/wake/ first (household-tuned, private) and then in the
repo's models/wake/ (trained on synthetic voices only, public-safe), so a fresh clone works.
"""
import time
from pathlib import Path

import numpy as np

from ..common import paths

REPO_MODELS = paths.REPO_WAKE_MODELS   # also holds openWakeWord's own feature models


def find_model(name: str) -> Path:
    for folder in paths.wake_model_folders():
        f = folder / f"{name}.onnx"
        if f.exists():
            return f
    raise FileNotFoundError(f"No wake-word model '{name}.onnx' in ~/pip-data/models/wake or models/wake")


class WakeDetector:
    def __init__(self, words: list[str], thresholds: dict | float = 0.5, refractory_s: float = 2.0):
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[str(find_model(w)) for w in words], inference_framework="onnx",
                           melspec_model_path=str(REPO_MODELS / "melspectrogram.onnx"),
                           embedding_model_path=str(REPO_MODELS / "embedding_model.onnx"))
        self.thresholds = {w: (thresholds.get(w, 0.5) if isinstance(thresholds, dict) else thresholds) for w in words}
        self.refractory_s = refractory_s
        self.last = 0.0

    def feed(self, frame: np.ndarray) -> tuple[str, float] | None:
        """Feed one 80 ms frame. Returns (word, score) when a wake word is heard."""
        scores = self.model.predict(frame)
        over = [(w, s) for w, s in scores.items() if s >= self.thresholds.get(w, 0.5)]
        if not over:
            return None
        word, score = max(over, key=lambda kv: kv[1])
        if time.monotonic() - self.last > self.refractory_s:
            self.last = time.monotonic()
            self.model.reset()
            return word, round(float(score), 3)
        return None
