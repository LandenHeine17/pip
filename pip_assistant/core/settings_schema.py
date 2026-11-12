"""The Settings page's side of settings (step 8): what every setting is, checking changes, saving the file.

Only pip-core's Settings page (core/settingsapi.py) uses this; loading settings, which every program
does, stays in common/settings.py.
  SCHEMA       every setting: its group on the page, label, explanation, type, allowed values,
               whether it's folded under Advanced, and which programs read it (they restart to apply it)
  validate()   checks changes from the page, with plain-language messages
  save()       writes ~/pip-data/settings.toml: only what differs from the defaults (old file kept as .bak)
"""
import math
import re
import tomllib
from pathlib import Path

from ..common import messages, paths
from ..common.settings import SettingsError, _defaults, _merge, load   # loading lives in common/settings.py
from .joining import INTERVALS

__all__ = ["SettingsError", "GROUPS", "RINGTONES", "MOODS", "ALL", "SCHEMA", "TYPES", "defaults", "flat", "choices",
           "module_settings", "validate", "clean", "dumps", "save"]


# ==================================================================== the Settings page's description
# Groups, in the page's order. "Personality" also holds the personality editor; "Advanced" is folded away.
GROUPS = ["Voice & sounds", "Listening", "Conversation & joining in", "Location & weather", "Brain", "Personality",
          "Advanced"]
RINGTONES = list(messages.RINGTONES)   # the ringtones pip-ears can play (one list, shared with ears/audio.py)
MOODS = list(INTERVALS)                # quiet, normal, chatty: the moods core/joining.py knows
ALL = ("core", "ears", "thinker", "web", "builder")


def _s(group, label, explain, kind, programs, *, choices=None, min=None, max=None, item=None, nonempty=False,
       pattern=None, example=""):
    """One setting. kind: str / int / float / bool / list (of text) / map (inline table, fixed names).
    choices/min/max apply to each item of a list or map. choices may be a function (looked up when needed)."""
    return {"group": group, "label": label, "explain": explain, "type": kind, "programs": tuple(programs),
            "choices": choices, "min": min, "max": max, "advanced": group == "Advanced", "nonempty": nonempty,
            "pattern": pattern, "example": example}


def _voice_choices():
    from ..common import voices
    return voices.choices()


def _wake_choices():
    """Wake-word models there are (household-tuned ones in ~/pip-data, then the repo's)."""
    found = set()
    for d in paths.wake_model_folders():   # the same folders ears/wake.py looks in
        if d.is_dir():
            found |= {f.stem for f in d.glob("*.onnx") if f.stem not in ("melspectrogram", "embedding_model")}
    return sorted(found)


