"""A phone as Pip's microphone and speaker (D-149): the link to pip-web, taking over and letting go, the phone's
speaker, and pip-ears switching between the room and the phone."""
import asyncio
import json
import struct

import numpy as np

from pip_assistant.common.wire import SOUND, TEXT, pack, read_message
from pip_assistant.ears import main as ears_main
from pip_assistant.ears.remote import RemoteLink, RemoteSpeaker
from pip_assistant.ears.segmenter import Segmenter


async def test_messages_travel_whole():
    r = asyncio.StreamReader()
    r.feed_data(pack(SOUND, b"\x01\x02" * 5) + pack(TEXT, b'{"hello": {}}'))
    r.feed_eof()
    assert await read_message(r) == (SOUND, b"\x01\x02" * 5)
    assert await read_message(r) == (TEXT, b'{"hello": {}}')
    assert await read_message(r) is None   # the other side's gone


class Changes:
    def __init__(self):
        self.seen, self.refusal = [], ""

    async def on_change(self, route, why):
        self.seen.append((route, why))

    def refuse(self):
        return self.refusal


async def link_up(tmp_path, **kw):
    ch = Changes()
    link = RemoteLink(tmp_path / "r.sock", ch.on_change, ch.refuse, **kw)
    task = asyncio.create_task(link.serve())
    for _ in range(50):
        if (tmp_path / "r.sock").exists():
            break
        await asyncio.sleep(0.01)
    return link, ch, task


async def phone(tmp_path):
    return await asyncio.open_unix_connection(str(tmp_path / "r.sock"))


async def text(reader):
    while True:
        kind, payload = await asyncio.wait_for(read_message(reader), 2)
        if kind == TEXT:
            return json.loads(payload)


