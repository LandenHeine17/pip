"""Hearing (core/hearing.py, common/voicecheck.py, core/stt.py): noise without a voice is dropped (D-85), speech-to-text is told the
household's names and fixed after (D-93), and Groq's daily limit moves to the second model (D-115)."""
import asyncio
from datetime import datetime

import httpx
import numpy as np
import pytest
from helpers import FakeSTT, clock_at, finish, room, utt
from helpers import OneVoice as FakeEmbedder
from helpers import alarms_bus as setup_bus
from helpers import room_setup as setup

from pip_assistant.core.people import Match, People
from pip_assistant.core.peoplecmds import PeopleCommands, StartLearning
from pip_assistant.core.stt import GroqSTT, RateLimited

NOW = datetime(2026, 11, 4, 19, 30)  # a Wednesday evening
Clock = clock_at(NOW)



def test_voice_check_ignores_noise():
    from pip_assistant.common.voicecheck import voice_seconds_sync
    rng = np.random.default_rng(0)
    hiss = (rng.standard_normal(16000) * 32768 * 10 ** (-45 / 20)).astype(np.int16)
    click = np.zeros(16000, np.int16)
    click[8000:8040] = 20000
    assert voice_seconds_sync(hiss.tobytes()) == 0 and voice_seconds_sync(click.tobytes()) == 0

# ---------------------------------------------------------------- names heard right (D-93)


async def test_landon_is_landen(tmp_path):
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen = people.add_person("Landen", "admin")
    assert people.by_name("Landon")["id"] == landen["id"]
    assert people.fix_names("Landon said London is lovely") == "Landen said London is lovely"
    r = await PeopleCommands(people).handle("Pip, I'm Landon.", Match(), b"x" * 64000)
    assert isinstance(r, StartLearning) and r.person_id == landen["id"] and not people.guests


async def test_speech_to_text_is_told_the_names_and_fixed_after(tmp_path):
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    people.add_person("Landen", "admin")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Landon, your turn for the dishes",
                                                    social={"join_in": False}, people=people)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.2)
    assert conv.stt.names == ["Landen"]
    assert conv.transcript.last()["text"] == "Landen, your turn for the dishes"
    await finish(bus, tasks)

# ---------------------------------------------------------------- Groq's daily limit (D-115)


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
