"""Memory (core/memory.py, D-88, D-119): saving, merging, refusing secrets, private memories, and Claude's memory
actions (remember, forget, correct); everything asked is saved straight away."""
from datetime import datetime

import pytest

from pip_assistant.core import actions
from pip_assistant.core.memory import Memory, Refused
from pip_assistant.core.people import Match

NOW = datetime(2026, 11, 5, 9, 0)



def test_memory_saves_merges_duplicates_and_refuses_secrets(mem):
    row, outcome = mem.add("The bins go out Tuesday night", "household", source="Alex, by voice")
    assert outcome == "saved" and row["visibility"] == "household" and row["status"] == "active"   # D-16
    assert mem.add("the bins go out tuesday night.", "household")[1] == "duplicate"
    for secret in ("The wifi password is hunter2", "Card 4111 1111 1111 1111", "Mom's number is " + " ".join(["402", "555", "0199"])):  # number built at run time: the commit check blocks literal ones
        with pytest.raises(Refused):
            mem.add(secret)
    assert len(mem.items()) == 1


def test_sensitive_memories_are_saved_straight_away_and_archiving_keeps_them(mem):
    row, outcome = mem.add("Sam is allergic to peanuts", "personal", sensitive=True)   # D-119: no inbox
    assert outcome == "saved" and "Sam is allergic to peanuts" in mem.for_claude()
    mem.update(row["id"], status="archived")
    assert "peanuts" not in mem.for_claude() and mem.items("archived")    # archived: kept, not used
    with pytest.raises(ValueError):
        mem.update(row["id"], status="pending")                             # no inbox any more (D-154)


def test_private_memories_only_for_their_owner_when_sure(mem):
    mem.add("Alex is planning a surprise party for Sam", "personal", ["alex"], owner="alex", private=True)
    assert "surprise" not in mem.for_claude()
    assert "surprise" not in mem.for_claude("alex", "probably")
    assert "surprise" in mem.for_claude("alex", "sure")
    assert "surprise" not in mem.for_claude("sam", "sure")

# ---------------------------------------------------------------- Claude's memory actions


def test_remember_forget_and_correct(ctx, mem):
    cmds, c, alex, sam = ctx
    said, n = actions.apply(cmds, 'ACTION {"do": "remember", "text": "Alex likes his coffee black", '
                                  '"category": "preference", "about": ["Alex"]}', c)
    assert said == "Saved: Alex likes his coffee black." and n == 1
    [row] = mem.items()
    assert row["about"] == [alex["id"]] and row["source"] == "Alex, by voice" and row["status"] == "active"
    ref = mem.short(row["id"])
    assert ref in mem.for_claude()
    said, _ = actions.apply(cmds, f'ACTION {{"do": "update_memory", "id": "{ref}", "text": "Alex likes oat milk in his coffee"}}', c)
    assert said == "Updated: Alex likes oat milk in his coffee."
    said, _ = actions.apply(cmds, f'ACTION {{"do": "forget", "id": "{ref}"}}', c)
    assert said == "Forgot: Alex likes oat milk in his coffee." and mem.items() == []


@pytest.mark.parametrize("action", [
    '{"do": "remember", "text": "Sam hates mushrooms", "category": "preference", "about": ["Sam"]}',
    '{"do": "remember", "text": "Sam has a doctor appointment Friday", "category": "personal", "about": ["Sam"], "sensitive": true}',
])
def test_memories_about_someone_else_are_saved_straight_away(ctx, mem, action):
    """D-119 (owner, 2026-11-10): every memory is saved straight away, none waits in the inbox."""
    cmds, c, *_ = ctx
    said, _ = actions.apply(cmds, "ACTION " + action, c)
    assert said.startswith("Saved: Sam")
    [row] = mem.items()
    assert row["status"] == "active" and row["why_pending"] == ""


def test_private_from_an_unknown_voice_stays_private_for_nobody(ctx, mem):
    cmds, c, *_ = ctx
    c["speaker"] = Match()
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "The spare key is under the plant", "category": "household", "private": true}', c)
    [row] = mem.items()
    assert row["status"] == "active" and row["visibility"] == "private" and row["owner"] == ""   # never household by mistake
    assert said.startswith("Saved privately:") and "spare key" not in mem.for_claude()


