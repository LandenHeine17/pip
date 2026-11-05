"""M1a step 5 tests: memory (with the approval inbox), people, voices, opting out, and privacy."""
import asyncio
import base64
import json
import os
from datetime import datetime

import numpy as np
import pytest

from pip_assistant.common.client import BusClient
from pip_assistant.core import actions
from pip_assistant.core.bus import Bus
from pip_assistant.core.commands import Commands
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.health import Health
from pip_assistant.core.memory import Memory, Refused
from pip_assistant.core.people import Match, People
from pip_assistant.core.peoplecmds import PeopleCommands
from pip_assistant.core.schedule import Schedule
from pip_assistant.core.trace import Trace

NOW = datetime(2026, 11, 5, 9, 0)
SECOND = 2 * 16000  # bytes of 16 kHz int16 audio


class Clock:
    def __init__(self, dt=NOW):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


def voice(tag: str, seconds: float = 1.5) -> bytes:
    """Fake audio: the tag stands for whose voice it is."""
    return tag.encode().ljust(8, b"_") + bytes(int(SECOND * seconds) - 8)


_RNG = np.random.default_rng(7)
_BASE = {t: _RNG.standard_normal(16) for t in ("alex", "sam", "tv", "guest")}


class FakeEmbedder:
    """Each voice tag maps to a fixed direction; 'alex2' is Alex on a different day (close, not identical)."""
    available = True
    rng = _RNG
    base = _BASE

    async def embed(self, pcm: bytes) -> np.ndarray:
        tag = pcm[:8].rstrip(b"_").decode()
        v = self.base[tag.rstrip("0123456789")].copy()
        if tag[-1:].isdigit():
            v = v + 0.35 * self.rng.standard_normal(16)
        return v / np.linalg.norm(v)


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "pip.db", clock=Clock())


@pytest.fixture
def people(tmp_path):
    return People(tmp_path / "pip.db", clock=Clock(), embedder=FakeEmbedder())


# ------------------------------------------------------------------ memory
def test_memory_saves_merges_duplicates_and_refuses_secrets(mem):
    row, outcome = mem.add("The bins go out Tuesday night", "household", source="Alex, by voice")
    assert outcome == "saved" and row["visibility"] == "household" and row["status"] == "active"   # D-16
    assert mem.add("the bins go out tuesday night.", "household")[1] == "duplicate"
    for secret in ("The wifi password is hunter2", "Card 4111 1111 1111 1111", "Mom's number is " + " ".join(["402", "555", "0199"])):  # number built at run time: the commit check blocks literal ones
        with pytest.raises(Refused):
            mem.add(secret)
    assert len(mem.items()) == 1


def test_sensitive_memories_wait_for_approval_and_nothing_is_silently_lost(mem):
    row, outcome = mem.add("Sam is allergic to peanuts", "personal", sensitive=True)
    assert outcome == "pending" and "peanuts" not in mem.for_claude()
    mem.approve(row["id"])
    assert "Sam is allergic to peanuts" in mem.for_claude()
    mem.update(row["id"], status="archived")
    assert "peanuts" not in mem.for_claude() and mem.items("archived")    # archived: kept, not used
    pend, _ = mem.add("Jordan's birthday is 3 May", "personal", pending_because="about someone else")
    mem.clock.t += 15 * 86400
    mem.prune()
    assert mem._row(pend["id"]) is None and mem._row(row["id"]) is not None  # only unapproved suggestions expire


def test_private_memories_only_for_their_owner_when_sure(mem):
    mem.add("Alex is planning a surprise party for Sam", "personal", ["alex"], owner="alex", private=True)
    assert "surprise" not in mem.for_claude()
    assert "surprise" not in mem.for_claude("alex", "probably")
    assert "surprise" in mem.for_claude("alex", "sure")
    assert "surprise" not in mem.for_claude("sam", "sure")


# ------------------------------------------------------------------ Claude's memory actions
@pytest.fixture
def ctx(tmp_path, mem, people):
    clock = Clock()
    alex = people.add_person("Alex", "admin")
    sam = people.add_person("Sam")
    cmds = Commands(Schedule(tmp_path / "pip.db", clock=clock), clock=clock)
    return cmds, {"memory": mem, "people": people, "speaker": Match(alex["id"], "Alex", "sure", 0.7)}, alex, sam


