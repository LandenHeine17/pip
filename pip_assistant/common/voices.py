"""Pip's voices (Piper, D-73): which are installed, and downloading a new one when it's chosen.

A voice is two files in ~/pip-data/models/voices: <name>.onnx (the voice) and <name>.onnx.json (its
settings). They come from the Piper voices collection on Hugging Face, where a name like
en_US-lessac-medium lives at en/en_US/lessac/medium/. Used by the Settings page (core/settingsapi.py)
and by deploy/install.sh, so both download the same way.
"""
import re
from pathlib import Path

from . import paths

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
NAME = re.compile(r"(?P<lang>[a-z]{2,3})_(?P<region>[A-Z]{2})-(?P<voice>[A-Za-z0-9_]+)-(?P<quality>x_low|low|medium|high)")

# A short list of English voices to choose from on the Settings page (any Piper voice name works).
KNOWN_VOICES = {
    "en_US-lessac-medium": "Lessac (US, female; Pip's first voice)",
    "en_US-amy-medium": "Amy (US, female)",
    "en_US-hfc_female-medium": "HFC female (US)",
    "en_US-ryan-medium": "Ryan (US, male)",
    "en_US-joe-medium": "Joe (US, male)",
    "en_US-hfc_male-medium": "HFC male (US)",
    "en_GB-alan-medium": "Alan (British, male)",
    "en_GB-cori-medium": "Cori (British, female)",
    "en_GB-northern_english_male-medium": "Northern English male (British)",
}


class VoiceError(RuntimeError):
    pass


def voices_dir() -> Path:
    return paths.data_dir() / "models" / "voices"


def valid_name(name: str) -> bool:
    return bool(NAME.fullmatch(name or ""))


def url(name: str) -> str:
    """Where the voice's .onnx lives (add ".json" for its settings file)."""
    m = NAME.fullmatch(name or "")
    if not m:
        raise VoiceError(f"'{name}' isn't a Piper voice name (like en_US-lessac-medium).")
    lang_region = f"{m['lang']}_{m['region']}"
    return f"{BASE_URL}/{m['lang']}/{lang_region}/{m['voice']}/{m['quality']}/{name}.onnx"


def installed_voices() -> list[str]:
    """Voices with both files downloaded."""
    d = voices_dir()
    if not d.is_dir():
        return []
    return sorted(f.name[:-5] for f in d.glob("*.onnx") if (d / f"{f.name}.json").exists())


def choices() -> list[str]:
    """For the Settings page: the known voices, then any other installed ones."""
    return list(KNOWN_VOICES) + [v for v in installed_voices() if v not in KNOWN_VOICES]


def _download(src: str, dest: Path, timeout: float = 300.0) -> None:
    """Download src to dest through dest.part, so a broken download never looks like a voice."""
    import httpx
    part = dest.with_name(dest.name + ".part")
    try:
        with httpx.stream("GET", src, follow_redirects=True, timeout=timeout) as r:
            if r.status_code != 200:
                raise VoiceError(f"Couldn't download {dest.name} (the voices site said {r.status_code}).")
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        part.rename(dest)
    except httpx.HTTPError as e:
        raise VoiceError(f"Couldn't download {dest.name} ({type(e).__name__}): is the internet up?") from e
    finally:
        part.unlink(missing_ok=True)


def ensure_voice(name: str, download=_download) -> Path:
    """The voice's .onnx file, downloading it (and its .onnx.json) first if it's missing. Raises VoiceError."""
    src = url(name)
    d = voices_dir()
    d.mkdir(parents=True, exist_ok=True)
    model = d / f"{name}.onnx"
    for dest, link in ((d / f"{name}.onnx.json", src + ".json"), (model, src)):
        if not dest.exists():
            download(link, dest)
    return model
