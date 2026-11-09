"""Listening like Ziggletron (D-92), the mic switches with claps (D-91), and names heard right (D-93)."""
import asyncio
import base64
import json

import numpy as np
import pytest

from pip_assistant.common.client import BusClient
from pip_assistant.core.bus import Bus
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.names import calls_pip, names_pip
from pip_assistant.core.health import Health
from pip_assistant.core.mic import MicSwitch
from pip_assistant.core.people import Match, People, sounds_like
from pip_assistant.core.peoplecmds import PeopleCommands, StartLearning
from pip_assistant.core.trace import Trace
from pip_assistant.ears.clap import ClapDetector
from pip_assistant.thinker.claude import ClaudeCLI, classify
from pip_assistant.thinker.main import Thinker

R = 16000
rng = np.random.default_rng(1)


# ------------------------------------------------------------------ claps
def quiet(s, lvl=30):
    return rng.normal(0, lvl, int(R * s))


def clap(amp=12000, tau=0.025, secs=0.25):
    n = int(R * secs)
    t = np.arange(n) / R
    return rng.normal(0, 1, n) * amp * np.exp(-t / tau)


def talking(s, amp=4000):
    n = int(R * s)
    t = np.arange(n) / R
    return rng.normal(0, 1, n) * amp * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))


def claps_heard(*parts, room=30) -> int:
    sig = np.concatenate(parts)
    sig = sig + rng.normal(0, room, len(sig))          # the room's own sound carries on under everything
    d, sig, hits = ClapDetector(), np.clip(sig, -32767, 32767).astype(np.int16), 0
    for i in range(0, len(sig) - 1280, 1280):
        hits += d.feed(sig[i:i + 1280])
    return hits


def test_two_claps_are_heard():
    assert claps_heard(quiet(1), clap(), quiet(0.15), clap(), quiet(1.2)) == 1
    assert claps_heard(quiet(1), clap(), quiet(0.2), clap(), quiet(1.2), room=300) == 1      # noisier room
    assert claps_heard(quiet(1), clap(), quiet(1.0), clap(), quiet(1.2)) == 1                    # a slower pair


@pytest.mark.parametrize("name,parts", [
    ("one clap", lambda: (quiet(1), clap(), quiet(2))),
    ("two claps too far apart", lambda: (quiet(1), clap(), quiet(1.6), clap(), quiet(1.5))),
    ("three claps", lambda: (quiet(1), clap(), quiet(0.15), clap(), quiet(0.15), clap(), quiet(1.5))),
    ("quieter taps", lambda: (quiet(1), clap(2500), quiet(0.2), clap(2500), quiet(1.5))),
    ("talking", lambda: (quiet(1), talking(3, 9000), quiet(1.5))),
    ("a door (rings on)", lambda: (quiet(1), clap(20000, tau=0.15, secs=0.8), quiet(0.2), clap(20000, tau=0.15, secs=0.8), quiet(1.5))),
])
def test_other_sounds_are_not_two_claps(name, parts):
    # Tuned on the owner's Session F recordings (D-91); these are the same rules on made-up sounds.
    assert claps_heard(*parts()) == 0, name


def test_mic_switches_are_separate(tmp_path):
    m = MicSwitch(tmp_path / "pip.db")
    assert m.listening and m.mode == "on"
    assert m.clapped() and m.mode == "claps_only" and not m.listening
    assert m.clapped() and m.listening
    m.set_dashboard(False)
    assert m.mode == "off" and not m.clapped() and not m.listening     # claps can't undo the dashboard
    assert MicSwitch(tmp_path / "pip.db").mode == "off"                 # kept across restarts


# ------------------------------------------------------------------ the conversation, with fake programs
class FakeSTT:
    def __init__(self, text=""):
        self.text, self.calls, self.names = text, 0, []

    async def transcribe(self, pcm):
        self.calls += 1
        return self.text


