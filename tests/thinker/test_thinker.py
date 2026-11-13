"""pip-thinker (thinker/): splitting Claude's answer into sentences to say, reading the Claude CLI's stream, holding back
ACTION replies, and PASS / SAY (only words after SAY are spoken)."""
import asyncio
import json

from pip_assistant.thinker.claude import (
    ClaudeCLI,
    SentenceSplitter,
    classify,
    is_action,
    speakable,
)
from pip_assistant.thinker.main import Thinker


def test_sentence_splitter_streams_and_cleans():
    sp = SentenceSplitter()
    out = []
    for piece in ["It's **cool** today", ", about 53 degrees. Grab a", " jacket! See [AccuWeather](https://x.y)",
                  " for more.\n\nSources:\n- [AccuWeather](https://x)"]:
        out += sp.feed(piece)
    out += sp.flush()
    assert out == ["It's cool today, about 53 degrees.", "Grab a jacket!", "See AccuWeather for more."]


def test_a_long_first_clause_is_spoken_at_its_comma():
    """D-122: the first piece may be cut at a comma after 60 characters, so his voice starts sooner; later ones
    only after 160. Only where there's a comma past the first 40 characters."""
    sp = SentenceSplitter()
    first = sp.feed("Well, the bus that goes downtown from the stop by the library, ")
    assert first == ["Well, the bus that goes downtown from the stop by the library,"]
    assert sp.feed("comes every twenty minutes on weekdays, and every half hour on weekends") == []
    assert sp.feed(". Sundays too.") == ["comes every twenty minutes on weekdays, and every half hour on weekends."]
    assert sp.flush() == ["Sundays too."]
    assert SentenceSplitter().feed("No commas at all in this one that is longer than sixty letters so far") == []


def test_speakable_removes_links_and_markdown():
    assert speakable("# Hi *there* https://example.com now") == "Hi there now"


async def test_claude_reader_streams_sentences_and_spots_search(tmp_path):
    events = [{"type": "system"},
              {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "server_tool_use"}}},
              {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "It's 53 degrees. "}}},
              {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Bring a jacket"}}},
              {"type": "result", "is_error": False, "total_cost_usd": 0.01}]
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\ncat <<'EOF'\n" + "\n".join(json.dumps(e) for e in events) + "\nEOF\n")
    fake.chmod(0o755)
    cli = ClaudeCLI()
    cli.exe = str(fake)
    said, searched = [], []

    async def on_sentence(s):
        said.append(s)

    async def on_search():
        searched.append(True)

    text, info = await cli.ask("a1", "system", "hi", on_sentence, on_search)
    assert said == ["It's 53 degrees.", "Bring a jacket"] and searched == [True]
    assert info["searched"] and info["first_sentence_s"] is not None
    cli.cancel()


async def test_action_replies_are_never_spoken(tmp_path, monkeypatch):
    """pip-thinker holds back an ACTION reply instead of speaking it."""
    cli = ClaudeCLI()
    lines = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": t}}}
             for t in ("AC", "TION {\"do\": \"add\", ", "\"kind\": \"timer\", \"seconds\": 60}")]
    lines.append({"type": "result", "is_error": False})

    class FakeProc:
        returncode = 0
        stdin = type("S", (), {"write": lambda self, b: None, "close": lambda self: None,
                               "drain": lambda self: asyncio.sleep(0)})()

        def __init__(self):
            async def gen():
                for line in lines:
                    yield (json.dumps(line) + "\n").encode()
            self.stdout = gen()

        async def wait(self):
            return 0

    async def spawn(prompt):
        return FakeProc()

    async def prepare(prompt):
        return None

    monkeypatch.setattr(cli, "_spawn", spawn)
    monkeypatch.setattr(cli, "prepare", prepare)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    async def on_searching():
        pass

    text, info = await cli.ask("a1", "sys", "hi", on_sentence, on_searching)
    assert spoken == [] and text == "" and info["action"].startswith("ACTION")
    assert is_action("  ACTION {}") and not is_action("Sure, done.")

# ---------------------------------------------------------------- PASS / SAY


def test_pass_and_say_are_understood():
    assert classify("PASS", final=True) == ("pass", "")
    assert classify("SAY: Oh nice", held=True) == ("speak", "Oh nice")
    assert classify("They're chatting about", held=True) is None            # thinking aloud: not spoken
    assert classify("They're chatting about sport.", held=True, final=True) == ("pass", "")
    assert classify("Eight.", final=True) == ("speak", "Eight.")
    assert classify("Passing through?", final=True) == ("speak", "Passing through?")
    text = Thinker.user_text("", ["[10:01] Landen: I wonder how far the moon is"], now="Sat", kind="join", mood="Be nice.")
    assert "Landen: I wonder" in text and "SAY:" in text and "PASS" in text and "Be nice." in text


async def test_only_words_after_say_are_spoken(monkeypatch):
    cli = ClaudeCLI()
    pieces = ("Hmm, they're talking about", " space. ", "SAY: About ", "239,000 miles.")
    lines = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": t}}} for t in pieces]
    lines.append({"type": "result", "is_error": False})

    class FakeProc:
        returncode = 0
        stdin = type("S", (), {"write": lambda self, b: None, "close": lambda self: None,
                               "drain": lambda self: asyncio.sleep(0)})()

        def __init__(self):
            async def gen():
                for line in lines:
                    yield (json.dumps(line) + "\n").encode()
            self.stdout = gen()

        async def wait(self):
            return 0

    async def spawn(prompt):
        return FakeProc()

    async def prepare(prompt):
        return None

    monkeypatch.setattr(cli, "_spawn", spawn)
    monkeypatch.setattr(cli, "prepare", prepare)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    async def nothing():
        pass

    text, info = await cli.ask("a1", "sys", "hi", on_sentence, nothing, held=True)
    assert spoken == ["About 239,000 miles."] and not info["passed"]
    lines[:] = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "PASS"}}},
                {"type": "result", "is_error": False}]
    spoken.clear()
    text, info = await cli.ask("a2", "sys", "hi", on_sentence, nothing, held=True)
    assert spoken == [] and info["passed"]


