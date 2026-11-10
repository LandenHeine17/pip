"""People and voices (core/people.py, peoplecmds.py, enrolment.py; D-87, D-89): recognising voices with confidence
levels, introductions, guided voice learning, visitors, the dashboard's People card, and who's talking reaching Claude."""
import asyncio
import json
import os
from datetime import datetime

import numpy as np
import pytest
from helpers import FakeSTT, clock_at, finish, voice
from helpers import people_bus as setup_bus
from helpers import voice_utt as utt

from pip_assistant.core.people import Match, People
from pip_assistant.core.peoplecmds import PeopleCommands, StartLearning

NOW = datetime(2026, 11, 5, 9, 0)
Clock = clock_at(NOW)



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
    assert await pc.handle("Pip, leave me out", me, voice("alex")) is None   # removed (D-90): the mic switch instead
    # A visitor: remembered for this visit only
    assert await pc.handle("Hey Pip, I'm Jordan.", Match(), voice("guest")) == \
        "Nice to meet you, Jordan! I'll remember your voice for this visit."
    g = await people.identify(voice("guest"))
    assert g.guest and g.name == "Jordan"
    people.clock.t += 3 * 3600                               # 2 hours of quiet: forgotten
    assert (await people.identify(voice("guest"))).level == "unknown"
    # Introducing yourself as a household member with no voice yet starts voice learning
    sam = people.add_person("Sam")
    r = await pc.handle("Hey Pip, my name is Sam.", Match(), voice("sam"))
    assert isinstance(r, StartLearning) and r.person_id == sam["id"] and r.say.startswith("Hi Sam! Let's learn your voice.")
    assert isinstance(await pc.handle("Pip, this is Sam, can you learn my voice?", Match(), voice("sam")), StartLearning)
    await people.add_samples(sam["id"], [voice("sam")], "guided")
    # Once learned: recognised → one more sample; not recognised → nobody can add to someone else's voice
    s = await people.identify(voice("sam"))
    assert await pc.handle("I'm Sam.", s, voice("sam")) == "Hi Sam! I'll get better at recognising you."
    assert people.all()[1]["voice_samples"] == 2
    assert "You don't sound like you" in await pc.handle("I'm Sam.", Match(), voice("tv"))
    assert people.all()[1]["voice_samples"] == 2
    assert await pc.handle("I'm tired.", Match(), voice("sam")) is None    # not an introduction
    assert await pc.handle("It's Monday.", Match(), voice("sam")) is None
    assert await pc.handle("I'm Sam and I was wondering what the weather will be like this weekend", Match(), voice("sam")) is None
    # "Learn my voice": recognised → learning; unknown → Pip asks the name, and the answer starts it
    assert isinstance(await pc.handle("Pip, learn my voice", s, voice("sam")), StartLearning)
    assert await pc.handle("Pip, can you learn my voice?", Match(), voice("alex")) == "Sure! Who am I learning? Say your name."
    assert (await pc.handle("Alex.", Match(), voice("tv"))).startswith("Hi Alex! You don't sound like you")   # learned already, not him
    assert await pc.handle("Pip, remember the plants need water on Sundays", me, voice("alex"), offline=True) == \
        "Saved: the plants need water on Sundays."
    assert mem.items()[0]["status"] == "active"


async def test_a_visitor_can_be_added_to_the_household(people):
    pc = PeopleCommands(people)
    people.add_person("Alex", "admin")
    await pc.handle("Hi Pip, I'm Jordan.", Match(), voice("guest"))
    [g] = people.snapshot()["guests"]
    j = people.promote_guest(g["id"])
    assert j["name"] == "Jordan" and people.all()[1]["voice_samples"] == 1 and not people.guests
    assert (await people.identify(voice("guest"))).person_id == j["id"]

# ---------------------------------------------------------------- the whole path, with fake programs


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


async def test_room_speech_from_everyone_goes_in_the_transcript_with_who_said_it(tmp_path):
    clock = Clock()
    stt = FakeSTT("did you see the game")
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt)
    sam = people.add_person("Sam")
    await people.add_samples(sam["id"], [voice("sam")], "guided")
    people.db.execute("UPDATE people SET opted_out = 1")       # an old opt-out is ignored now (D-90)
    await ears.emit("heard.utterance", *utt("room", voice("sam")))
    await asyncio.sleep(0.2)
    stt.text = "yeah, Landon's team won"
    await ears.emit("heard.utterance", *utt("room", voice("tv")))
    await asyncio.sleep(0.2)
    assert stt.calls == 2
    assert [x.split("] ", 1)[1] for x in conv.transcript.render()] == ["Sam: did you see the game",
                                                                         "Someone: yeah, Landon's team won"]
    await finish(bus, tasks)


