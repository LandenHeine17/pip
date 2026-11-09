"""The Settings page's back end (step 8): SCHEMA, validate, save, applying changes, voices."""
import asyncio
import tomllib

import pytest

from pip_assistant.common import settings, voices
from pip_assistant.core import settings_schema, settingsapi
from pip_assistant.core.settingsapi import SettingsAPI, programs_for


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    monkeypatch.delenv("INVOCATION_ID", raising=False)   # never restart real services from a test
    return tmp_path


class FakeConv:
    def __init__(self):
        self.state, self.pending, self.ringing, self.speaking = "idle", "", False, False
        self.commands = []

    async def command(self, target, msg_type, data=None, private=None):
        self.commands.append((target, msg_type, data or {}, private or {}))

    async def prefs_snapshot(self):
        return {"ok": True, "chime": True, "mics": []}


def api(tmp_path, conv=None, calls=None, **kw):
    calls = [] if calls is None else calls
    return SettingsAPI(None, conv or FakeConv(), restarter=lambda name: calls.append(name) or True,
                       path=tmp_path / "settings.toml", poll_seconds=0.01, **kw), calls


# ---------------------------------------------------------------- SCHEMA
def test_schema_matches_defaults_toml():
    defaults = settings_schema.defaults()
    assert set(defaults) == set(settings_schema.SCHEMA), "every default needs a SCHEMA entry, and the reverse"
    for key, e in settings_schema.SCHEMA.items():
        assert e["explain"].strip() and e["label"].strip(), key
        assert e["group"] in settings_schema.GROUPS, key
        assert e["programs"] and set(e["programs"]) <= set(settings_schema.ALL), key
        assert isinstance(defaults[key], settings_schema.TYPES[e["type"]]), key
        assert not settings_schema.validate({key: defaults[key]}), f"{key}'s default must pass its own check"


def test_fixed_choices_match_the_code():
    from pip_assistant.core.joining import INTERVALS
    from pip_assistant.ears.audio import RINGTONES
    assert settings_schema.RINGTONES == list(RINGTONES)
    assert settings_schema.MOODS == list(INTERVALS)
    assert "en_US-lessac-medium" in settings_schema.choices("ears.voice")
    assert {"pippletron", "hey_pip"} <= set(settings_schema.choices("ears.wake_words"))


# ---------------------------------------------------------------- validate
def test_validate_rejects_wrong_values_in_plain_words():
    errors = settings_schema.validate({
        "ears.barge_in": "yes", "web.port": 80.5, "social.mood": "grumpy", "ears.speech_rate": 3,
        "trace.keep_hours": 0, "social.quiet_hours": "late", "alarms.ringtones": {"timer": "siren"},
        "ears.wake_thresholds": {"pippletron": 1.5}, "ears.wake_words": [], "thinker.location": "  ",
        "no.such": 1, "alarms.ringtones.extra": "x"})
    assert errors["ears.barge_in"] == "Should be on or off."
    assert errors["web.port"] == "Should be a whole number."
    assert errors["social.mood"] == "Should be one of: quiet, normal, chatty."
    assert errors["ears.speech_rate"] == "Should be at most 2."
    assert errors["trace.keep_hours"] == "Should be at least 1."
    assert errors["social.quiet_hours"] == "Should look like 23:00-07:00."
    assert "timer:" in errors["alarms.ringtones"] and "classic" in errors["alarms.ringtones"]
    assert "pippletron:" in errors["ears.wake_thresholds"]
    assert errors["ears.wake_words"] == "Needs at least one."
    assert errors["thinker.location"] == "Can't be empty."
    assert errors["no.such"] == errors["alarms.ringtones.extra"] == "There's no such setting."
    assert settings_schema.validate({"alarms.ringtones": {"party": "classic"}})["alarms.ringtones"].startswith("Unknown name")


def test_validate_accepts_good_values_and_tidies_them():
    changes = {"ears.speech_rate": 1, "social.quiet_hours": "", "ears.voice": "en_GB-alan-medium",
               "ears.wake_thresholds": {"hey_pip": 0.6}, "stt.spare_models": ["a", "a", ""]}
    assert settings_schema.validate(changes) == {}
    c = settings_schema.clean(changes)
    assert c["ears.speech_rate"] == 1.0 and isinstance(c["ears.speech_rate"], float)
    assert c["ears.wake_thresholds"] == {"pippletron": 0.8, "hey_pip": 0.6}
    assert c["stt.spare_models"] == ["a"]
    assert settings_schema.validate({"ears.voice": "not a voice"})      # any Piper-style name is fine, nonsense isn't


