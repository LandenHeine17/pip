"""The screen module (step 7, D-111): boxes by voice, ready-made and saved screens, fixes, undo."""
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry
from pip_assistant.modules.screen.layout import COLS, ROWS
from pip_assistant.modules.screen.screen import MAX_SAVED, READY, Screen, ScreenError, add_kinds

# The calendar's and to-do lists' kinds of box (their module.toml [monitor.<kind>]), as when they're loaded with the screen
add_kinds({kind: b for name in ("calendar", "todos") for kind, b in read(ROOT / name).boxes.items()})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    reg = Registry.load({}, infos=[read(ROOT / "screen")], db_path=tmp_path / "pip.db")
    return reg, reg.modules["screen"], tmp_path


def say(reg, verb, **a):
    act, call = reg.action(verb), Call()
    return act.do(act.check({"do": verb, **a}, call), call)


def clear(view) -> bool:
    """Every box on the grid, clear of the face and of each other."""
    taken = set()
    f = view["face_shown"]
    cells = lambda b: {(x, y) for x in range(b["x"], b["x"] + b["w"]) for y in range(b["y"], b["y"] + b["h"])}
    for box in [{"x": 0, "y": 0, **f}, *view["sections"]]:
        c = cells(box)
        if taken & c or any(x >= COLS or y >= ROWS for x, y in c):
            return False
        taken |= c
    return True


def test_ready_made_screens_fit():
    for name, view in READY.items():
        assert clear(view), name
    assert [s["type"] for s in READY["default"]["sections"]] == ["clock", "weather", "timers", "calendar"]
    assert READY["night"].get("dim")


def test_adding_a_box_moves_the_others_without_asking():
    s = Screen()
    s.add_box({"type": "notes", "title": "Hall of Fame", "lines": ["one", "two"]})
    assert [x["type"] for x in s.view["sections"]][-1] == "notes" and clear(s.view)
    assert s.pending is None
    with pytest.raises(ScreenError, match="already on the screen"):
        s.add_box({"type": "clock"})


def test_what_wont_fit_gets_a_fix_to_okay():
    """PIP-NEW-061 / AC-UI-04: not a bare failure, a proposed fix; nothing changes until it's okayed."""
    s = Screen()
    before = s.view
    with pytest.raises(ScreenError) as e:
        s.add_box({"type": "conversation", "w": 6, "h": 6})
    assert s.view == before and s.pending and "face" in s.pending["words"]
    s.apply_fix()
    conv = next(x for x in s.view["sections"] if x["type"] == "conversation")
    assert (conv["w"], conv["h"]) == (6, 6) and clear(s.view) and s.pending is None
    assert "doesn't fit" in str(e.value)


def test_a_size_that_cant_be_met_is_not_quietly_shrunk():
    s = Screen()
    with pytest.raises(ScreenError):
        s.move_box("clock", {"w": 8, "h": 3})   # beside a large face there are only 5 columns
    s.apply_fix()
    clock = next(x for x in s.view["sections"] if x["type"] == "clock")
    assert (clock["w"], clock["h"]) == (8, 3)


def test_undo_puts_the_last_screen_back_exactly():
    """AC-UI-05."""
    s = Screen()
    first = s.view
    s.move_box("face", {"size": "small"})
    s.change_box("clock", {"color": "blue"})
    second = s.view
    s.remove_box("weather")
    s.undo()
    assert s.view == second
    s.undo()
    s.undo()
    assert s.view == first and "default" not in s.saved   # back to the ready-made one, unchanged
    with pytest.raises(ScreenError, match="nothing to undo"):
        s.undo()


def test_saved_screens_and_putting_ready_made_ones_back():
    s = Screen()
    s.save("Study", [{"type": "clock"}, {"type": "timers"}], "small")
    assert s.name == "study" and s.view["label"] == "Study"
    s.show("default")
    s.remove_box("calendar")
    assert "default" in s.saved
    s.delete("default")                       # a changed ready-made screen: put back
    assert s.view == READY["default"]
    s.delete("study")
    with pytest.raises(ScreenError):
        s.show("study")
    for i in range(MAX_SAVED):
        s.save(f"s{i}", ["clock"])
    with pytest.raises(ScreenError, match="at most"):
        s.save("one more", ["clock"])


