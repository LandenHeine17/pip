"""The usage meter (D-69, PIP-NEW-089): counting Claude requests, spotting the usage limit, and pausing joining in."""
import asyncio
import time
from datetime import date, datetime, timedelta

import pytest

from pip_assistant.common import usage
from pip_assistant.common.messages import Message
from pip_assistant.common.usage import Usage, is_limit_error, paused_until, reset_time
from pip_assistant.core.joining import Joining
from pip_assistant.thinker.main import Thinker


def test_record_and_summaries(tmp_path):
    u = Usage(tmp_path / "pip.db")
    today = date(2026, 11, 9)
    u.record("answer", 0.02, day=today)
    u.record("answer", 0.03, day=today)
    u.record("join", None, error=True, day=today)
    u.record("whatever", 0.01, day=today)                          # unknown kinds count as "other"
    u.record("builder", 1.5, day=today - timedelta(days=3))
    u.record("answer", 9.0, day=today - timedelta(days=7))         # just outside the week
    t = u.today(today)
    assert t == {"requests": 4, "errors": 1, "cost_usd": 0.06, "by_kind": {"answer": 2, "join": 1, "other": 1}}
    w = u.week(today)
    assert w["requests"] == 5 and w["cost_usd"] == 1.56 and w["by_kind"]["builder"] == 1
    assert Usage(tmp_path / "pip.db").today(today)["requests"] == 4   # it's in the database, not in memory


def test_uses_pips_database_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    Usage().record("answer", 0.01)
    assert (tmp_path / "pip.db").exists() and Usage().today()["requests"] == 1


@pytest.mark.parametrize("text", ["Claude AI usage limit reached|1900000000", "5-hour limit reached ∙ resets 3pm",
                                  "You've hit your limit · resets 7pm (America/Chicago)", "API Error: 429 rate limit",
                                  "Usage limit reached. Resets at 15:30."])
def test_limit_messages_are_spotted(text):
    assert is_limit_error(text)


@pytest.mark.parametrize("text", ["", None, "claude exited 1: network unreachable", "Overloaded", "timed out"])
def test_other_errors_are_not_limits(text):
    assert not is_limit_error(text)


def test_reset_time_is_parsed():
    now = datetime(2026, 11, 9, 13, 0).timestamp()
    assert reset_time("Claude AI usage limit reached|1900000000", now) == 1900000000
    assert reset_time("limit reached ∙ resets 3pm", now) == datetime(2026, 11, 9, 15, 0).timestamp()
    assert reset_time("Usage limit reached. Resets at 3:30 PM", now) == datetime(2026, 11, 9, 15, 30).timestamp()
    assert reset_time("resets 12am", now) == datetime(2026, 11, 10, 0, 0).timestamp()
    assert reset_time("resets at 9:00", now) == datetime(2026, 11, 10, 9, 0).timestamp()    # already past: tomorrow
    assert reset_time("usage limit reached", now) is None
    assert paused_until("usage limit reached", now) == now + usage.DEFAULT_PAUSE          # no time given: an hour
    assert paused_until("limit reached|1000", now) == now + usage.DEFAULT_PAUSE           # a time in the past


# ---------------------------------------------------------------- joining in pauses (D-69)
class FakeTranscript:
    count = 1

    def __init__(self, clock):
        self.line = {"ours": False, "ts": clock() - 2}

    def last(self):
        return self.line

    def last_human(self):
        return self.line


class FakeBus:
    def __init__(self):
        self.listeners = []

    def core_listen(self, pattern, handler):
        self.listeners.append((pattern, handler))


class FakeConv:
    def __init__(self, clock):
        self.bus, self.transcript = FakeBus(), FakeTranscript(clock)
        self.answer_id = None

    def can_join(self, joining=False):
        return True


def test_joining_in_pauses_until_the_limit_resets():
    now = [1_000_000.0]
    clock = lambda: now[0]
    conv = FakeConv(clock)
    j = Joining(conv, {"social": {"quiet_hours": ""}}, clock=clock)
    assert j.ready()
    (pattern, handler), = conv.bus.listeners
    assert pattern == "usage.limited"
    asyncio.run(handler(Message("event", "usage.limited", source="thinker", data={"until": now[0] + 600})))
    assert not j.ready()
    now[0] += 601
    conv.transcript.line["ts"] = now[0] - 2
    assert j.ready()


# ---------------------------------------------------------------- pip-thinker counts and spots the limit
class FakeClientBus:
    def __init__(self):
        self.status, self.emitted = {}, []

    async def emit(self, msg_type, data=None, private=None):
        self.emitted.append((msg_type, data))


class FakeClaude:
    def __init__(self, cost=0.02, error="", text="Eight."):
        self.cost, self.error, self.text, self.asked = cost, error, text, 0

    async def ask(self, aid, system_prompt, user_text, on_sentence, on_searching, held=False, on_result=None):
        self.asked += 1
        on_result(self.cost, bool(self.error), self.error)
        return ("" if self.error else self.text), {"is_error": bool(self.error), "cost_usd": self.cost, "passed": False}

    def cancel(self, aid=None):
        pass


def make_thinker(tmp_path, claude):
    th = object.__new__(Thinker)
    th.bus, th.usage, th.claude, th.paused_until = FakeClientBus(), Usage(tmp_path / "pip.db"), claude, 0.0

    async def system_prompt(persona=None):
        return "rules"
    th.system_prompt = system_prompt
    return th


def ask(th, kind="direct"):
    return asyncio.run(th.on_ask(Message("request", "thinker.ask", source="core", target="thinker", id="r1",
                                         data={"answer_id": "a1", "kind": kind}, private={"text": "hi"})))


def test_thinker_counts_each_answer(tmp_path):
    th = make_thinker(tmp_path, FakeClaude(cost=0.05))
    reply = ask(th)
    assert reply.private["text"] == "Eight."
    assert th.bus.status["usage_today"]["by_kind"] == {"answer": 1} and th.bus.status["usage_today"]["cost_usd"] == 0.05
    assert th.bus.status["paused_until"] is None and not th.bus.emitted


def test_thinker_publishes_usage_limited_on_a_limit_error(tmp_path):
    at = int(time.time()) + 7200
    th = make_thinker(tmp_path, FakeClaude(cost=0, error=f"Claude AI usage limit reached|{at}"))
    reply = ask(th)
    assert reply.data.get("error")                                    # Pip says his brain isn't answering
    assert th.bus.emitted == [("usage.limited", {"until": at})]
    assert th.bus.status["paused_until"] == at and th.bus.status["usage_today"]["errors"] == 1
    claude = th.claude
    reply = ask(th, "join")                                           # joining in: not even asked while paused
    assert reply.data.get("passed") and claude.asked == 1
    claude.error = ""
    assert ask(th).private["text"] == "Eight." and claude.asked == 2   # replies to people keep trying (D-69)


def test_a_crash_with_a_limit_message_also_pauses(tmp_path):
    class Crashing(FakeClaude):
        async def ask(self, *a, **kw):
            raise RuntimeError("claude exited 1: You've hit your limit · resets 3pm")
    th = make_thinker(tmp_path, Crashing())
    assert ask(th).data.get("error")
    assert th.bus.emitted and th.bus.emitted[0][0] == "usage.limited"
    assert th.bus.status["usage_today"] == {"requests": 1, "errors": 1, "cost_usd": 0.0, "by_kind": {"answer": 1}}
