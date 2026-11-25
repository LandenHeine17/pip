"""Pip's self-changes, done in Claude Hub (D-152): pip-builder hands them over and keeps track.

    asked → planning → building → ready → live          (or dropped; failed if Claude Hub refused it)
                                          live → undoing → undone   (the Undo button)

- **Asking** (`ask`): Pip offered "Want me to write that up as a change?" and they said yes (by voice, or in the
  dashboard's chat box). The change gets a row in the store, and a request file goes into Claude Hub's inbox
  (hub.py). The hub opens a chat in ~/pip in plan mode with Pip's write-up as its first message; the owner does
  everything else there: answers Claude's questions, approves the plan, says when to install.
- **Following it** (`sync`, every few seconds, while a change is open): the hub's state file says which chat it is and whether Claude is
  working or waiting for the owner (asked → planning). Claude runs `tools/pip-change <id> building` once the plan
  is approved (cli.py writes the store directly). Its commits end with a `Pip-Change: <id>` line: once there are
  some on main the change is "ready", and once they're all in the version Pip is running it's "live". Claude
  installs it with deploy/install.sh. If the owner says no, Claude runs `tools/pip-change <id> dropped`.
- **Telling the room**: a change that went live or was dropped is said aloud once, when Pip is free and not in
  quiet hours (pip-core's `core.announce`), within two days.
- **Undo** (`undo`, the Changes page): a new commit that takes the change's commits back out (deploy.py), installed
  as its own job (releasejob.py) with the usual health check and rollback. The install restarts pip-builder too,
  so a marker file says an undo was under way and `start` picks it up again.
"""
import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path

from ..release import EXIT_LIVE, EXIT_NEEDS_OWNER, EXIT_ROLLED_BACK, EXIT_STOPPED
from .deploy import Deployer
from .hub import HubInbox, name_for
from .releasejob import ReleaseRunner
from .store import ACTIVE, ANNOUNCE, FINAL, Store

log = logging.getLogger("pip-builder")

LABELS = {
    "asked": "Sent to Claude Hub", "planning": "Planning", "building": "Building",
    "ready": "Committed, not installed yet", "live": "Live", "dropped": "Dropped", "undoing": "Undoing it",
    "undone": "Undone", "failed": "Failed",
    # D-118's, for the old changes:
    "rolled_back": "Rolled back", "rejected": "Rejected", "stopped": "Stopped",
}
HUB_STATES = {"queued": "waiting for room in Claude Hub", "working": "Claude is working",
              "waiting": "waiting for you", "idle": "your turn", "failed": "Claude Hub couldn't open it"}
SUMMARY_MAX, REQUEST_MAX = 300, 6000
ANNOUNCE_WITHIN = 2 * 86400       # don't bring up a change that went live more than two days ago
ANNOUNCE_EVERY = 30.0             # how often to try again while Pip is busy (or it's quiet hours)


