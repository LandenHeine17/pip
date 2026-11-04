"""Speech-to-text providers. Every provider offers one thing, so they're swappable (PIP-NEW-008):

    await stt.transcribe(pcm_int16_16k: bytes) -> str

Groq Whisper Large V3 Turbo with a word list is the default (D-48).
"""
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


PROVIDERS = {"groq": GroqSTT}


def make_stt(cfg: dict):
    s = cfg["stt"]
    if s["provider"] not in PROVIDERS:
        raise ValueError(f"Unknown speech-to-text provider '{s['provider']}'. Available: {', '.join(PROVIDERS)}")
    return PROVIDERS[s["provider"]](s["model"], s["word_list"], s["timeout_seconds"])