async def test_what_pip_sends_keeps_sizes_and_tokens_never_the_words(monkeypatch):
    """Owner's feedback (2026-11-10): a page showing what Pip sends to Claude and how many tokens it uses. The
    fixed part is shown in full; each request only as sizes and Claude's token counts (CLAUDE.md rule 1)."""
    from collections import deque

    from pip_assistant.common.messages import Message

    class Weather:
        async def text(self):
            return "sunny"
    th = Thinker.__new__(Thinker)
    th.recent, th.module_texts, th.location, th.weather = deque(maxlen=20), {"calendar": "Calendar: …"}, "Lincoln", Weather()
    secret = "my bank PIN is in the drawer"
    msg = Message("request", "thinker.ask", data={"kind": "direct"},
                  private={"text": secret, "transcript": [f"Sam: {secret}"], "memories": "Sam likes jazz", "schedule": "a timer"})
    th.remember_request("direct", "SYSTEM" * 100, msg, "USER" * 50, {"usage": {"input_tokens": 12, "cache_read_input_tokens": 3400,
                                                                                "output_tokens": 30}, "cost_usd": 0.012})
    reply = await th.on_prompt(Message("request", "thinker.prompt"))
    [r] = reply.private["recent"]
    assert r["system_chars"] == 600 and r["user_chars"] == 200 and r["tokens"]["cache_read_input_tokens"] == 3400
    assert r["parts"]["memories"] == len("Sam likes jazz") and r["parts"]["lines heard"] == 1
    assert secret not in repr(reply.private["recent"]) and "jazz" not in repr(reply.private["recent"])   # never the words
    fixed = reply.private["fixed"]
    assert "sunny" in fixed["rules"] and fixed["modules"] == {"calendar": "Calendar: …"} and fixed["personality"]


def test_narrating_the_request_is_not_said():
    # owner, 2026-11-12: "Landen's asking what I just did. Honest answer, short: I jumped in..."
    sp = SentenceSplitter()
    out = sp.feed("Landen's asking what I just did. Honest answer, short: I jumped in and got it wrong. ") + sp.flush()
    assert out == ["I jumped in and got it wrong."]
    sp = SentenceSplitter()
    out = sp.feed("Caleb's asking about upgrading me to an H200. Ha, I'd take it. ") + sp.flush()
    assert out == ["Ha, I'd take it."]
    sp = SentenceSplitter()                                          # only notes: nothing said, and it shows
    out = sp.feed("Caleb's asking what I think of putting my brain in a talking fish. Keep it short and cheeky.") + sp.flush()
    assert out == [] and sp.notes == 2
    for note in ("Be playful here.", "One-liner.", "Short and cheeky: no way."):
        sp = SentenceSplitter()
        assert (sp.feed(note + " Ha, never.") + sp.flush())[-1] == "Ha, never." and len(sp.feed("")) == 0, note
    sp = SentenceSplitter()
    assert sp.feed("Keep it simple, go with the blue one. ") + sp.flush() == ["Keep it simple, go with the blue one."]
    sp = SentenceSplitter()                                          # ordinary answers are untouched
    out = sp.feed("Seven: thirty minutes, then. Landen's asking too. ") + sp.flush()
    assert out == ["Seven: thirty minutes, then.", "Landen's asking too."]
    sp = SentenceSplitter()
    assert sp.feed("Your alarm's at 7:30. ") + sp.flush() == ["Your alarm's at 7:30."]


async def test_a_reply_of_only_notes_is_asked_again(monkeypatch):
    """owner, 2026-11-12: "Caleb's asking what I think of... Keep it short and cheeky." and nothing else."""
    from pip_assistant.common.messages import Message
    from pip_assistant.thinker import main as thinker_main
    replies = [("", {"only_notes": True, "passed": False}), ("Glub glub, sign me up.", {"passed": False})]
    asked = []

    class Notes:
        async def ask(self, aid, system, user, *a, **kw):
            asked.append(user)
            return replies[len(asked) - 1]

    th = object.__new__(Thinker)
    th.claude, th.cancelled = Notes(), set()
    th.paused = lambda: False
    th.remember_request = lambda *a: None

    async def system_prompt(persona=None):
        return "sys"

    async def _ask(cli, kind, aid, *args, **kw):
        return await cli.ask(aid, *args, **kw)
    th.system_prompt, th._ask = system_prompt, _ask
    th.bus = type("B", (), {"emit": lambda self, *a, **k: asyncio.sleep(0)})()
    reply = await th.on_ask(Message("request", "thinker.ask", source="core", id="r1",
                                    data={"answer_id": "a1", "kind": "direct"}, private={"text": "what do you think?"}))
    assert reply.private["text"] == "Glub glub, sign me up." and reply.data["asked_again"]
    assert len(asked) == 2 and asked[1].endswith(thinker_main.AGAIN)