class Builder:
    def __init__(self, store: Store, *, repo: Path, data: Path, settings_fn, inbox: HubInbox | None = None,
                 deployer: Deployer | None = None, releaser: ReleaseRunner | None = None, publish=None,
                 announce=None, clock=time.time, poll_seconds: float = 3.0):
        self.store, self.repo, self.data = store, Path(repo), Path(data)
        self.settings_fn = settings_fn                 # → the [builder] settings, read fresh each time
        self.inbox = inbox or HubInbox(Path(self.cfg().get("hub_inbox", "~/.local/state/claude-hub/inbox")))
        self.deployer = deployer or Deployer(repo, self.data / "deploy")
        self.releaser = releaser or ReleaseRunner(repo, self.data / "releases")
        self.publish = publish                         # async (type, data): builder.changed
        self.announce = announce                       # async (text) → said?  (pip-core's core.announce)
        self.clock, self.poll_seconds = clock, poll_seconds
        self.job: asyncio.Task | None = None           # an undo being installed
        self.on_stage = None                           # callback(stage) for the heartbeat
        self._in_release: dict[tuple[str, str], bool] = {}
        self._tried_announce = 0.0
        self._seen: dict[str, str] = {}                # each change's status at the last look

    def cfg(self) -> dict:
        return self.settings_fn()

    # ------------------------------------------------------------------ bits
    async def _emit(self, cid, status) -> None:
        if self.publish:
            with contextlib.suppress(Exception):
                await self.publish("builder.changed", {"id": str(cid), "status": status})

    async def set_status(self, cid, status: str, **fields) -> None:
        self.store.update(cid, status=status, **fields)
        self._seen[str(cid)] = status
        self.store.log(cid, "status", LABELS.get(status, status))
        log.info("builder: change #%s is %s", cid, status)       # ids and statuses only, never the words
        if status in FINAL | {"live"}:
            c = self.store.get(cid)
            if c and c["hub_name"]:
                self.inbox.forget(c["hub_name"])                  # the hub can stop following the chat
        self._update_stage()
        await self._emit(cid, status)

    def _update_stage(self) -> None:
        if self.on_stage:
            n = sum(1 for c in self.store.all() if c["status"] in ACTIVE)
            self.on_stage("undoing" if self.job and not self.job.done() else f"{n} open" if n else "idle")

    # ------------------------------------------------------------------ asking
    async def ask(self, summary: str, request: str, asked_by: str, source: str) -> dict:
        summary = re.sub(r"\s+", " ", summary or "").strip()
        request = (request or "").strip() or summary
        if not self.cfg().get("enabled", True):
            return {"error": "Self-changes are switched off in Settings."}
        if len(summary) < 5:
            return {"error": "Say what you'd like changed."}
        if len(summary) > SUMMARY_MAX or len(request) > REQUEST_MAX:
            return {"error": "That's too long: describe the change in a few sentences."}
        cid = self.store.add(request, summary, str(asked_by or "someone")[:80],
                             source if source in ("voice", "chat") else "voice")
        self.store.log(cid, "status", LABELS["asked"])
        try:
            name = await asyncio.to_thread(self.inbox.send, self.store.get(cid), self.repo,
                                           str(self.cfg().get("model", "") or ""))
        except OSError as e:
            await self.set_status(cid, "failed", error=f"Couldn't hand it to Claude Hub: {e}"[:300])
            return {"error": "I couldn't pass that on to Claude Hub."}
        self.store.update(cid, hub_name=name)
        await self._emit(cid, "asked")
        self._update_stage()
        return {"id": cid}

    # ------------------------------------------------------------------ following the chat and the commits
    async def sync(self) -> None:
        """One look at Claude Hub's state files and at git: move each change on, and tell the room."""
        rows = self.store.all(200)
        for c in rows:            # a status set by tools/pip-change (cli.py) in Claude's chat
            seen = self._seen.get(c["id"])
            self._seen[c["id"]] = c["status"]
            if seen is not None and seen != c["status"]:
                if c["status"] in FINAL and c["hub_name"]:
                    self.inbox.forget(c["hub_name"])
                self._update_stage()
                await self._emit(c["id"], c["status"])
        open_ = [c for c in rows if c["status"] in ACTIVE]
        for c in open_:
            if c["hub_name"]:
                await self._follow_hub(c)
        if open_:
            await self._follow_git([self.store.get(c["id"]) for c in open_])
        await self._tell_the_room()

    async def _follow_hub(self, c: dict) -> None:
        st = self.inbox.state(c["hub_name"])
        if not st:
            return
        session, state = str(st.get("session") or ""), str(st.get("state") or "")
        if state == "failed" and c["status"] == "asked":
            await self.set_status(c["id"], "failed", hub_state=state,
                                  error=f"Claude Hub: {str(st.get('error') or 'it refused the request')}"[:300])
            return
        if session != c["hub_session"] or state != c["hub_state"]:
            self.store.update(c["id"], hub_session=session, hub_state=state)
            if c["status"] == "asked" and session:
                await self.set_status(c["id"], "planning")
            else:
                await self._emit(c["id"], c["status"])

    def _released(self, sha: str, release: str) -> bool:
        key = (sha, release)
        if key not in self._in_release:
            self._in_release[key] = self.deployer.in_release(sha, release)
        return self._in_release[key]

    async def _follow_git(self, changes: list[dict]) -> None:
        try:
            found = await asyncio.to_thread(self.deployer.change_commits)
            release = self.releaser.release_id()
        except Exception as e:  # noqa: BLE001 - git busy or the repo mid-rebase: try again next time
            log.warning("builder: couldn't read the commits: %s", type(e).__name__)
            return
        for c in changes:
            if not c or c["status"] not in ACTIVE:
                continue
            commits = found.get(c["id"], [])
            if commits != c["commits"]:
                self.store.update(c["id"], commits=commits, commit=commits[-1] if commits else "")
                self.store.log(c["id"], "note", f"{len(commits)} commit(s) on main.")
            if not commits:
                continue
            live = await asyncio.to_thread(lambda: all(self._released(s, release) for s in commits))
            new = "live" if live else "ready"
            if new != c["status"]:
                await self.set_status(c["id"], new, release=release if live else c["release"], error="")

    @staticmethod
    def saying(c: dict) -> str:
        s = c["summary"].rstrip(".")
        if c["status"] == "live":
            return f"Heads up: the change to {s} is live now."
        return f"The change to {s} was dropped."

    async def _tell_the_room(self) -> None:
        if not self.announce or self.clock() - self._tried_announce < ANNOUNCE_EVERY:
            return
        due = [c for c in self.store.all(50) if c["status"] in ANNOUNCE and c["announced"] != c["status"]]
        if not due:
            return
        self._tried_announce = self.clock()
        for c in due:
            if self.clock() - c["updated"] > ANNOUNCE_WITHIN:
                self.store.update(c["id"], announced=c["status"])   # too late to bring it up
                continue
            try:
                said = await self.announce(self.saying(c))
            except Exception:  # noqa: BLE001 - pip-core restarting: next time
                said = False
            if not said:
                return
            self.store.update(c["id"], announced=c["status"])

    # ------------------------------------------------------------------ the Changes page
    def _row(self, c: dict) -> dict:
        active = c["status"] in ACTIVE
        hub = HUB_STATES.get(c["hub_state"], "") if active else ""
        return {"id": c["id"], "status": c["status"], "label": LABELS.get(c["status"], c["status"]),
                "hub": hub, "summary": c["summary"], "asked_by": c["asked_by"], "source": c["source"],
                "created": c["created"], "updated": c["updated"], "session": c["hub_session"],
                "commits": [s[:10] for s in c["commits"]] or ([c["commit"][:10]] if c["commit"] else []),
                "waiting": active and c["hub_state"] in ("waiting", "idle"),
                "can_undo": c["status"] == "live" and bool(c["commits"] or c["commit"]),
                "error": c["error"]}

    def listing(self) -> dict:
        cfg = self.cfg()
        return {"changes": [self._row(c) for c in self.store.all()],
                "hub": {"url": str(cfg.get("hub_url", "") or ""), "folder": str(self.repo)},
                "undoing": bool(self.job and not self.job.done())}

    async def page(self, action: str, p: dict) -> dict:
        if action == "list":
            return self.listing()
        if action == "undo":
            return await self.undo(p.get("id"))
        return {"error": f"Unknown action {action!r}."}

    # ------------------------------------------------------------------ undo
    def _marker(self, cid) -> Path:
        return self.data / "releases" / f"{cid}.deploy.json"

    async def undo(self, cid) -> dict:
        c = self.store.get(cid)
        if not c or c["status"] != "live" or not (c["commits"] or c["commit"]):
            return {"error": "Only a change that's live can be undone."}
        if self.job and not self.job.done():
            return {"error": "Another undo is still being installed; try again when it's done."}
        with contextlib.suppress(Exception):       # any follow-up commits made since it went live come out too
            commits = (await asyncio.to_thread(self.deployer.change_commits)).get(c["id"])
            if commits:
                self.store.update(c["id"], commits=commits)
        await self.set_status(c["id"], "undoing", error="")
        self.job = asyncio.create_task(self._undo(c["id"]))
        return {"ok": True}

    async def _undo(self, cid) -> None:
        change = self.store.get(cid)
        try:
            commit, branch = await asyncio.to_thread(self.deployer.revert, change)
        except Exception as e:  # noqa: BLE001
            await self.set_status(cid, "live", error=f"Couldn't undo it: {str(e)[:400]}")
            return
        self.store.update(cid, undo_commit=commit)
        self.store.log(cid, "note", f"Undo committed as {commit[:10]} on branch {branch}. Installing it.")
        m = self._marker(cid)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"tag": "undo"}))
        try:
            await self.releaser.start(cid, "undo", commit, f"Undo: {change['summary']}"[:100])
        except Exception as e:  # noqa: BLE001
            m.unlink(missing_ok=True)
            self.deployer.delete_branch(branch)
            await self.set_status(cid, "live", error=f"Couldn't start the install: {str(e)[:300]}")
            return
        await self._watch(cid)

    async def _watch(self, cid) -> None:
        """Wait for the undo's install job to end (it restarts pip-builder, so this may be after a restart)."""
        while (code := self.releaser.result(cid, "undo")) is None:
            if not self.releaser.running(cid, "undo"):
                await asyncio.sleep(self.poll_seconds)      # the exit file is written just before the job ends
                if (code := self.releaser.result(cid, "undo")) is None:
                    from ..release import release_id
                    code, why = self.releaser.from_state(release_id(self.store.get(cid)["undo_commit"] or ""))
                    if code is None:
                        code = -1
                    elif why:
                        self.store.log(cid, "release", why)
                    break
            await asyncio.sleep(self.poll_seconds)
        await self._finish_undo(cid, code)

    async def _finish_undo(self, cid, code: int) -> None:
        tail = self.releaser.tail(cid, "undo")
        self._marker(cid).unlink(missing_ok=True)
        if tail:
            self.store.log(cid, "release", tail)
        branch = f"pip/undo-{cid}"
        if code == EXIT_LIVE:
            ok, why = await asyncio.to_thread(self.deployer.fast_forward_main, branch)
            self.releaser.clear(cid, "undo")
            await self.set_status(cid, "undone", release=self.releaser.release_id(),
                                  error="" if ok else f"Kept on branch {branch} ({why}): merge it into main.")
            return
        why = {EXIT_ROLLED_BACK: "Pip wasn't healthy on the new version, so he went back to the one before.",
               EXIT_STOPPED: "The install stopped before anything changed.",
               EXIT_NEEDS_OWNER: "Pip wasn't healthy and going back didn't work either: he needs you (see the release log).",
               -1: "The install job ended without saying how it went."}.get(code, f"The install ended with code {code}.")
        self.deployer.delete_branch(branch)
        await self.set_status(cid, "live", error=f"Undo didn't go live: {why}")

    # ------------------------------------------------------------------ start-up and the loop
    async def start(self) -> None:
        """After a (re)start: an undo that was being installed is watched again (the install restarts us)."""
        for c in self.store.with_status("undoing"):
            cid = c["id"]
            if self._marker(cid).exists() and (self.releaser.result(cid, "undo") is not None
                                               or self.releaser.running(cid, "undo")):
                self.job = asyncio.create_task(self._watch(cid))
            else:
                self._marker(cid).unlink(missing_ok=True)
                self.deployer.delete_branch(f"pip/undo-{cid}")
                await self.set_status(cid, "live", error="Pip restarted before the undo was installed; try again.")
        with contextlib.suppress(Exception):
            self.store.forget_old(float(self.cfg().get("keep_days", 90)))
        self._update_stage()

    async def loop(self) -> None:
        last_forget = self.clock()
        while True:
            try:
                await self.sync()
            except Exception:  # noqa: BLE001 - keep following
                log.exception("builder: sync failed")
            if self.clock() - last_forget > 3600:
                last_forget = self.clock()
                with contextlib.suppress(Exception):
                    self.store.forget_old(float(self.cfg().get("keep_days", 90)))
            await asyncio.sleep(self.poll_seconds)

    async def shutdown(self) -> None:
        if self.job and not self.job.done():
            self.job.cancel()        # the install job runs on its own; start() watches it again