V, L, C, W, B, P, A = GROUPS
SCHEMA = {
    # ---- Voice & sounds
    "ears.voice": _s(V, "Voice", "Pip's voice (a Piper voice). A new one is downloaded when you save (about 60 MB).",
                     "str", ["ears"], choices=_voice_choices),
    "ears.speech_rate": _s(V, "Talking speed", "1.0 = normal; 1.2 = a bit faster; 0.8 = slower.",
                           "float", ["ears"], min=0.5, max=2.0),
    "alarms.ringtones": _s(V, "Ringtones", "The sound for each kind of alert: timers, alarms, reminders and calendar "
                           "heads-ups.", "map", ["core"], choices=RINGTONES),
    "alarms.ring_minutes": _s(V, "Ring for", "Keep ringing this many minutes if nobody answers, then stop and note "
                              "it on the dashboard.", "float", ["core"], min=0.5, max=30),
    "alarms.announce_every_seconds": _s(V, "Say what it is every", "While ringing, say what it is again this often, "
                                        "in seconds (\"Your pasta timer is done.\").", "float", ["core"],
                                        min=10, max=300),
    "alarms.snooze_minutes": _s(V, "Snooze for", "Minutes, when you say \"snooze\" without a length.",
                                "float", ["core"], min=1, max=60),
    # ---- Listening
    "ears.wake_words": _s(L, "Wake words", "The names Pip listens for (D-74).", "list", ["ears"],
                          choices=_wake_choices, nonempty=True),
    "ears.wake_thresholds": _s(L, "Wake-word strictness", "How sure each detector must be (0–1): higher = fewer "
                               "false wakes, more misses. Pippletron is strict because \"Ziggletron\" sounds similar.",
                               "map", ["ears"], min=0.05, max=0.99),
    "ears.barge_in": _s(L, "Stop when talked over", "Stop talking straight away when someone talks over Pip.",
                        "bool", ["ears"]),
    "ears.barge_in_voice_check": _s(L, "Only a voice cuts him off", "Talked over only counts if it has a human voice "
                                    "in it, so a knock, a clap or the door doesn't stop him (D-122).", "bool", ["ears"]),
    "listen.transcript_wake": _s(L, "Hear the whole room", "Transcribe everything said in the room, like Ziggletron "
                                 "(D-92). Off: Pip only reacts to the wake word.", "bool", ["ears"]),
    "conversation.follow_up_seconds": _s(L, "Follow-up window", "After Pip answers, you can talk again within this "
                                         "many seconds without saying his name.", "float", ["core"], min=0, max=30),
    "listen.wait_for_speech_seconds": _s(L, "Wait for you to start", "In the follow-up window, how many seconds to "
                                         "wait for you to start talking.", "float", ["ears"], min=1, max=15),
    "conversation.wait_after_name_seconds": _s(L, "After just his name", "Said only his name? Pip listens quietly "
                                               "this many seconds for the rest.", "float", ["core"], min=1, max=15),
    "conversation.unfinished_wait_seconds": _s(L, "After a half-finished sentence", "Stopped mid-sentence (\"what's "
                                               "the...\", \"remind me to\")? Seconds from when you stop talking before "
                                               "Pip answers (D-122).", "float", ["core"], min=0.5, max=10),
    "conversation.turn_wait_short_seconds": _s(L, "Turn wait: question", "Seconds from when you stop talking before "
                                               "Pip answers a question or short request. The 0.7 s pause and "
                                               "speech-to-text count towards it (D-106, D-122).",
                                               "float", ["core"], min=0.5, max=5),
    "conversation.turn_wait_statement_seconds": _s(L, "Turn wait: statement", "…after something longer that ends "
                                                   "like a statement.", "float", ["core"], min=0.5, max=5),
    "conversation.turn_wait_long_seconds": _s(L, "Turn wait: story", "…after two or more sentences (you might be "
                                              "telling a story).", "float", ["core"], min=0.5, max=5),
    "conversation.turn_wait_after_interrupt_seconds": _s(L, "Turn wait: after being talked over", "Added to the "
                                                         "next turn after someone talked over him: he takes the hint.",
                                                         "float", ["core"], min=0, max=5),
    # ---- Conversation & joining in
    "social.join_in": _s(C, "Join in", "Pip listens to the room and decides for himself when to join in (D-92). "
                         "Off: he only answers when called or his name comes up.", "bool", ["core"]),
    "social.mood": _s(C, "Mood", "How often he considers joining in: quiet (every 2 min), normal (40 s), "
                      "chatty (15 s).", "str", ["core"], choices=MOODS),
    "social.quiet_hours": _s(C, "Quiet hours", "Never joins in (or says welcome back) at these times, like "
                             "23:00-07:00 (D-66). Empty = no quiet hours.", "str", ["core"],
                             pattern=r"(([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d)?", example="23:00-07:00"),
    "social.welcome_back_minutes": _s(C, "Welcome back after", "\"Hey Landen, welcome back\" after the room was "
                                      "quiet this many minutes (0 = never).", "int", ["core"], min=0, max=600),
    "conversation.max_sentences": _s(C, "Short answers", "Pip stops after this many sentences unless you asked for "
                                     "detail or a story (D-79).", "int", ["core"], min=1, max=10),
    "conversation.own_commands": _s(C, "Pip's own commands", "\"offline\": Pip's fixed-phrasing commands are used "
                                    "only without internet (online, Claude understands any wording, D-94); "
                                    "\"always\": try them first.", "str", ["core"], choices=["offline", "always"]),
    "thinker.thinking_tone": _s(C, "Thinking tone", "A soft tone when Pip starts thinking (D-86): always, slow "
                                "(only if the answer is slow) or off.", "str", ["core"],
                                choices=["always", "slow", "off"]),
    "thinker.thinking_tone_after_seconds": _s(C, "Slow means", "For \"slow\": play the tone if the answer hasn't "
                                              "started after this many seconds.", "float", ["core"], min=0.3, max=10),
    # ---- Location & weather
    "thinker.location": _s(W, "Location", "Where Pip lives (D-63): used for the weather and local answers, "
                           "like \"Lincoln, Nebraska\".", "str", ["thinker", "web"], nonempty=True),
    # ---- Brain
    "thinker.model": _s(B, "Claude model", "sonnet starts answering in ~0.9 s; opus is cleverer but ~2.3 s; "
                        "haiku is quickest.", "str", ["thinker"], choices=["sonnet", "opus", "haiku"]),
    "thinker.effort": _s(B, "Effort", "How hard Claude thinks: low is quickest (like Ziggletron).", "str", ["thinker"],
                         choices=["low", "medium", "high", "xhigh", "max"]),
    "thinker.web_search": _s(B, "Web search", "Let Claude look things up for current facts.", "bool", ["thinker"]),
    "thinker.timeout_seconds": _s(B, "Give up after", "Give up on an answer after this many seconds.",
                                  "float", ["thinker"], min=10, max=180),
    "builder.enabled": _s(B, "Self-changes", "Let Pip change his own code when asked (each change needs your "
                          "approval twice, on the Changes page).", "bool", ["builder"]),
    "builder.model": _s(B, "Claude model for changes", "Which Claude plans and builds self-changes.", "str",
                        ["builder"], choices=["sonnet", "opus", "haiku"]),
    "builder.effort": _s(B, "Effort for changes", "How hard Claude thinks while planning and building a change.",
                         "str", ["builder"], choices=["low", "medium", "high", "xhigh", "max"]),
    # ---- Personality (the editor is on the same part of the page)
    "pip.name": _s(P, "Name", "What Pip calls himself in his personality ({name}). It doesn't change the wake words "
                   "or the names he answers to.", "str", ["thinker"], nonempty=True, max=30),
    # ---- Advanced: speech-to-text, voice recognition, clap tuning, programs, trace, web
    "ears.voice_engine": _s(A, "Voice engine", "Swappable later (PIP-NEW-097); only Piper for now.", "str", ["ears"],
                            choices=["piper"]),
    "ears.barge_in_dbfs": _s(A, "Talked-over loudness", "How loud speech over Pip must be to count, in dBFS "
                             "(−96 silent … 0 max).", "float", ["ears"], min=-90, max=-5),
    "listen.speech_dbfs": _s(A, "Speech loudness", "Louder than this (dBFS) counts as speech when cutting out what "
                             "you said.", "float", ["ears"], min=-90, max=-10),
    "listen.end_silence_seconds": _s(A, "End-of-speech pause", "A pause this long (seconds) means you've finished "
                                     "speaking (Ziggletron: 0.7).", "float", ["ears"], min=0.2, max=3),
    "listen.max_utterance_seconds": _s(A, "Longest request", "Longest single request, in seconds.", "float",
                                       ["ears", "core"], min=3, max=60),
    "listen.after_wake_seconds": _s(A, "Wait after the wake word", "After the wake word, how long to wait for a "
                                    "question (else it came before the name).", "float", ["ears"], min=0.2, max=3),
    "stt.provider": _s(A, "Speech-to-text service", "Swappable (PIP-NEW-008, D-48); only Groq for now.", "str",
                       ["core"], choices=["groq"]),
    "stt.model": _s(A, "Speech-to-text model", "Groq's Whisper model.", "str", ["core"], nonempty=True),
    "stt.spare_models": _s(A, "Spare speech-to-text models", "Used when the model above has hit Groq's daily limit "
                           "(D-115).", "list", ["core"]),
    "stt.timeout_seconds": _s(A, "Speech-to-text timeout", "Seconds to wait for Groq before using the Pi's own.",
                              "float", ["core"], min=1, max=30),
    "stt.voice_check": _s(A, "Only send voices", "Only send sounds with a human voice in them to speech-to-text "
                          "(D-85: stops phantom \"Thank you.\").", "bool", ["core"]),
    "stt.min_voice_seconds": _s(A, "Least voice", "Less voice than this (seconds, except right after the wake word) "
                                "is treated as noise.", "float", ["core"], min=0, max=2),
    "stt.offline_model": _s(A, "Offline speech-to-text", "Whisper model on the Pi, used only when Groq can't be "
                            "reached (empty = none, D-83).", "str", ["core"],
                            choices=["", "tiny.en", "base.en", "small.en"]),
    "stt.word_list": _s(A, "Word list", "Words that help speech-to-text spell names and terms right.", "str", ["core"],
                        max=1000),
    "voices.model": _s(A, "Voice recognition model", "In ~/pip-data/models/speaker (D-87). Changing it means "
                       "everyone re-learns their voice.", "str", ["core"], nonempty=True),
    "voices.sure": _s(A, "Sure match", "Match this close (and clearly ahead of anyone else) = \"sure\": personal "
                      "things, private memories.", "float", ["core"], min=0, max=1),
    "voices.probably": _s(A, "Probable match", "This close = \"probably\": low-stakes personalising only. Below = "
                          "\"Someone\".", "float", ["core"], min=0, max=1),
    "voices.margin": _s(A, "Sure margin", "\"Sure\" also needs to beat the next-closest person by this much.",
                        "float", ["core"], min=0, max=0.5),
    "voices.min_seconds": _s(A, "Shortest voice to recognise", "Shorter speech (seconds) is too little to recognise "
                             "a voice.", "float", ["core"], min=0.2, max=5),
    "social.pause_seconds": _s(A, "Room pause before joining in", "The room must pause this many seconds before he "
                               "joins in.", "float", ["core"], min=0.3, max=10),
    "social.live_conversation_seconds": _s(A, "Live conversation", "He only joins in if someone spoke within this "
                                           "many seconds.", "float", ["core"], min=10, max=600),
    "alarms.missed_grace_minutes": _s(A, "Missed-alert grace", "Pip was off when it was due? Still ring if it's no "
                                      "later than this many minutes; else mark it missed.", "float", ["core"],
                                      min=0, max=240),
    "mic.clap_peak_dbfs": _s(A, "Clap loudness", "How loud a clap must be (its loudest 10 ms, dBFS).", "float",
                             ["ears"], min=-40, max=0),
    "mic.clap_jump_db": _s(A, "Clap jump", "How far (dB) above the room's sound just before it.", "float", ["ears"],
                           min=5, max=60),
    "mic.clap_max_ms": _s(A, "Clap length", "A clap is over within this (ms); anything longer (a door, talking) "
                          "isn't one.", "int", ["ears"], min=50, max=1000),
    "mic.clap_gap_min_ms": _s(A, "Shortest gap between claps", "The two claps come at least this far apart (ms).",
                              "int", ["ears"], min=50, max=1000),
    "mic.clap_gap_max_ms": _s(A, "Longest gap between claps", "…and at most this far (ms).", "int", ["ears"],
                              min=300, max=5000),
    "mic.clap_settle_ms": _s(A, "Quiet after claps", "…and then nothing more for this long (ms): three claps or "
                             "applause don't count.", "int", ["ears"], min=100, max=3000),
    "programs.expected": _s(A, "Programs", "Programs pip-core expects to hear from.", "list", ["core"],
                            choices=["ears", "thinker", "web", "builder"]),
    "programs.heartbeat_seconds": _s(A, "Heartbeat", "How often (seconds) each program says \"I'm alive\".", "float",
                                     ALL, min=0.5, max=10),
    "programs.stuck_after_seconds": _s(A, "Stuck after", "No heartbeat for this many seconds means a program is "
                                       "stuck.", "float", ["core"], min=3, max=120),
    "programs.restart_stuck": _s(A, "Restart stuck programs", "Restart a stuck program automatically (only when run "
                                 "as a service).", "bool", ["core"]),
    "trace.keep_hours": _s(A, "Keep the trace", "How many hours the decision trace (what happened, never the words) "
                           "is kept.", "int", ["core"], min=1, max=720),
    "builder.plan_minutes": _s(A, "Planning time limit", "Give up on planning a change after this many minutes.",
                               "int", ["builder"], min=2, max=60),
    "builder.build_minutes": _s(A, "Building time limit", "Give up on building a change after this many minutes.",
                                "int", ["builder"], min=5, max=120),
    "builder.keep_days": _s(A, "Keep change requests", "The words of a change request are deleted after this "
                            "many days (the record of the change stays).", "int", ["builder"], min=1, max=3650),
    "web.host": _s(A, "Dashboard address", "Address the dashboard listens on. On the Pi this is the Tailscale "
                   "address.", "str", ["web"], nonempty=True),
    "web.port": _s(A, "Dashboard port", "Port for the dashboard.", "int", ["web"], min=1024, max=65535),
    "web.tv_host": _s(A, "Monitor address", "Also listen here (same port), so the monitor's browser on the Pi works "
                      "without Tailscale.", "str", ["web"]),
}
TYPES = {"str": str, "int": int, "float": float, "bool": bool, "list": list, "map": dict}