def test_boxes_are_checked():
    s = Screen()
    with pytest.raises(ScreenError, match="no 'toaster' box"):
        s.add_box({"type": "toaster"})
    with pytest.raises(ScreenError):
        s.change_box("clock", {"color": "plaid"})
    s.add_box({"type": "shopping list", "list": "Shopping"})
    assert s.view["sections"][-1]["type"] == "todos" and s.view["sections"][-1]["list"] == "Shopping"
    s.change_box("shopping", {"color": "grey"})
    assert s.view["sections"][-1]["color"] == "gray"


def test_room_talk_only_with_a_conversation_box_showing_it():
    s = Screen()
    assert not s.room_talk()
    s.add_box({"type": "conversation"})
    assert s.room_talk()                       # on by default (owner, D-111)
    s.change_box("conversation", {"room": False})
    assert not s.room_talk()


def test_by_voice_and_kept(env):
    reg, mod, tmp = env
    assert say(reg, "show_screen", name="morning") == "Here's the Morning screen."
    assert "Morning" in reg.summary(Call())
    assert reg.summary(Call(kind="join")) == ""          # join-in checks don't need it (D-102)
    said = say(reg, "add_box", type="conversation", w=12, h=8)
    assert said.endswith("Okay?") and "waiting for an okay" in reg.summary(Call()).lower()
    assert say(reg, "screen_fix").startswith("Done")
    assert any(s["type"] == "conversation" for s in mod.screen.view["sections"])
    assert say(reg, "undo_screen").startswith("Put it back")
    assert not any(s["type"] == "conversation" for s in mod.screen.view["sections"])
    assert say(reg, "move_box", box="face", size="large").startswith("Moved my face")
    with pytest.raises(Invalid):
        say(reg, "add_box", type="toaster")
    # Kept across a restart of pip-core.
    reg2 = Registry.load({}, infos=[read(ROOT / "screen")], db_path=tmp / "pip.db")
    assert reg2.modules["screen"].screen.view == mod.screen.view


async def test_dashboard(env):
    reg, mod, tmp = env
    r = await mod.dashboard("status", {})
    assert r["showing"] == "default" and not r["can_undo"] and len(r["screens"]) == 4
    r = await mod.dashboard("show", {"name": "focus"})
    assert r["showing"] == "focus" and r["can_undo"]
    r = await mod.dashboard("undo", {})
    assert r["showing"] == "default"
    assert "error" in await mod.dashboard("delete", {"name": "focus"})   # unchanged ready-made: nothing to put back


def test_several_changes_at_once(env):
    """Owner's feedback (2026-11-10): move, resize, add and take off boxes in one go, as one change."""
    reg, mod, _ = env
    s = mod.screen
    say(reg, "add_box", type="notes", title="Hall of Fame", lines=["Sam: 12 wins"])
    before = len(s.history)
    said = say(reg, "arrange_screen", face="small", boxes=[
        {"type": "clock", "x": 4, "y": 0, "w": 8, "h": 3}, {"type": "notes", "color": "blue"}, {"type": "health"}])
    assert said.startswith("Done:")
    kinds = [x["type"] for x in s.view["sections"]]
    assert kinds == ["clock", "notes", "health"]                       # weather, timers and calendar came off
    clock = s.view["sections"][0]
    assert (clock["x"], clock["y"], clock["w"], clock["h"]) == (4, 0, 8, 3)
    notes = s.view["sections"][1]
    assert notes["lines"] == ["Sam: 12 wins"] and notes["title"] == "Hall of Fame" and notes["color"] == "blue"  # kept
    assert len(s.history) == before + 1                                # one change: one undo
    say(reg, "undo_screen")
    assert "weather" in [x["type"] for x in s.view["sections"]]
