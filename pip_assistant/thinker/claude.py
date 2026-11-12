"""Claude through the Claude Code CLI on the owner's Pro plan (D-05, D-17).

One fresh CLI process per answer (so two answers can never share a session: Ziggletron's bug P-01),
but the *next* process is always started in advance, so it's warm and ready (~1 s saved).
The answer streams in and is cut into spoken sentences as it arrives.
"""
import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time

log = logging.getLogger(__name__)

SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+")
URL = re.compile(r"https?://\S+|\[([^\]]+)\]\([^)]+\)")


def speakable(text: str) -> str:
    """Turn text into something sensible to say out loud."""
    text = URL.sub(lambda m: m.group(1) or "", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    """Feed text pieces; get back complete sentences. Stops at a trailing 'Sources:' block."""

    def __init__(self, long_after: int = 160):
        self.buf = ""
        self.long_after = long_after
        self.stopped = False

    def feed(self, piece: str) -> list[str]:
        if self.stopped:
            return []
        self.buf += piece
        m = re.search(r"\n\s*\**sources?\**\s*:", self.buf, re.I)
        if m:
            self.buf = self.buf[:m.start()]
            self.stopped = True
        out = []
        while True:
            m = SENTENCE_END.search(self.buf)
            if not m:
                if len(self.buf) > self.long_after and "," in self.buf[40:]:
                    cut = self.buf.rindex(",") + 1
                    out.append(self.buf[:cut]); self.buf = self.buf[cut:]
                break
            out.append(self.buf[:m.end()]); self.buf = self.buf[m.end():]
        return [s for s in (speakable(x) for x in out) if s]

    def flush(self) -> list[str]:
        rest, self.buf = speakable(self.buf), ""
        return [rest] if rest else []


def is_action(text: str) -> bool:
    return text.lstrip(" `\n").upper().startswith("ACTION")


SAY = re.compile(r"\bSAY:\s*")


def classify(head: str, held: bool = False, final: bool = False):
    """How to treat a reply from its start (D-84, D-92). Returns (mode, text to speak) or None if it's too
    early to tell. Modes: "speak", "action" (an ACTION line for pip-core, never spoken), "pass" (stay quiet).
    held=True (joining in): nothing is spoken unless it comes after "SAY:"; anything else is thinking aloud."""
    h = head.lstrip(" `\n")
    if not final and len(h) < 6 and not SAY.search(head):
        return None
    if is_action(h):
        return "action", ""
    if re.match(r"PASS\b", h):
        return "pass", ""
    if m := SAY.search(head):
        return "speak", head[m.end():]
    if held:
        return ("pass", "") if final else None
    return "speak", head


class ClaudeCLI:
    """Runs `claude -p` with Pip's system prompt, no tools but web search, and no project or user settings."""

    def __init__(self, model: str = "", web_search: bool = True, timeout: float = 45.0, effort: str = ""):
        self.exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        self.model, self.web_search, self.timeout, self.effort = model, web_search, timeout, effort
        self.workdir = tempfile.mkdtemp(prefix="pip-thinker-")  # neutral folder: no project files loaded
        self.warm: asyncio.subprocess.Process | None = None
        self.warm_prompt: str | None = None
        self.current: dict[str, asyncio.subprocess.Process] = {}

    def _args(self, system_prompt: str) -> list[str]:
        tools = ["--tools", "WebSearch", "--allowedTools", "WebSearch"] if self.web_search else ["--tools", ""]
        model = (["--model", self.model] if self.model else []) + (["--effort", self.effort] if self.effort else [])
        return [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--system-prompt", system_prompt, *tools, *model,
                "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]

    async def _spawn(self, system_prompt: str):
        return await asyncio.create_subprocess_exec(
            *self._args(system_prompt), cwd=self.workdir, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024)

    async def prepare(self, system_prompt: str) -> None:
        """Start the next process in advance (it waits for its question)."""
        if self.warm and self.warm.returncode is None and self.warm_prompt == system_prompt:
            return
        if self.warm and self.warm.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.warm.kill()
        self.warm, self.warm_prompt = await self._spawn(system_prompt), system_prompt

    async def ask(self, answer_id: str, system_prompt: str, user_text: str, on_sentence, on_searching,
                  held: bool = False, on_result=None):
        """Stream an answer. Calls on_sentence(text) per sentence and on_searching() once if it searches.
        held=True: only words after "SAY:" are spoken (joining in). Returns (full_text, info); info["passed"]
        is True when Claude chose to stay quiet.
        on_result(cost_usd, is_error, error_text) is called once the CLI reports how the request went (the
        usage meter, common/usage.py); error_text is the CLI's own message ("usage limit reached…"), never
        anything that was said."""
        warm = bool(self.warm and self.warm.returncode is None and self.warm_prompt == system_prompt)
        if warm:
            proc, self.warm = self.warm, None
        else:
            proc = await self._spawn(system_prompt)   # a cold start: about a second slower
        asyncio.create_task(self.prepare(system_prompt))  # warm up the next one now
        self.current[answer_id] = proc
        t0 = time.perf_counter()
        info = {"first_sentence_s": None, "searched": False, "warm": warm}
        split, full = SentenceSplitter(), []
        mode, head, pieces = None, "", []  # mode: "speak", "action" (for pip-core, never spoken) or "pass" (quiet)

        async def speak(piece: str):
            for s in split.feed(piece):
                info["first_sentence_s"] = info["first_sentence_s"] or round(time.perf_counter() - t0, 2)
                full.append(s)
                await on_sentence(s)
        try:
            proc.stdin.write((json.dumps({"type": "user", "message": {"role": "user", "content": user_text}}) + "\n").encode())
            await proc.stdin.drain()
            proc.stdin.close()

            async def read():
                nonlocal mode, head
                async for raw in proc.stdout:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if ev.get("type") == "stream_event":
                        e = ev.get("event", {})
                        if e.get("type") == "content_block_start" and \
                                e.get("content_block", {}).get("type") in ("server_tool_use", "tool_use"):
                            if not info["searched"]:
                                info["searched"] = True
                                await on_searching()
                        d = e.get("delta", {})
                        if d.get("type") == "text_delta":
                            piece = d.get("text", "")
                            pieces.append(piece)
                            if mode is None:
                                head += piece
                                decided = classify(head, held)
                                if decided is None:
                                    continue  # not sure yet whether it's an ACTION line, PASS or SAY:
                                mode, piece = decided
                            if mode == "speak":
                                await speak(piece)
                    elif ev.get("type") == "result":
                        info["is_error"] = bool(ev.get("is_error", False))
                        info["cost_usd"] = ev.get("total_cost_usd")
                        # Claude's own token counts (the dashboard's "What Pip sends" page): numbers only
                        u = ev.get("usage") or {}
                        info["usage"] = {k: u.get(k) for k in ("input_tokens", "cache_creation_input_tokens",
                                                               "cache_read_input_tokens", "output_tokens")}
                        if on_result is not None:
                            err = str(ev.get("result") or ev.get("subtype") or "")[:300] if info["is_error"] else ""
                            try:
                                on_result(info["cost_usd"], info["is_error"], err)
                            except Exception:
                                log.exception("usage callback failed")
            await asyncio.wait_for(read(), self.timeout)
            if mode is None:  # a very short answer ("Sure."), or a join-in that never said SAY:
                mode, rest = classify(head, held, final=True)
                if mode == "speak":
                    await speak(rest)
            if mode == "action":
                info["action"] = "".join(pieces)
            info["passed"] = mode == "pass"
            for s in split.flush():
                full.append(s)
                await on_sentence(s)
            await proc.wait()
            if proc.returncode not in (0, None) and not full and mode not in ("action", "pass"):
                err = (await proc.stderr.read()).decode(errors="replace")[-200:]
                raise RuntimeError(f"claude exited {proc.returncode}: {err}")
        finally:
            self.current.pop(answer_id, None)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        info["total_s"] = round(time.perf_counter() - t0, 2)
        return " ".join(full), info

    def cancel(self, answer_id: str | None = None) -> None:
        for aid, proc in list(self.current.items()):
            if answer_id is None or aid == answer_id:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
