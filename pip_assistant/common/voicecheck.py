"""Is there a human voice in this sound? (D-85)

Speech-to-text models "hear" words in noise: a click or a soft hiss comes back as "Thank you."
They also give no usable "this wasn't speech" signal: Groq always reports no_speech_prob = 0, and the
phantom "Thank you." scores as confidently as real speech (test stt-no-speech, 2026-11-05).
So before sending a capture to speech-to-text, Pip checks it with Silero VAD, a small voice detector
that runs on the Pi in about 10 ms. In the test it found no voice in any of 12 noises (click, knock,
hiss, breath, beep) and found it in all 56 real speech clips, even quiet one-word "yes" or "thank you".
A real "thank you" still gets through; only sounds without a voice are dropped.

    seconds = await voice_seconds(pcm_int16_16k)   # 0.0 for noise
"""
import asyncio

import numpy as np

_vad = None


def _load():
    global _vad
    if _vad is None:
        from faster_whisper.vad import VadOptions, get_speech_timestamps  # ships with the offline Whisper (D-83)
        _vad = (get_speech_timestamps, VadOptions(threshold=0.5, min_speech_duration_ms=100, min_silence_duration_ms=300))
    return _vad


def voice_seconds_sync(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    find, opts = _load()
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    return sum(t["end"] - t["start"] for t in find(audio, opts)) / 16000


async def voice_seconds(pcm: bytes) -> float:
    return await asyncio.get_running_loop().run_in_executor(None, voice_seconds_sync, pcm)
