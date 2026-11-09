"""pip-builder: the self-change pipeline as a small bus program (M1b). The heavy work runs in sandboxed systemd
jobs with their own memory caps (sandbox.py); this program only keeps the record and starts the jobs.

Takes:  builder.ask (request) data {source: voice|dashboard, asked_by}, private {text}
            → data {id, queued} or {error}
        builder.page (request) private {action, id?, text?, answers?, read_diff?} → private reply:
            list → {changes: [...], paused, busy}; get {id} → {change: {...}}; ask {text} → {id};
            approve_plan / answer / approve / reject / stop / undo {id, …} → {ok: true} or {error}
        usage.limited (event) {until}: don't start a Claude run before then
Sends:  builder.changed {id, status} on every status change (never the words of a request)
        usage.limited {until} when Claude says the plan's usage limit was reached
Heartbeat: stage = the status of the change in progress, "paused" or "idle".
"""
import asyncio
import logging
import os
from pathlib import Path

from ..common import paths, settings
from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .agent import ClaudeAgent
from .pipeline import Builder
from .store import Store

log = logging.getLogger("pip-builder")
DEFAULTS = {"enabled": True, "model": "sonnet", "effort": "medium", "plan_minutes": 10, "build_minutes": 30,
            "keep_days": 90}


def builder_settings() -> dict:
    """The [builder] settings, read fresh for each job (so a change on the Settings page applies straight away)."""
    try:
        return {**DEFAULTS, **settings.load().get("builder", {})}
    except Exception:
        log.exception("builder: couldn't read settings; using the defaults")
        return dict(DEFAULTS)


def wire(bus, builder: Builder) -> None:
    async def ask(msg: Message) -> dict:
        return await builder.ask(msg.private.get("text", ""), msg.data.get("asked_by", ""),
                                 msg.data.get("source", "dashboard"))

    async def page(msg: Message) -> Message:
        p = msg.private or {}
        result = await builder.page(str(p.get("action", "")), p, asked_by=str(msg.data.get("asked_by", "")))
        return msg.reply({"ok": "error" not in result}, private=result)

    async def limited(msg: Message) -> None:
        try:
            builder.pause(float(msg.data.get("until")), str(msg.data.get("why") or "Claude's usage limit was reached"))
        except (TypeError, ValueError):
            return
        builder._update_stage()

    bus.on("builder.ask", ask)
    bus.on("builder.page", page)
    bus.subscribe("usage.limited", limited)


async def main() -> None:
    bus, cfg = make_bus("builder")
    try:
        from ..common.usage import Usage
        meter = Usage()
        usage_fn = lambda cost, err: meter.record("builder", cost, err)   # noqa: E731
    except Exception:
        usage_fn = None
    repo = Path(os.environ.get("PIP_REPO", Path.home() / "pip"))   # the git repo changes are committed to

    async def publish(t: str, data: dict) -> None:
        if bus.connected.is_set():
            await bus.emit(t, data)

    builder = Builder(Store(paths.database()), ClaudeAgent(), repo=repo, data=paths.data_dir() / "builder",
                      settings_fn=builder_settings, publish=publish, usage_fn=usage_fn)
    def on_limit(until: float) -> None:
        if bus.connected.is_set():
            asyncio.ensure_future(bus.emit("usage.limited", {"until": until, "from": "builder"}))
    builder.on_limit = on_limit

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