async def test_guided_voice_learning(tmp_path):
    clock = Clock()
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT("should not be used"))
    conv.enrol_speech_wait = 0.05
    alex = people.add_person("Alex", "admin")
    status = await conv.start_enrolment(alex["id"])
    assert status["of"] == 5 and status["done"] == 0 and status["name"] == "Alex"
    await asyncio.sleep(0.1)
    await ears.emit("heard.utterance", *utt("follow_up", voice("alex", 0.4)))     # too short: asked again, not counted
    await asyncio.sleep(0.15)
    assert conv.enrol_status()["done"] == 0 and conv.enrol_status()["retry"]
    for i in range(5):
        await ears.emit("heard.utterance", *utt("follow_up", voice("alex", 2.5)))
        await asyncio.sleep(0.1)
    assert conv.stt.calls == 0                                   # read sentences aren't transcribed or answered
    assert people.all()[0]["voice_samples"] == 5 and conv.enrol_status() is None
    says = [t for k, t, _ in got if k == "ears.say"]
    assert says[0].startswith("Hi Alex!") and "Read sentence 1 again" in says[1]
    assert says[-1].startswith("Thanks, Alex. I know your voice now.")
    assert len([1 for k, _, _ in got if k == "ears.chime"]) >= 6
    await finish(bus, tasks)


async def test_introducing_yourself_out_loud_learns_your_voice(tmp_path):
    clock = Clock()
    stt = FakeSTT("Hey Pip, I'm Alex.")      # Claude understands the introduction (any wording, D-94)
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt,
                                                                  'ACTION {"do": "learn_voice", "name": "Alex"}')
    conv.enrol_speech_wait = 0.05
    alex = people.add_person("Alex", "admin")
    await ears.emit("heard.utterance", *utt("wake", voice("alex", 2.0)))
    await asyncio.sleep(0.2)
    assert conv.enrol_status()["name"] == "Alex"
    assert [t for k, t, _ in got if k == "ears.say"][0].startswith("Hi Alex! Let's learn your voice.")
    assert len([x for x in got if x[0] == "thinker.ask"]) == 1
    for i in range(5):
        await ears.emit("heard.utterance", *utt("room" if i == 2 else "follow_up", voice("alex", 2.5)))  # one read late
        await asyncio.sleep(0.1)
    assert people.all()[0]["voice_samples"] == 6 and stt.calls == 1          # 5 sentences + the introduction
    stt.text = "Pip, who am I?"                # Claude is told who's speaking
    await ears.emit("heard.utterance", *utt("wake", voice("alex2")))
    await asyncio.sleep(0.2)
    assert [x for x in got if x[0] == "thinker.ask"][-1][2]["people"].startswith("Speaking: Alex (")
    await finish(bus, tasks)


async def test_learning_stops_if_nobody_reads(tmp_path):
    clock = Clock()
    bus, people, memory, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(""))
    conv.enrol_speech_wait, conv.ENROL_WAIT = 0.02, 0.05
    alex = people.add_person("Alex", "admin")
    await conv.start_enrolment(alex["id"])
    await asyncio.sleep(4.0)
    says = [t for k, t, _ in got if k == "ears.say"]
    assert any("Whenever you're ready, read sentence 1" in s for s in says)
    assert says[-1].startswith("I'll stop there.") and conv.enrol_status() is None
    await finish(bus, tasks)


async def test_dashboard_people_and_memory_actions(tmp_path):
    from pip_assistant.common.messages import Message
    from pip_assistant.core.dashapi import memory_action, memory_snapshot, people_action
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

# ---------------------------------------------------------------- the real voice model (only on the Pi)


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
    from scipy.signal import resample_poly

    from pip_assistant.ears.voice import make_voice
    v = make_voice("piper", "en_US-lessac-medium", 1.0)
    a = resample_poly(v.speak("Hey Pip, what's the weather like tomorrow?").astype(float), 16000, v.rate).astype(np.int16)
    assert (await people.identify(a.tobytes())).level == "unknown"     # a different voice
