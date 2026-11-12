"""What the tests share: fake programs (pip-ears and pip-thinker on a real message hub), fake
speech-to-text and voices, a clock you can move, and loading the real modules the way pip-core does.

Each harness below is one way the tests wire up Pip; a test file imports the one it uses, often under a
short name (`from helpers import room_setup as setup`):

    room_setup     the conversation with Listening, claps, people and modules (most conversation tests)
    answering_setup  the plainest path: a question, Claude's sentences, Pip says them
    alarms_bus     the conversation with timers and alarms ringing, and the calendar module
    state_bus      like alarms_bus, also hearing the thinking tone (Pip's state, D-86)
    people_bus     the conversation with people, voices and memory

(pytest puts tests/ on the import path, so module tests in pip_assistant/modules/ use these too.)"""
import asyncio
import base64
from datetime import datetime
from pathlib import Path

import numpy as np

from pip_assistant.common.client import BusClient
from pip_assistant.common.modules import discover
from pip_assistant.core.bus import Bus
from pip_assistant.core.commands import Commands
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.health import Health
from pip_assistant.core.memory import Memory
from pip_assistant.core.mic import MicSwitch
from pip_assistant.core.modules import Registry
from pip_assistant.core.people import People
from pip_assistant.core.schedule import Schedule
from pip_assistant.core.trace import Trace

REPO = Path(__file__).resolve().parents[1]   # the pip folder
SECOND = 2 * 16000  # bytes of 16 kHz int16 audio
LOUD = (np.ones(1280) * 3000).astype(np.int16)
QUIET = np.zeros(1280, np.int16)


# ---------------------------------------------------------------- time, speech-to-text, voices
class Clock:
    """A clock the test moves by hand (clock.t += 60)."""
    def __init__(self, dt: datetime):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


def clock_at(now: datetime):
    """A Clock whose Clock() starts at `now`: each test file keeps its own moment (`Clock = clock_at(NOW)`)."""
    class At(Clock):
        def __init__(self, dt: datetime = now):
            super().__init__(dt)
    return At


class FakeSTT:
    """Speech-to-text that always hears `text` (or fails, like no internet)."""
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.calls, self.names = text, fail, 0, []

    async def transcribe(self, pcm):
        self.calls += 1
        if self.fail:
            raise ConnectionError("no internet")
        return self.text



def utt(reason="wake", pcm=b"\1\0" * 800):
    return {"reason": reason, "had_speech": True, "seconds": 1.5}, {"audio_b64": base64.b64encode(pcm).decode()}


def voice_utt(reason, pcm):
    return {"reason": reason, "had_speech": True, "seconds": len(pcm) / SECOND}, {"audio_b64": base64.b64encode(pcm).decode()}


def voice(tag: str, seconds: float = 1.5) -> bytes:
    """Fake audio: the tag stands for whose voice it is."""
    return tag.encode().ljust(8, b"_") + bytes(int(SECOND * seconds) - 8)


_RNG = np.random.default_rng(7)
_BASE = {t: _RNG.standard_normal(16) for t in ("alex", "sam", "tv", "guest")}


class TaggedVoices:
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


class OneVoice:
    """Everyone sounds the same."""
    available = True

    async def embed(self, pcm):
        v = np.ones(4, np.float32)
        return v / np.linalg.norm(v)

# ---------------------------------------------------------------- modules


def registry(tmp_path, schedule=None, clock=None, people=None, names=("google", "calendar", "todos"), transport=None,
             settings=None):
    """Plug the real modules into a test, the way pip-core does (core/modules.py)."""
    infos = [m for m in discover({"modules": settings or {}}) if m.name in names]
    kw = {"schedule": schedule, "people": people, "db_path": tmp_path / "pip.db", "transport": transport}
    if clock is not None:
        kw["clock"] = clock
    return Registry.load({}, infos=infos, **kw)


async def say(c, text):
    """Say something to Pip's own commands (c from commands_at)."""
    return await c.handle(text)


def commands_at(tmp_path, clock):
    """Pip's own commands with the calendar module, as the conversation has them: (commands, schedule, clock)."""
    s = Schedule(tmp_path / "pip.db", clock=clock)
    reg = registry(tmp_path, s, clock)                  # the calendar is a module now (D-96)
    c = Commands(s, clock=clock, items=reg.offline_items())
    c.reg = reg
    c.cal = reg.module("calendar").store   # the calendar's events, for checking
    return c, s, clock

# ---------------------------------------------------------------- the fake programs


async def finish(bus, tasks):
    for t in tasks:
        t.cancel()
    await bus.stop()