async def settle(cond, secs=2.0):
    for _ in range(int(secs / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_a_phone_takes_over_sends_its_sound_and_gives_back(tmp_path):
    link, ch, task = await link_up(tmp_path)
    r, w = await phone(tmp_path)
    assert await text(r) == {"hello": {}}
    assert await settle(lambda: ch.seen == [("phone", "taken")])
    sound = (np.arange(1280 * 2 + 100) % 300).astype(np.int16).tobytes()
    w.write(pack(SOUND, sound[:3000]) + pack(SOUND, sound[3000:]))   # any lengths: cut into 80 ms frames
    await w.drain()
    frames = link.frames()
    first, second = await asyncio.wait_for(anext(frames), 1), await asyncio.wait_for(anext(frames), 1)
    assert len(first) == len(second) == 1280 and first.tobytes() + second.tobytes() == sound[:1280 * 4]
    w.close()                                           # the page closed
    assert await settle(lambda: ch.seen[-1] == ("room", "closed"))
    assert not link.connected
    task.cancel()


async def test_the_newest_phone_takes_over(tmp_path):
    link, ch, task = await link_up(tmp_path)
    r1, w1 = await phone(tmp_path)   # (kept: a writer that's let go of closes its connection)
    assert await text(r1) == {"hello": {}}
    r2, w2 = await phone(tmp_path)
    assert await text(r1) == {"bye": "moved"}
    assert await text(r2) == {"hello": {}}
    await asyncio.sleep(0.05)
    assert ch.seen == [("phone", "taken")] and link.connected   # still on a phone: no switch back and forth
    task.cancel()


async def test_not_while_listening_is_off(tmp_path):
    link, ch, task = await link_up(tmp_path)
    ch.refusal = "listening_off"
    r, w = await phone(tmp_path)
    assert await text(r) == {"bye": "listening_off"}
    await asyncio.sleep(0.05)
    assert ch.seen == [] and not link.connected
    task.cancel()


async def test_back_to_the_room_when_the_phone_goes_quiet_or_nobody_talks(tmp_path):
    link, ch, task = await link_up(tmp_path, no_audio_s=0.5)
    r, w = await phone(tmp_path)
    assert await text(r) == {"hello": {}}
    assert await text(r) == {"bye": "no_audio"}          # locked: no sound came
    assert await settle(lambda: ch.seen[-1] == ("room", "no_audio"))
    link.idle_s, link.no_audio_s = 0.5, 60
    r, w = await phone(tmp_path)
    assert await text(r) == {"hello": {}}
    w.write(pack(SOUND, bytes(2560)))
    await w.drain()
    assert await text(r) == {"bye": "idle"}              # sound, but nobody talking
    assert await settle(lambda: ch.seen[-1] == ("room", "idle"))
    task.cancel()


async def test_the_phones_speaker_sends_everything_or_stops_and_flushes(tmp_path):
    link, ch, task = await link_up(tmp_path)
    r, w = await phone(tmp_path)
    assert await text(r) == {"hello": {}}
    speaker = link.speaker
    assert isinstance(speaker, RemoteSpeaker)
    audio = (np.ones(2205) * 1000).astype(np.int16)      # 0.1 s at 22050
    levels = []
    assert await speaker.play(audio, 22050, on_level=levels.append) is True
    got = b""
    while len(got) < audio.nbytes:
        kind, payload = await asyncio.wait_for(read_message(r), 1)
        assert kind == SOUND and struct.unpack("<I", payload[:4])[0] == 22050
        got += payload[4:]
    assert got == audio.tobytes() and levels and not speaker.playing
    long = np.zeros(22050 * 5, np.int16)
    playing = asyncio.create_task(speaker.play(long, 22050))
    await asyncio.sleep(0.2)
    await speaker.stop()
    assert await asyncio.wait_for(playing, 1) is False
    assert await text(r) == {"flush": True}
    task.cancel()


class Bus:
    def __init__(self):
        self.status, self.emitted = {}, []

    async def emit(self, kind, data, private=None):
        self.emitted.append((kind, data))


def ears_without_devices():
    e = ears_main.Ears.__new__(ears_main.Ears)
    e.bus, e.seg, e.cfg = Bus(), Segmenter(speech_dbfs=-50), {"barge_in_dbfs": -45.0}
    e.levels = {"room": (-50.0, -45.0), "phone": (-40.0, -35.0)}
    e.queue, e.stops, e.mic_mode, e.ring_task, e.room_task = asyncio.Queue(), 0, "on", None, None
    e.follow_until, e.ducked_at, e.quiet_since = 0.0, 0.0, None
    e.room_frames = ears_main.deque(maxlen=4)

    class Room:
        gain, stopped = 1.0, 0

        async def stop(self):
            Room.stopped += 1
    e.speaker = e.room_speaker = Room()
    e.remote = type("Link", (), {"speaker": type("PhoneSpeaker", (), {"gain": 1.0, "stop": Room.stop})()})()
    return e


async def test_pip_ears_hears_and_speaks_wherever_pip_is():
    e = ears_without_devices()
    assert e.refuse_phone() == ""
    e.seg.start("room")
    await e.on_route("phone", "taken")
    assert e.speaker is e.remote.speaker and not e.seg.capturing         # what was half-heard is dropped
    assert e.seg.speech_dbfs == -40.0 and e.cfg["barge_in_dbfs"] == -35.0   # the phone's own levels
    assert e.bus.status["route"] == "phone" and ("ears.route", {"to": "phone", "why": "taken"}) in e.bus.emitted
    await e.on_route("room", "idle")
    assert e.speaker is e.room_speaker and e.seg.speech_dbfs == -50.0 and e.cfg["barge_in_dbfs"] == -45.0
    e.mic_mode = "off"
    assert e.refuse_phone() == "listening_off"


async def test_listening_switched_off_sends_the_phone_back():
    e = ears_without_devices()
    ended = []

    async def end(why):
        ended.append(why)
    e.remote.end = end
    e.route, e.claps = "phone", type("C", (), {"reset": lambda self: None})()
    await e.on_mic(type("M", (), {"data": {"mode": "off"}})())
    assert ended == ["listening_off"]


async def test_an_alarm_rings_in_the_room_too_while_a_phone_has_pip():
    e = ears_without_devices()
    played = []

    class Speaker:
        def __init__(self, where):
            self.where, self.gain = where, 1.0

        async def play(self, audio, rate, on_level=None):
            played.append(self.where)
            return True

        async def stop(self):
            pass
    e.room_speaker, e.speaker = Speaker("room"), Speaker("phone")
    e.voice = type("V", (), {"rate": 22050})()
    e.speaking = e.voiced = False
    e.said_n = 0
    e.unduck = lambda: None
    await e.queue.put(("ring", np.ones(50, np.int16)))
    await e.queue.put(("sound", np.ones(50, np.int16)))   # a chime: only where Pip is
    loop = asyncio.create_task(e.speak_loop())
    await asyncio.sleep(0.05)
    loop.cancel()
    assert sorted(played[:2]) == ["phone", "room"] and played[2:] == ["phone"]


async def test_the_room_is_not_heard_while_a_phone_has_pip(monkeypatch):
    """The room's recorder is closed as soon as a phone has Pip; the phone's frames are heard instead."""
    e = ears_without_devices()
    e.route = "room"
    closed = []

    class Mic:
        async def frames(self):
            try:
                while True:
                    yield np.full(1280, 1, np.int16)
                    await asyncio.sleep(0)
            finally:
                closed.append(True)
    e.mic = Mic()

    class Link:
        async def frames(self):
            yield np.full(1280, 2, np.int16)
            e.route = "room"
    e.remote = Link()
    heard = []
    async for frame in e.frames():
        heard.append(int(frame[0]))
        if len(heard) == 2:
            e.route = "phone"
        if len(heard) == 4:
            break
    assert heard == [1, 1, 2, 1] and closed