def defaults() -> dict:
    """Every default, flat: {"ears.voice": "en_US-lessac-medium", …}."""
    return {f"{sec}.{k}": v for sec, keys in _defaults().items() for k, v in keys.items()}


def flat(cfg: dict) -> dict:
    """Merged settings (from load()) as {"section.key": value}, without [modules.*]."""
    return {f"{sec}.{k}": v for sec, keys in cfg.items() if sec != "modules" for k, v in keys.items()}


def choices(key: str, entry: dict | None = None) -> list | None:
    c = (entry or SCHEMA[key])["choices"]
    return list(c()) if callable(c) else (list(c) if c is not None else None)


# ---- module settings ([modules.<name>] keys come from each module's module.toml)
def module_settings() -> dict:
    """{"modules.calendar.sync_minutes": {module, name, default, explain, type, min}} for every module."""
    from ..common.modules import discover
    out = {}
    for info in discover({}):
        for name, default in info.settings.items():
            kind = next(t for t, py in TYPES.items() if type(default) is py)
            out[f"modules.{info.name}.{name}"] = {
                "module": info.name, "name": name, "default": default, "explain": info.explain.get(name, ""),
                "type": kind, "min": {"int": 0, "float": 0.1}.get(kind), "max": None, "choices": None,
                "nonempty": False, "pattern": None, "example": ""}
    return out