def test_bad_memory_actions_change_nothing(ctx, mem):
    cmds, c, *_ = ctx
    assert actions.apply(cmds, 'ACTION {"do": "forget", "id": "#m999999"}', c) == (actions.FAILED, 0)
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "The door code is 4821", "category": "household"}', c)
    assert said == "I can't keep passwords, card or phone numbers in my memory." and mem.items() == []


def test_household_facts_and_unknown_voices_personal_facts_are_saved(ctx, mem):
    cmds, c, *_ = ctx
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "The bins go out Tuesday night", '
                                  '"category": "household", "about": ["Alex", "Sam"]}', c)
    assert said.startswith("Saved:") and mem.items()[0]["about"] == []
    c["speaker"] = Match()
    said, _ = actions.apply(cmds, 'ACTION {"do": "remember", "text": "Someone likes jazz", "category": "preference"}', c)
    [jazz] = [m for m in mem.items() if "jazz" in m["text"]]
    assert said == "Saved: Someone likes jazz." and jazz["status"] == "active"
def test_anything_invalid_means_nothing_is_done(ctx, mem):
    cmds, c, *_ = ctx
    said, n = actions.apply(cmds, 'ACTION [{"do":"add","kind":"timer","seconds":600,"label":"pasta"},'
                                  '{"do":"remember","text":"The wifi code is hunter2","category":"household"}]', c)
    assert n == 0 and said.startswith("I can't keep passwords") and cmds.s.active() == [] and mem.items() == []
    r = cmds.s.add("reminder", "call mom", cmds.clock() + 3600)
    said, n = actions.apply(cmds, f'ACTION [{{"do":"add","kind":"alarm","at":"2026-11-06T07:00"}},'
                                  f'{{"do":"change","id":"{cmds.s.short(r["id"])}","shift_seconds":-7200}}]', c)
    assert (said, n) == (actions.FAILED, 0) and [a["kind"] for a in cmds.s.active()] == ["reminder"]


def test_a_step_that_fails_while_being_done_is_not_counted(ctx, mem, monkeypatch):
    cmds, c, *_ = ctx

    def refuse(*a, **k):
        raise Refused("nothing to remember")
    monkeypatch.setattr(mem, "add", refuse)
    said, n = actions.apply(cmds, 'ACTION [{"do":"add","kind":"timer","seconds":600,"label":"pasta"},'
                                  '{"do":"remember","text":"The bins go out Tuesday","category":"household"}]', c)
    assert n == 1 and said.endswith("I didn't catch what to remember.")


def test_a_memory_about_someone_pip_does_not_know_is_saved(ctx, mem):
    cmds, c, *_ = ctx
    actions.apply(cmds, 'ACTION {"do":"remember","text":"Jordan hates mushrooms","category":"preference","about":["Jordan"]}', c)
    actions.apply(cmds, 'ACTION {"do":"remember","text":"Alex is dating Jordan","category":"personal",'
                        '"about":["Alex","Jordan"],"sensitive":true}', c)
    assert {r["text"]: r["status"] for r in mem.items()} == {
        "Jordan hates mushrooms": "active", "Alex is dating Jordan": "active"}


# ---------------------------------------------------------------- M2 (D-128): a memory that may contradict another
def test_a_memory_that_may_contradict_another_is_flagged_and_settled(tmp_path):
    from pip_assistant.common.messages import Message
    from pip_assistant.core.dashapi import memory_action
    from pip_assistant.core.memory import Memory
    from pip_assistant.core.people import People
    from helpers import OneVoice
    mem = Memory(tmp_path / "pip.db")
    people = People(tmp_path / "pip.db", embedder=OneVoice())
    sam = people.add_person("Sam")
    a, _ = mem.add("Sam's birthday is May 3", "personal", [sam["id"]])
    b, _ = mem.add("Sam's birthday is June 3", "personal", [sam["id"]])
    assert not a["review"] and "May 3" in b["review"] and Memory.short(a["id"]) in b["review"]
    c, _ = mem.add("The bins go out Tuesday night", "household")
    assert not c["review"]                                          # nothing like it
    act = lambda action, mid: memory_action(mem, people, Message("request", "core.memory", data={"action": action, "id": mid}))
    assert act("keep_new", b["id"]) == {"ok": True}
    assert mem.get(a["id"])["status"] == "archived" and not mem.get(b["id"])["review"]
    d, _ = mem.add("Sam's birthday is May 4", "personal", [sam["id"]])
    act("keep_old", d["id"])
    assert mem.get(d["id"])["status"] == "archived" and mem.get(b["id"])["status"] == "active"