def test_remember_forget_and_correct(ctx, mem):
    cmds, c, alex, sam = ctx
    said, n = actions.apply(cmds, 'ACTION {"do": "remember", "text": "Alex likes his coffee black", '
                                  '"category": "preference", "about": ["Alex"]}', c)
    assert said == "Saved: Alex likes his coffee black." and n == 1
    [row] = mem.items()
    assert row["about"] == [alex["id"]] and row["source"] == "Alex, by voice" and row["status"] == "active"
    ref = mem.short(row["id"])
    assert ref in mem.for_claude()
    said, _ = actions.apply(cmds, f'ACTION {{"do": "update_memory", "id": "{ref}", "text": "Alex likes oat milk in his coffee"}}', c)
    assert said == "Updated: Alex likes oat milk in his coffee."
    said, _ = actions.apply(cmds, f'ACTION {{"do": "forget", "id": "{ref}"}}', c)
    assert said == "Forgot: Alex likes oat milk in his coffee." and mem.items() == []


@pytest.mark.parametrize("action,why", [
    ('{"do": "remember", "text": "Sam hates mushrooms", "category": "preference", "about": ["Sam"]}', "about someone else"),
    ('{"do": "remember", "text": "Alex has a doctor appointment Friday", "category": "personal", "about": ["Alex"], "sensitive": true}', "sensitive"),
])
def test_some_memories_go_to_the_inbox(ctx, mem, action, why):
    cmds, c, *_ = ctx
    said, _ = actions.apply(cmds, "ACTION " + action, c)
    assert said.startswith("I'll remember that once it's approved")
    [row] = mem.items()
    assert row["status"] == "pending" and row["why_pending"] == why


def test_private_from_an_unknown_voice_waits(ctx, mem):
    cmds, c, *_ = ctx
    c["speaker"] = Match()
    actions.apply(cmds, 'ACTION {"do": "remember", "text": "The spare key is under the plant", "category": "household", "private": true}', c)
    [row] = mem.items()
    assert row["status"] == "pending" and row["visibility"] == "household"


def test_bad_memory_actions_change_nothing(ctx, mem):
    cmds, c, *_ = ctx
    assert actions.apply(cmds, 'ACTION {"do": "forget", "id": "#m999999"}', c) == (actions.FAILED, 0)
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "The door code is 4821", "category": "household"}', c)
    assert said == "I can't keep passwords, card or phone numbers in my memory." and mem.items() == []


# ------------------------------------------------------------------ people and voices
async def test_voices_are_recognised_with_confidence_levels(people):
    alex = people.add_person("Alex", "admin")
    sam = people.add_person("sam")
    assert sam["name"] == "Sam"
    with pytest.raises(ValueError):
        people.add_person("alex")
    assert await people.add_samples(alex["id"], [voice("alex")] * 3, "guided") == 3
    await people.add_samples(sam["id"], [voice("sam")], "guided")
    m = await people.identify(voice("alex2"))
    assert m.person_id == alex["id"] and m.level in ("sure", "probably") and m.label.startswith("Alex")
    assert (await people.identify(voice("tv"))).level == "unknown"
    assert (await people.identify(voice("alex", 0.3))).level == "unknown"   # too short to tell
    people.remove_person(alex["id"])
    assert (await people.identify(voice("alex"))).person_id != alex["id"]


async def test_people_commands(people, mem):
    pc = PeopleCommands(people, mem)
    alex = people.add_person("Alex", "admin")
    await people.add_samples(alex["id"], [voice("alex")], "guided")
    me = await people.identify(voice("alex"))
    assert await pc.handle("Pip, who am I?", me, voice("alex")) == "You sound like Alex."
    assert (await pc.handle("Pip, leave me out", me, voice("alex"))).startswith("Okay Alex, I'll leave you out")
    assert people.get(alex["id"])["opted_out"] == 1 and people.has_opt_outs()
    assert (await pc.handle("Pip, include me again", me, voice("alex"))) == "Okay Alex, you're back in."
    # A visitor: remembered for this visit only
    assert await pc.handle("Hey Pip, I'm Jordan.", Match(), voice("guest")) == \
        "Nice to meet you, Jordan! I'll remember your voice for this visit."
    g = await people.identify(voice("guest"))
    assert g.guest and g.name == "Jordan"
    assert await pc.handle("Pip, leave me out", g, voice("guest")) == "Okay, I'll leave you out for this visit."
    assert (await people.identify(voice("guest"))).opted_out
    people.clock.t += 3 * 3600                               # 2 hours of quiet: forgotten
    assert not people.has_opt_outs() and (await people.identify(voice("guest"))).level == "unknown"
    # Introducing yourself as a household member adds a voice sample
    sam = people.add_person("Sam")
    assert await pc.handle("I'm Sam.", Match(), voice("sam")) == "Hi Sam! I'll get better at recognising you."
    assert people.all()[1]["voice_samples"] == 1
    assert await pc.handle("I'm tired.", Match(), voice("sam")) is None    # not an introduction
    assert await pc.handle("Pip, remember the plants need water on Sundays", me, voice("alex"), offline=True) == \
        "I'm offline, so I put that in the memory inbox to check later."
    assert mem.items()[0]["status"] == "pending"


