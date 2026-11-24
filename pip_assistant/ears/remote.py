"""A phone (or any browser) as Pip's microphone and speaker (D-149).

The /phone page sends what its microphone hears and plays what Pip says. While it has Pip, the room's
speakerphone isn't listened to and Pip speaks only on the phone (pip-ears switches over, ears/main.py);
alarms ring in both places. Everything heard goes through exactly the same steps as in the room.

    phone (/phone page)  ⇄ websocket /api/phone ⇄  pip-web  ⇄ Unix socket ~/pip-data/run/ears-remote.sock ⇄  pip-ears

pip-web only passes messages along (it keeps nothing); how they travel on the socket is in common/wire.py.
Phone → Pip: sound = 16 kHz mono 16-bit PCM (any length; cut here into 80 ms frames).
Pip → phone: sound = 4 bytes sample rate (little-endian) + 16-bit mono PCM; text = {"hello": {}} once it has Pip,
{"flush": true} (drop what's queued: he was stopped), {"bye": why} (it doesn't have Pip any more:
"moved" another phone took over, "idle" nobody talked for a while, "no_audio" its sound stopped coming
(the phone locked), "listening_off" listening is switched off in the room).

Nothing heard is kept here; frames go to the same segmenter as the room's.
"""
import asyncio
import contextlib
import json
import logging
import os
import struct
import time

import numpy as np

from ..common.wire import SOUND, TEXT, pack, read_message
from .audio import FRAME

log = logging.getLogger("pip-ears")


class Phone:
    """One phone's connection."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.open = True
        self.why = ""   # why it was let go, if Pip let go of it

    async def send(self, kind: int, payload: bytes) -> None:
        if not self.open:
            return
        try:
            self.writer.write(pack(kind, payload))
            await self.writer.drain()
        except (ConnectionError, RuntimeError):
            self.open = False

    async def say(self, **msg) -> None:
        await self.send(TEXT, json.dumps(msg).encode())

    async def close(self, why: str = "") -> None:
        if why and self.open:
            self.why = self.why or why
            await self.say(bye=why)
        self.open = False
        with contextlib.suppress(Exception):
            self.writer.close()


class RemoteLink:
    """The phone that has Pip, if any. `on_change(route, why)` is called when one takes over ("phone") or lets go
    ("room"); `refuse()` says why one may not take over now ("" = it may)."""

    def __init__(self, path, on_change, refuse, no_audio_s: float = 4.0, idle_s: float = 600.0):
        self.path = str(path)
        self.on_change, self.refuse = on_change, refuse
        self.no_audio_s, self.idle_s = no_audio_s, idle_s
        self.phone: Phone | None = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=50)   # 4 s of frames; older ones go if it backs up
        self.buffer = b""
        self.sound_at = self.speech_at = 0.0
        self.speaker = RemoteSpeaker(self)

    @property
    def connected(self) -> bool:
        return self.phone is not None and self.phone.open

    async def serve(self) -> None:
        """Listen for pip-web (runs for as long as pip-ears does)."""
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
        server = await asyncio.start_unix_server(self._client, path=self.path)
        os.chmod(self.path, 0o600)
        async with server:
            await asyncio.gather(server.serve_forever(), self._watch())

    async def _client(self, reader, writer) -> None:
        phone = Phone(reader, writer)
        why = self.refuse()
        if why:
            await phone.close(why)
            return
        old, self.phone = self.phone, phone
        if old:
            await old.close("moved")   # the newest phone takes over (owner, D-149)
        self._clear()
        self.sound_at = self.speech_at = time.monotonic()
        await phone.say(hello={})
        if not old:
            await self.on_change("phone", "taken")
        try:
            while phone.open:
                msg = await read_message(reader)
                if msg is None:
                    break
                kind, payload = msg
                if kind == SOUND:
                    self._sound(payload)
        finally:
            if self.phone is phone:   # (a phone that was taken over from just goes)
                self.phone = None
                self._clear()
                self.queue.put_nowait(None)   # whoever waits for frames: the phone's gone
                await phone.close()
                await self.on_change("room", phone.why or "closed")   # "closed": the page closed, or gave Pip back

    def _sound(self, payload: bytes) -> None:
        self.sound_at = time.monotonic()
        self.buffer += payload
        size = FRAME * 2
        while len(self.buffer) >= size:
            frame, self.buffer = self.buffer[:size], self.buffer[size:]
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(np.frombuffer(frame, np.int16).copy())

    def _clear(self) -> None:
        self.buffer = b""
        while not self.queue.empty():
            self.queue.get_nowait()

    def heard_speech(self) -> None:
        """pip-ears heard speech through the phone: it isn't idle."""
        self.speech_at = time.monotonic()

    async def end(self, why: str) -> None:
        """Give Pip back to the room ("idle", "no_audio", "listening_off")."""
        if self.phone:
            await self.phone.close(why)   # its connection ends, and _client hands Pip back to the room

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not self.connected:
                continue
            now = time.monotonic()
            if now - self.sound_at > self.no_audio_s:
                log.info("the phone stopped sending sound: back to the room")
                await self.end("no_audio")
            elif now - self.speech_at > self.idle_s:
                log.info("nobody talked through the phone for a while: back to the room")
                await self.end("idle")

    async def frames(self):
        """80 ms int16 frames from the phone, until it lets go."""
        while self.connected or not self.queue.empty():
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame


class RemoteSpeaker:
    """Plays int16 audio on the phone. Same shape as audio.Speaker: play() → True if it played to the end, stop()
    cuts it off (the phone drops what it has queued), gain lowers it while Pip is being talked over (D-128)."""

    AHEAD_S = 0.2   # how far ahead of real time it sends: little, so stopping is quick

    def __init__(self, link: RemoteLink):
        self.link = link
        self.gain = 1.0
        self._stopped = False
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    async def play(self, audio: np.ndarray, rate: int, on_level=None) -> bool:
        self._stopped = False
        self._playing = True
        phone = self.link.phone
        audio = audio.astype(np.int16)
        chunk = rate // 20   # 50 ms pieces, like audio.Speaker
        start, sent = time.monotonic(), 0.0
        try:
            for i in range(0, len(audio), chunk):
                if self._stopped or phone is None or not phone.open or self.link.phone is not phone:
                    return False
                piece = audio[i:i + chunk]
                if self.gain < 0.999:
                    piece = (piece * self.gain).astype(np.int16)
                await phone.send(SOUND, struct.pack("<I", rate) + piece.tobytes())
                if on_level is not None:
                    p = audio[i:i + chunk].astype(np.float32)
                    on_level(float(min(1.0, np.sqrt(np.mean(p * p)) / 8000)) if len(p) else 0.0)
                sent += len(piece) / rate
                await self._wait_until(start + sent - self.AHEAD_S)
            await self._wait_until(start + sent)   # until the phone has played it
            return not self._stopped
        finally:
            self._playing = False

    async def _wait_until(self, t: float) -> None:
        while not self._stopped and time.monotonic() < t:
            await asyncio.sleep(min(0.05, t - time.monotonic()))

    async def stop(self) -> None:
        self._stopped = True
        phone = self.link.phone
        if phone is not None and self._playing:
            await phone.say(flush=True)