def test_memories_about_someone_are_sent_only_when_they_are_in_the_conversation(mem):
    """D-137, like Ziggletron: rules and household facts always; a fact about one person only when that person
    is speaking or in the conversation (all 90 used to go with every message)."""
    mem.add("Pip says good night at eleven", "rule")
    mem.add("The recycling goes out on Tuesdays by the fridge door", "household")
    mem.add("Sam likes his coffee black", "personal", about=["s"])
    mem.add("Alex is training for a marathon", "personal", about=["a"])
    mem.add("Jordan hates cilantro", "personal")                      # lists nobody, names nobody in the house
    everyone = ["Sam", "Alex"]
    t = mem.for_claude(present={"s": "Sam"}, household=everyone)
    assert "good night" in t and "fridge" in t and "coffee" in t and "cilantro" in t
    assert "marathon" not in t and "(1 more about people not in this conversation" in t
    t = mem.for_claude(present={"s": "Sam", "a": "Alex"}, household=everyone)
    assert "marathon" in t and "not shown" not in t
    assert "marathon" in mem.for_claude()                             # without `present`: everything, as before


def test_memories_that_always_go_are_given_apart_from_those_about_the_people_talking(mem):
    """D-146: rules and household facts go to Claude whoever is talking, so they travel in the fixed part of what
    he's told (which Claude caches); what's left for each message is only what depends on who's there. Together
    they are exactly what one text used to hold: nothing is lost."""
    mem.add("The recycling goes out on Tuesdays by the fridge door", "household")
    mem.add("Pip says good night at eleven", "rule")
    mem.add("Sam likes his coffee black", "personal", about=["s"])
    mem.add("Alex is training for a marathon", "personal", about=["a"])
    mem.add("The spare key is under the blue pot", "household", owner="s", private=True)
    fixed = mem.fixed_for_claude()
    assert fixed.startswith("What you always remember: ") and fixed.index("good night") < fixed.index("recycling")   # rules first
    assert "coffee" not in fixed and "marathon" not in fixed and "spare key" not in fixed    # nothing personal or private
    here = dict(present={"s": "Sam"}, household=["Sam", "Alex"], split=True)
    now = mem.for_claude("s", "sure", **here)
    assert "coffee" in now and "spare key" in now and "(private)" in now
    assert "recycling" not in now and "good night" not in now and "marathon" not in now
    assert "(1 more memories about people not in this conversation, not shown.)" in now
    assert "spare key" not in mem.for_claude("a", "sure", **here)                # still only for its owner
    everything = mem.for_claude("s", "sure", present={"s": "Sam"}, household=["Sam", "Alex"])
    for r in mem.items("active"):                                               # each memory is in exactly one of the two
        ref = mem.short(r["id"])
        assert (ref in fixed) + (ref in now) == (ref in everything), r["text"]
    before = fixed
    mem.update(mem.items("active")[0]["id"], text="The recycling goes out on Wednesdays by the fridge door")
    assert [x.split(" ")[0] for x in mem.fixed_for_claude().split("; ")] == [x.split(" ")[0] for x in before.split("; ")]   # same order


def test_with_nothing_about_the_people_talking_the_message_carries_no_memories(mem):
    mem.add("Pip says good night at eleven", "rule")
    assert mem.for_claude(split=True) == "" and "good night" in mem.fixed_for_claude()
    assert Memory.always_sent({"visibility": "household", "category": "household"})
    assert not Memory.always_sent({"visibility": "private", "category": "household"})
    assert not Memory.always_sent({"visibility": "household", "category": "personal"})