# ---- checking
def _fmt(x) -> str:
    return f"{x:g}" if isinstance(x, float) else str(x)


def _check(entry: dict, default, value, key: str):
    """The cleaned value, or raise SettingsError with a message for the page."""
    kind = entry["type"]

    def one(v, kind, where="", entry=entry):   # a single value (or an item of a list/map)
        if kind == "bool":
            if not isinstance(v, bool):
                raise SettingsError(f"{where}Should be on or off.")
            return v
        if kind in ("int", "float"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind == "int" and not
                                                                          (isinstance(v, int) or float(v).is_integer())):
                raise SettingsError(f"{where}Should be {'a whole number' if kind == 'int' else 'a number'}.")
            v = int(v) if kind == "int" else float(v)
            if isinstance(v, float) and not math.isfinite(v):
                raise SettingsError(f"{where}Should be a number.")
            lo, hi = entry.get("min"), entry.get("max")
            if lo is not None and v < lo:
                raise SettingsError(f"{where}Should be at least {_fmt(lo)}.")
            if hi is not None and v > hi:
                raise SettingsError(f"{where}Should be at most {_fmt(hi)}.")
            return v
        if not isinstance(v, str):
            raise SettingsError(f"{where}Should be text.")
        v = v.strip()
        allowed = choices(key, entry)
        if allowed is not None and v not in allowed and not (key == "ears.voice" and _voice_ok(v)):
            raise SettingsError(f"{where}Should be one of: {', '.join(c or '(empty)' for c in allowed)}.")
        if kind == "str":
            if entry.get("nonempty") and not v:
                raise SettingsError("Can't be empty.")
            if entry.get("max") is not None and len(v) > entry["max"]:
                raise SettingsError(f"Too long: at most {entry['max']} characters.")
            if entry.get("pattern") and not re.fullmatch(entry["pattern"], v):
                raise SettingsError(f"Should look like {entry['example']}.")
        return v

    if kind == "list":
        if not isinstance(value, list):
            raise SettingsError("Should be a list.")
        sub = {**entry, "nonempty": False, "max": None, "pattern": None}
        items = [v for v in (one(x, "str", entry=sub) for x in value) if v]
        if entry.get("nonempty") and not items:
            raise SettingsError("Needs at least one.")
        return list(dict.fromkeys(items))
    if kind == "map":
        if not isinstance(value, dict):
            raise SettingsError("Should be a set of named values.")
        unknown = [k for k in value if k not in default]
        if unknown:
            raise SettingsError(f"Unknown name '{unknown[0]}': should be one of {', '.join(default)}.")
        out = dict(default)
        for k, v in value.items():
            out[k] = one(v, "float" if isinstance(default[k], float) else "str", f"{k}: ")
        return out
    return one(value, kind)