async def setup(tmp_path, stt_text="", replies=None, social=None, people=None, schedule=None, modules=None, prefs=None,
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
    replies = {"direct": "Sure.", "mentioned": "", "join": "", **(replies or {})}

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


async def finish(bus, tasks):
    for t in tasks:
        t.cancel()
    await bus.stop()


def room(text_pcm=b"x" * 32000):
    return {"reason": "room", "had_speech": True, "seconds": 1.0}, {"audio_b64": base64.b64encode(text_pcm).decode()}


def says(got):
    return [t for k, t, _ in got if k == "ears.say"]


async def test_ears_is_told_the_mic_mode_when_it_connects(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    assert ("ears.mic", None, {"mode": "on"}) in got
    await finish(bus, tasks)


async def test_two_claps_switch_listening_off_and_on_but_not_past_the_dashboard(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game")
    await ears.emit("mic.clapped", {})
    await asyncio.sleep(0.1)
    assert got[-2:] == [("ears.mic", None, {"mode": "claps_only"}), ("ears.tone", None, {"name": "mic_off"})]
    assert conv.state == "mic_off"
    await ears.emit("heard.utterance", *room())               # heard just before: still used (D-114); pip-ears
    await asyncio.sleep(0.1)                                   # captures nothing new once it's off
    assert conv.stt.calls == 1 and conv.state == "mic_off"
    await ears.emit("mic.clapped", {})
    await asyncio.sleep(0.1)
    assert got[-2:] == [("ears.mic", None, {"mode": "on"}), ("ears.tone", None, {"name": "mic_on"})]
    await conv.set_mic(False)                                  # the dashboard switch
    await asyncio.sleep(0.05)
    assert got[-2:] == [("ears.mic", None, {"mode": "off"}), ("ears.tone", None, {"name": "mic_off"})]
    n = len(got)
    await ears.emit("mic.clapped", {})                         # clapping can't turn him back on
    await asyncio.sleep(0.1)
    assert len(got) == n and not conv.mic.listening
    await conv.set_mic(True)
    assert conv.mic.listening
    await finish(bus, tasks)


def test_called_versus_mentioned():
    for t in ["Pip, what's the time?", "Hey Pip what's up", "thanks, Pip", "What do you think, Pippletron?",
              "okay so Pip, the weather?", "Pipple tron, stop."]:
        assert calls_pip(t), t
    for t in ["Pip's being weird today", "ask Pip later", "I told Pip about it", "did you hear what Pip said"]:
        assert not calls_pip(t) and names_pip(t), t
    for t in ["Ziggletron, what time is it?", "pass the pipe"]:
        assert not calls_pip(t) and not names_pip(t), t


async def test_everything_said_goes_in_the_transcript_and_claude_sees_it(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game last night", social={"join_in": False})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.2)
    assert says(got) == [] and not asks                        # not for Pip: just heard
    conv.stt.text = "Pip, who won?"
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert ("ears.chime", None, {}) in got and says(got) == ["Sure."]
    kind, private = asks[0]
    assert kind == "direct" and [x.split("] ", 1)[1] for x in private["transcript"]] == \
        ["Someone: did you see the game last night", "Someone: Pip, who won?"]
    assert conv.transcript.last()["ours"] and conv.transcript.last()["text"] == "Sure."
    trace = json.dumps(bus.trace.recent())
    assert "game" not in trace and "who won" not in trace     # never logged (D-34)
    await finish(bus, tasks)


async def test_a_mention_lets_claude_decide_and_pass_means_quiet(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip's been glitchy today", social={"join_in": False})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert [k for k, _ in asks] == ["mentioned"] and says(got) == [] and ("ears.chime", None, {}) not in got
    assert "answer.passed" in [r["type"] for r in bus.trace.recent()]
    await finish(bus, tasks)


async def test_pip_joins_in_after_a_pause(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "I wonder how far away the moon is",
                                                    replies={"join": "About 239,000 miles."})
    conv.joining.last_check = -1e9
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.5)
    assert [k for k, _ in asks] == ["join"] and says(got) == ["About 239,000 miles."]
    assert "SAY:" not in json.dumps(asks)                      # the instructions are added by pip-thinker
    # Not again straight away: at most every 40 s in the normal mood
    conv.stt.text = "huh, that's further than I thought"
    await ears.emit("speech.finished", {"interrupted": False})
    await asyncio.sleep(0.4)                                   # (the follow-up window closes)
    conv.expect_until = 0
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.4)
    assert [k for k, _ in asks] == ["join"]
    await finish(bus, tasks)


async def test_no_join_in_if_someone_starts_talking_or_the_conversation_moves_on(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "I wonder how far away the moon is",
                                                    replies={"join": "About 239,000 miles."})
    conv.joining.last_check = -1e9
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.02)
    await ears.emit("heard.started", {"reason": "room"})       # someone carries on before the pause
    await asyncio.sleep(0.3)
    assert asks == [] and says(got) == []
    # Claude decided to say something, but meanwhile someone else spoke: dropped
    conv.transcript.add("Someone", "never mind, I'll look it up")
    conv.join_count = conv.transcript.count - 1
    conv.answer_id, conv.answer_kind, conv.join_spoke = "j1", "join", False
    from pip_assistant.common.messages import Message
    await conv.on_sentence(Message("event", "answer.sentence", data={"answer_id": "j1"}, private={"text": "Hi."}))
    await asyncio.sleep(0.05)
    assert says(got) == [] and conv.answer_id is None
    await finish(bus, tasks)


# ------------------------------------------------------------------ PASS / SAY: in pip-thinker
def test_pass_and_say_are_understood():
    assert classify("PASS", final=True) == ("pass", "")
    assert classify("SAY: Oh nice", held=True) == ("speak", "Oh nice")
    assert classify("They're chatting about", held=True) is None            # thinking aloud: not spoken
    assert classify("They're chatting about sport.", held=True, final=True) == ("pass", "")
    assert classify("Eight.", final=True) == ("speak", "Eight.")
    assert classify("Passing through?", final=True) == ("speak", "Passing through?")
    text = Thinker.user_text("", ["[10:01] Landen: I wonder how far the moon is"], now="Sat", kind="join", mood="Be nice.")
    assert "Landen: I wonder" in text and "SAY:" in text and "PASS" in text and "Be nice." in text


async def test_only_words_after_say_are_spoken(monkeypatch):
    cli = ClaudeCLI()
    pieces = ("Hmm, they're talking about", " space. ", "SAY: About ", "239,000 miles.")
    lines = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": t}}} for t in pieces]
    lines.append({"type": "result", "is_error": False})

    class FakeProc:
        returncode = 0
        stdin = type("S", (), {"write": lambda self, b: None, "close": lambda self: None,
                               "drain": lambda self: asyncio.sleep(0)})()

        def __init__(self):
            async def gen():
                for line in lines:
                    yield (json.dumps(line) + "\n").encode()
            self.stdout = gen()

        async def wait(self):
            return 0

    async def spawn(prompt):
        return FakeProc()

    async def prepare(prompt):
        return None

    monkeypatch.setattr(cli, "_spawn", spawn)
    monkeypatch.setattr(cli, "prepare", prepare)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    async def nothing():
        pass

    text, info = await cli.ask("a1", "sys", "hi", on_sentence, nothing, held=True)
    assert spoken == ["About 239,000 miles."] and not info["passed"]
    lines[:] = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "PASS"}}},
                {"type": "result", "is_error": False}]
    spoken.clear()
    text, info = await cli.ask("a2", "sys", "hi", on_sentence, nothing, held=True)
    assert spoken == [] and info["passed"]