def test_settings_that_go_together():
    assert "voices.probably" in settings_schema.validate({"voices.probably": 0.5})          # sure is 0.45
    assert settings_schema.validate({"voices.probably": 0.5, "voices.sure": 0.6}) == {}
    assert "mic.clap_gap_max_ms" in settings_schema.validate({"mic.clap_gap_max_ms": 100 + 300, "mic.clap_gap_min_ms": 500})


def test_module_settings_validate():
    assert settings_schema.validate({"modules.calendar.sync_minutes": 5, "modules.todos.show_done_days": 3,
                              "modules.calendar.enabled": False}) == {}
    errors = settings_schema.validate({"modules.calendar.sync_minutes": "often", "modules.calendar.colour": 1,
                                "modules.nope.enabled": True, "modules.calendar.days_ahead": -1})
    assert errors["modules.calendar.sync_minutes"] == "Should be a number."
    assert errors["modules.calendar.colour"] == "There's no such module setting."
    assert errors["modules.nope.enabled"] == "There's no such module."
    assert errors["modules.calendar.days_ahead"] == "Should be at least 0."


# ---------------------------------------------------------------- save
def test_save_writes_only_non_defaults_and_keeps_modules(tmp_path):
    f = tmp_path / "settings.toml"
    f.write_text('[web]\nhost = "100.1.2.3"  # mine\n\n[modules.calendar]\nsync_minutes = 5.0\n')
    settings_schema.save({"ears.speech_rate": 1.2, "web.port": 8790, "social.welcome_back_minutes": 0,
                   "thinker.location": 'Omaha, "NE" \\ USA', "modules.todos.show_done_days": 3}, f)
    saved = tomllib.loads(f.read_text())
    assert saved["ears"] == {"speech_rate": 1.2}
    assert saved["web"] == {"host": "100.1.2.3"}                 # the port is the default: not written
    assert saved["social"] == {"welcome_back_minutes": 0}
    assert saved["thinker"]["location"] == 'Omaha, "NE" \\ USA'
    assert saved["modules"] == {"calendar": {"sync_minutes": 5.0}, "todos": {"show_done_days": 3}}
    assert (tmp_path / "settings.toml.bak").read_text().startswith("[web]")
    cfg = settings.load(f)
    assert cfg["ears"]["speech_rate"] == 1.2 and cfg["ears"]["voice"] == "en_US-lessac-medium"
    assert cfg["modules"]["calendar"]["sync_minutes"] == 5.0
    # back to the default: the line (and the empty section) go away
    settings_schema.save({"ears.speech_rate": 1.0, "modules.todos.show_done_days": 7}, f)
    saved = tomllib.loads(f.read_text())
    assert "ears" not in saved and "todos" not in saved["modules"]


def test_save_round_trips_every_kind_of_value(tmp_path):
    f = tmp_path / "settings.toml"
    changes = {"ears.wake_thresholds": {"pippletron": 0.85, "hey_pip": 0.5},
               "alarms.ringtones": {"timer": "gentle", "alarm": "classic", "reminder": "chimes", "event": "chimes"},
               "stt.spare_models": ["x-1", "y"], "ears.barge_in": False, "stt.word_list": "Pip,\tline\nnext",
               "mic.clap_max_ms": 300}
    settings_schema.save(changes, f)
    flat = settings_schema.flat(settings.load(f))
    for k, v in changes.items():
        assert flat[k] == v, k


def test_save_with_one_bad_value_saves_nothing(tmp_path):
    f = tmp_path / "settings.toml"
    f.write_text("[web]\nport = 9000\n")
    with pytest.raises(settings_schema.SettingsError):
        settings_schema.save({"ears.speech_rate": 1.1, "social.mood": "grumpy"}, f)
    assert f.read_text() == "[web]\nport = 9000\n"
    assert not (tmp_path / "settings.toml.bak").exists()


# ---------------------------------------------------------------- applying changes
def test_changed_keys_map_to_the_programs_that_read_them():
    assert programs_for(["ears.voice"]) == ["ears"]
    assert programs_for(["thinker.location"]) == ["thinker", "web"]
    assert programs_for(["listen.max_utterance_seconds"]) == ["ears", "core"]
    assert programs_for(["social.mood", "ears.speech_rate"]) == ["ears", "core"]
    assert programs_for(["programs.heartbeat_seconds"]) == ["ears", "thinker", "web", "core"]   # core last
    assert programs_for(["modules.calendar.sync_minutes"]) == ["core"]
    assert programs_for(["modules.calendar.enabled"]) == ["thinker", "core"]