def _voice_ok(name: str) -> bool:
    from ..common import voices
    return voices.valid_name(name)


def validate(changes: dict, path: Path | None = None) -> dict:
    """Check changes from the Settings page ({"section.key": value, "modules.calendar.sync_minutes": value}).
    Returns {key: plain-language error}; empty means all fine."""
    errors, cleaned = {}, {}
    _clean(changes, errors, cleaned, path)
    return errors


def _current(path: Path | None) -> dict:
    try:
        return flat(load(path))
    except (SettingsError, OSError, tomllib.TOMLDecodeError):
        return flat({**_defaults()})


def _clean(changes: dict, errors: dict, cleaned: dict, path: Path | None = None) -> None:
    if not isinstance(changes, dict):
        errors["changes"] = "Nothing to save."
        return
    flat_defaults, mods = defaults(), None
    for key, value in changes.items():
        try:
            if not isinstance(key, str):
                raise SettingsError("There's no such setting.")
            if key.startswith("modules."):
                mods = mods if mods is not None else module_settings()
                if key.endswith(".enabled") and key.count(".") == 2:
                    from ..common.modules import discover
                    if key.split(".")[1] not in {m.name for m in discover({})}:
                        raise SettingsError("There's no such module.")
                    if not isinstance(value, bool):
                        raise SettingsError("Should be on or off.")
                    cleaned[key] = value
                    continue
                if key not in mods:
                    raise SettingsError("There's no such module setting.")
                cleaned[key] = _check(mods[key], mods[key]["default"], value, key)
            elif key in SCHEMA:
                cleaned[key] = _check(SCHEMA[key], flat_defaults[key], value, key)
            else:
                raise SettingsError("There's no such setting.")
        except SettingsError as e:
            errors[key] = str(e)
    # Settings that only make sense together (checked against what they'll be after saving)
    if "voices.sure" in cleaned or "voices.probably" in cleaned:
        now = _current(path)
        sure = cleaned.get("voices.sure", now.get("voices.sure"))
        probably = cleaned.get("voices.probably", now.get("voices.probably"))
        if sure is not None and probably is not None and probably > sure:
            errors["voices.probably"] = "Should be no more than the \"sure\" match."
    if "mic.clap_gap_min_ms" in cleaned or "mic.clap_gap_max_ms" in cleaned:
        now = _current(path)
        lo = cleaned.get("mic.clap_gap_min_ms", now.get("mic.clap_gap_min_ms"))
        hi = cleaned.get("mic.clap_gap_max_ms", now.get("mic.clap_gap_max_ms"))
        if lo is not None and hi is not None and lo >= hi:
            errors["mic.clap_gap_max_ms"] = "Should be longer than the shortest gap."


