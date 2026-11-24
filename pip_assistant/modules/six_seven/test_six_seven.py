"""The six-seven module, loaded the way pip-core loads modules (core/modules.py Registry)."""
import asyncio

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Done, Registry
from pip_assistant.modules.six_seven.module import CHANT, heard, only_six_seven


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


@pytest.mark.parametrize("text", ["six seven", "Six, seven!", "6 7", "6-7", "it's 67", "SIX SEVEN SIX SEVEN", "67",
                                  "six... 7", "6, 7", "6. 7", "he's 6'7", "sixseven", "five six seven eight",
                                  "we got 67 problems"])
def test_a_six_straight_followed_by_a_seven(text):
    """D-142: a six then a seven, next to each other, anywhere; to Pip and in the room alike."""
    assert heard(text), text


@pytest.mark.parametrize("text", ["6:07", "167", "6.7", "67.5", "1.67", "6/7", "678", "sixty seven", "seventy six",
                                  "seven six", "it was seven, then six", "that's a six and a seven", "6 and 7",
                                  "six apples", "seven days", "sixteen seventeen", "set a timer for 6 minutes and 7 seconds", ""])
def test_not_otherwise(text):
    assert not heard(text), text


def test_the_room_and_pip_both_get_the_chant(reg):
    assert reg.room_heard("six seven", Call(kind="room")) == CHANT
    assert CHANT == "six seven, six seven, six fucking seven"
    reg.clock.now += 31
    said = reg.room_heard("Pip, six seven!", Call(kind="direct"))
    assert said == CHANT and isinstance(said, Done)   # Claude is never asked about it, even when he's busy


def test_a_request_with_67_in_it_still_reaches_claude(reg):
    """"Pip, set a timer for 67 minutes": he freaks out, but the timer is still set (no chant answers it)."""
    assert reg.room_heard("Pip, set a timer for 67 minutes", Call(kind="direct")) is None
    assert reg.clock.now < reg.modules["six_seven"].quiet_until   # it did go off (the face, the lamps)


def test_only_six_seven():
    for yes in ("Pip, six seven!", "hey pip 6 7", "yo, 67 haha", "six seven six seven", "say six seven", "Pip 67 bro"):
        assert only_six_seven(yes), yes
    for no in ("Pip, set a timer for 67 minutes", "what's 67 times 2", "Pip, turn on the lights, six seven"):
        assert not only_six_seven(no), no


def test_it_waits_before_going_off_again(reg):
    assert reg.room_heard("six seven", Call(kind="room")) == CHANT
    reg.clock.now += 29
    assert reg.room_heard("six seven", Call(kind="room")) is None
    reg.clock.now += 2
    assert reg.room_heard("6 7", Call(kind="room")) == CHANT


def test_the_pause_is_a_setting(tmp_path):
    clock = Clock()
    info = read(ROOT / "six_seven", {"pause_seconds": 5})
    r = Registry.load({}, infos=[info], db_path=tmp_path / "pip.db", clock=clock)
    assert r.room_heard("six seven", Call(kind="room")) == CHANT
    clock.now += 6
    assert r.room_heard("six seven", Call(kind="room")) == CHANT


def test_it_keeps_no_tables_and_no_pages(reg):
    info = reg.infos["six_seven"]
    assert not info.page and not info.boxes and reg.home_cards() == [] and reg.offline_items() == []


class FakeHome:
    """Stands in for the home_control module: records each disco."""
    def __init__(self):
        self.discos = []
        self.api = self

    def disco(self, seconds):
        self.discos.append(seconds)


async def test_the_face_and_the_lamps_freak_out(tmp_path):
    published = []

    class Bus:
        async def publish(self, msg):
            published.append((msg.type, msg.data))

    for seconds, expected in ((4.0, [4.0]), (0.0, [])):
        published.clear()
        r = Registry.load({}, infos=[read(ROOT / "six_seven", {"disco_seconds": seconds})], db_path=tmp_path / "pip.db",
                          bus=Bus())
        r.modules["home_control"] = home = FakeHome()   # (loaded beside it, as pip-core would)
        assert r.room_heard("67!", Call(kind="room")) == CHANT
        await asyncio.sleep(0)
        assert home.discos == expected                                     # 0: no disco
        assert published == [("module.six_seven.reaction", {"reaction": "sixseven"})]


async def test_without_home_control_it_still_goes_off(reg):
    assert "home_control" not in reg.modules
    assert reg.room_heard("six seven", Call(kind="room")) == CHANT
