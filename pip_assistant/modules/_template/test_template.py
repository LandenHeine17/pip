"""Every module has its own tests. This one loads the template like pip-core would (core/modules.py Registry)
and tries each of its parts through the same hooks pip-core and pip-web use."""
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry


@pytest.fixture
def reg(tmp_path):
    return Registry.load({}, infos=[read(ROOT / "_template")], db_path=tmp_path / "pip.db")


def test_claude_can_roll(reg):
    act = reg.action("roll")
    plan = act.check({"do": "roll", "sides": 6}, Call())
    assert act.do(plan, Call()) in {f"It's a {n}." for n in range(1, 7)}
    with pytest.raises(Invalid):
        act.check({"do": "roll", "sides": 1}, Call())


def test_offline_the_dashboard_and_the_monitor(reg):
    [offline] = reg.offline_items()
    assert offline.rules[0]("roll a 20-sided die", None).startswith("It's a ")
    assert offline.rules[0]("what's the weather", None) is None
    [card] = reg.home_cards()                                   # the front page's card: the last roll
    assert card["module"] == "_template" and card["items"][0]["title"].endswith("(d20)")
    assert reg.tv()["boxes"] == {}                              # its box isn't on the screen
    assert reg.modules["_template"].tv([{"type": "dice"}])["dice"]["sides"] == 20


def test_the_box_is_declared_and_drawn():
    info = read(ROOT / "_template")
    assert info.boxes["dice"]["label"] == "Dice" and (info.path / "static" / "monitor.js").exists()
