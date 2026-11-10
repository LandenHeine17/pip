"""The six-seven module, loaded the way pip-core loads modules (core/modules.py Registry)."""
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Registry
from pip_assistant.modules.six_seven.module import CHANT


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def reg(tmp_path):
    clock = Clock()
    r = Registry.load({}, infos=[read(ROOT / "six_seven")], db_path=tmp_path / "pip.db", clock=clock)
    r.clock = clock
    return r


@pytest.mark.parametrize("text", ["six seven", "that's a Six and a SEVEN", "6 and 7", "it was seven, then six",
                                  "six... 7", "67"])
def test_a_six_and_a_seven_make_the_chant(reg, text):
    assert reg.room_heard(text, Call()) == CHANT
    assert CHANT == "six seven, six seven, six fucking seven"


@pytest.mark.parametrize("text", ["six apples", "seven days", "I have 6", "7 o'clock", "sixty seven", "sixteen seventeen", ""])
def test_either_alone_does_not(reg, text):
    assert reg.room_heard(text, Call()) is None


def test_it_waits_before_chanting_again(reg):
    assert reg.room_heard("six seven", Call()) == CHANT
    reg.clock.now += 29
    assert reg.room_heard("six seven", Call()) is None
    reg.clock.now += 2
    assert reg.room_heard("6 7", Call()) == CHANT


def test_the_pause_is_a_setting(tmp_path):
    clock = Clock()
    info = read(ROOT / "six_seven", {"pause_seconds": 5})
    r = Registry.load({}, infos=[info], db_path=tmp_path / "pip.db", clock=clock)
    assert r.room_heard("six seven", Call()) == CHANT
    clock.now += 6
    assert r.room_heard("six seven", Call()) == CHANT


def test_it_keeps_no_tables_and_no_pages(reg):
    info = reg.infos["six_seven"]
    assert not info.page and not info.boxes and reg.home_cards() == [] and reg.offline_items() == []