# ------------------------------------------------------------------ names heard right (D-93)
class FakeEmbedder:
    available = True

    async def embed(self, pcm):
        v = np.ones(4, np.float32)
        return v / np.linalg.norm(v)


def test_names_that_sound_the_same():
    assert sounds_like("Landon", "Landen") and sounds_like("Jon", "John")
    for heard in ("London", "Lemon", "Landing", "Brandon", "Lauren"):
        assert not sounds_like(heard, "Landen"), heard


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


# ------------------------------------------------------------------ natural language actions (D-94)
def test_ringing_and_voice_actions_are_checked():
    from pip_assistant.core import actions
    assert actions.check_here([{"do": "snooze", "minutes": 10}, {"do": "stop_alarm"}]) == [("snooze", 10.0), ("stop_alarm",)]
    assert actions.check_here([{"do": "learn_voice", "name": "landen"}]) == [("learn_voice", "Landen")]
    for bad in ({"do": "snooze", "minutes": 500}, {"do": "learn_voice", "name": ""}):
        with pytest.raises(actions.Invalid):
            actions.check_here([bad])


async def test_claude_learn_voice_finds_landen_even_spelled_landon(tmp_path):
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen = people.add_person("Landen", "admin")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, it's Landon here", social={"join_in": False},
                                                    people=people)
    conv.enrol_speech_wait = 0.02
    conv.last_pcm = b"x" * 64000
    await conv.claude_action('ACTION {"do": "learn_voice", "name": "Landon"}', 0.0)
    assert conv.enrol_status()["person_id"] == landen["id"]
    await asyncio.sleep(0.1)
    assert says(got)[-1].startswith("Hi Landen! Let's learn your voice.")
    conv.cancel_enrolment()
    await finish(bus, tasks)


