"""The self-change pipeline: from a request to a live change, with the owner's approval twice (M1b).

    asked → planning → questions | plan_ready → building → checking → ready → deploying → live
    (or rolled_back, failed, rejected, stopped; a live change can be undone → undone)

This file is the state machine and its steps; the pieces it uses each have their own file:

- **Workspace** (workspace.py, Workspaces): a plain export of ~/pip's main branch with no .git, plus an
  untouched copy to diff against, and a .venv made before the agent runs. Deleted when the change ends.
- **Plan / build**: Claude Code in the agent sandbox (agent.py, sandbox.py). One change at a time; the next
  one waits as "asked" until the current one has ended.
- **Check** (_check() here, using checks.py): what changed (text only), forbidden paths, secrets, then the
  full test suite and an import check in the strict sandbox. Passing only makes it reviewable (AC-SM-05).
- **Deploy** (deploy.py, Deployer): a git worktree of ~/pip on a new branch, the changed files, a commit made
  from the approved summary (never the request, AC-SM-07) with the normal hooks, then `release.py install` as
  its own systemd job (releasejob.py, ReleaseRunner). The install restarts pip-builder too, so the builder
  watches for its exit file and picks up where it left off after a restart. Live: main is fast-forwarded if
  ~/pip is clean; else the branch stays.
- **Undo**: `git revert` as a new commit, released the same way.
- **What people do** (api.py, ChangesPage): ask, answer, approve, reject, stop and undo, from the Changes
  page or by voice.
"""
import asyncio
import contextlib
import json
import logging
import shutil
import time
from pathlib import Path

from ..release import EXIT_LIVE, EXIT_NEEDS_OWNER, EXIT_ROLLED_BACK, EXIT_STOPPED, release_id
from .api import ChangesPage
from .checks import (MAX_DIFF_CHARS, compute_diff, forbidden, risk_for, sandbox_stop_check, secret_problems,
                     strict_check)
from .deploy import Deployer, GitError
from .releasejob import ReleaseRunner
from .store import RUNNING, WAITING, Store
from .workspace import Workspaces

log = logging.getLogger("pip-builder")


# ============================================================================ the pipeline
LABELS = {
    "asked": "Waiting its turn", "planning": "Claude is planning it", "questions": "Pip has questions for you",
    "plan_ready": "Plan ready: approve it to build", "building": "Claude is building it",
    "checking": "Checking it (tests in the sandbox)", "ready": "Ready: review and approve to put it live",
    "deploying": "Putting it live", "live": "Live", "rolled_back": "Rolled back: it wasn't healthy",
    "failed": "Failed", "rejected": "Rejected", "stopped": "Stopped", "undone": "Undone",
}
MAX_QUESTION_ROUNDS = 3


