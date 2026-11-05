"""pip-ears: listens all the time, hears the wake words, captures what's said, speaks, and stops
instantly when talked over.

Events it sends:   heard.wake {word, score} · heard.started {reason} · heard.utterance {reason, seconds, had_speech} (+ private audio)
                   speech.started · speech.interrupted · speech.finished {interrupted}
                   ears.status {state}  (listening / speaking / muted_or_quiet)
Commands it takes: ears.say (private.text) · ears.chime · ears.stop · ears.listen {seconds} (follow-up window)
"""
import asyncio
import base64
import logging
import time

from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .audio import Microphone, Speaker, chime, level_dbfs
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
        self.seg = Segmenter(speech_dbfs=li["speech_dbfs"], end_silence_s=li["end_silence_seconds"],
                             max_s=li["max_utterance_seconds"], wait_for_speech_s=li["wait_for_speech_seconds"],
                             after_wake_s=li["after_wake_seconds"])
        self.transcript_wake = li["transcript_wake"]
        self.queue: asyncio.Queue = asyncio.Queue()
        self.speaking = False
        self.loud_frames = 0
        self.follow_until = 0.0
        self.silent_since = None
        self.state = "listening"

    # ---------------------------------------------------------------- listening
    async def listen(self) -> None:
        loop = asyncio.get_running_loop()
        async for frame in self.mic.frames():
            db = level_dbfs(frame)
            self.bus.status["level_dbfs"] = db
            await self._update_state(frame)
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
                    self.seg.start("room", lookback=0.4)  # transcribed to spot Pip's name, like Ziggletron
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
        state = "speaking" if self.speaking else (
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
            first = not self.speaking
            self.speaking = True
            if first and kind == "speech":
                await self.bus.emit("speech.started", {})
            finished = await self.speaker.play(audio, self.voice.rate)
            if not finished or self.queue.empty():
                self.speaking = False
                await self.bus.emit("speech.finished", {"interrupted": not finished})
                if not finished:
                    self._clear_queue()

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

    async def on_stop(self, msg: Message):
        await self.stop_speaking()
        return {"ok": True}

    async def on_listen(self, msg: Message):
        self.follow_until = time.monotonic() + float(msg.data.get("seconds", 8))
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
    bus.on("ears.listen", ears.on_listen)
    bus.status.update(activity="listening", wake_words=cfg["ears"]["wake_words"])
    log.info("listening for %s", ", ".join(cfg["ears"]["wake_words"]))
    await serve(bus, ears.listen, ears.speak_loop)


def run() -> None:
    start("ears", main)


if __name__ == "__main__":
    run()
