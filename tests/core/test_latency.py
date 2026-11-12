"""tools/latency.py: where the seconds go between someone stopping talking and Pip's voice, from the trace."""
import importlib.util

from helpers import REPO

from pip_assistant.common.messages import Message
from pip_assistant.core.trace import Trace

spec = importlib.util.spec_from_file_location("latency", REPO / "tools/latency.py")
latency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latency)


def trace(tmp_path, events):
    t = Trace(tmp_path / "pip.db")
    for ts, typ, data in events:
        t.add(Message("event", typ, source="core", data=data, ts=1000.0 + ts))
    t.flush()
    return latency.load(tmp_path / "pip.db", hours=1e6)


def test_a_spoken_answer_is_broken_into_its_stages(tmp_path):
    rows = trace(tmp_path, [
        (0.0, "heard.utterance", {"reason": "wake", "quiet_s": 0.7}),
        (0.4, "stt.done", {"reason": "wake", "chars": 20}),
        (0.6, "turn.decision", {"decision": "answered", "why": "short", "since_speech_s": 1.3}),
        (0.6, "thinker.ask", {"answer_id": "a1", "reason": "wake", "kind": "direct"}),
        (2.6, "answer.sentence", {"answer_id": "a1"}),
        (2.9, "speech.started", {}),
        (3.0, "thinker.ask", {"total_s": 2.5, "passed": False, "warm": True}),
    ])
    rep = latency.report(rows)
    g = rep["groups"]["direct/spoke"]
    assert g["stt"]["p50"] == 0.4 and g["hold"]["p50"] == 0.2 and g["claude"]["p50"] == 2.0
    assert g["voice"]["p50"] == 0.3 and g["warm_share"] == 1.0
    assert rep["spoken_direct"]["p50"] == 3.6                     # 1.3 s since speech + 2.3 s to his voice
    assert "p50" in latency.show(rep)


def test_fast_commands_and_talk_overs_are_counted(tmp_path):
    rows = trace(tmp_path, [
        (0.0, "heard.utterance", {"reason": "follow_up", "quiet_s": 0.7}),
        (0.4, "stt.done", {"reason": "follow_up", "chars": 20}),
        (1.0, "turn.decision", {"decision": "fast_command", "why": "sure", "since_speech_s": 1.7, "reason": "follow_up"}),
        (1.3, "speech.started", {}),
        (5.0, "speech.interrupted", {"level_dbfs": -30}),
        (6.0, "heard.noise", {"reason": "barge_in"}),
        (9.0, "speech.overlap_ignored", {"level_dbfs": -40}),
    ])
    rep = latency.report(rows)
    assert rep["groups"]["fast/fast"]["total"]["p50"] == 2.0
    assert rep["talk_overs"] == {"noise": 1, "ignored (no voice)": 1}
