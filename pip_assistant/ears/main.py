"""pip-ears: listens all the time, hears the wake words, speaks, and stops instantly when talked over.

Events it sends:   heard.wake {word, score} · speech.started · speech.finished {interrupted}
                   ears.status {state}  (listening / speaking / muted_or_quiet)
Commands it takes: ears.say (private.text) · ears.chime · ears.stop
"""
import asyncio
import logging
import time

from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .audio import FRAME, Microphone, Speaker, chime, level_dbfs
from .voice import make_voice
from .wake import WakeDetector

log = logging.getLogger("pip-ears")


class Ears:
    def __init__(self, bus, cfg: dict):
        e = cfg["ears"]
        self.bus = bus
        self.cfg = e
        self.mic = Microphone()
        self.speaker = Speaker()
        self.wake = WakeDetector(e["wake_words"], thresholds=e["wake_thresholds"])
        self.voice = make_voice(e["voice_engine"], e["voice"], e["speech_rate"])
        self.chime = chime(self.voice.rate)
        self.speaking = False
        self.loud_frames = 0
        self.silent_since = None
        self.state = "listening"
        self.say_lock = asyncio.Lock()

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
                    await self.speaker.stop()
                    await self.bus.emit("speech.interrupted", {"level_dbfs": db})
            hit = await loop.run_in_executor(None, self.wake.feed, frame)
            if hit:
                word, score = hit
                log.info("heard wake word %s (%.2f)", word, score)
                if self.speaking:
                    await self.speaker.stop()
                await self.bus.emit("heard.wake", {"word": word, "score": score})

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
    async def say(self, text: str) -> bool:
        async with self.say_lock:
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            audio = await loop.run_in_executor(None, self.voice.speak, text)
            self.speaking = True
            await self.bus.emit("speech.started", {"chars": len(text), "synth_s": round(time.perf_counter() - t0, 2)})
            try:
                finished = await self.speaker.play(audio, self.voice.rate)
            finally:
                self.speaking = False
            await self.bus.emit("speech.finished", {"interrupted": not finished})
            return finished

    async def on_say(self, msg: Message):
        text = msg.private.get("text", "").strip()
        if text:
            asyncio.create_task(self.say(text))
        return {"ok": bool(text)}

    async def play_chime(self) -> None:
        async with self.say_lock:  # chime and speech take turns, never cut each other off
            await self.speaker.play(self.chime, self.voice.rate)

    async def on_chime(self, msg: Message):
        asyncio.create_task(self.play_chime())
        return {"ok": True}

    async def on_stop(self, msg: Message):
        await self.speaker.stop()
        return {"ok": True}


async def main() -> None:
    bus, cfg = make_bus("ears")
    ears = Ears(bus, cfg)
    bus.on("ears.say", ears.on_say)
    bus.on("ears.chime", ears.on_chime)
    bus.on("ears.stop", ears.on_stop)
    bus.status.update(activity="listening", wake_words=cfg["ears"]["wake_words"])
    log.info("listening for %s", ", ".join(cfg["ears"]["wake_words"]))
    await serve(bus, ears.listen)


def run() -> None:
    start("ears", main)


if __name__ == "__main__":
    run()