async def test_set_with_an_invalid_key_saves_nothing(tmp_path):
    a, calls = api(tmp_path)
    r = await a.handle({"action": "set", "changes": {"ears.speech_rate": 1.1, "web.port": "eighty"}})
    assert r == {"errors": {"web.port": "Should be a whole number."}}
    assert not (tmp_path / "settings.toml").exists() and a.applying == [] and calls == []


async def test_restart_waits_until_pip_is_quiet_and_core_goes_last(tmp_path):
    conv = FakeConv()
    conv.state, conv.speaking = "speaking", True
    a, calls = api(tmp_path, conv)
    r = await a.handle({"action": "set", "changes": {"social.mood": "chatty", "ears.speech_rate": 1.1}})
    assert r["ok"] and r["applying"] == ["ears", "core"]
    await asyncio.sleep(0.05)
    assert calls == []                                   # still talking
    conv.state, conv.speaking, conv.pending = "idle", False, "what's the"
    await asyncio.sleep(0.05)
    assert calls == []                                   # holding a turn
    conv.pending, conv.ringing = "", True
    await asyncio.sleep(0.05)
    assert calls == []                                   # an alarm is ringing
    conv.ringing = False
    await asyncio.wait_for(a.task, 1)
    assert calls == ["ears", "core"] and a.applying == []
    assert (await a.handle({"action": "get"}))["applying"] == []


async def test_restart_gives_up_waiting(tmp_path):
    conv = FakeConv()
    conv.state = "thinking"
    a, calls = api(tmp_path, conv, max_wait_seconds=0.05)
    await a.handle({"action": "set", "changes": {"thinker.web_search": False}})
    await asyncio.wait_for(a.task, 1)
    assert calls == ["thinker"]


async def test_changes_while_waiting_join_the_same_restart(tmp_path):
    conv = FakeConv()
    conv.state = "speaking"
    a, calls = api(tmp_path, conv)
    await a.handle({"action": "set", "changes": {"social.mood": "quiet"}})
    await a.handle({"action": "set", "changes": {"web.port": 8800}})
    assert a.applying == ["web", "core"]
    conv.state = "idle"
    await asyncio.wait_for(a.task, 1)
    assert calls == ["web", "core"]


async def test_without_systemd_it_only_says_a_restart_is_needed(tmp_path):
    a = SettingsAPI(None, FakeConv(), path=tmp_path / "settings.toml")
    assert a.restarter is None
    r = await a.handle({"action": "set", "changes": {"ears.speech_rate": 1.1}})
    assert r["ok"] and r["applying"] == [] and r["restart_needed"] == ["ears"]


async def test_unchanged_values_restart_nothing_and_reset_goes_back(tmp_path):
    a, calls = api(tmp_path)
    r = await a.handle({"action": "set", "changes": {"social.mood": "normal"}})
    assert r["ok"] and r["changed"] == [] and r["applying"] == []
    await a.handle({"action": "set", "changes": {"social.mood": "chatty"}})
    await asyncio.wait_for(a.task, 1)
    r = await a.handle({"action": "reset", "key": "social.mood"})
    assert r["changed"] == ["social.mood"]
    assert "social" not in tomllib.loads((tmp_path / "settings.toml").read_text())


async def test_get_describes_every_setting(tmp_path):
    a, _ = api(tmp_path)
    await a.handle({"action": "set", "changes": {"ears.speech_rate": 1.3, "modules.calendar.days_back": 10}})
    r = await a.handle({"action": "get"})
    assert [g["name"] for g in r["groups"]] == settings_schema.GROUPS
    rows = {s["key"]: s for g in r["groups"] for s in g["settings"]}
    assert set(rows) == set(settings_schema.SCHEMA)
    assert rows["ears.speech_rate"]["value"] == 1.3 and rows["ears.speech_rate"]["default"] == 1.0
    assert rows["ears.speech_rate"]["min"] == 0.5 and rows["social.mood"]["choices"] == settings_schema.MOODS
    assert rows["stt.model"]["advanced"] and not rows["social.mood"]["advanced"]
    mods = {s["key"]: s for m in r["modules"] for s in m["settings"]}
    assert mods["modules.calendar.days_back"]["value"] == 10 and mods["modules.calendar.days_back"]["default"] == 30
    assert mods["modules.calendar.days_back"]["explain"]
    assert r["prefs"]["chime"] is True