# ------------------------------------------------------------------ the whole path, with fake programs
async def setup_bus(tmp_path, clock, stt, answer="Claude here."):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {}, clock=clock)
    people = People(tmp_path / "pip.db", clock=clock, embedder=FakeEmbedder())
    memory = Memory(tmp_path / "pip.db", clock=clock)
    conv = Conversation(bus, {"conversation": {"follow_up_seconds": 0.3}, "thinker": {"thinking_tone": "off"}}, stt,
                        schedule=sched, people=people, memory=memory)
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.stop", "ears.tone"):
        ears.on(t, record)

    async def ask(msg):
        got.append(("thinker.ask", msg.private.get("text"), msg.private))
        if answer.startswith("ACTION"):
            return msg.reply({"action": True}, private={"text": "", "action": answer}, source="thinker")
        await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": answer})
        return msg.reply({}, private={"text": answer}, source="thinker")

    thinker.on("thinker.ask", ask)
    tasks = [asyncio.create_task(x.run()) for x in (ears, thinker)]
    for x in (ears, thinker):
        await asyncio.wait_for(x.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, people, memory, conv, ears, got, tasks


async def finish(bus, tasks):
    for t in tasks:
        t.cancel()
    await bus.stop()


class FakeSTT:
    def __init__(self, text=""):
        self.text, self.calls = text, 0

    async def transcribe(self, pcm):
        self.calls += 1
        return self.text


def utt(reason, pcm):
    return {"reason": reason, "had_speech": True, "seconds": len(pcm) / SECOND}, {"audio_b64": base64.b64encode(pcm).decode()}


async def test_claude_hears_who_is_talking_and_what_pip_remembers(tmp_path):
    clock = Clock()
    raw = 'ACTION {"do": "remember", "text": "Alex likes his coffee black", "category": "preference", "about": ["Alex"]}'
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT("Pip, remember I like my coffee black"), raw)
    alex = people.add_person("Alex", "admin")
    await people.add_samples(alex["id"], [voice("alex")] * 2, "guided")
    await ears.emit("heard.utterance", *utt("wake", voice("alex2")))
    await asyncio.sleep(0.4)
    [ask] = [x for x in got if x[0] == "thinker.ask"]
    assert ask[2]["people"].startswith("Speaking: Alex (") and "Household: Alex" in ask[2]["people"]
    assert [t for k, t, _ in got if k == "ears.say"] == ["Saved: Alex likes his coffee black."]
    assert memory.items()[0]["status"] == "active"
    conv.stt.text = "Pip, how do I take my coffee?"
    await ears.emit("heard.utterance", *utt("wake", voice("alex")))
    await asyncio.sleep(0.4)
    assert "Alex likes his coffee black" in [x for x in got if x[0] == "thinker.ask"][-1][2]["memories"]
    trace = json.dumps(bus.trace.recent())
    assert "coffee" not in trace and "Alex" not in trace                 # never logged (D-34)
    await finish(bus, tasks)


async def test_opted_out_room_speech_never_leaves_the_pi(tmp_path):
    clock = Clock()
    stt = FakeSTT("did you see the game")
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt)
    sam = people.add_person("Sam")
    await people.add_samples(sam["id"], [voice("sam")], "guided")
    people.update_person(sam["id"], opted_out=True)
    await ears.emit("heard.utterance", *utt("room", voice("sam")))
    await asyncio.sleep(0.2)
    assert stt.calls == 0 and "room.left_out" in [r["type"] for r in bus.trace.recent()]
    await ears.emit("heard.utterance", *utt("room", voice("tv")))   # someone else: still transcribed (D-77)
    await asyncio.sleep(0.2)
    assert stt.calls == 1
    await finish(bus, tasks)