class Builder(ChangesPage):
    def __init__(self, store: Store, agent, *, repo: Path, data: Path, settings_fn, publish=None,
                 checker=None, venv_maker=None, deployer=None, releaser=None, usage_fn=None, clock=time.time,
                 poll_seconds: float = 3.0):
        self.store, self.agent, self.repo = store, agent, Path(repo)
        self.data = Path(data)
        self.settings_fn = settings_fn                        # → the [builder] settings, read fresh per job
        self.publish = publish                                # async (type, data)
        self.checker = checker or strict_check
        self.ws = Workspaces(repo, self.data / "work", venv_maker)
        self.deployer = deployer or Deployer(repo, self.data / "deploy")
        self.releaser = releaser or ReleaseRunner(repo, self.data / "releases")
        self.usage_fn = usage_fn                              # (cost_usd, is_error) for the usage meter
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.job: asyncio.Task | None = None
        self.job_id: str | None = None
        self._live: list[dict] = []
        self.stopping: set[str] = set()
        self.paused: dict | None = None                       # {"until": unix, "why": str}
        self.stage = "idle"
        self.on_stage = None                                  # callback(stage) for the heartbeat
        self.on_limit = None                                  # callback(until) when our own run hit the limit

    # ------------------------------------------------------------------ bits
    def cfg(self) -> dict:
        return self.settings_fn()

    def busy(self) -> dict | None:
        """The one change in progress (anything not waiting its turn and not ended)."""
        found = self.store.with_status(*(RUNNING | WAITING | {"deploying"}))
        return found[0] if found else None

    def is_paused(self) -> bool:
        if self.paused and self.paused["until"] <= self.clock():
            self.paused = None
        return self.paused is not None

    def pause(self, until: float, why: str = "Claude's usage limit was reached") -> None:
        if until > self.clock() and (not self.paused or until > self.paused["until"]):
            self.paused = {"until": until, "why": why}
            log.info("builder: paused until %s", time.strftime("%H:%M", time.localtime(until)))

    def limit_reached(self, until: float) -> None:
        """Claude said the usage limit was reached during one of our runs: pause, and tell the others."""
        self.pause(until)
        if self.on_limit:
            with contextlib.suppress(Exception):
                self.on_limit(until)

    def note(self, cid, text: str, kind: str = "note") -> None:
        self.store.log(cid, kind, text)

    async def _emit(self, cid, status) -> None:
        if self.publish:
            with contextlib.suppress(Exception):
                await self.publish("builder.changed", {"id": str(cid), "status": status})

    async def set_status(self, cid, status: str, **fields) -> None:
        self.store.update(cid, status=status, **fields)
        self.store.log(cid, "status", LABELS.get(status, status))
        log.info("builder: change #%s is %s", cid, status)       # ids and statuses only, never the words
        self._update_stage()
        await self._emit(cid, status)

    def _update_stage(self) -> None:
        b = self.busy()
        self.stage = b["status"] if b else ("paused" if self.is_paused() else "idle")
        if self.on_stage:
            self.on_stage(self.stage)

    async def fail(self, cid, why: str, status: str = "failed", **fields) -> None:
        self.ws.remove(cid)
        await self.set_status(cid, status, error=why[:2000], **fields)

    def _record_usage(self, r) -> None:
        if self.usage_fn and r is not None and (r.cost_usd is not None or r.is_error):
            with contextlib.suppress(Exception):
                self.usage_fn(r.cost_usd, r.is_error)

    # ------------------------------------------------------------------ start-up and the loop
    async def start(self) -> None:
        """After a (re)start: anything a job was doing is lost: mark it failed and delete its workspace
        (AC-SM-04). Waiting changes keep theirs. A change being put live is picked up again."""
        for c in self.store.with_status(*RUNNING):
            await self.fail(c["id"], "Pip restarted while this was in progress; ask again if you still want it.")
        keep = {c["id"] for c in self.store.with_status(*WAITING)}
        for name in self.ws.existing():
            if name not in keep:
                shutil.rmtree(self.ws.path(name), ignore_errors=True)
        for c in self.store.with_status("deploying"):
            await self._recover_deploy(c)
        with contextlib.suppress(Exception):
            self.store.forget_old(float(self.cfg().get("keep_days", 90)))
        self._update_stage()
        self.kick()

    async def loop(self) -> None:
        """Re-check the queue now and then (a pause ends; old requests are forgotten hourly)."""
        last_forget = self.clock()
        while True:
            await asyncio.sleep(30)
            self._update_stage()
            self.kick()
            if self.clock() - last_forget > 3600:
                last_forget = self.clock()
                with contextlib.suppress(Exception):
                    self.store.forget_old(float(self.cfg().get("keep_days", 90)))

    def kick(self) -> None:
        """Start the next job if nothing is running."""
        if self.job and not self.job.done():
            return
        b = self.busy()
        job = None
        if b is not None and b["id"] in self.stopping:
            return
        if b is None:
            if self.is_paused() or not self.cfg().get("enabled", True):
                return
            waiting = self.store.with_status("asked")
            if waiting:
                job = self._plan(waiting[0]["id"])
        elif b["status"] == "building" and not self.is_paused():
            job = self._build(b["id"])
        elif b["status"] == "deploying":
            job = self._watch(b["id"])
        if job is not None:
            self.job_id = b["id"] if b else waiting[0]["id"]
            self.job = asyncio.create_task(self._guard(job))

    async def _guard(self, coro) -> None:
        cid = self.job_id
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never leave a change stuck mid-way
            log.exception("builder: job for change #%s failed", cid)
            c = self.store.get(cid)
            if c and c["status"] in RUNNING:
                await self.fail(cid, f"Something went wrong in Pip's builder: {type(e).__name__}: {str(e)[:300]}")
        finally:
            self.job_id = None
            asyncio.get_running_loop().call_soon(self.kick)

    async def _step(self, cid, kind: str, text: str) -> None:
        self.store.log(cid, kind, text)

    # ------------------------------------------------------------------ plan
    async def _plan(self, cid) -> None:
        await self.set_status(cid, "planning", error="")
        cfg = self.cfg()
        base = await asyncio.to_thread(self.deployer.main_sha)
        self.store.update(cid, base=base)
        self.note(cid, f"Workspace: a copy of Pip's code at {base[:10]} (no git), with its own Python environment.")
        work = await self.ws.prepare(cid, base)
        change = self.store.get(cid)
        r = await self.agent.plan(change, work, cfg, lambda k, t: self._step(cid, k, t))
        self._record_usage(r)
        if cid in self.stopping:
            return
        self.store.add_cost(cid, r.cost_usd)
        if r.limited_until:
            self.limit_reached(r.limited_until)
            self.ws.remove(cid)
            await self.set_status(cid, "asked", error="Claude's usage limit was reached; it'll be planned when it resets.")
            return
        if not r.ok:
            await self.fail(cid, f"Planning didn't work: {r.error}")
            return
        from .agent import parse_plan
        try:
            plan = parse_plan(r.text)
        except (ValueError, TypeError) as e:
            await self.fail(cid, f"Claude's plan couldn't be read ({e}).")
            return
        change = self.store.get(cid)
        risk = risk_for(plan["files"], plan["risk"])
        questions = list(change["questions"])
        rounds = sum(1 for s in change["log"] if s["kind"] == "status" and s["text"] == LABELS["questions"])
        if plan["questions"] and rounds < MAX_QUESTION_ROUNDS:
            await self.set_status(cid, "questions", plan=plan, summary=plan["summary"], risk=risk,
                                  files=plan["files"], questions=questions + plan["questions"])
            return
        await self.set_status(cid, "plan_ready", plan=plan, summary=plan["summary"], risk=risk, files=plan["files"])

    # ------------------------------------------------------------------ build and check
    async def _build(self, cid) -> None:
        cfg = self.cfg()
        work = self.ws.path(cid)
        change = self.store.get(cid)
        if not (work / "src").is_dir() or not change["base"]:
            base = await asyncio.to_thread(self.deployer.main_sha)
            self.store.update(cid, base=base)
            work = await self.ws.prepare(cid, base)
        self.note(cid, "Claude is making the change (it can only edit this copy and run the tests offline).")
        r = await self.agent.build(change, work, cfg, lambda k, t: self._step(cid, k, t))
        self._record_usage(r)
        if cid in self.stopping:
            return
        self.store.add_cost(cid, r.cost_usd)
        if r.limited_until:
            self.limit_reached(r.limited_until)
            await self.fail(cid, "Claude's usage limit was reached part-way through; ask again after it resets.")
            return
        if not r.ok:
            await self.fail(cid, f"Building didn't work: {r.error}")
            return
        if r.text:
            self.note(cid, r.text[:1500], "said")
        await self.set_status(cid, "checking")
        await self._check(cid)

    async def _check(self, cid) -> None:
        change = self.store.get(cid)
        work = self.ws.path(cid)
        src = work / "src"
        await asyncio.to_thread(Workspaces.clean_caches, src)
        diff = await asyncio.to_thread(compute_diff, work / "base", src)
        problems = list(diff.problems)
        for rel in diff.files:
            if why := forbidden(rel):
                problems.append(f"{rel}: changes to {why} aren't allowed")
        problems += secret_problems(diff)
        if not diff.files:
            problems.append("nothing was changed")
        risk = risk_for(diff.files, (change["plan"] or {}).get("risk", change["risk"]))
        fields = dict(diff=diff.text[:MAX_DIFF_CHARS], files=diff.files, risk=risk)
        if problems:
            await self.fail(cid, "The change was refused: " + "; ".join(problems[:12]), **fields)
            return
        self.note(cid, f"{len(diff.files)} file(s) changed; no forbidden files or secrets. Running all the tests "
                       "in the strict sandbox (no network, no home folder)…")
        code, report = await self.checker(cid, src)
        if cid in self.stopping:
            return
        report = report[-12000:]
        if code != 0:
            await self.fail(cid, "The tests or the start-up check failed (see the test report).",
                            test_report=report, **fields)
            return
        await self.set_status(cid, "ready", test_report=report, **fields)

    # ------------------------------------------------------------------ deploy, watch, undo
    def _marker(self, cid) -> Path:
        return self.data / "releases" / f"{cid}.deploy.json"

    def _set_marker(self, cid, tag: str | None) -> None:
        m = self._marker(cid)
        if tag is None:
            m.unlink(missing_ok=True)
            return
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"tag": tag}))

    def _tag(self, cid) -> str | None:
        try:
            return json.loads(self._marker(cid).read_text())["tag"]
        except (OSError, ValueError, KeyError):
            return None

    def _target(self, cid, tag) -> str:
        c = self.store.get(cid)
        return release_id((c["undo_commit"] if tag == "undo" else c["commit"]) or "")

    def _unmerged_live(self, except_id=None) -> list[dict]:
        """Live changes whose commit isn't in main: a new release from main would take them back out.
        (Runs in a thread: it's given the changes, it doesn't read the store.)"""
        out = []
        for c in self._live:
            if c["id"] != except_id and c["commit"]:
                with contextlib.suppress(Exception):
                    if not self.deployer.is_ancestor(c["commit"], "main"):
                        out.append(c)
        return out

    async def _deploy(self, cid) -> None:
        self._set_marker(cid, "install")
        change = self.store.get(cid)
        work = self.ws.path(cid)
        try:
            diff = await asyncio.to_thread(compute_diff, work / "base", work / "src")
            if diff.files != change["files"] or diff.problems:
                raise GitError("the workspace isn't what was checked any more")
            notes: list[str] = []
            commit, branch = await asyncio.to_thread(
                self.deployer.commit_change, change, work / "src", diff, notes.append)
            for n in notes:
                self.note(cid, n)
        except Exception as e:
            self._set_marker(cid, None)
            await self.fail(cid, f"Couldn't commit the change: {str(e)[:500]}")
            return
        self.store.update(cid, commit=commit, branch=branch, release=release_id(commit))
        self.note(cid, f"Committed {commit[:10]} on branch {branch}. Installing it (Pip waits for a quiet moment, "
                       "restarts, and checks his health; if he isn't healthy he goes back automatically).")
        self.ws.remove(cid)
        try:
            await self.releaser.start(cid, "install", commit, change["summary"])
        except Exception as e:
            self._set_marker(cid, None)
            self.deployer.delete_branch(branch)
            await self.fail(cid, f"Couldn't start the install: {str(e)[:300]}")
            return
        await self._watch(cid)

    async def _watch(self, cid) -> None:
        tag = self._tag(cid) or "install"
        while (code := self.releaser.result(cid, tag)) is None:
            if not self.releaser.running(cid, tag):
                await asyncio.sleep(self.poll_seconds)        # the exit file is written just before the job ends
                if (code := self.releaser.result(cid, tag)) is None:
                    code, why = self.releaser.from_state(self._target(cid, tag))
                    if code is None:
                        code = -1
                    elif why:
                        self.note(cid, why, "release")
                    break
            await asyncio.sleep(self.poll_seconds)
        await self._finish_release(cid, tag, code)

    async def _finish_release(self, cid, tag: str, code: int) -> None:
        change = self.store.get(cid)
        tail = self.releaser.tail(cid, tag)
        self._set_marker(cid, None)
        if tail:
            self.note(cid, tail, "release")
        release = self.releaser.release_id() or self._target(cid, tag)
        undo = tag == "undo"
        branch = f"pip/undo-{cid}" if undo else change["branch"]
        if code == EXIT_LIVE:
            ok, why = await asyncio.to_thread(self.deployer.fast_forward_main, branch)
            self.note(cid, "main now includes it." if ok else
                      f"Kept on branch {branch} ({why}): merge it into main when ready.")
            self.releaser.clear(cid, tag)
            if undo:
                await self.set_status(cid, "undone", release=release, error="" if ok else f"Merge {branch} when ready.")
            else:
                await self.set_status(cid, "live", release=release, error="" if ok else f"Merge {branch} when ready.")
            return
        why = {EXIT_ROLLED_BACK: "Pip wasn't healthy on the new version, so he went back to the one before.",
               EXIT_STOPPED: "The install stopped before anything changed.",
               EXIT_NEEDS_OWNER: "Pip wasn't healthy and going back didn't work either: he needs you (see the release log).",
               -1: "The install job ended without saying how it went."}.get(code, f"The install ended with code {code}.")
        if undo:
            self.deployer.delete_branch(branch)
            await self.set_status(cid, "live", error=f"Undo didn't go live: {why}")
        elif code == EXIT_ROLLED_BACK:
            await self.set_status(cid, "rolled_back", error=why)
        else:
            self.deployer.delete_branch(change["branch"])
            await self.set_status(cid, "failed", error=why)

    async def _undo(self, cid) -> None:
        self._set_marker(cid, "undo")
        change = self.store.get(cid)
        try:
            commit, branch = await asyncio.to_thread(self.deployer.revert, change)
        except Exception as e:
            self._set_marker(cid, None)
            await self.set_status(cid, "live", error=f"Couldn't undo it: {str(e)[:400]}")
            return
        self.store.update(cid, undo_commit=commit)
        self.note(cid, f"Undo committed as {commit[:10]} on branch {branch}. Installing it.")
        try:
            await self.releaser.start(cid, "undo", commit, f"Undo: {change['summary']}")
        except Exception as e:
            self._set_marker(cid, None)
            self.deployer.delete_branch(branch)
            await self.set_status(cid, "live", error=f"Couldn't start the install: {str(e)[:300]}")
            return
        await self._watch(cid)

    async def _recover_deploy(self, change: dict) -> None:
        cid, tag = change["id"], self._tag(change["id"])
        if tag and (self.releaser.result(cid, tag) is not None or self.releaser.running(cid, tag)):
            self.note(cid, "Pip restarted during the install (that's normal); still watching it.")
            return  # kick() starts _watch
        if tag and self._target(cid, tag):
            code, why = self.releaser.from_state(self._target(cid, tag))
            if code is not None:
                if why:
                    self.note(cid, why, "release")
                await self._finish_release(cid, tag, code)
                return
        self._set_marker(cid, None)
        if tag == "undo" or change["undo_commit"]:
            await self.set_status(cid, "live", error="Pip restarted before the undo was installed; try again.")
        else:
            self.ws.remove(cid)
            self.deployer.delete_branch(change["branch"])
            await self.set_status(cid, "failed", error="Pip restarted before the change was installed.")


    async def shutdown(self) -> None:
        """Pip's builder is stopping: stop a running Claude or check job (it'd be marked failed at start-up)."""
        if self.job and not self.job.done() and self.job_id:
            c = self.store.get(self.job_id)
            if c and c["status"] in RUNNING:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.agent.stop, self.job_id)
                    await asyncio.to_thread(sandbox_stop_check, self.job_id)
            self.job.cancel()
