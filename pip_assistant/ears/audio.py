"""Microphone in and speaker out, through PipeWire (the speakerphone is the default device).

The speakerphone cancels Pip's own voice from the microphone (measured in M0), so speech heard
while Pip talks is a person talking over him.
"""
import asyncio
import contextlib
import math
import shutil

import numpy as np

RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz: the size openWakeWord expects


def level_dbfs(frame: np.ndarray) -> float:
    """Loudness of a frame in dBFS (0 = maximum, about −96 = silence)."""
    if not len(frame):
        return -96.0
    rms = math.sqrt(float(np.mean(frame.astype(np.float64) ** 2)))
    return round(20 * math.log10(rms / 32768), 1) if rms > 0 else -96.0


def chime(rate: int = 22050) -> np.ndarray:
    """A short, friendly two-note chime (int16)."""
    def tone(freq, secs):
        t = np.arange(int(rate * secs)) / rate
        env = np.minimum(1, t / 0.01) * np.exp(-t * 9)
        return 0.32 * np.sin(2 * np.pi * freq * t) * env
    sound = np.concatenate([tone(880, 0.12), tone(1320, 0.22)])
    return (sound * 32767).astype(np.int16)


RINGTONES = ("classic", "kitchen", "chimes", "gentle")


def ringtone(name: str = "classic", rate: int = 22050) -> np.ndarray:
    """One round of an alarm sound (int16), about 2 seconds; pip-ears loops it with a short gap (FEAT-040)."""
    def tone(freq, secs, vol=0.3, decay=0.0):
        t = np.arange(int(rate * secs)) / rate
        env = np.minimum(1, t / 0.008) * np.minimum(1, (secs - t) / 0.02)
        if decay:
            env = env * np.exp(-t * decay)
        return vol * np.sin(2 * np.pi * freq * t) * env

    def gap(secs):
        return np.zeros(int(rate * secs))
    if name == "kitchen":     # quick beeps, like a kitchen timer
        parts = [p for _ in range(4) for p in (tone(2000, 0.09), gap(0.06), tone(2000, 0.09), gap(0.26))]
    elif name == "chimes":    # three falling bell notes
        parts = [tone(f, 0.55, 0.32, 4.5) for f in (1047, 880, 659)] + [gap(0.3)]
    elif name == "gentle":    # soft notes that rise
        parts = [p for f in (523, 659, 784, 1047) for p in (tone(f, 0.35, 0.22, 3.0), gap(0.08))]
    else:                     # classic: two-tone alarm clock
        parts = [p for _ in range(3) for p in (tone(988, 0.16), gap(0.04), tone(784, 0.16), gap(0.3))]
    return (np.concatenate(parts) * 32767).astype(np.int16)


class Microphone:
    """Yields 80 ms int16 frames from the default microphone. Reopens if the device goes away."""

    def __init__(self, command=None):
        self.command = command or [shutil.which("pw-record") or "pw-record", "--rate", str(RATE),
                                   "--channels", "1", "--format", "s16", "-"]
        self.proc = None

    async def frames(self):
        while True:
            self.proc = await asyncio.create_subprocess_exec(*self.command, stdout=asyncio.subprocess.PIPE,
                                                             stderr=asyncio.subprocess.DEVNULL)
            try:
                while True:
                    data = await self.proc.stdout.readexactly(FRAME * 2)
                    yield np.frombuffer(data, np.int16)
            except asyncio.IncompleteReadError:
                pass  # device gone or recorder stopped: reopen
            finally:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
            await asyncio.sleep(1)


class Speaker:
    """Plays int16 audio through the default output. stop() cuts it off immediately."""

    def __init__(self, player=None):
        self.player = player or shutil.which("pw-play") or "pw-play"
        self.proc = None
        self._stopped = False

    @property
    def playing(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def play(self, audio: np.ndarray, rate: int) -> bool:
        """Play to the end (True) or until stop() is called (False)."""
        await self.stop()
        self._stopped = False
        self.proc = await asyncio.create_subprocess_exec(
            self.player, "--raw", "--rate", str(rate), "--channels", "1", "--format", "s16", "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        raw = audio.astype(np.int16).tobytes()
        chunk = rate // 20 * 2  # 50 ms pieces, so stopping is quick
        try:
            for i in range(0, len(raw), chunk):
                if self._stopped:
                    break
                self.proc.stdin.write(raw[i:i + chunk])
                await self.proc.stdin.drain()
            if not self._stopped:
                self.proc.stdin.close()
                await self.proc.wait()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            done = not self._stopped
            if self.proc and self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
            self.proc = None
        return done

    async def stop(self) -> None:
        self._stopped = True
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
