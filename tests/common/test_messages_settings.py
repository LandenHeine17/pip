"""Messages between Pip's programs, and loading the settings (common/messages.py, common/settings.py)."""
import pytest

from pip_assistant.common import settings
from pip_assistant.common.messages import BadMessage, Message


def test_message_round_trip():
    m = Message("event", "timer.done", source="core", data={"id": 3}, private={"text": "pasta"})
    back = Message.decode(m.encode())
    assert back == m


@pytest.mark.parametrize("line", [b"not json", b'{"kind":"nope","type":"x"}', b'{"kind":"event"}', b'[1,2]',
                                  b'{"kind":"event","type":"x","data":[]}'])
def test_bad_messages_rejected(line):
    with pytest.raises(BadMessage):
        Message.decode(line)


def test_settings_defaults_without_user_file(tmp_path):
    cfg = settings.load(tmp_path / "missing.toml")
    assert cfg["web"]["port"] == 8790 and cfg["trace"]["keep_hours"] == 48


def test_settings_override_and_validation(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('[web]\nport = 9000\n[programs]\nstuck_after_seconds = 5\n')
    cfg = settings.load(f)
    assert cfg["web"]["port"] == 9000 and cfg["programs"]["stuck_after_seconds"] == 5.0
    f.write_text('[web]\nprot = 9000\n')
    with pytest.raises(settings.SettingsError, match="Unknown setting 'web.prot'"):
        settings.load(f)
    f.write_text('[web]\nport = "9000"\n')
    with pytest.raises(settings.SettingsError, match="should be int"):
        settings.load(f)
    f.write_text('[programs]\nrestart_stuck = 1\n')
    with pytest.raises(settings.SettingsError, match="should be bool"):
        settings.load(f)
