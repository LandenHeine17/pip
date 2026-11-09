"""Speech-to-text providers. Every provider offers one thing, so they're swappable (PIP-NEW-008):

    await stt.transcribe(pcm_int16_16k: bytes) -> str

Groq Whisper Large V3 Turbo with a word list is the default (D-48). Groq's free plan allows 2,000
requests a day per model, and Pip hears the whole room (D-92), so when one model's limit is used up he
switches to a spare model with its own limit (D-115); when all are used up, `RateLimited` is raised (that
isn't being offline: the internet and Claude still work). When Groq can't be reached,
Pip falls back to Whisper base on the Pi (LocalWhisperSTT): about 2 s per request and fine for
clear, close commands like "timer ten minutes" or "snooze" (M0: 12% of words wrong close up), but
not for room speech (D-50). The owner agreed offline only needs to understand everyday phrasings (D-83); online,
Claude understands any wording (D-94).
"""
import asyncio
import os
import io
import wave

import time

import httpx

from ..common import secrets


class RateLimited(Exception):
    """Every Groq model's request limit is used up for now (Groq said 429). Not offline."""


def wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


def with_names(word_list: str, names: list[str]) -> str:
    """The word list plus the household's names, so Whisper spells them the household's way (D-93)."""
    extra = [n for n in names if n and n.lower() not in word_list.lower()]
    return (word_list.rstrip(". ") + ". " + ", ".join(extra) + ".") if extra else word_list


def secret(name: str) -> str:
    """One of Pip's own keys from ~/pip-data/secrets.toml (common/secrets.py), or "" if it isn't there."""
    return str(secrets.load().get(name, ""))


class GroqSTT:
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, model: str, word_list: str, timeout: float = 6.0, spare_models: list[str] | None = None):
        self.model, self.word_list = model, word_list
        self.models = [model, *(m for m in (spare_models or []) if m != model)]
        self.limited_until: dict[str, float] = {}   # model → when Groq said to try it again (time.monotonic)
        self.names: list[str] = []   # household names, set by pip-core before each request (D-93)
        key = secret("groq_api_key")
        if not key:
            raise RuntimeError("No groq_api_key in ~/pip-data/secrets.toml")
        self.http = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {key}",
                                                                 "User-Agent": "pip-assistant"})

    async def transcribe(self, pcm: bytes) -> str:
        """The text, from the first model whose limit isn't used up. Raises RateLimited if none is free."""
        for model in self.models:
            if time.monotonic() < self.limited_until.get(model, 0):
                continue
            data = {"model": model, "language": "en", "temperature": "0", "response_format": "json"}
            if prompt := with_names(self.word_list, self.names):
                data["prompt"] = prompt
            r = await self.http.post(self.URL, data=data, files={"file": ("speech.wav", wav_bytes(pcm), "audio/wav")})
            if r.status_code == 429:   # this model's limit is used up: rest it as long as Groq says, try the next
                try:
                    wait = float(r.headers.get("retry-after", 60))
                except ValueError:
                    wait = 60.0
                self.limited_until[model] = time.monotonic() + max(wait, 30.0)
                continue
            r.raise_for_status()
            return r.json().get("text", "").strip()
        raise RateLimited("every speech-to-text model's limit is used up for now")

    @property
    def limited(self) -> bool:
        """True while every model is resting after hitting its limit."""
        now = time.monotonic()
        return all(now < self.limited_until.get(m, 0) for m in self.models)


class LocalWhisperSTT:
    """Whisper on the Pi. Loaded on first use (about 150 MB), then kept ready."""

    def __init__(self, model: str = "base.en", word_list: str = "", threads: int = 2):
        self.model_name, self.word_list, self.threads = model, word_list, threads
        self.names: list[str] = []
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
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, initial_prompt=with_names(self.word_list, self.names) or None,
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
    return PROVIDERS[s["provider"]](s["model"], s["word_list"], s["timeout_seconds"], s.get("spare_models", []))