def clean(changes: dict, path: Path | None = None) -> dict:
    """The changes with values tidied (ints → floats where needed, text trimmed). Raises SettingsError."""
    errors, cleaned = {}, {}
    _clean(changes, errors, cleaned, path)
    if errors:
        raise SettingsError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return cleaned


# ---- saving: a tiny TOML writer (str/int/float/bool, lists of text, inline tables), no extra package
def _toml_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _toml_str(k)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise SettingsError("Numbers must be finite.")
        r = repr(v)
        return r if any(c in r for c in ".en") else r + ".0"
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise SettingsError(f"Can't save a {type(v).__name__}.")


def dumps(user: dict) -> str:
    """{"web": {"host": …}, "modules": {"calendar": {…}}} → the text of a settings.toml."""
    lines = ["# Your Pip settings, saved by the Settings page. Anything not listed here uses the default in",
             "# pip_assistant/defaults.toml. You can still edit this file by hand (then restart Pip).", ""]
    order = [s for s in _defaults() if s in user] + sorted(s for s in user if s not in _defaults() and s != "modules")
    for sec in order:
        if not user[sec]:
            continue
        lines.append(f"[{_toml_key(sec)}]")
        for k, v in user[sec].items():
            label = SCHEMA.get(f"{sec}.{k}", {}).get("label")
            lines.append(f"{_toml_key(k)} = {_toml_value(v)}" + (f"   # {label}" if label else ""))
        lines.append("")
    for name, values in sorted((user.get("modules") or {}).items()):
        if values:
            lines.append(f"[modules.{_toml_key(name)}]")
            lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in values.items()]
            lines.append("")
    return "\n".join(lines)


