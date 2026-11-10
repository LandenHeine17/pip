"""Versioned installs with automatic rollback (PIP-NEW-080, M1b).

Pip runs from a separate live copy, never straight from the repo:

    ~/pip-app/releases/<id>/   one folder per installed version (a git worktree of ~/pip, with its own .venv)
    ~/pip-app/current          a link to the version that runs (the services start from here)
    ~/pip-app/release-state.json  what's current, what was before, and the history of installs and rollbacks
    ~/pip-app/release.log      the progress lines of every install

Commands (run with the system python3 from ~/pip: `python3 -m pip_assistant.release …`):

    install <commit|HEAD> [--now] [--summary TEXT] [--install-units]
        make the version, wait for a quiet moment (not while Pip talks, listens or rings; at most 2 min;
        --now skips the wait), switch to it, restart Pip and check his health for up to 2 minutes plus a
        30 s settling time. If he isn't healthy, switch back to the version before and say why.
    rollback [--reason TEXT]   go back to the version before
    list                       the installed versions
    status [--json]            what runs now and what happened last (--json: one line, for programs)
    dirty                      list uncommitted changes to tracked files in ~/pip (exit 1 if there are any)

Only the standard library is used: this file must work whatever state the versions it switches are in.
Every outside action (git, uv, systemctl, the health check over HTTP, the clock) can be swapped out, so
tests can fake them (tests/builder/test_release.py).

Other programs (pip-builder) rely on this file as the one place that says what a release id is
(release_id), what `install` and `rollback` exit with (the EXIT_ numbers) and how to read how an install
went (install_outcome, or `status --json`). They import these names instead of copying them.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

# pip-core says whether now is a quiet moment (`quiet` in his state, core/states.py quiet()); BUSY is only
# the fallback for a version that doesn't say yet.
BUSY = {"speaking", "ringing", "listening", "thinking", "interrupted"}
QUIET_WAIT_S = 120      # longest wait for a quiet moment
HEALTH_WAIT_S = 120     # longest wait for every program to be up on the new version
SETTLE_S = 30           # then it must stay healthy this long
POLL_S = 2.0
KEEP = 3                # newest versions kept (plus the current and the previous one)
HISTORY_MAX = 200
UNIT_GLOB = "pip-*.service"

# What `install` and `rollback` exit with (pip-builder reads these).
EXIT_LIVE = 0           # the new version is live and healthy (or there was nothing to do)
EXIT_ROLLED_BACK = 1    # it wasn't healthy, so Pip went back to the version before, which is healthy
EXIT_STOPPED = 2        # stopped before anything running was changed
EXIT_NEEDS_OWNER = 3    # not healthy, and going back didn't work either: Pip needs the owner

RELEASE_ID_LEN = 10     # a release's id is the first 10 hex digits of its commit


def release_id(sha: str) -> str:
    """The id of the release made from commit `sha` (also its folder name under releases/)."""
    return sha[:RELEASE_ID_LEN]


def install_outcome(state: dict, rid: str) -> tuple[int | None, str]:
    """How an install of release `rid` went, read from release-state.json's contents (`state`):
    (EXIT_LIVE, "") it's live, (EXIT_ROLLED_BACK, reason) it was rolled back, (None, "") can't tell."""
    problem = state.get("last_problem") or {}
    if isinstance(problem, dict) and problem.get("release") == rid:
        return EXIT_ROLLED_BACK, str(problem.get("reason") or "")
    if state.get("current") == rid:
        return EXIT_LIVE, ""
    return None, ""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ReleaseError(Exception):
    """Something went wrong before Pip was switched: nothing running was changed."""


class Releases:
    def __init__(self, repo: Path | str | None = None, app: Path | str | None = None,
                 data: Path | str | None = None, unit_dir: Path | str | None = None, *,
                 run: Callable | None = None, uv_sync: Callable | None = None, systemctl: Callable | None = None,
                 fetch: Callable | None = None, sleep: Callable = time.sleep, clock: Callable = time.time,
                 out: Callable | None = None, port: int | None = None):
        home = Path.home()
        self.repo = Path(repo or os.environ.get("PIP_REPO") or home / "pip")
        self.app = Path(app or os.environ.get("PIP_APP") or home / "pip-app")
        self.data = Path(data or os.environ.get("PIP_DATA") or home / "pip-data")
        self.unit_dir = Path(unit_dir or home / ".config" / "systemd" / "user")
        self.releases = self.app / "releases"
        self.current = self.app / "current"
        self.state_file = self.app / "release-state.json"
        self.log_file = self.app / "release.log"
        self.units_backup = self.app / "units-before-releases"   # the units that ran Pip from ~/pip (first run)
        self.run = run or self._run
        self.uv_sync = uv_sync or self._uv_sync
        self.systemctl = systemctl or self._systemctl
        self.fetch = fetch or self._fetch
        self.sleep = sleep
        self.clock = clock
        self.out = out
        self.port = port

    # ------------------------------------------------------------------ talking
    def say(self, line: str) -> None:
        """A plain-language progress line: on the terminal (if it's still there) and in release.log."""
        stamp = datetime.now().strftime("%H:%M:%S")
        if self.out:
            self.out(line)
        else:
            try:
                print(line, flush=True)
            except OSError:   # the terminal went away (e.g. SSH dropped): carry on, the log still has it
                pass
        try:
            self.app.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {stamp} {line}\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ outside actions (replaceable)
    @staticmethod
    def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        r = self.run(["git", "-C", str(cwd or self.repo), *args])
        if r.returncode != 0:
            raise ReleaseError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()

    def _uv_sync(self, folder: Path) -> str:
        """Make the version's .venv from uv's cache (offline). Returns a note if it had to download."""
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        r = self.run([uv, "sync", "--frozen", "--offline", "-q"], cwd=folder, env=env)
        if r.returncode == 0:
            return ""
        r2 = self.run([uv, "sync", "--frozen", "-q"], cwd=folder, env=env)
        if r2.returncode != 0:
            raise ReleaseError(f"uv sync failed: {(r2.stderr or r2.stdout or r.stderr).strip()[-400:]}")
        return "uv's cache was missing something, so it downloaded it (needed the internet)"

    def _systemctl(self, *args: str) -> tuple[bool, str]:
        r = self.run(["systemctl", "--user", *args])
        return r.returncode == 0, (r.stderr or r.stdout).strip()

    def _fetch(self, path: str) -> tuple[int, bytes]:
        """GET http://127.0.0.1:<port><path> → (status, body); (0, b"") if Pip's dashboard doesn't answer."""
        url = f"http://127.0.0.1:{self.web_port()}{path}"
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except (OSError, ValueError):
            return 0, b""

    # ------------------------------------------------------------------ settings
    def _user_settings(self) -> dict:
        f = self.data / "settings.toml"
        try:
            return tomllib.loads(f.read_text()) if f.exists() else {}
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def web_port(self) -> int:
        if self.port:
            return self.port
        port = self._user_settings().get("web", {}).get("port")
        if not port:
            try:
                defaults = tomllib.loads((Path(__file__).parent / "defaults.toml").read_text())
                port = defaults["web"]["port"]
            except (OSError, KeyError, tomllib.TOMLDecodeError):
                port = 8790
        return int(port)

    def programs(self, folder: Path | None) -> list[str]:
        """Pip's programs for a version: core plus [programs] expected (that version's defaults, or the
        owner's ~/pip-data/settings.toml when it sets the list)."""
        expected: list = []
        if folder is not None:
            try:
                expected = tomllib.loads((folder / "pip_assistant" / "defaults.toml").read_text())["programs"]["expected"]
            except (OSError, KeyError, tomllib.TOMLDecodeError):
                expected = []
        override = self._user_settings().get("programs", {}).get("expected")
        if isinstance(override, list):
            expected = override
        if not expected:
            expected = ["ears", "thinker", "web"]
        return list(dict.fromkeys(["core", *[str(p) for p in expected]]))

    # ------------------------------------------------------------------ state
    def load_state(self) -> dict:
        try:
            s = json.loads(self.state_file.read_text())
            return s if isinstance(s, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, state: dict) -> None:
        state["history"] = state.get("history", [])[-HISTORY_MAX:]
        self.app.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, self.state_file)

    def record(self, entry: dict, **fields) -> dict:
        state = self.load_state()
        state.setdefault("history", []).append({"time": _now_iso(), **entry})
        state.update(fields)
        state["current"] = self.current_id()
        self.save_state(state)
        return state

    def current_id(self) -> str | None:
        if not self.current.is_symlink():
            return None
        return Path(os.readlink(self.current)).name

    def folder(self, rid: str) -> Path:
        return self.releases / rid

    def release_info(self, rid: str) -> dict:
        try:
            return json.loads((self.folder(rid) / "RELEASE").read_text())
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ repo checks
    def dirty(self) -> list[str]:
        """Uncommitted changes to tracked files in the repo (new untracked files don't count)."""
        r = self.run(["git", "-C", str(self.repo), "status", "--porcelain", "--untracked-files=no"])
        if r.returncode != 0:
            raise ReleaseError(f"git status failed: {r.stderr.strip()[:300]}")
        return [line[3:] for line in r.stdout.splitlines() if line.strip()]

    # ------------------------------------------------------------------ the steps
    def make(self, commit: str, summary: str | None) -> tuple[str, Path]:
        sha = self.git("rev-parse", "--verify", f"{commit}^{{commit}}")
        rid = release_id(sha)
        dest = self.folder(rid)
        self.releases.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            try:
                have = self.git("rev-parse", "HEAD", cwd=dest)
            except ReleaseError:
                have = ""
            if have != sha:
                self.say(f"Removing a broken copy of {rid} and making it again.")
                self.remove_folder(dest)
        if dest.exists():
            self.say(f"Version {rid} is already there; reusing it.")
        else:
            self.say(f"Making version {rid} from commit {sha[:10]}…")
            self.git("worktree", "add", "--detach", str(dest), sha)
        self.say("Setting up its Python packages (from uv's cache)…")
        note = self.uv_sync(dest)
        if note:
            self.say(f"Note: {note}.")
        if summary is None:
            summary = self.git("log", "-1", "--format=%s", sha)
        info = {"sha": sha, "short": rid, "time": _now_iso(), "installed_at": self.clock(), "summary": summary}
        (dest / "RELEASE").write_text(json.dumps(info, indent=1) + "\n")
        return rid, dest

    def wait_quiet(self) -> None:
        start = self.clock()
        first = True
        while True:
            status, body = self.fetch("/api/health")
            if status != 200:
                self.say("Pip's dashboard isn't answering, so not waiting for a quiet moment.")
                return
            try:
                health = json.loads(body)
                state, quiet = health.get("state", "idle"), (health.get("state_detail") or {}).get("quiet")
            except (ValueError, AttributeError):
                state, quiet = "idle", None
            if quiet is True or (quiet is None and state not in BUSY):
                return
            if self.clock() - start >= QUIET_WAIT_S:
                self.say(f"Pip is still {state} after {QUIET_WAIT_S // 60} minutes; switching anyway.")
                return
            if first:
                self.say(f"Pip is {state}; waiting for a quiet moment (at most {QUIET_WAIT_S // 60} minutes)…")
                first = False
            self.sleep(POLL_S)

    def point_current(self, rid: str | None) -> None:
        """Atomically point ~/pip-app/current at a version (or remove it with None)."""
        if rid is None:
            if self.current.is_symlink():
                self.current.unlink()
            return
        tmp = self.app / f".current-{os.getpid()}"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(f"releases/{rid}", tmp)
        os.replace(tmp, self.current)

    def install_units(self, folder: Path) -> None:
        """Copy the version's service files into systemd (the first run backs up the old ones first)."""
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        old = sorted(self.unit_dir.glob(UNIT_GLOB))
        if old and not self.units_backup.exists() and not any("pip-app" in f.read_text() for f in old):
            self.units_backup.mkdir(parents=True)
            for f in old:
                shutil.copy2(f, self.units_backup / f.name)
            self.say(f"Kept a copy of the old service files (they ran Pip from ~/pip) in {self.units_backup}.")
        for f in sorted((folder / "deploy" / "systemd").glob(UNIT_GLOB)):
            shutil.copy2(f, self.unit_dir / f.name)

    def restart(self, programs: list[str], enable: bool = False) -> tuple[bool, str]:
        units = [f"pip-{p}.service" for p in programs]
        present = [u for u in units if (self.unit_dir / u).exists()]
        missing = [u for u in units if u not in present]
        if missing:
            self.say(f"No service file for {', '.join(missing)}; can't start {'it' if len(missing) == 1 else 'them'}.")
        ok, msg = self.systemctl("daemon-reload")
        if not ok:
            return False, f"systemd reload failed: {msg[:200]}"
        if enable and present:
            ok, msg = self.systemctl("enable", "-q", *present)
            if not ok:
                self.say(f"Couldn't enable the services: {msg[:200]}")
        self.say(f"Restarting {', '.join('pip-' + p for p in programs)}…")
        ok, msg = self.systemctl("restart", *present)
        return ok, ("" if ok else f"restart failed: {msg[:200]}")

    def _health_now(self, rid: str, programs: list[str]) -> tuple[list[str], dict]:
        """What's wrong right now (empty = healthy), and the programs' health entries."""
        status, body = self.fetch("/api/health")
        if status != 200:
            return ["the dashboard isn't answering"], {}
        try:
            progs = json.loads(body).get("programs", {}) or {}
        except ValueError:
            return ["the dashboard's health answer was garbled"], {}
        problems = []
        for p in programs:
            e = progs.get(p) or {}
            if e.get("state") != "ok":
                problems.append(f"pip-{p} is {e.get('state') or 'missing'}")
            elif str(e.get("release")) != rid:
                problems.append(f"pip-{p} is still on version {e.get('release')}")
        for path, name in (("/", "the dashboard page"), ("/api/tv", "the TV page's data")):
            if self.fetch(path)[0] != 200:
                problems.append(f"{name} doesn't answer")
        return problems, progs

    def check_health(self, rid: str, programs: list[str]) -> tuple[bool, str]:
        """Up to 2 minutes for everything to be up on version `rid`, then 30 s that it stays up."""
        self.say(f"Checking Pip's health (up to {HEALTH_WAIT_S // 60} minutes)…")
        start = self.clock()
        while True:
            problems, progs = self._health_now(rid, programs)
            if not problems:
                break
            if self.clock() - start >= HEALTH_WAIT_S:
                return False, "; ".join(problems)
            self.sleep(POLL_S)
        self.say(f"All up on {rid}. Making sure it stays up for {SETTLE_S} seconds…")
        uptimes = {p: (progs.get(p) or {}).get("uptime_s") for p in programs}
        settle = self.clock()
        while self.clock() - settle < SETTLE_S:
            self.sleep(POLL_S)
            problems, progs = self._health_now(rid, programs)
            for p in programs:
                up, before = (progs.get(p) or {}).get("uptime_s"), uptimes.get(p)
                if isinstance(up, (int, float)) and isinstance(before, (int, float)) and up + 1 < before:
                    problems.append(f"pip-{p} restarted")
                uptimes[p] = up if up is not None else before
            if problems:
                return False, "after starting, " + "; ".join(problems)
        return True, ""

    def switch(self, rid: str | None, units: bool, expect: str) -> tuple[bool, str]:
        """Point current at `rid` (None = back to the old service files), restart, check health."""
        folder = self.folder(rid) if rid else None
        if rid:
            if units:
                self.install_units(folder)
            self.point_current(rid)
        else:
            for f in self.units_backup.glob(UNIT_GLOB):
                shutil.copy2(f, self.unit_dir / f.name)
            self.point_current(None)
        programs = self.programs(folder or self.repo)
        ok, why = self.restart(programs, enable=units)
        if not ok:
            return False, why
        return self.check_health(expect, programs)

    # ------------------------------------------------------------------ commands
    def install(self, commit: str = "HEAD", now: bool = False, summary: str | None = None,
                units: bool = False) -> int:
        with self.locked():
            try:
                rid, folder = self.make(commit, summary)
            except ReleaseError as e:
                self.say(f"Install stopped before anything changed: {e}")
                self.record({"action": "install", "commit": commit, "result": "failed", "reason": str(e)[:300]})
                return EXIT_STOPPED
            info = self.release_info(rid)
            before = self.current_id()
            if before and not self.folder(before).exists():
                before = None
            if before == rid and not units:
                self.say(f"Version {rid} is already the one running. Nothing to do.")
                return EXIT_LIVE
            if not now:
                self.wait_quiet()
            first = before is None
            self.say(f"Switching Pip to version {rid}" + (" (the first versioned install)." if first else
                                                           f" (was {before})."))
            try:
                ok, why = self.switch(rid, units, rid)
            except Exception as e:  # never stop half-way: anything unexpected counts as unhealthy
                ok, why = False, f"{type(e).__name__}: {e}"
            entry = {"action": "install", "release": rid, "sha": info.get("sha"), "summary": info.get("summary")}
            if ok:
                self.say(f"Pip is running version {rid} and healthy.")
                self.record({**entry, "result": "live"}, previous=before if before != rid else
                            self.load_state().get("previous"), last_problem=None)
                self.prune()
                return EXIT_LIVE
            self.say(f"PROBLEM: version {rid} isn't healthy: {why}.")
            code = self._recover(before, units, why, entry)
            self.prune()
            return code

    def _recover(self, before: str | None, units: bool, why: str, entry: dict) -> int:
        if before:
            self.say(f"Switching back to version {before}…")
            try:
                ok, why2 = self.switch(before, units, before)
            except Exception as e:
                ok, why2 = False, f"{type(e).__name__}: {e}"
            note = {"release": entry["release"], "reason": why[:300], "time": _now_iso(),
                    "back_to": before, "back_ok": ok}
            self.record({**entry, "result": "rolled_back", "reason": why[:300], "back_to": before,
                         "back_ok": ok, **({} if ok else {"back_problem": why2[:300]})}, last_problem=note)
            if ok:
                self.say(f"Rolled back: Pip is running version {before} again and healthy.")
                return EXIT_ROLLED_BACK
            self.say(f"PROBLEM: version {before} isn't healthy either: {why2}. Pip needs the owner: "
                     f"look at `journalctl --user -u 'pip-*' -n 100`.")
            return EXIT_NEEDS_OWNER
        if self.units_backup.exists() and any(self.units_backup.glob(UNIT_GLOB)):
            self.say("There's no earlier version to go back to. Putting the old service files back, "
                     "so Pip runs from ~/pip again like before…")
            try:
                ok, why2 = self.switch(None, False, "dev")
            except Exception as e:
                ok, why2 = False, f"{type(e).__name__}: {e}"
            note = {"release": entry["release"], "reason": why[:300], "time": _now_iso(), "back_to": "~/pip",
                    "back_ok": ok}
            self.record({**entry, "result": "rolled_back", "reason": why[:300], "back_to": "~/pip",
                         "back_ok": ok, **({} if ok else {"back_problem": why2[:300]})}, last_problem=note)
            if ok:
                self.say("Pip is running from ~/pip again (like before versioned installs) and healthy. "
                         "Fix the problem and run deploy/install.sh again.")
                return EXIT_ROLLED_BACK
            self.say(f"PROBLEM: running from ~/pip isn't healthy either: {why2}. Pip needs the owner: "
                     f"look at `journalctl --user -u 'pip-*' -n 100`.")
            return EXIT_NEEDS_OWNER
        self.say("PROBLEM: this was the first versioned install and there's nothing to go back to. "
                 "Pip stays on it; it needs the owner: look at `journalctl --user -u 'pip-*' -n 100`.")
        self.record({**entry, "result": "failed", "reason": why[:300], "back_to": None},
                    last_problem={"release": entry["release"], "reason": why[:300], "time": _now_iso(),
                                  "back_to": None, "back_ok": False})
        return EXIT_NEEDS_OWNER

    def rollback(self, reason: str = "asked for") -> int:
        with self.locked():
            state = self.load_state()
            cur, prev = self.current_id(), state.get("previous")
            if not prev or prev == cur or not self.folder(prev).exists():
                self.say("There's no earlier version to go back to.")
                return EXIT_STOPPED
            self.say(f"Rolling back from {cur} to {prev} ({reason})…")
            try:
                ok, why = self.switch(prev, False, prev)
            except Exception as e:
                ok, why = False, f"{type(e).__name__}: {e}"
            self.record({"action": "rollback", "release": prev, "from": cur, "reason": reason[:300],
                         "result": "live" if ok else "unhealthy", **({} if ok else {"problem": why[:300]})},
                        previous=cur)
            if ok:
                self.say(f"Pip is running version {prev} again and healthy.")
                return EXIT_LIVE
            self.say(f"PROBLEM: version {prev} isn't healthy: {why}. Run `rollback` again to go back to {cur}.")
            return EXIT_NEEDS_OWNER

    def prune(self) -> None:
        """Keep the newest 3 versions, plus the current and the previous one; remove the rest."""
        if not self.releases.is_dir():
            return
        folders = [f for f in self.releases.iterdir() if f.is_dir()]

        def made(f: Path) -> float:
            t = self.release_info(f.name).get("installed_at")
            return float(t) if isinstance(t, (int, float)) else f.stat().st_mtime
        folders.sort(key=made, reverse=True)
        keep = {f.name for f in folders[:KEEP]} | {self.current_id(), self.load_state().get("previous")}
        removed = []
        for f in folders:
            if f.name not in keep:
                self.remove_folder(f)
                removed.append(f.name)
        if removed:
            self.say(f"Removed old versions: {', '.join(removed)}.")
        try:
            self.git("worktree", "prune")
        except ReleaseError:
            pass

    def remove_folder(self, f: Path) -> None:
        try:
            self.git("worktree", "remove", "--force", str(f))
        except ReleaseError:
            pass
        if f.exists():
            shutil.rmtree(f, ignore_errors=True)

    def listing(self) -> list[dict]:
        if not self.releases.is_dir():
            return []
        cur, prev = self.current_id(), self.load_state().get("previous")
        rows = []
        for f in self.releases.iterdir():
            if f.is_dir():
                i = self.release_info(f.name)
                rows.append({"release": f.name, "time": i.get("time"), "summary": i.get("summary"),
                             "current": f.name == cur, "previous": f.name == prev,
                             "installed_at": i.get("installed_at") or 0})
        return sorted(rows, key=lambda r: r["installed_at"], reverse=True)

    def status(self) -> dict:
        state = self.load_state()
        return {"current": self.current_id(), "previous": state.get("previous"),
                "running_from": "~/pip-app/current" if self.current_id() else "~/pip (no versioned install yet)",
                "last": (state.get("history") or [None])[-1], "last_problem": state.get("last_problem")}

    # ------------------------------------------------------------------ one at a time
    def locked(self):
        rel = self

        class _Lock:
            def __enter__(self):
                rel.app.mkdir(parents=True, exist_ok=True)
                self.f = open(rel.app / "release.lock", "w")
                try:
                    fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    self.f.close()
                    raise ReleaseError("another install or rollback is running")
                return self

            def __exit__(self, *exc):
                fcntl.flock(self.f, fcntl.LOCK_UN)
                self.f.close()
        return _Lock()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m pip_assistant.release", description="Pip's versioned installs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="install a commit and switch Pip to it (with health check and rollback)")
    i.add_argument("commit", nargs="?", default="HEAD")
    i.add_argument("--now", action="store_true", help="don't wait for a quiet moment")
    i.add_argument("--summary", help="what the change does (default: the commit's subject)")
    i.add_argument("--install-units", action="store_true",
                   help="also copy the version's service files into systemd and enable them (deploy/install.sh)")
    r = sub.add_parser("rollback", help="go back to the version before")
    r.add_argument("--reason", default="asked for")
    sub.add_parser("list", help="the installed versions")
    st = sub.add_parser("status", help="what runs now and what happened last")
    st.add_argument("--json", action="store_true",
                    help="one line of JSON for programs: current, previous, running_from, last, last_problem")
    sub.add_parser("dirty", help="uncommitted changes to tracked files in the repo (exit 1 if any)")
    a = ap.parse_args(argv)
    rel = Releases()
    if a.cmd in ("install", "rollback"):
        # Never stop half-way through a switch (a dropped SSH session, Ctrl-C or `systemctl stop` would leave
        # Pip half-switched): it finishes by itself, including switching back. release.log has every line.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
    try:
        if a.cmd == "install":
            return rel.install(a.commit, now=a.now, summary=a.summary, units=a.install_units)
        if a.cmd == "rollback":
            return rel.rollback(a.reason)
        if a.cmd == "list":
            rows = rel.listing()
            if not rows:
                print("No versions installed yet (Pip runs from ~/pip).")
            for row in rows:
                mark = "* current " if row["current"] else ("  previous" if row["previous"] else "          ")
                print(f"{mark} {row['release']}  {row['time'] or '?'}  {row['summary'] or ''}")
            return 0
        if a.cmd == "status":
            if a.json:   # one line, the same keys every time: what other programs read
                print(json.dumps(rel.status(), separators=(",", ":")))
            else:
                print(json.dumps(rel.status(), indent=1))
            return 0
        if a.cmd == "dirty":
            files = rel.dirty()
            for f in files:
                print(f)
            return 1 if files else 0
    except ReleaseError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
