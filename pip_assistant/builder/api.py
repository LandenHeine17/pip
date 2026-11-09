"""What people do on the Changes page (and by voice): ask, answer, approve, reject, stop, undo (M1b).

These are the Builder's actions for requests from the dashboard (builder.page) and from voice (builder.ask).
They only check that the action makes sense right now and set the change's status; the work itself (plan,
build, check, put live) is done by the steps in pipeline.py. Builder (pipeline.py) includes them through
ChangesPage, so `builder.approve(...)` etc. work as before.
"""
import asyncio
import contextlib

from .checks import sandbox_stop_check
from .store import RUNNING, WAITING


class ChangesPage:
    """The Builder's page actions (mixed into Builder; they use its store, statuses and job)."""

    async def ask(self, text: str, asked_by: str, source: str) -> dict:
        text = (text or "").strip()
        cfg = self.cfg()
        if not cfg.get("enabled", True):
            return {"error": "Self-changes are switched off in Settings."}
        if not text:
            return {"error": "Say what you'd like changed."}
        if len(text) > 4000:
            return {"error": "That's too long: describe the change in a few sentences."}
        queued = self.busy() is not None or bool(self.store.with_status("asked")) or self.is_paused()
        cid = self.store.add(text, str(asked_by or "someone")[:80], source if source in ("voice", "dashboard") else "dashboard")
        self.store.log(cid, "status", "Asked" + (" (voice)" if source == "voice" else " (dashboard)"))
        await self._emit(cid, "asked")
        self.kick()
        return {"id": cid, "queued": queued}

    def _summary(self, c: dict) -> dict:
        return {"id": c["id"], "status": c["status"], "summary": c["summary"], "risk": c["risk"],
                "asked_by": c["asked_by"], "created": c["created"], "updated": c["updated"],
                "waiting": c["status"] in WAITING}

    def listing(self) -> dict:
        b = self.busy()
        return {"changes": [self._summary(c) for c in self.store.all()],
                "paused": dict(self.paused) if self.is_paused() else None, "busy": b["id"] if b else None}

    def detail(self, cid) -> dict:
        c = self.store.get(cid)
        if not c:
            return {"error": "There's no such change."}
        keys = ("id", "status", "request", "asked_by", "source", "questions", "answers", "plan", "summary", "risk",
                "files", "diff", "test_report", "log", "commit", "branch", "release", "error", "created", "updated",
                "cost_usd")
        return {"change": {k: c[k] for k in keys}}

    async def approve_plan(self, cid) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] != "plan_ready":
            return {"error": "Only a plan that's ready can be approved."}
        if not c["plan"].get("files"):
            return {"error": "This plan changes no files, so there's nothing to build."}
        await self.set_status(cid, "building")
        if self.is_paused():
            self.note(cid, "Claude's usage limit was reached: building starts when it resets.")
        self.kick()
        return {"ok": True}

    async def answer(self, cid, answers) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] != "questions":
            return {"error": "This change isn't waiting for answers."}
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            return {"error": "Answers must be a list of text."}
        need = len(c["questions"]) - len(c["answers"])
        if len(answers) < need or not all(a.strip() for a in answers[:need]):
            return {"error": "Please answer every question."}
        all_answers = c["answers"] + [a.strip()[:2000] for a in answers[:need]]
        self.ws.remove(cid)
        await self.set_status(cid, "asked", answers=all_answers)   # planned again, with the answers
        self.kick()
        return {"ok": True}

    async def approve(self, cid, read_diff: bool) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] != "ready":
            return {"error": "Only a change that has passed its checks can be approved."}
        if c["risk"] == "security" and read_diff is not True:
            return {"error": "This change touches security-sensitive code: tick \"I've read the diff\" first."}
        if self.job and not self.job.done():
            return {"error": "Something else is still running; try again in a moment."}
        self._live = self.store.with_status("live")
        unmerged = await asyncio.to_thread(self._unmerged_live, cid)
        if unmerged:
            names = ", ".join(f"#{u['id']} ({u['branch']})" for u in unmerged)
            return {"error": f"Change {names} isn't in main yet. Merge it into main first, or this change "
                             "would take it back out."}
        await self.set_status(cid, "deploying")
        self.job_id = cid
        self.job = asyncio.create_task(self._guard(self._deploy(cid)))
        return {"ok": True}

    async def reject(self, cid) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] not in WAITING | {"asked"}:
            return {"error": "Only a change that's waiting can be rejected."}
        await self.fail(cid, "", status="rejected")
        self.kick()
        return {"ok": True}

    async def stop(self, cid) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] not in RUNNING | {"asked"}:
            return {"error": "Only a change that's being planned, built or checked can be stopped."}
        cid = c["id"]
        if c["status"] in RUNNING:
            self.stopping.add(cid)
            if self.job and self.job_id == cid and not self.job.done():
                self.job.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.job
            await self.fail(cid, "Stopped on the dashboard.", status="stopped")
            await asyncio.to_thread(self.agent.stop, cid)        # the sandbox jobs outlive their client
            await asyncio.to_thread(sandbox_stop_check, cid)
            self.ws.remove(cid)
            self.stopping.discard(cid)
        else:
            await self.fail(cid, "Stopped on the dashboard.", status="stopped")
        self.kick()
        return {"ok": True}

    async def undo(self, cid) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] != "live" or not c["commit"]:
            return {"error": "Only a change that's live can be undone."}
        b = self.busy()
        if b or (self.job and not self.job.done()):
            return {"error": f"Change #{b['id'] if b else '?'} is in progress; undo when it's done."}
        await self.set_status(cid, "deploying", error="")
        self.note(cid, "Undoing it (a new commit that takes it back out).")
        self.job_id = cid
        self.job = asyncio.create_task(self._guard(self._undo(cid)))
        return {"ok": True}

    async def page(self, action: str, p: dict, asked_by: str = "") -> dict:
        """The Changes page's requests (builder.page)."""
        cid = p.get("id")
        if action == "list":
            return self.listing()
        if action == "get":
            return self.detail(cid)
        if action == "ask":
            r = await self.ask(p.get("text", ""), asked_by or "the dashboard", "dashboard")
            return {"id": r["id"]} if "id" in r else r
        handlers = {"approve_plan": lambda: self.approve_plan(cid), "reject": lambda: self.reject(cid),
                    "stop": lambda: self.stop(cid), "undo": lambda: self.undo(cid),
                    "answer": lambda: self.answer(cid, p.get("answers")),
                    "approve": lambda: self.approve(cid, p.get("read_diff"))}
        if action not in handlers:
            return {"error": f"Unknown action {action!r}."}
        return await handlers[action]()