async def test_guided_voice_learning(tmp_path):
    clock = Clock()
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT("should not be used"))
    alex = people.add_person("Alex", "admin")
    status = await conv.start_enrolment(alex["id"])
    assert status["of"] == 5 and status["done"] == 0
    for i in range(5):
        await ears.emit("heard.utterance", *utt("follow_up", voice("alex", 2.5)))
        await asyncio.sleep(0.1)
    assert conv.stt.calls == 0                                   # read sentences aren't transcribed or answered
    assert people.all()[0]["voice_samples"] == 5 and conv.enrol_status() is None
    says = [t for k, t, _ in got if k == "ears.say"]
    assert says[0].startswith("Hi Alex!") and says[-1] == "Thanks, Alex. I'll know your voice now."
    assert len([1 for k, _, _ in got if k == "ears.listen"]) >= 5
    await finish(bus, tasks)


async def test_dashboard_people_and_memory_actions(tmp_path):
    from pip_assistant.common.messages import Message
    from pip_assistant.core.main import memory_action, memory_snapshot, people_action
    clock = Clock()
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(""))
    r = await people_action(people, memory, conv, Message("request", "core.people_action", data={"action": "add", "role": "admin"},
                                                          private={"name": "Alex"}))
    pid = r["id"]
    r = memory_action(memory, people, Message("request", "x", data={"action": "add", "category": "preference", "private": True},
                                              private={"text": "Alex likes jazz", "about": "Alex"}))
    assert r["outcome"] == "saved"
    snap = memory_snapshot(memory, people)
    [m] = snap["memories"]
    assert m["visibility"] == "private" and m["owner_name"] == "Alex"
    assert memory_action(memory, people, Message("request", "x", data={"action": "edit", "id": m["id"]},
                                                 private={"text": "my pin is 1234"}))["error"].startswith("Passwords")
    r = await people_action(people, memory, conv, Message("request", "x", data={"action": "remove", "id": pid}))
    assert r["memories_removed"] == 1 and people.all() == [] and memory.items() == []
    await finish(bus, tasks)


# ------------------------------------------------------------------ the real voice model (only on the Pi with the owner's recordings)
MODEL = os.path.expanduser("~/pip-data/models/speaker/nemo_en_titanet_small.onnx")


@pytest.mark.skipif(not os.path.exists(MODEL) or not os.path.exists(os.path.expanduser("~/pip-data/m0/recordings/manifest.json")),
                    reason="needs the voice model and the owner's M0 recordings")
async def test_real_model_recognises_the_owner(tmp_path):
    import wave
    from pip_assistant.core.people import m0_owner_clips
    people = People(tmp_path / "pip.db", {"voices": {}})
    me = people.add_person("Owner", "admin")
    assert await people.add_samples(me["id"], m0_owner_clips(), "m0 recordings") == 8
    hits = 0
    for k in ("A030", "A040", "B045", "D001", "D005"):
        with wave.open(os.path.expanduser(f"~/pip-data/m0/recordings/{k}.wav")) as w:
            hits += (await people.identify(w.readframes(w.getnframes()))).level == "sure"
    assert hits >= 4
    from pip_assistant.ears.voice import make_voice
    from scipy.signal import resample_poly
    v = make_voice("piper", "en_US-lessac-medium", 1.0)
    a = resample_poly(v.speak("Hey Pip, what's the weather like tomorrow?").astype(float), 16000, v.rate).astype(np.int16)
    assert (await people.identify(a.tobytes())).level == "unknown"     # a different voice


def test_household_facts_need_no_approval_but_unknown_voices_personal_facts_do(ctx, mem):
    cmds, c, *_ = ctx
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "The bins go out Tuesday night", '
                                  '"category": "household", "about": ["Alex", "Sam"]}', c)
    assert said.startswith("Saved:") and mem.items()[0]["about"] == []
    c["speaker"] = Match()
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "Someone likes jazz", "category": "preference"}', c)
    [jazz] = [m for m in mem.items() if "jazz" in m["text"]]
    assert said.startswith("I'll remember that once") and jazz["why_pending"] == "I didn't recognise the voice"
