"""pip-ears: listens all the time, captures what's said (all of it goes to speech-to-text: pip-core listens for
Pip's name in the words, D-134), speaks, and stops instantly when talked over.

Events it sends:   heard.started {reason} · heard.utterance {reason, seconds, had_speech,
                   quiet_s} (+ private audio) · speech.started · speech.interrupted {level_dbfs, said} · speech.finished
                   {interrupted, spoken} · speech.overlap_ignored {level_dbfs} (loud over him, but no voice: he carries on)
                   speech.level {level 0–1} (~10 a second while he talks, for the face's mouth; not traced)
                   ears.status {state}  (listening / speaking / muted_or_quiet / mic_off)
                   ears.route {to: room / phone, why}  (a phone took over Pip's mic and speaker, or let go: D-149)
                   mic.clapped   (two claps: pip-core decides what that switches, D-91) · mic.clapped_trial (heard, but
                   the clap switch isn't ticked yet: only shown on the dashboard)
Commands it takes: ears.say (private.text) · ears.chime · ears.tone {name: "thinking" / "mic_on" / "mic_off"} · ears.stop
                   ears.listen {seconds} (follow-up window; 0 ends it) · ears.mic {mode: on / claps_only / off}
                   ears.clap_mic {device, on} (which microphone listens for claps, "" = none; on = claps switch at all) · ears.mics (request: list them)
                   ears.ring {tone, max_seconds} · ears.ring_stop   (alarms: loop a ringtone until told to stop)

Claps are heard on a **separate, plain microphone** (D-91): the speakerphone's own noise filter erases
claps. With no clap microphone chosen (on the dashboard), the clap switch does nothing.

Listening switched off (ears.mic): "claps_only" means the speakerphone is ignored completely and only
the clap microphone is listened to (nothing recognised or sent); "off" means nothing listens at all. It starts "off" and pip-core
says which mode to use as soon as it's connected, so a restart never listens when it shouldn't.

While an alarm rings, talking over it cuts the ringtone for a moment and captures what you said, so
"stop" or "snooze" works; if it wasn't for the alarm, ringing carries on. The ringing stops by
itself after `max_seconds`, even if pip-core is stuck, so it can never ring forever.

A phone can take over (D-149, ears/remote.py): the /phone page becomes Pip's microphone and speaker. Then the
speakerphone isn't listened to at all and he speaks only on the phone; alarms ring in both places, and while
one rings the room's microphone is mixed in, so "stop" works from either. The rest is the same: the same
segmenter, talk-over check and events, so pip-core can't tell the difference. Not while listening is off.
"""
import asyncio
import base64
import logging
import time
from collections import deque

import numpy as np

from ..common import paths
from ..common.messages import Message
from ..common.program import make_bus, serve, start
from ..common.voicecheck import voice_seconds_sync
from .audio import Microphone, Speaker, chime, level_dbfs, mic_tone, microphones, ringtone, thinking_tone
from .clap import ClapDetector, off_refused
from .remote import RemoteLink
from .segmenter import Segmenter
from .voice import make_voice

log = logging.getLogger("pip-ears")
# Talked over (D-128): a voice over him first lowers his voice to DUCK_GAIN; still talking DUCK_S later and he stops;
# the room quiet again for UNDUCK_AFTER_S and he comes back up (a short laugh or "yeah")
DUCK_GAIN = 0.3
DUCK_S = 0.6
UNDUCK_AFTER_S = 0.4


