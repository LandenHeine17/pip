"""pip-builder: Pip's self-changes (D-152). A small bus program: it hands each change to Claude Hub, follows it
there and in git, undoes live ones, and asks pip-core to tell the room when one goes live (changes.py).

Takes:  builder.ask (request) data {source: voice|chat, asked_by}, private {summary, request}
            → data {id} or {error}
        builder.page (request) private {action: list | undo, id?} → private reply:
            list → {changes: [...], hub: {url, folder}, undoing}; undo {id} → {ok: true} or {error}
Sends:  builder.changed {id, status} on every status change (never the words of a request)
        core.announce (request to pip-core) private {text}: say this once he's free → {said}
Heartbeat: stage = "idle", "<n> open" or "undoing".
"""
import os
from pathlib import Path

from ..common import paths, settings
from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .changes import Builder
from .store import Store

import logging
log = logging.getLogger("pip-builder")
DEFAULTS = {"enabled": True, "hub_inbox": "~/.local/state/claude-hub/inbox", "hub_url": "", "model": "",
            "keep_days": 90}


def builder_settings() -> dict:
    """The [builder] settings, read fresh each time (so a change on the Settings page applies straight away)."""
    try:
        return {**DEFAULTS, **settings.load().get("builder", {})}
    except Exception:
        log.exception("builder: couldn't read settings; using the defaults")
        return dict(DEFAULTS)


def wire(bus, builder: Builder) -> None:
    async def ask(msg: Message) -> dict:
        p = msg.private or {}
        return await builder.ask(str(p.get("summary", "")), str(p.get("request", "")),
                                 str(msg.data.get("asked_by", "")), str(msg.data.get("source", "voice")))

    async def page(msg: Message) -> Message:
        p = msg.private or {}
        result = await builder.page(str(p.get("action", "")), p)
        return msg.reply({"ok": "error" not in result}, private=result)

    bus.on("builder.ask", ask)
    bus.on("builder.page", page)


async def main() -> None:
    bus, cfg = make_bus("builder")
    repo = Path(os.environ.get("PIP_REPO", Path.home() / "pip"))   # where Claude Hub's chat works and commits

    async def publish(t: str, data: dict) -> None:
        if bus.connected.is_set():
            await bus.emit(t, data)

    async def announce(text: str) -> bool:
        if not bus.connected.is_set():
            return False
        r = await bus.request("core", "core.announce", {"why": "change"}, {"text": text}, timeout=10)
        return bool(r.data.get("said"))

    builder = Builder(Store(paths.database()), repo=repo, data=paths.data_dir() / "builder",
                      settings_fn=builder_settings, publish=publish, announce=announce)

    def on_stage(stage: str) -> None:
        bus.status["stage"] = stage
    builder.on_stage = on_stage
    wire(bus, builder)
    await builder.start()
    try:
        await serve(bus, builder.loop)
    finally:
        await builder.shutdown()


def run() -> None:
    start("builder", main)


if __name__ == "__main__":
    run()