def save(changes: dict, path: Path | None = None) -> dict:
    """Apply changes ({"section.key": value}) to ~/pip-data/settings.toml, keeping only values that differ
    from the defaults (a value set back to its default is removed). Checks everything first and raises
    SettingsError without writing anything if one is wrong. The old file is kept as settings.toml.bak.
    Returns the merged settings as they'll be loaded."""
    path = path or paths.user_settings()
    cleaned = clean(changes, path)
    user = tomllib.loads(path.read_text()) if path.exists() else {}
    mods = user.setdefault("modules", {})
    flat_defaults, mod_defaults = defaults(), None
    for key, value in cleaned.items():
        if key.startswith("modules."):
            _, name, setting = key.split(".", 2)
            mod_defaults = mod_defaults if mod_defaults is not None else module_settings()
            default = True if setting == "enabled" else mod_defaults[key]["default"]
            section = mods.setdefault(name, {})
        else:
            sec, setting = key.split(".", 1)
            default = flat_defaults[key]
            section = user.setdefault(sec, {})
        if value == default:
            section.pop(setting, None)
        else:
            section[setting] = value
    for sec in [s for s, v in user.items() if s != "modules" and isinstance(v, dict) and not v]:
        del user[sec]
    for name in [n for n, v in mods.items() if not v]:
        del mods[name]
    if not mods:
        del user["modules"]
    text = dumps(user)
    merged = tomllib.loads(text)              # what we wrote reads back, and is valid settings
    m = merged.pop("modules", {})
    out = {**_merge(_defaults(), merged), "modules": m}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_text(path.read_text())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return out