async def room_setup(tmp_path, stt_text="", replies=None, social=None, people=None, schedule=None, modules=None, prefs=None,
                memory=None, conversation=None):
    """replies: what the fake Claude says for each kind of request (direct / mentioned / join); a list gives
    one reply per request in turn, and "ACTION …" replies are actions."""
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    mic = MicSwitch(tmp_path / "pip.db")
    cfg = {"conversation": {"follow_up_seconds": 0.3, **(conversation or {})},
           "social": {"pause_seconds": 0.1, "welcome_back_minutes": 0, "quiet_hours": "", **(social or {})}}
    conv = Conversation(bus, cfg, FakeSTT(stt_text), people=people, mic=mic, schedule=schedule, modules=modules,
                        prefs=prefs, memory=memory)
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got, asks = [], []
    replies = {"direct": "Sure.", "mentioned": "", "follow_up": "", "join": "", **(replies or {})}

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.mic", "ears.tone", "ears.stop", "ears.clap_mic"):
        ears.on(t, record)

    async def mics(msg):
        return {"mics": [{"name": "alsa_input.usb-Speaker_Phone", "label": "USB Speaker Phone"},
                         {"name": "alsa_input.usb-cheap-mic", "label": "Cheap USB mic"}],
                "speakerphone": "alsa_input.usb-Speaker_Phone"}
    ears.on("ears.mics", mics)

    async def ask(msg):
        kind = msg.data.get("kind")
        asks.append((kind, msg.private))
        text = replies[kind]
        if isinstance(text, list):
            text = text.pop(0)
        if text.startswith("ACTION"):
            return msg.reply({"action": True}, private={"text": "", "action": text}, source="thinker")
        if text:
            await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": text})
        return msg.reply({"passed": not text}, private={"text": text}, source="thinker")

    thinker.on("thinker.ask", ask)
    thinker.on("thinker.cancel", record)
    tasks = [asyncio.create_task(c.run()) for c in (ears, thinker)]
    for c in (ears, thinker):
        await asyncio.wait_for(c.connected.wait(), 3)
    await asyncio.sleep(0.1)
    return bus, conv, ears, got, asks, tasks


def room(text_pcm=b"x" * 32000):
    # quiet_s 0: as if they'd only just stopped talking, so the turn waits (D-122) are the whole budget
    return {"reason": "room", "had_speech": True, "seconds": 1.0, "quiet_s": 0.0}, \
        {"audio_b64": base64.b64encode(text_pcm).decode()}


def says(got):
    return [t for k, t, _ in got if k == "ears.say"]


asked = []   # what answering_setup's fake Claude was asked


async def answering_setup(tmp_path, stt_text, sentences=("Canberra is the capital.", "Not Sydney!")):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    conv = Conversation(bus, {"conversation": {"follow_up_seconds": 8.0, "keep_minutes": 10.0, "max_turns": 10}},
                        FakeSTT(stt_text))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data.get("seconds")))

    for t in ("ears.chime", "ears.say", "ears.listen"):
        ears.on(t, record)

    async def ask(msg):
        asked.append(msg.private.get("text"))
        for s in sentences:
            await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": s})
        return msg.reply({"first_sentence_s": 0.5}, private={"text": " ".join(sentences)}, source="thinker")

    thinker.on("thinker.ask", ask)
    thinker.on("thinker.cancel", record)
    tasks = [asyncio.create_task(c.run()) for c in (ears, thinker)]
    for c in (ears, thinker):
        await asyncio.wait_for(c.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, conv, ears, got, tasks


def audio_b64():
    return base64.b64encode(LOUD.tobytes() * 10).decode()


async def alarms_bus(tmp_path, clock, stt=None, local_stt=None, answers=None):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {"alarms": {"announce_every_seconds": 0.2, "ring_minutes": 0.02}}, clock=clock)
    conv = Conversation(bus, {"conversation": {"follow_up_seconds": 8.0}}, stt, schedule=sched, local_stt=local_stt,
                        modules=registry(tmp_path, sched, clock))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.ring", "ears.ring_stop", "ears.stop"):
        ears.on(t, record)

    async def ask(msg):
        got.append(("thinker.ask", msg.private.get("text"), msg.private.get("schedule")))
        answer = (answers or {}).get(msg.private.get("text"), "Claude here.")
        if answer.startswith("ACTION"):
            return msg.reply({"action": True}, private={"text": "", "action": answer}, source="thinker")
        await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": answer})
        return msg.reply({}, private={"text": answer}, source="thinker")

    thinker.on("thinker.ask", ask)
    tasks = [asyncio.create_task(x.run()) for x in (ears, thinker)] + [asyncio.create_task(sched.run())]
    for x in (ears, thinker):
        await asyncio.wait_for(x.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, sched, conv, ears, got, tasks


async def state_bus(tmp_path, clock, stt, answer="Claude here.", cfg=None):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {"alarms": {"announce_every_seconds": 0.2, "ring_minutes": 0.02}}, clock=clock)
    conv = Conversation(bus, cfg or {"conversation": {"follow_up_seconds": 0.3}}, stt, schedule=sched,
                        modules=registry(tmp_path, sched, clock))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.ring", "ears.ring_stop", "ears.stop", "ears.tone"):
        ears.on(t, record)

    async def ask(msg):
        await asyncio.sleep(0.05)
        if answer.startswith("ACTION"):
            return msg.reply({"action": True}, private={"text": "", "action": answer}, source="thinker")
        await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": answer})
        return msg.reply({}, private={"text": answer}, source="thinker")

    thinker.on("thinker.ask", ask)
    tasks = [asyncio.create_task(x.run()) for x in (ears, thinker)] + [asyncio.create_task(sched.run())]
    for x in (ears, thinker):
        await asyncio.wait_for(x.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, sched, conv, ears, got, tasks


def states(bus, details=False):
    """The states published, each change once (within a state, what he's doing can change: D-112)."""
    seen = [(r["data"]["state"], r["data"].get("detail", "")) for r in reversed(bus.trace.recent()) if r["type"] == "pip.state"]
    if details:
        return seen
    return [s for i, (s, _) in enumerate(seen) if i == 0 or seen[i - 1][0] != s]


async def people_bus(tmp_path, clock, stt, answer="Claude here."):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {}, clock=clock)
    people = People(tmp_path / "pip.db", clock=clock, embedder=TaggedVoices())
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
