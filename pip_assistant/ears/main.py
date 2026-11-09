"""pip-ears: listens all the time, hears the wake words, captures what's said, speaks, and stops
instantly when talked over.

Events it sends:   heard.wake {word, score} · heard.started {reason} · heard.utterance {reason, seconds, had_speech, word} (+ private audio)
                   speech.started · speech.interrupted · speech.finished {interrupted, spoken}
                   speech.level {level 0–1} (~10 a second while he talks, for the face's mouth; not traced)
                   ears.status {state}  (listening / speaking / muted_or_quiet / mic_off)
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
"""
import asyncio
import base64
import logging
import time

from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .audio import Microphone, Speaker, chime, level_dbfs, mic_tone, microphones, ringtone, thinking_tone
from .clap import ClapDetector
from .segmenter import Segmenter
from .voice import make_voice
from .wake import WakeDetector

log = logging.getLogger("pip-ears")


class Ears:
    def __init__(self, bus, cfg: dict):
        e, li = cfg["ears"], cfg["listen"]
        self.bus = bus
        self.cfg = e
        self.mic = Microphone()
        self.speaker = Speaker()
        self.wake = WakeDetector(e["wake_words"], thresholds=e["wake_thresholds"])
        self.voice = make_voice(e["voice_engine"], e["voice"], e["speech_rate"])
        self.chime = chime(self.voice.rate)
        self.tones = {"thinking": thinking_tone(self.voice.rate), "mic_on": mic_tone(True, self.voice.rate),
                      "mic_off": mic_tone(False, self.voice.rate)}
        self.mic_mode = "off"      # until pip-core says (D-91)
        self.claps = ClapDetector(cfg.get("mic", {}))
        self.clap_mic = ""         # which microphone listens for claps (pip-core says; "" = none, D-91)
        self.clap_task: asyncio.Task | None = None
        self.claps_live = False    # False: trial (only reported), until "Two claps switch listening" is ticked
        self.quiet_after = 0.0     # Pip's own sound reaches the clap microphone: ignore it until a moment after
        self.seg = Segmenter(speech_dbfs=li["speech_dbfs"], end_silence_s=li["end_silence_seconds"],
                             max_s=li["max_utterance_seconds"], wait_for_speech_s=li["wait_for_speech_seconds"],
                             after_wake_s=li["after_wake_seconds"])
        self.transcript_wake = li["transcript_wake"]
        self.queue: asyncio.Queue = asyncio.Queue()
        self.speaking = False
        self.voiced = False          # his voice has played in the current run of sounds (speech.started sent)
        self.loud_frames = 0
        self.follow_until = 0.0
        self.silent_since = None
        self.state = "listening"
        self.ring_task: asyncio.Task | None = None
        self._level_n = 0          # counts mouth-level readings, to send every other one

    # ---------------------------------------------------------------- the clap microphone (D-91)
    async def listen_for_claps(self, device: str) -> None:
        self.claps.reset()
        async for frame in Microphone(target=device).frames():
            busy = self.speaking or self.ring_task is not None
            if busy:
                self.quiet_after = time.monotonic() + 0.5
            if self.mic_mode == "off" or busy or time.monotonic() < self.quiet_after:
                self.claps.reset()   # switched off on the dashboard, or it's Pip's own sound
                continue
            if self.claps.feed(frame):
                if self.claps_live:
                    log.info("heard two claps")
                    await self.bus.emit("mic.clapped", {})
                else:   # trial: the clap switch isn't ticked yet, just show it was heard (D-91)
                    log.info("heard two claps (trial)")
                    await self.bus.emit("mic.clapped_trial", {})

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

    async def on_mics(self, msg: Message):
        mics = await asyncio.get_running_loop().run_in_executor(None, microphones)
        return {"mics": mics, "speakerphone": next((m["name"] for m in mics if "Speaker_Phone" in m["name"]), "")}

    # ---------------------------------------------------------------- listening
    async def listen(self) -> None:
        loop = asyncio.get_running_loop()
        async for frame in self.mic.frames():
            if self.mic_mode == "off":
                await self._update_state(frame)
                continue  # switched off on the dashboard: nothing listens
            db = level_dbfs(frame)
            self.bus.status["level_dbfs"] = db
            await self._update_state(frame)
            if self.mic_mode != "on":
                continue  # switched off by claps: only the clap microphone is listened to
            if self.speaking and self.cfg["barge_in"]:
                # The speakerphone removes Pip's own voice, so sound now is someone talking over him.
                self.loud_frames = self.loud_frames + 1 if db > self.cfg["barge_in_dbfs"] else 0
                if self.loud_frames >= 3:  # ~240 ms of speech
                    self.loud_frames = 0
                    await self.stop_speaking()
                    await self.bus.emit("speech.interrupted", {"level_dbfs": db})
                    self.seg.start("barge_in", lookback=0.4)
            elif not self.seg.capturing and self.seg.is_speech(db) and not self.speaking:
                if time.monotonic() < self.follow_until:
                    self.follow_until = 0.0
                    self.seg.start("follow_up", lookback=0.4)
                    await self.bus.emit("heard.started", {"reason": "follow_up"})
                elif self.transcript_wake:
                    self.seg.start("room", lookback=0.4)  # everything heard is transcribed, like Ziggletron (D-92)
                    await self.bus.emit("heard.started", {"reason": "room"})
            utt = self.seg.feed(frame, db)
            if utt and (utt.reason != "room" or utt.seconds >= 0.5):
                await self._send_utterance(utt)
            hit = await loop.run_in_executor(None, self.wake.feed, frame)
            if hit:
                word, score = hit
                log.info("heard wake word %s (%.2f)", word, score)
                await self.stop_speaking()
                await self.bus.emit("heard.wake", {"word": word, "score": score})
                if self.seg.capturing and self.seg.capture["reason"] in ("room", "follow_up"):
                    c = self.seg.capture  # the same speech: now it's a wake-word request
                    c["reason"], c["word"], c["after"] = "wake", word, c["had_speech"]
                else:
                    self.seg.start("wake", word=word, lookback="phrase")

    async def _send_utterance(self, utt) -> None:
        data = {"reason": utt.reason, "seconds": utt.seconds, "had_speech": utt.had_speech, "word": utt.word}
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
            try:
                audio = await item if kind == "speech" else item
            except asyncio.CancelledError:
                continue
            if audio is None or not len(audio):
                continue
            self.speaking = True
            if kind == "speech" and not self.voiced:   # his voice starts (even right after a chime or tone)
                self.voiced = True
                await self.bus.emit("speech.started", {})
            finished = await self.speaker.play(audio, self.voice.rate,
                                               on_level=self._mouth if kind == "speech" else None)
            if not finished or self.queue.empty():
                # spoken: whether his voice played in this run, or only sounds (a chime, a tone)
                spoken, self.speaking, self.voiced = self.voiced, False, False
                await self.bus.emit("speech.finished", {"interrupted": not finished, "spoken": spoken})
                if not finished:
                    self._clear_queue()

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
        self._clear_queue()
        await self.speaker.stop()

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
        return {"ok": True}

    async def _ring(self, tone, max_seconds: float) -> None:
        """Loop the ringtone. Spoken announcements queue between rounds; never more than max_seconds."""
        end = time.monotonic() + max_seconds
        secs = len(tone) / self.voice.rate
        try:
            while time.monotonic() < end:
                if self.queue.empty() and not self.speaking:
                    await self.queue.put(("sound", tone))
                await asyncio.sleep(secs + 0.6)
        finally:
            self.bus.status["ringing"] = False

    async def stop_ring(self) -> None:
        if self.ring_task and not self.ring_task.done():
            self.ring_task.cancel()
            await self.stop_speaking()
        self.ring_task = None
        self.bus.status["ringing"] = False

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
    bus.status.update(activity="listening", wake_words=cfg["ears"]["wake_words"])
    log.info("listening for %s", ", ".join(cfg["ears"]["wake_words"]))
    await serve(bus, ears.listen, ears.speak_loop)


def run() -> None:
    start("ears", main)


if __name__ == "__main__":
    run()
