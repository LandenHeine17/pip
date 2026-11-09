"""Hearing what was said to Pip: is there a voice in it, what are the words, and who said it.

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `Hearing`, so
they read and set its fields (`self.stt`, `self.offline_until`, …) like its own.

    has_voice      noise (a click, a cough) never reaches speech-to-text (D-85)
    transcribe     Groq first; Whisper on the Pi if Groq can't be reached (offline, D-83) or its daily
                   limit is used up (not offline, D-115). Marks Pip online/offline (core/net.py).
    identify       who's talking, from their voice (core/people.py)
    fix_names      household names spelled right ("Landon" → "Landen", D-93)
"""
import logging
import time

from ..common.messages import Message
from .stt import RateLimited

log = logging.getLogger(__name__)


class Hearing:
    def fix_names(self, text: str) -> str:
        if self.people is None:
            return text
        try:
            return self.people.fix_names(text)
        except Exception:
            return text

    async def transcribe(self, pcm: bytes, reason: str) -> tuple[str, bool]:
        """(text, offline). Groq first; if it can't be reached, Whisper on the Pi."""
        names = []
        if self.people is not None:
            try:
                names = self.people.names()
            except Exception:
                names = []
        for s in (self.stt, self.local_stt):
            if s is not None:
                s.names = names   # so Groq spells "Landen", not "Landon" (D-93)
        try:
            if self.stt is None:
                raise RuntimeError("no speech-to-text service")
            text = await self.stt.transcribe(pcm)
            if self.net is not None and not self.net.online:
                await self.net.set(True)
            await self._stt_limited(False)
            return text, False
        except RateLimited:
            # Groq's daily limit is used up (D-115). The internet and Claude still work, so this isn't offline:
            # what's said to Pip is heard by Whisper on the Pi and answered as usual; room talk waits (D-50).
            await self._stt_limited(True)
            if self.local_stt is None or (reason == "room" and not self.ringing):
                raise
            return await self.local_stt.transcribe(pcm), False
        except Exception as e:
            if self.net is not None and self.stt is not None:
                await self.net.failed()
            if self.local_stt is None or (reason == "room" and not self.ringing):
                raise  # room speech is never transcribed on the Pi (D-50), except to hear "stop" during an alarm
            await self.bus.publish(Message("event", "stt.offline", data={"error": type(e).__name__, "reason": reason}))
            self.offline_until = time.monotonic() + 120
            return await self.local_stt.transcribe(pcm), True

    async def _stt_limited(self, limited: bool) -> None:
        """A bar on the Conversation feed when Groq's limit runs out, and when it's back (D-115)."""
        if limited != self.stt_limited:
            self.stt_limited = limited
            await self.note("stt_limited" if limited else "stt_back")

    async def has_voice(self, pcm: bytes, reason: str) -> bool:
        """Noise (a click, a cough, a hiss) never reaches speech-to-text, which would hear "Thank you." (D-85)."""
        if not self.voice_check or reason == "wake":
            return True  # right after the wake word, someone was certainly talking
        from .voicecheck import voice_seconds
        try:
            secs = await voice_seconds(pcm)
        except Exception as e:  # never lose speech because the check failed
            log.warning("voice check failed: %s", e)
            return True
        if secs < self.min_voice:
            await self.bus.publish(Message("event", "heard.noise", data={"reason": reason, "voice_s": round(secs, 2)}))
            return False
        return True

    async def identify(self, pcm: bytes):
        if self.people is None:
            return None
        try:
            return await self.people.identify(pcm)
        except Exception:
            log.exception("voice recognition failed")
            return None
