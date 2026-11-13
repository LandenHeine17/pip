"""The recall module (conversation memory, D-128): notes of each conversation, kept 30 days, never the words."""
import asyncio
from datetime import datetime

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, ModuleContext, Registry
from pip_assistant.core.people import Match


class Clock:
    t = datetime(2026, 11, 12, 22, 0).timestamp()

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    reg = Registry.load({}, infos=[read(ROOT / "recall")], db_path=tmp_path / "pip.db", clock=clock)
    rec = reg.modules["recall"]
    asked = []

    async def summarize(lines):
        asked.append(lines)
        return "Talked about H200 chips and whether to upgrade Pip; Caleb wants to look at prices."
    rec.ctx.summarize = summarize
    return reg, rec, clock, asked


def hear(rec, clock, n=4, who=Match("c", "Caleb", "sure", 0.8)):
    for i in range(n):
        rec.room_heard(f"line {i} about chips", Call(speaker=who, kind="room"))
        clock.t += 20


def test_a_quiet_room_ends_the_conversation_and_notes_are_written(setup):
    reg, rec, clock, asked = setup
    hear(rec, clock)
    assert not rec.due()
    clock.t += 10 * 60
    assert rec.due()
    nid = asyncio.run(rec.write_notes())
    assert nid and rec.lines == [] and asked[0][0].endswith("Caleb: line 0 about chips")
    [n] = rec.notes()
    assert n["people"] == "Caleb" and "H200" in n["notes"]
    assert "Earlier conversations" in reg.summary(Call()) and "H200" in reg.summary(Call())


def test_small_talk_and_nothing_worth_noting_are_not_kept(setup):
    reg, rec, clock, asked = setup
    hear(rec, clock, n=2)                                        # too short to be a conversation
    assert asyncio.run(rec.write_notes()) is None and not asked

    async def nothing(lines):
        return ""
    rec.ctx.summarize = nothing
    hear(rec, clock)
    assert asyncio.run(rec.write_notes()) is None and rec.notes() == []


def test_older_notes_are_looked_up_and_pruned_after_30_days(setup):
    reg, rec, clock, asked = setup
    hear(rec, clock)
    asyncio.run(rec.write_notes())
    clock.t += 3 * 86400
    assert reg.summary(Call()) == ""                             # not in every request after a day…
    act = reg.action("look:recall")
    got = act.do(act.check({"do": "look", "kind": "recall", "query": "the chips"}, Call()), Call())
    assert "H200" in got                                         # …but found when asked
    day = act.do(act.check({"do": "look", "kind": "recall", "day": "2026-11-12"}, Call()), Call())
    assert "H200" in day and "Thu Nov 12 22:00" in day
    assert "No notes" in act.do(act.check({"do": "look", "kind": "recall", "query": "pizza"}, Call()), Call())
    with pytest.raises(Invalid):
        act.check({"do": "look", "kind": "recall"}, Call())
    clock.t += 28 * 86400
    assert rec.prune() == 1 and rec.notes() == []


def test_the_page_lists_and_deletes(setup):
    reg, rec, clock, asked = setup
    hear(rec, clock)
    asyncio.run(rec.write_notes())
    hear(rec, clock, n=3)
    asyncio.run(rec.write_notes())
    got = asyncio.run(rec.dashboard("list", {}))
    assert len(got["notes"]) == 2 and got["keep_days"] == 30
    asyncio.run(rec.dashboard("delete", {"id": got["notes"][0]["id"]}))
    assert len(rec.notes()) == 1
    hear(rec, clock, n=2)
    asyncio.run(rec.dashboard("delete_all", {}))
    assert rec.notes() == [] and rec.lines == []


def test_only_a_module_allowed_to_may_ask_for_summaries(tmp_path):
    info = read(ROOT / "_template")
    ctx = ModuleContext(info, db_path=tmp_path / "pip.db")
    with pytest.raises(PermissionError):
        asyncio.run(ctx.summarize(["a"]))