# ---------------------------------------------------------------- voices
async def test_a_new_voice_is_downloaded_before_saving(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(voices, "ensure_voice", lambda name: got.append(name))
    a, calls = api(tmp_path)
    r = await a.handle({"action": "set", "changes": {"ears.voice": "en_US-amy-medium"}})
    assert r["ok"] and got == ["en_US-amy-medium"] and r["applying"] == ["ears"]

    def fail(name):
        raise voices.VoiceError("Couldn't download it: is the internet up?")
    monkeypatch.setattr(voices, "ensure_voice", fail)
    r = await a.handle({"action": "set", "changes": {"ears.voice": "en_GB-alan-medium", "social.mood": "quiet"}})
    assert r == {"errors": {"ears.voice": "Couldn't download it: is the internet up?"}}
    assert settings.load(tmp_path / "settings.toml")["ears"]["voice"] == "en_US-amy-medium"


def test_voice_url():
    assert voices.url("en_US-lessac-medium") == \
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    assert voices.url("en_GB-northern_english_male-medium").endswith(
        "/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx")
    with pytest.raises(voices.VoiceError):
        voices.url("../../etc")


class FakeResponse:
    def __init__(self, status, dest_part, seen):
        self.status_code, self.part, self.seen = status, dest_part, seen

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, size):
        yield b"abc"
        self.seen.append(self.part.exists())   # written to the .part file first…
        yield b"def"


def test_ensure_voice_downloads_through_part_files(data_dir, monkeypatch):
    import httpx
    urls, seen = [], []
    d = data_dir / "models" / "voices"

    def stream(method, url, **kw):
        urls.append(url)
        name = url.rsplit("/", 1)[1]
        return FakeResponse(200, d / f"{name}.part", seen)
    monkeypatch.setattr(httpx, "stream", stream)
    path = voices.ensure_voice("en_US-ryan-medium")
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/medium/en_US-ryan-medium.onnx"
    assert urls == [base + ".json", base]
    assert seen == [True, True]
    assert path == d / "en_US-ryan-medium.onnx" and path.read_bytes() == b"abcdef"
    assert not list(d.glob("*.part"))
    assert voices.installed_voices() == ["en_US-ryan-medium"]
    voices.ensure_voice("en_US-ryan-medium")      # already there: nothing downloaded
    assert len(urls) == 2


def test_ensure_voice_failure_leaves_nothing_behind(data_dir, monkeypatch):
    import httpx
    d = data_dir / "models" / "voices"
    monkeypatch.setattr(httpx, "stream", lambda m, url, **kw: FakeResponse(404, d / "x.part", []))
    with pytest.raises(voices.VoiceError, match="404"):
        voices.ensure_voice("en_US-joe-medium")
    assert list(d.iterdir()) == []


# ---------------------------------------------------------------- try buttons
async def test_try_ringtone_stops_after_three_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(settingsapi, "TRY_SECONDS", 0.02)
    conv = FakeConv()
    a, _ = api(tmp_path, conv)
    assert (await a.handle({"action": "try_ringtone", "tone": "siren"}))["error"]
    r = await a.handle({"action": "try_ringtone", "tone": "kitchen"})
    assert r == {"ok": True}
    assert conv.commands == [("ears", "ears.ring", {"tone": "kitchen", "max_seconds": 0.02}, {})]
    await asyncio.wait_for(a._try_task, 1)
    assert conv.commands[-1][:2] == ("ears", "ears.ring_stop")


async def test_try_ringtone_never_stops_a_real_alarm(tmp_path, monkeypatch):
    monkeypatch.setattr(settingsapi, "TRY_SECONDS", 0.02)
    conv = FakeConv()
    a, _ = api(tmp_path, conv)
    await a.handle({"action": "try_ringtone", "tone": "classic"})
    conv.ringing = True                                    # a real timer went off meanwhile
    await asyncio.wait_for(a._try_task, 1)
    assert [c[1] for c in conv.commands] == ["ears.ring"]
    assert (await a.handle({"action": "try_ringtone", "tone": "classic"}))["error"]


async def test_try_voice_says_a_sample(tmp_path):
    conv = FakeConv()
    a, _ = api(tmp_path, conv)
    r = await a.handle({"action": "try_voice"})
    assert r["ok"] and "now" in r["note"]
    target, kind, data, private = conv.commands[0]
    assert (target, kind) == ("ears", "ears.say") and "Pip" in private["text"]
