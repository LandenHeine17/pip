"""Pip answering questions about himself (core/selfknow.py, common/capabilities.py; M2, D-128)."""
import asyncio
import json
import time

from helpers import finish, room
from helpers import room_setup as setup

from pip_assistant.common import capabilities
from pip_assistant.common.messages import Message
from pip_assistant.core import selfknow
from pip_assistant.core.trace import Trace


class Info:
    def __init__(self, name, summary, label="", says=()):
        self.name, self.summary, self.label, self.says_examples = name, summary, label, list(says)


def test_what_he_can_do_lists_built_ins_and_modules():
    t = capabilities.text([Info("calendar", "The household calendar.", "Calendar", ["what's on Friday?"]),
                           Info("quiet", "")])
    assert "timers, alarms and reminders" in t and 'Calendar: The household calendar (e.g. "what\'s on Friday?")' in t
    assert "quiet" not in t                                     # no summary, nothing to say


def test_why_is_told_from_the_trace_without_words(tmp_path):
    tr = Trace(tmp_path / "pip.db")
    now = time.time()
    for i, (ty, data) in enumerate([("stt.done", {"chars": 12, "reason": "follow_up", "speaker": "unknown"}),
                                    ("heard.phantom", {"reason": "follow_up"}),
                                    ("answer.passed", {"kind": "mentioned"}),
                                    ("turn.decision", {"decision": "answered", "why": "short", "since_speech_s": 1.1}),
                                    ("health.snapshot", {"x": 1})]):
        tr.add(Message("event", ty, data=data, ts=now - 60 + i))
    tr.add(Message("event", "stt.done", data={"chars": 3, "reason": "wake"}, ts=now - 3600))   # too old
    t = selfknow.why(tr, now)
    assert "a voice he doesn't know (follow_up)" in t and "\"Thank you.\"" in t and "wasn't said to him" in t
    assert "answered (short), 1.1 s after they stopped" in t and "wake" not in t and "health" not in t


def test_settings_are_read_out_with_their_labels():
    t = selfknow.settings_text({"conversation": {"turn_wait_short_seconds": 1.4, "follow_up_anyone": True}})
    assert "After a question: 1.4 s" in t and "Follow-ups from anyone: on" in t


async def test_claude_looking_up_why_gets_the_trace(tmp_path):
    from pip_assistant.core.schedule import Schedule
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path, "Pip, why didn't you answer me?", social={"join_in": False}, schedule=Schedule(tmp_path / "pip.db"),
        replies={"direct": ['ACTION {"do": "look", "kind": "why"}', "Your voice was a bit quiet."]})
    await ears.emit("heard.utterance", {**room()[0], "reason": "wake"}, private=room()[1])
    await asyncio.sleep(2.0)
    assert len(asks) == 2
    second = asks[1][1]
    assert second["looked_up"].startswith("What Pip heard and did in the last 10 minutes")
    assert "why didn't you answer" not in json.dumps(bus.trace.recent())   # (the words never reach the trace)
    await finish(bus, tasks)


def test_his_own_docs_and_code_answer_how_he_works(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PIP-SPEC.md").write_text("# Pip\n\n## Memory\nSaves things when you ask.\n\n## Timers\nRing.\n")
    (tmp_path / "pip_assistant" / "core").mkdir(parents=True)
    (tmp_path / "pip_assistant" / "core" / "memory.py").write_text('"""Memories: saved when asked."""\nx = 1\n')
    (tmp_path / "pip_assistant" / "core" / "alarms.py").write_text('"""Alarms."""\n')
    d = selfknow.docs_text("how does your memory work?", tmp_path)
    assert "[PIP-SPEC.md: Memory] Saves things when you ask." in d and "Ring" not in d
    c = selfknow.code_text("memory", tmp_path)
    assert "core/memory.py: Memories: saved when asked." in c and "alarms" not in c
    assert "Nothing" in selfknow.docs_text("bananas", tmp_path)
    assert "docs" in selfknow.KINDS and selfknow.docs_text("memory").startswith("From your own docs")   # the real ones