async def test_offline_indicator(tmp_path):
    from pip_assistant.core.net import Net
    bus = Bus(tmp_path / "core.sock", Trace(tmp_path / "pip.db"), Health([], restart=False))
    net = Net(bus)
    await net.failed()
    assert not net.online and "net.changed" in [r["type"] for r in bus.trace.recent()]
    await net.set(True)
    assert net.online and sorted(r["data"]["online"] for r in bus.trace.recent() if r["type"] == "net.changed") == [False, True]
    bus.trace.flush()


async def test_claude_looks_the_calendar_up_then_answers(tmp_path):
    """D-102: only today and tomorrow go with each message; for next Friday Claude asks Pip to look, once."""
    from datetime import datetime, timedelta
    from pip_assistant.core.schedule import Schedule
    from modhelp import registry
    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched)
    day = (datetime.now() + timedelta(days=9)).replace(hour=15, minute=0, second=0, microsecond=0)
    reg.get("calendar").add_event("dentist", day)
    look = 'ACTION {"do": "look", "kind": "calendar", "from": "%s"}' % day.date().isoformat()
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path, "Pip, what have I got that day?", social={"join_in": False}, schedule=sched, modules=reg,
        replies={"direct": [look, "The dentist at 3."]})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.6)
    assert len(asks) == 2 and "dentist" not in asks[0][1]["schedule"] and not asks[0][1]["looked_up"]
    assert "dentist" in asks[1][1]["looked_up"] and says(got) == ["The dentist at 3."]
    await finish(bus, tasks)
    # Asking to look again on the second round doesn't go round in circles
    (tmp_path / "again").mkdir()
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path / "again", "Pip, and the day after?", social={"join_in": False}, schedule=sched, modules=reg,
        replies={"direct": [look, look]})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.6)
    assert len(asks) == 2 and says(got) == ["Sorry, I couldn't find that."]
    await finish(bus, tasks)


F_FILES = __import__("pathlib").Path.home() / "pip-data" / "m0" / "recordings"


@pytest.mark.skipif(not (F_FILES / "F001.wav").exists(), reason="the owner's Session F recordings are only on the Pi")
def test_the_owners_session_f_recordings():
    """D-91 tuning: every double clap (F001–F007) heard; a single clap, tongue clicks, snaps, knocks, 3+ claps,
    talking and a door (F008–F014) not. (Recordings stay on the Pi, D-72.)"""
    import wave

    def fires(n):
        with wave.open(str(F_FILES / f"F{n:03d}.wav")) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = np.concatenate([x, np.zeros(16000, np.int16)])
        d = ClapDetector()
        return sum(d.feed(x[i:i + 1280]) for i in range(0, len(x), 1280))
    assert [fires(n) for n in range(1, 15)] == [1] * 7 + [0] * 7
