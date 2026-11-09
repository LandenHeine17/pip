"""Pip's voice. Every voice engine offers the same two things, so engines are swappable (PIP-NEW-097):

    voice.rate                     sample rate of the audio it makes
    voice.speak(text) -> np.int16  the spoken audio

Piper "lessac" is the default (D-73). A future engine (e.g. Kokoro) is a new class with the same shape.
"""
import numpy as np

from ..common import paths


class PiperVoice:
    def __init__(self, name: str = "en_US-lessac-medium", speed: float = 1.0, repeatable: bool = False):
        from piper import PiperVoice as _Piper
        from piper.config import SynthesisConfig
        model = paths.data_dir() / "models" / "voices" / f"{name}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"Voice '{name}' not found: run deploy/install.sh to download it into ~/pip-data/models/voices")
        self._voice = _Piper.load(str(model))
        extra = {"noise_scale": 0.0, "noise_w_scale": 0.0} if repeatable else {}  # identical audio every time (tests)
        self._cfg = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed)), **extra)
        self.rate = self._voice.config.sample_rate

    def speak(self, text: str) -> np.ndarray:
        chunks = list(self._voice.synthesize(text, syn_config=self._cfg))
        if not chunks:
            return np.zeros(0, np.int16)
        return np.concatenate([np.frombuffer(c.audio_int16_bytes, np.int16) for c in chunks])


ENGINES = {"piper": PiperVoice}


def make_voice(engine: str, name: str, speed: float, repeatable: bool = False):
    if engine not in ENGINES:
        raise ValueError(f"Unknown voice engine '{engine}'. Available: {', '.join(ENGINES)}")
    return ENGINES[engine](name, speed, repeatable)
