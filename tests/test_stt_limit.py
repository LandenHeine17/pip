"""Groq's daily limit (D-115): a spare model takes over, and when every limit is used up Pip isn't
"offline": what's said to him is heard on the Pi and Claude still answers."""
import asyncio

import httpx
import pytest

from pip_assistant.core.stt import GroqSTT, RateLimited
from test_step4 import Clock, setup_bus, utt
from test_step6 import FakeSTT, finish


def groq(monkeypatch, limited: set[str]):
    """A GroqSTT talking to a pretend Groq, where the models in `limited` have used up their limit."""
    monkeypatch.setattr("pip_assistant.core.stt.secret", lambda name: "test-key")
    asked = []

    def answer(request):
        model = next(p for p in request.content.split(b"\r\n\r\n")[1:] if p)  # first form field is the model
        model = model.split(b"\r\n")[0].decode()
        asked.append(model)
        if model in limited:
            return httpx.Response(429, headers={"retry-after": "44"}, json={"error": {"code": "rate_limit_exceeded"}})
        return httpx.Response(200, json={"text": f"heard by {model}"})

    stt = GroqSTT("whisper-large-v3-turbo", "", spare_models=["whisper-large-v3"])
    stt.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return stt, asked


async def test_the_spare_model_takes_over_when_the_first_is_used_up(monkeypatch):
    stt, asked = groq(monkeypatch, {"whisper-large-v3-turbo"})
    assert await stt.transcribe(b"\0\0" * 1600) == "heard by whisper-large-v3"
    assert await stt.transcribe(b"\0\0" * 1600) == "heard by whisper-large-v3"
    assert asked == ["whisper-large-v3-turbo", "whisper-large-v3", "whisper-large-v3"]   # the used-up one rests
    assert not stt.limited


async def test_every_limit_used_up_is_rate_limited_not_offline(monkeypatch):
    stt, asked = groq(monkeypatch, {"whisper-large-v3-turbo", "whisper-large-v3"})
    with pytest.raises(RateLimited):
        await stt.transcribe(b"\0\0" * 1600)
    assert stt.limited


class LimitedSTT(FakeSTT):
    async def transcribe(self, pcm):
        raise RateLimited()


async def test_when_groq_is_used_up_pip_still_answers_with_claude(tmp_path):
    local = FakeSTT("Pip, what's the capital of Australia?")
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, Clock(), LimitedSTT(), local_stt=local)
    await ears.emit("heard.utterance", *utt("wake"))
    await asyncio.sleep(0.4)
    assert local.calls == 1                                    # heard on the Pi…
    assert "thinker.ask" in [k for k, _, _ in got]             # …and Claude answers: not offline
    assert conv.state != "offline" and conv.stt_limited
    await finish(bus, tasks)