class Ears:
    route = "room"            # where Pip hears and speaks: "room" (the speakerphone) or "phone" (D-149)
    remote = None             # the phone's link (ears/remote.py), if phones may take over
    room_speaker = None       # the speakerphone (self.speaker is whichever he speaks through now)

    def __init__(self, bus, cfg: dict):
        e, li, ph = cfg["ears"], cfg["listen"], cfg.get("phone", {})
        self.bus = bus
        self.cfg = e
        self.mic = Microphone()      # the room's (the speakerphone)
        self.speaker = self.room_speaker = Speaker()
        # How loud counts as speech, and as talking over him, in each place: a phone's microphone may differ (D-149)
        self.levels = {"room": (li["speech_dbfs"], e["barge_in_dbfs"]),
                       "phone": (ph.get("speech_dbfs", li["speech_dbfs"]), ph.get("barge_in_dbfs", e["barge_in_dbfs"]))}
        if ph.get("enabled", True):
            self.remote = RemoteLink(paths.remote_socket(), self.on_route, self.refuse_phone,
                                     no_audio_s=ph.get("no_audio_seconds", 4), idle_s=ph.get("idle_minutes", 10) * 60)
        self.room_frames: deque = deque(maxlen=4)   # the room's microphone while an alarm rings on a phone
        self.room_task: asyncio.Task | None = None
        self.voice = make_voice(e["voice_engine"], e["voice"], e["speech_rate"])
        self.chime = chime(self.voice.rate)
        self.tones = {"thinking": thinking_tone(self.voice.rate), "mic_on": mic_tone(True, self.voice.rate),
                      "mic_off": mic_tone(False, self.voice.rate)}
        self.mic_mode = "off"      # until pip-core says (D-91)
        self.claps = ClapDetector(cfg.get("mic", {}))
        self.cfg_mic = cfg.get("mic", {})
        self.clap_mic = ""         # which microphone listens for claps (pip-core says; "" = none, D-91)
        self.clap_task: asyncio.Task | None = None
        self.claps_live = False    # False: trial (only reported), until "Two claps switch listening" is ticked
        self.quiet_after = 0.0     # Pip's own sound reaches the clap microphone: ignore it until a moment after
        self.talk_at: deque[float] = deque(maxlen=600)   # when the speakerphone last heard speech (clap switch-off rule)
        self.seg = Segmenter(speech_dbfs=li["speech_dbfs"], end_silence_s=li["end_silence_seconds"],
                             max_s=li["max_utterance_seconds"], wait_for_speech_s=li["wait_for_speech_seconds"])
        self.queue: asyncio.Queue = asyncio.Queue()
        self.speaking = False
        self.stops = 0               # how many times he's been stopped: a sentence being made meanwhile is dropped
        self.voiced = False          # his voice has played in the current run of sounds (speech.started sent)
        self.loud_frames = 0
        self.ducked_at = 0.0         # his voice is lowered while deciding if someone's talking over him (D-128)
        self.quiet_since = None      # …and the room has been quiet since then (a short laugh, a "yeah")
        self.overlap_noted = False   # a sound over him without a voice was already traced (heard_over)
        self.said_n = 0              # sentences played to the end in the current run of speech
        self.follow_until = 0.0
        self.silent_since = None
        self.state = "listening"
        self.ring_task: asyncio.Task | None = None
        self._level_n = 0          # counts mouth-level readings, to send every other one

    # ---------------------------------------------------------------- the clap microphone (D-91)
    async def listen_for_claps(self, device: str) -> None:
        self.claps.reset()
        async for frame in Microphone(target=device).frames():
            busy = self.speaking or self.ring_task is not None or self.route == "phone"
            if busy:
                self.quiet_after = time.monotonic() + 0.5
            if self.mic_mode == "off" or busy or time.monotonic() < self.quiet_after:
                self.claps.reset()   # switched off on the dashboard, or it's Pip's own sound
                continue
            if self.claps.feed(frame):
                shape = dict(self.claps.last)   # how loud and far apart: numbers only, kept in the trace
                why = off_refused(shape, self.talked_between, self.cfg_mic) if self.mic_mode == "on" else ""
                if why:   # they'd switch him off, but by the look of it nobody clapped (owner, 2026-11-17)
                    log.info("two claps ignored (%s) %s", why, shape)
                    await self.bus.emit("mic.clap_ignored", {**shape, "why": why})
                elif self.claps_live:
                    log.info("heard two claps %s", shape)
                    await self.bus.emit("mic.clapped", shape)
                else:   # trial: the clap switch isn't ticked yet, just show it was heard (D-91)
                    log.info("heard two claps (trial) %s", shape)
                    await self.bus.emit("mic.clapped_trial", shape)

    async def on_clap_mic(self, msg: Message):
        """Which microphone hears claps. With claps switched off it still listens, as a trial: two claps only
        show up on the dashboard's conversation feed ("would have switched"), nothing is switched."""
        device = str(msg.data.get("device") or "")
        self.claps_live = bool(msg.data.get("on", True))
        if device == self.clap_mic and (self.clap_task or not device):
            return {"ok": True}
        if self.clap_task:
            self.clap_task.cancel()
            self.clap_task = None
        self.clap_mic = device
        if device:
            self.clap_task = asyncio.create_task(self.listen_for_claps(device))
        self.bus.status["clap_mic"] = bool(device)
        return {"ok": True}

    def talked_between(self, ago_from: float, ago_to: float) -> bool:
        """Did the speakerphone hear speech between `ago_from` and `ago_to` seconds ago?"""
        now = time.monotonic()
        return any(now - ago_from <= t <= now - ago_to for t in self.talk_at)

    async def on_mics(self, msg: Message):
        mics = await asyncio.get_running_loop().run_in_executor(None, microphones)
        return {"mics": mics, "speakerphone": next((m["name"] for m in mics if "Speaker_Phone" in m["name"]), "")}

    # ---------------------------------------------------------------- listening
    async def frames(self):
        """What Pip hears: the room's microphone, or the phone's while it has Pip (D-149). The room's recorder is
        closed while the phone has him, so the room really isn't heard; it opens again when the phone lets go."""
        while True:
            if self.route == "phone" and self.remote is not None:
                async for frame in self.remote.frames():
                    if self.room_frames:   # an alarm is ringing: the room can say "stop" too
                        frame = np.clip(frame.astype(np.int32) + self.room_frames.popleft(), -32768, 32767).astype(np.int16)
                    yield frame
                if self.route == "phone":
                    await asyncio.sleep(0.05)   # the phone's gone; the switch back to the room is on its way
                continue
            room = self.mic.frames()
            try:
                async for frame in room:
                    yield frame
                    if self.route != "room":
                        break
            finally:
                await room.aclose()

    async def listen(self) -> None:
        async for frame in self.frames():
            if self.mic_mode == "off":
                await self._update_state(frame)
                continue  # switched off on the dashboard: nothing listens
            db = level_dbfs(frame)
            self.bus.status["level_dbfs"] = db
            await self._update_state(frame)
            if self.mic_mode != "on":
                continue  # switched off by claps: only the clap microphone is listened to
            if self.seg.is_speech(db):
                self.talk_at.append(time.monotonic())   # for the clap switch-off rule (ears/clap.py)
                if self.route == "phone":
                    self.remote.heard_speech()          # someone's talking through it: it isn't idle
            if self.speaking and self.cfg["barge_in"]:
                await self.heard_over(frame, db)
            elif not self.seg.capturing and self.seg.is_speech(db) and not self.speaking:
                if time.monotonic() < self.follow_until:
                    self.follow_until = 0.0
                    self.seg.start("follow_up", lookback=0.4)
                    await self.bus.emit("heard.started", {"reason": "follow_up"})
                else:
                    self.seg.start("room", lookback=0.4)  # everything heard is transcribed, like Ziggletron (D-92, D-134)
                    await self.bus.emit("heard.started", {"reason": "room"})
            utt = self.seg.feed(frame, db)
            if utt and (utt.reason != "room" or utt.seconds >= 0.5):
                await self._send_utterance(utt)

    async def heard_over(self, frame, db: float) -> None:
        """Pip is talking: is someone talking over him? The speakerphone removes his own voice, so ~240 ms of
        loud sound is a candidate. Since D-122 it must also have a human voice in it (the same Silero check
        pip-core uses before speech-to-text, ~2 ms): a knock, a clap or the door doesn't cut him off (the owner's
        spec, 2026-11-12: about 1 in 7 talk-overs was followed by nothing or noise).
        Since D-128 (`duck_before_stop`) a voice first only **lowers** his voice: if it keeps going another
        DUCK_S he stops (they're talking), and if the room goes quiet again he comes back up (a short laugh, a
        "yeah"), so laughing along doesn't stop him."""
        loud = db > self.cfg["barge_in_dbfs"]
        self.loud_frames = self.loud_frames + 1 if loud else 0
        now = time.monotonic()
        if self.ducked_at:
            if loud:
                self.quiet_since = None
                if self.loud_frames >= 3:
                    self.loud_frames = 0
                    if now - self.ducked_at >= DUCK_S and await self.voice_in(frame):
                        held = now - self.ducked_at
                        self.unduck()
                        await self.talked_over(db, lookback=0.4 + held)   # keep what they said while he was lowered
            elif self.quiet_since is None:
                self.quiet_since = now
            elif now - self.quiet_since >= UNDUCK_AFTER_S:
                self.unduck()
                await self.bus.emit("speech.unducked", {})
            return
        if self.loud_frames < 3:
            return
        self.loud_frames = 0
        if self.cfg.get("barge_in_voice_check", True) and not await self.voice_in(frame):
            if not self.overlap_noted:      # once per thing he says, for the trace (tools/latency.py)
                self.overlap_noted = True
                await self.bus.emit("speech.overlap_ignored", {"level_dbfs": round(db, 1)})
            return
        if self.cfg.get("duck_before_stop", True):
            self.ducked_at, self.quiet_since = now, None
            self.speaker.gain = DUCK_GAIN
            await self.bus.emit("speech.ducked", {"level_dbfs": round(db, 1)})
            return
        await self.talked_over(db)

    def unduck(self) -> None:
        self.ducked_at, self.quiet_since = 0.0, None
        self.speaker.gain = 1.0

    async def voice_in(self, frame) -> bool:
        """Is there a voice in the last ~0.5 s (the frames just heard and this one)?"""
        frames = [f for f, _ in list(self.seg.history)[-6:]] + [frame]
        pcm = np.concatenate(frames).astype(np.int16).tobytes()
        secs = await asyncio.get_running_loop().run_in_executor(None, voice_seconds_sync, pcm)
        return secs >= 0.1

    async def talked_over(self, db: float, lookback: float = 0.4) -> None:
        """Someone is talking over Pip: he stops, and what they say is for him. If they were already part-way
        through a sentence when he started (an alarm went off mid-sentence), that recording carries on whole;
        starting a new one would throw away what they'd said so far (owner's feedback, 2026-11-10). Unless it's
        already over half its longest, from long room talk: then a fresh one, so the end isn't cut off."""
        said = self.said_n   # whole sentences he'd finished: pip-core can carry on from the next (CB-020)
        await self.stop_speaking()
        await self.bus.emit("speech.interrupted", {"level_dbfs": db, "said": said})
        if self.seg.capturing and self.seg.capture.get("n", 0) < self.seg.max_frames // 2:
            if self.seg.capture["reason"] == "room":
                self.seg.capture["reason"] = "barge_in"   # room talk that carried on over him: it's for him now
        else:
            self.seg.start("barge_in", lookback=lookback)

    async def _send_utterance(self, utt) -> None:
        data = {"reason": utt.reason, "seconds": utt.seconds, "had_speech": utt.had_speech, "quiet_s": utt.quiet_s}
        private = {"audio_b64": base64.b64encode(utt.audio.tobytes()).decode()} if utt.had_speech else {}
        await self.bus.emit("heard.utterance", data, private=private)

    async def _update_state(self, frame) -> None:
        now = time.monotonic()
        if frame.any():
            self.silent_since = None
        elif self.silent_since is None:
            self.silent_since = now
        state = "mic_off" if self.mic_mode != "on" else "speaking" if self.speaking else (
            "muted_or_quiet" if self.silent_since and now - self.silent_since > 10 else "listening")
        if state != self.state:
            self.state = state
            self.bus.status["activity"] = state
            await self.bus.emit("ears.status", {"state": state})

    # ---------------------------------------------------------------- speaking
    async def speak_loop(self) -> None:
        """Plays queued sounds in order. Each spoken sentence is synthesised as soon as it's queued."""
        while True:
            kind, item = await self.queue.get()
            stops = self.stops
            try:
                audio = await item if kind == "speech" else item
            except asyncio.CancelledError:
                continue
            except Exception:   # one sentence Piper couldn't make: skip it, keep speaking
                log.exception("speech synthesis failed")
                continue
            if audio is None or not len(audio) or self.stops != stops:
                continue   # (stopped while Piper was making it: "stop", talked over)
            self.speaking = True
            if kind == "speech" and not self.voiced:   # his voice starts (even right after a chime or tone)
                self.voiced, self.overlap_noted, self.said_n = True, False, 0
                await self.bus.emit("speech.started", {})
            if kind == "ring" and self.speaker is not self.room_speaker and self.room_speaker is not None:
                # a phone has Pip: alarms ring in the room too (owner, D-149)
                finished = all(await asyncio.gather(self.speaker.play(audio, self.voice.rate),
                                                    self.room_speaker.play(audio, self.voice.rate)))
            else:
                finished = await self.speaker.play(audio, self.voice.rate,
                                                   on_level=self._mouth if kind == "speech" else None)
            if finished and kind == "speech":
                self.said_n += 1
            if not finished or self.queue.empty():
                # spoken: whether his voice played in this run, or only sounds (a chime, a tone)
                spoken, self.speaking, self.voiced = self.voiced, False, False
                ducked_at = self.ducked_at
                self.unduck()
                await self.bus.emit("speech.finished", {"interrupted": not finished, "spoken": spoken})
                if not finished:
                    self._clear_queue()
                elif ducked_at and not self.seg.capturing:
                    # Someone started talking over his last words (he'd only lowered his voice) and he finished:
                    # keep what they said from its start, not just from now (2026-11-16: the start of a question
                    # was lost, and Claude passed on what was left).
                    self.seg.start("follow_up", lookback=0.4 + time.monotonic() - ducked_at)
                    await self.bus.emit("heard.started", {"reason": "follow_up"})

    def _mouth(self, level: float) -> None:
        """How loud Pip's voice is right now, ~10 times a second, for the face's mouth (never traced)."""
        self._level_n += 1
        if self._level_n % 2 == 0:
            asyncio.ensure_future(self.bus.emit("speech.level", {"level": round(level, 2)}))

    def _clear_queue(self) -> None:
        while not self.queue.empty():
            kind, item = self.queue.get_nowait()
            if kind == "speech":
                item.cancel()

    async def stop_speaking(self) -> None:
        self.stops += 1
        self._clear_queue()
        await self.speaker.stop()
        if self.room_speaker is not None and self.room_speaker is not self.speaker:
            await self.room_speaker.stop()   # (a ring plays in the room too while a phone has Pip)

    async def on_say(self, msg: Message):
        text = msg.private.get("text", "").strip()
        if text:
            fut = asyncio.get_running_loop().run_in_executor(None, self.voice.speak, text)
            await self.queue.put(("speech", fut))
        return {"ok": bool(text)}

    async def on_chime(self, msg: Message):
        await self.queue.put(("sound", self.chime))
        return {"ok": True}

    async def on_tone(self, msg: Message):
        tone = self.tones.get(msg.data.get("name", ""))
        if tone is not None and not self.speaking:  # never over Pip's own voice
            await self.queue.put(("sound", tone))
        return {"ok": tone is not None}

    async def on_stop(self, msg: Message):
        await self.stop_speaking()
        return {"ok": True}

    async def on_ring(self, msg: Message):
        await self.stop_ring()
        tone = ringtone(msg.data.get("tone", "classic"), self.voice.rate)
        self.ring_task = asyncio.create_task(self._ring(tone, float(msg.data.get("max_seconds", 330))))
        self.bus.status["ringing"] = True
        self._hear_room_while_ringing()
        return {"ok": True}

    async def _ring(self, tone, max_seconds: float) -> None:
        """Loop the ringtone. Spoken announcements queue between rounds; never more than max_seconds."""
        end = time.monotonic() + max_seconds
        secs = len(tone) / self.voice.rate
        try:
            while time.monotonic() < end:
                if self.queue.empty() and not self.speaking:
                    await self.queue.put(("ring", tone))
                await asyncio.sleep(secs + 0.6)
        finally:
            self.bus.status["ringing"] = False

    async def stop_ring(self) -> None:
        if self.ring_task and not self.ring_task.done():
            self.ring_task.cancel()
            await self.stop_speaking()
        self.ring_task = None
        self.bus.status["ringing"] = False
        self._stop_hearing_room()

    # ---------------------------------------------------------------- a phone has Pip (D-149)
    def refuse_phone(self) -> str:
        """Why a phone may not take over now ("" = it may): not while listening is switched off (owner, D-149)."""
        return "" if self.mic_mode == "on" else "listening_off"

    async def on_route(self, route: str, why: str) -> None:
        """A phone took over ("phone") or let go ("room"): stop what he was saying, drop anything half-heard,
        and hear and speak in the new place, with its own loudness levels."""
        self.route = route
        await self.stop_speaking()
        self.seg.cancel()
        self.follow_until = 0.0
        self.unduck()
        self.speaker = self.remote.speaker if route == "phone" else self.room_speaker
        self.seg.speech_dbfs, self.cfg["barge_in_dbfs"] = self.levels[route]
        self.bus.status["route"] = route
        log.info("hearing and speaking %s (%s)", "on a phone" if route == "phone" else "in the room", why)
        await self.bus.emit("ears.route", {"to": route, "why": why})
        if route == "phone":
            self._hear_room_while_ringing()
        else:
            self._stop_hearing_room()

    def _hear_room_while_ringing(self) -> None:
        """While an alarm rings and a phone has Pip, the room's microphone is mixed in (frames())."""
        if self.route == "phone" and self.ring_task is not None and self.room_task is None:
            self.room_task = asyncio.create_task(self._room_while_ringing())

    async def _room_while_ringing(self) -> None:
        room = Microphone().frames()
        try:
            async for frame in room:
                self.room_frames.append(frame)
        finally:
            await room.aclose()

    def _stop_hearing_room(self) -> None:
        if self.room_task is not None:
            self.room_task.cancel()
            self.room_task = None
        self.room_frames.clear()

    async def on_ring_stop(self, msg: Message):
        await self.stop_ring()
        return {"ok": True}

    async def on_mic(self, msg: Message):
        mode = msg.data.get("mode", "on")
        if mode not in ("on", "claps_only", "off"):
            return {"error": "unknown mode"}
        if mode != "on":
            self.seg.cancel()          # anything half-heard is dropped, never sent
            self.follow_until = 0.0
            if self.route == "phone":  # listening switched off: the phone lets go too (owner, D-149)
                await self.remote.end("listening_off")
        if mode != self.mic_mode:
            self.claps.reset()
        self.mic_mode = mode
        self.bus.status["mic"] = mode
        return {"ok": True}

    async def on_listen(self, msg: Message):
        secs = float(msg.data.get("seconds", 8))
        if secs <= 0:   # stop listening for a follow-up (told to be quiet)
            self.follow_until = 0.0
            return {"ok": True}
        self.follow_until = time.monotonic() + secs
        if self.seg.capturing and self.seg.capture["reason"] == "room":
            self.seg.capture["reason"] = "follow_up"  # they already started talking: that's for Pip
            await self.bus.emit("heard.started", {"reason": "follow_up"})
        return {"ok": True}


async def main() -> None:
    bus, cfg = make_bus("ears")
    ears = Ears(bus, cfg)
    bus.on("ears.say", ears.on_say)
    bus.on("ears.chime", ears.on_chime)
    bus.on("ears.stop", ears.on_stop)
    bus.on("ears.tone", ears.on_tone)
    bus.on("ears.listen", ears.on_listen)
    bus.on("ears.mic", ears.on_mic)
    bus.on("ears.clap_mic", ears.on_clap_mic)
    bus.on("ears.mics", ears.on_mics)
    bus.on("ears.ring", ears.on_ring)
    bus.on("ears.ring_stop", ears.on_ring_stop)
    bus.status.update(activity="listening", route="room")
    if cfg["ears"].get("barge_in_voice_check", True):
        voice_seconds_sync(b"\0\0" * 1600)   # load the voice detector now, not the first time he's talked over
    log.info("listening (for Pip's name in what's said, D-134)")
    await serve(bus, ears.listen, ears.speak_loop, *([ears.remote.serve] if ears.remote else []))


def run() -> None:
    start("ears", main)


if __name__ == "__main__":
    run()
