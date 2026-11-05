"""Speech-to-text providers. Every provider offers one thing, so they're swappable (PIP-NEW-008):

    await stt.transcribe(pcm_int16_16k: bytes) -> str

Groq Whisper Large V3 Turbo with a word list is the default (D-48). When Groq can't be reached,
Pip falls back to Whisper base on the Pi (LocalWhisperSTT): about 2 s per request and fine for
clear, close commands like "timer ten minutes" or "snooze" (M0: 12% of words wrong close up), but
not for room speech (D-50). The owner agreed offline only needs to understand everyday phrasings (D-83).
"""
import asyncio
import os
import io
import tomllib
import wave

import httpx

from ..common import paths


def wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


def secret(name: str) -> str:
    f = paths.data_dir() / "secrets.toml"
    return tomllib.loads(f.read_text()).get(name, "") if f.exists() else ""


class GroqSTT:
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, model: str, word_list: str, timeout: float = 6.0):
        self.model, self.word_list = model, word_list
        key = secret("groq_api_key")
        if not key:
            raise RuntimeError("No groq_api_key in ~/pip-data/secrets.toml")
        self.http = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {key}",
                                                                 "User-Agent": "pip-assistant"})

    async def transcribe(self, pcm: bytes) -> str:
        data = {"model": self.model, "language": "en", "temperature": "0", "response_format": "json"}
        if self.word_list:
            data["prompt"] = self.word_list
        r = await self.http.post(self.URL, data=data, files={"file": ("speech.wav", wav_bytes(pcm), "audio/wav")})
        r.raise_for_status()
        return r.json().get("text", "").strip()


class LocalWhisperSTT:
    """Whisper on the Pi. Loaded on first use (about 150 MB), then kept ready."""

    def __init__(self, model: str = "base.en", word_list: str = "", threads: int = 2):
        self.model_name, self.word_list, self.threads = model, word_list, threads
        self.model = None
        self.lock = asyncio.Lock()

    def _load(self):
        from faster_whisper import WhisperModel
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # use the copy already on the Pi; never download while offline
        return WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=self.threads)

    def _run(self, pcm: bytes) -> str:
        import numpy as np
        if self.model is None:
            self.model = self._load()
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, initial_prompt=self.word_list or None,
                                        vad_filter=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()

    async def transcribe(self, pcm: bytes) -> str:
        async with self.lock:  # one at a time: it's CPU-heavy
            return await asyncio.get_running_loop().run_in_executor(None, self._run, pcm)


def make_local_stt(cfg: dict):
    s = cfg["stt"]
    return LocalWhisperSTT(s["offline_model"], s["word_list"]) if s["offline_model"] else None


PROVIDERS = {"groq": GroqSTT}


def make_stt(cfg: dict):
    s = cfg["stt"]
    if s["provider"] not in PROVIDERS:
        raise ValueError(f"Unknown speech-to-text provider '{s['provider']}'. Available: {', '.join(PROVIDERS)}")
    return PROVIDERS[s["provider"]](s["model"], s["word_list"], s["timeout_seconds"])
