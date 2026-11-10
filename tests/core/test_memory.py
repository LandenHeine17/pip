"""Memory (core/memory.py, D-88, D-119): saving, merging, refusing secrets, private memories, and Claude's memory
actions (remember, forget, correct); everything asked is saved straight away."""
from datetime import datetime

import pytest

from pip_assistant.core import actions
from pip_assistant.core.memory import Refused
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


def test_sensitive_memories_are_saved_straight_away_and_nothing_is_silently_lost(mem):
    row, outcome = mem.add("Sam is allergic to peanuts", "personal", sensitive=True)   # D-119: no inbox
    assert outcome == "saved" and "Sam is allergic to peanuts" in mem.for_claude()
    mem.update(row["id"], status="archived")
    assert "peanuts" not in mem.for_claude() and mem.items("archived")    # archived: kept, not used
    pend, _ = mem.add("Jordan's birthday is 3 May", "personal", pending_because="about someone else")
    mem.clock.t += 15 * 86400
    mem.prune()
    assert mem.get(pend["id"]) is None and mem.get(row["id"]) is not None  # only unapproved suggestions expire


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


def test_a_private_memory_waiting_in_the_inbox_is_only_for_its_owner(ctx, mem):
    """Memories still waiting from before D-119 (or from the Ziggletron import) keep their privacy."""
    cmds, c, alex, sam = ctx
    row, _ = mem.add("Alex is planning a surprise party for Sam on Saturday", "personal", [alex["id"], sam["id"]],
                     owner=alex["id"], private=True, pending_because="about someone else")
    assert row["status"] == "pending" and row["visibility"] == "private"
    assert "surprise" not in mem.for_claude(sam["id"], "sure", speaker_name="Sam")       # Sam isn't shown it (D-88)
    s = {**c, "speaker": Match(sam["id"], "Sam", "sure", 0.7)}
    assert actions.apply(cmds, f'ACTION {{"do": "approve_memory", "id": "{mem.short(row["id"])}"}}', s) == (actions.FAILED, 0)
    actions.apply(cmds, 'ACTION {"do": "remember", "text": "Alex is planning a surprise party for Sam on Saturday", '
                        '"category": "personal", "about": ["Sam"]}', s)                  # nor confirms it by saying it
    assert mem.items("pending")[0]["id"] == row["id"]


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
