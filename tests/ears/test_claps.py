"""The two-clap switch (ears/clap.py, D-91): two claps are heard; other sounds, and the owner's own recordings, are not."""
import numpy as np
import pytest

from pip_assistant.ears.clap import ClapDetector

R = 16000


rng = np.random.default_rng(1)


def quiet(s, lvl=30):
    return rng.normal(0, lvl, int(R * s))


def clap(amp=12000, tau=0.025, secs=0.25):
    n = int(R * secs)
    t = np.arange(n) / R
    return rng.normal(0, 1, n) * amp * np.exp(-t / tau)


def talking(s, amp=4000):
    n = int(R * s)
    t = np.arange(n) / R
    return rng.normal(0, 1, n) * amp * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))


def claps_heard(*parts, room=30) -> int:
    sig = np.concatenate(parts)
    sig = sig + rng.normal(0, room, len(sig))          # the room's own sound carries on under everything
    d, sig, hits = ClapDetector(), np.clip(sig, -32767, 32767).astype(np.int16), 0
    for i in range(0, len(sig) - 1280, 1280):
        hits += d.feed(sig[i:i + 1280])
    return hits


def test_two_claps_are_heard():
    assert claps_heard(quiet(1), clap(), quiet(0.15), clap(), quiet(1.2)) == 1
    assert claps_heard(quiet(1), clap(), quiet(0.2), clap(), quiet(1.2), room=300) == 1      # noisier room
    assert claps_heard(quiet(1), clap(), quiet(1.0), clap(), quiet(1.2)) == 1                    # a slower pair


@pytest.mark.parametrize("name,parts", [
    ("one clap", lambda: (quiet(1), clap(), quiet(2))),
    ("two claps too far apart", lambda: (quiet(1), clap(), quiet(1.6), clap(), quiet(1.5))),
    ("three claps", lambda: (quiet(1), clap(), quiet(0.15), clap(), quiet(0.15), clap(), quiet(1.5))),
    ("quieter taps", lambda: (quiet(1), clap(2500), quiet(0.2), clap(2500), quiet(1.5))),
    ("talking", lambda: (quiet(1), talking(3, 9000), quiet(1.5))),
    ("a door (rings on)", lambda: (quiet(1), clap(20000, tau=0.15, secs=0.8), quiet(0.2), clap(20000, tau=0.15, secs=0.8), quiet(1.5))),
])
def test_other_sounds_are_not_two_claps(name, parts):
    # Tuned on the owner's Session F recordings (D-91); these are the same rules on made-up sounds.
    assert claps_heard(*parts()) == 0, name


F_FILES = __import__("pathlib").Path.home() / "pip-data" / "m0" / "recordings"


@pytest.mark.skipif(not (F_FILES / "F001.wav").exists(), reason="the owner's Session F recordings are only on the Pi")
def test_the_owners_session_f_recordings():
    """D-91 tuning: every double clap (F001–F007) heard; a single clap, tongue clicks, snaps, knocks, 3+ claps,
    talking and a door (F008–F014) not. (Recordings stay on the Pi, D-72.)"""
    import wave

    def fires(n):
        with wave.open(str(F_FILES / f"F{n:03d}.wav")) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = np.concatenate([x, np.zeros(16000, np.int16)])
        d = ClapDetector()
        return sum(d.feed(x[i:i + 1280]) for i in range(0, len(x), 1280))
    assert [fires(n) for n in range(1, 15)] == [1] * 7 + [0] * 7
