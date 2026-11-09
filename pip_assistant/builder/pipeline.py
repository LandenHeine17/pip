"""The self-change pipeline: from a request to a live change, with the owner's approval twice (M1b).

    asked → planning → questions | plan_ready → building → checking → ready → deploying → live
    (or rolled_back, failed, rejected, stopped; a live change can be undone → undone)

- **Workspace** (Workspaces): a plain export of ~/pip's main branch with no .git, plus an untouched copy to
  diff against, and a .venv made before the agent runs. Deleted when the change ends.
- **Plan / build**: Claude Code in the agent sandbox (agent.py, sandbox.py). One change at a time; the next
  one waits as "asked" until the current one has ended.
- **Check** (check()): what changed (text only), forbidden paths, secrets, then the full test suite and an
  import check in the strict sandbox. Passing only makes it reviewable (AC-SM-05).
- **Deploy** (Deployer): a git worktree of ~/pip on a new branch, the changed files, a commit made from the
  approved summary (never the request, AC-SM-07) with the normal hooks, then `release.py install` as its own
  systemd job (the install restarts pip-builder too, so the builder watches for its exit file and picks up
  where it left off after a restart). Live: main is fast-forwarded if ~/pip is clean; else the branch stays.
- **Undo**: `git revert` as a new commit, released the same way.
"""
import asyncio
import contextlib
import difflib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox
from .store import FINAL, RUNNING, WAITING, Store

log = logging.getLogger("pip-builder")

# Paths a change may never touch (checked at "checking" time; plan rule in agent.RULES).
FORBIDDEN = [
    (re.compile(r"^pyproject\.toml$"), "dependencies need the owner (pyproject.toml)"),
    (re.compile(r"^uv\.lock$"), "dependencies need the owner (uv.lock)"),
    (re.compile(r"^\.venv(/|$)"), "the virtual environment"),
    (re.compile(r"^deploy/systemd/"), "the service files"),
    (re.compile(r"^tools/check_"), "the commit checks"),
    (re.compile(r"^tools/install_hooks\.sh$"), "the commit checks"),
    (re.compile(r"^tools/sandbox-test$"), "the test sandbox"),
    (re.compile(r"(^|/)\.gitignore$"), ".gitignore"),
    (re.compile(r"(^|/)\.git"), "git's own files"),
    (re.compile(r"(^|/)\.claude(/|$)|(^|/)\.mcp\.json$"), "Claude Code's settings"),
    (re.compile(r"^(setup\.py|setup\.cfg|MANIFEST\.in)$"), "the build set-up"),
    (re.compile(r"(^|/)\.\.(/|$)"), "outside the project"),
]
# A change touching any of these is "security" risk, whatever the plan said: approval needs "I've read the diff".
SECURITY = re.compile(r"^(pip_assistant/web/security\.py|pip_assistant/builder/|pip_assistant/release\.py|deploy/|"
                      r"pip_assistant/core/bus\.py|pip_assistant/common/secrets|tools/|CLAUDE\.md$)")
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
MAX_FILE_BYTES = 1_000_000
MAX_DIFF_CHARS = 400_000


# ============================================================================ checking what changed
def _walk(root: Path) -> dict[str, Path]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in filenames:
            if f.endswith(".pyc"):
                continue
            p = Path(dirpath) / f
            out[p.relative_to(root).as_posix()] = p
    return out


@dataclass
class Diff:
    files: list[str] = field(default_factory=list)          # changed paths, sorted
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    text: str = ""
    new_content: dict = field(default_factory=dict)          # path → new text (not for deleted files)
    problems: list[str] = field(default_factory=list)


def _read_text(p: Path) -> str | None:
    """A file's text, or None if it's binary, a link, or too big."""
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
        return None
    data = p.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def compute_diff(base: Path, src: Path) -> Diff:
    before, after = _walk(base), _walk(src)
    d = Diff()
    parts = []
    for rel in sorted(set(before) | set(after)):
        a, b = before.get(rel), after.get(rel)
        if a and b and not a.is_symlink() and not b.is_symlink() and a.read_bytes() == b.read_bytes() \
                and os.access(a, os.X_OK) == os.access(b, os.X_OK):
            continue
        d.files.append(rel)
        old = _read_text(a) if a else ""
        new = _read_text(b) if b else ""
        if (a and old is None) or (b and new is None):
            d.problems.append(f"{rel}: only text files can be changed (not binary files, links or huge files)")
            continue
        if not a:
            d.added.append(rel)
        if not b:
            d.deleted.append(rel)
        else:
            d.new_content[rel] = new
        parts.append("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                  "/dev/null" if not a else f"a/{rel}",
                                                  "/dev/null" if not b else f"b/{rel}", n=3)))
        if a and b and os.access(a, os.X_OK) != os.access(b, os.X_OK):
            parts.append(f"(mode of {rel} changed)\n")
    d.text = "\n".join(p for p in parts if p)
    if len(d.text) > MAX_DIFF_CHARS:
        d.problems.append("the change is too big to review")
    return d


def forbidden(rel: str) -> str | None:
    for pat, why in FORBIDDEN:
        if pat.search(rel):
            return why
    return None


def risk_for(files: list[str], planned: str) -> str:
    return "security" if any(SECURITY.search(f) for f in files) else (planned or "behaviour")


def _load_secret_scan():
    """tools/check_secrets.py's scan() (the same patterns as the commit check)."""
    path = Path(__file__).resolve().parents[2] / "tools" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("pip_check_secrets", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scan


def secret_problems(diff: Diff) -> list[str]:
    try:
        scan = _load_secret_scan()
    except (OSError, ImportError, AttributeError) as e:
        return [f"couldn't load the secret check ({type(e).__name__}), so the change can't be checked"]
    out = []
    for rel, text in diff.new_content.items():
        out += scan(text, rel) + scan(rel, "file name")
    return out


# ============================================================================ git (for deploy and undo)
class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True, env_extra: dict | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env_extra or {})}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300, env=env)
    if check and r.returncode != 0:
        raise GitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


PIP_AUTHOR = ["-c", "user.name=Pip", "-c", "user.email=pip@localhost"]


def commit_message(change: dict) -> str:
    """Made from the approved summary only, never the request (AC-SM-07)."""
    summary = re.sub(r"\s+", " ", (change.get("plan") or {}).get("summary") or change.get("summary") or "").strip()
    request = re.sub(r"\s+", " ", change.get("request") or "").strip().lower()
    if not summary or (len(request) >= 12 and request in summary.lower()) or \
            (len(summary) >= 12 and summary.lower() in request):
        summary = f"Self-change #{change['id']}: " + ", ".join(change.get("files") or [])[:60]
    return f"{summary[:100]}\n\nPip self-change #{change['id']} (approved on the dashboard)"


class Deployer:
    """Git work in ~/pip: a branch per change, a commit by "Pip", fast-forwarding main. Never pushes."""

    def __init__(self, repo: Path, scratch: Path):
        self.repo, self.scratch = Path(repo), Path(scratch)

    def main_sha(self) -> str:
        return git(self.repo, "rev-parse", "--verify", "main^{commit}")

    def is_ancestor(self, a: str, b: str) -> bool:
        r = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", a, b], capture_output=True)
        return r.returncode == 0

    def branch_exists(self, branch: str) -> bool:
        return bool(git(self.repo, "branch", "--list", branch))

    def _worktree(self, name: str, branch: str, start: str) -> Path:
        self.scratch.mkdir(parents=True, exist_ok=True)
        wt = self.scratch / name
        if wt.exists():
            git(self.repo, "worktree", "remove", "--force", str(wt), check=False)
            shutil.rmtree(wt, ignore_errors=True)
        git(self.repo, "worktree", "prune", check=False)
        if self.branch_exists(branch):
            git(self.repo, "branch", "-D", branch)
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt), start)
        return wt

    def _drop_worktree(self, wt: Path) -> None:
        git(self.repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(wt, ignore_errors=True)
        git(self.repo, "worktree", "prune", check=False)

    def commit_change(self, change: dict, src: Path, diff: Diff, note) -> tuple[str, str]:
        """Apply the change on a new branch from its base, commit it, and put it on top of main if main moved.
        Returns (commit sha, branch)."""
        branch = f"pip/change-{change['id']}"
        wt = self._worktree(f"change-{change['id']}", branch, change["base"])
        try:
            for rel in diff.files:
                if forbidden(rel):
                    raise GitError(f"refusing to write {rel}")
                target = wt / rel
                if rel in diff.deleted:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(diff.new_content[rel], encoding="utf-8")
                os.chmod(target, 0o755 if os.access(src / rel, os.X_OK) else 0o644)
            git(wt, "add", "-A")
            staged = sorted(git(wt, "diff", "--cached", "--name-only").splitlines())
            if staged != sorted(diff.files):
                extra = sorted(set(diff.files) ^ set(staged))
                raise GitError(f"git would commit different files than were checked: {', '.join(extra)[:300]}")
            git(wt, *PIP_AUTHOR, "commit", "-q", "-m", commit_message(change))   # the hooks run here
            main = self.main_sha()
            if main != change["base"]:
                note("Pip's code changed since this was planned: putting the change on top of the newest version.")
                r = subprocess.run(["git", "-C", str(wt), *PIP_AUTHOR, "rebase", "-q", main],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    git(wt, "rebase", "--abort", check=False)
                    raise GitError("the newest version of Pip changed the same lines; ask for the change again")
            return git(wt, "rev-parse", "HEAD"), branch
        except BaseException:
            self._drop_worktree(wt)
            git(self.repo, "branch", "-D", branch, check=False)
            raise
        finally:
            self._drop_worktree(wt)

    def revert(self, change: dict) -> tuple[str, str]:
        """A new commit taking the change back out, on a branch from main (or from the change's branch, if it
        was never merged). Returns (commit sha, branch)."""
        commit = change["commit"]
        start = "main" if self.is_ancestor(commit, "main") else commit
        branch = f"pip/undo-{change['id']}"
        wt = self._worktree(f"undo-{change['id']}", branch, start)
        try:
            r = subprocess.run(["git", "-C", str(wt), "revert", "--no-commit", commit], capture_output=True, text=True)
            if r.returncode != 0:
                git(wt, "revert", "--abort", check=False)
                raise GitError("later changes touched the same lines, so it can't be taken out automatically")
            summary = re.sub(r"\s+", " ", change.get("summary") or "").strip()
            git(wt, *PIP_AUTHOR, "commit", "-q", "-m",
                f"Undo: {summary[:90]}\n\nPip self-change #{change['id']} undone (approved on the dashboard)")
            return git(wt, "rev-parse", "HEAD"), branch
        except BaseException:
            git(self.repo, "branch", "-D", branch, check=False)
            raise
        finally:
            self._drop_worktree(wt)

    def fast_forward_main(self, branch: str) -> tuple[bool, str]:
        """Move main to the branch if ~/pip has main checked out, is clean, and it's a fast-forward."""
        head = git(self.repo, "symbolic-ref", "--short", "-q", "HEAD", check=False)
        if head != "main":
            return False, f"~/pip has {head or 'no branch'} checked out, not main"
        if git(self.repo, "status", "--porcelain", "--untracked-files=no"):
            return False, "~/pip has uncommitted changes"
        if not self.is_ancestor("main", branch):
            return False, "main has moved on"
        try:
            git(self.repo, "merge", "-q", "--ff-only", branch)
        except GitError as e:
            return False, str(e)
        git(self.repo, "branch", "-d", branch, check=False)
        return True, ""

    def delete_branch(self, branch: str) -> None:
        if branch:
            git(self.repo, "branch", "-D", branch, check=False)


# ============================================================================ installing a release
class ReleaseRunner:
    """Runs `python3 -m pip_assistant.release install <commit> --summary …` from ~/pip as its own systemd job
    (pip-release-<id>, detached), writing its exit code to a file; release-state.json is the fallback.
    The release id is the first 10 hex digits of the commit (release.py's id). Exit codes: 0 live, 1 rolled back (healthy again),
    2 stopped before anything changed, 3 unhealthy and needs the owner."""

    def __init__(self, repo: Path, folder: Path, state_file: Path | None = None):
        self.repo, self.folder = Path(repo), Path(folder)
        self.state_file = Path(state_file) if state_file else Path.home() / "pip-app/release-state.json"

    def _files(self, cid, tag):
        return self.folder / f"{cid}-{tag}.exit", self.folder / f"{cid}-{tag}.log"

    def unit(self, cid, tag) -> str:
        return f"pip-release-{cid}" + ("" if tag == "install" else f"-{tag}")

    async def start(self, cid, tag: str, commit: str, summary: str) -> None:
        """Start the install as a detached job (no --wait: the install restarts pip-builder itself)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        exit_file, log_file = self._files(cid, tag)
        exit_file.unlink(missing_ok=True)
        script = ('/usr/bin/python3 -m pip_assistant.release install "$1" --summary "$2" >"$3" 2>&1; '
                  'echo $? >"$4.tmp" && mv "$4.tmp" "$4"')
        cmd = ["systemd-run", "--user", "--quiet", "--collect", f"--unit={self.unit(cid, tag)}",
               "-p", "MemoryMax=2G", "-p", "TimeoutStopSec=15min", "-p", "Nice=10",
               f"--working-directory={self.repo}", f"--setenv=PYTHONPATH={self.repo}",
               f"--setenv=PATH={Path.home() / '.local/bin'}:/usr/bin:/bin",
               "/bin/sh", "-c", script, "sh", commit, summary, str(log_file), str(exit_file)]
        r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"couldn't start the install job: {(r.stderr or r.stdout).strip()[-300:]}")

    def from_state(self, rid: str) -> tuple[int | None, str]:
        """How an install of release `rid` went, from ~/pip-app/release-state.json (when the exit file is
        missing): (0, "") live, (1, reason) rolled back, (None, "") can't tell."""
        try:
            state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return None, ""
        problem = state.get("last_problem") or {}
        if isinstance(problem, dict) and problem.get("release") == rid:
            return 1, str(problem.get("reason") or "")
        if state.get("current") == rid:
            return 0, ""
        return None, ""

    def result(self, cid, tag) -> int | None:
        exit_file = self._files(cid, tag)[0]
        try:
            return int(exit_file.read_text().strip())
        except (OSError, ValueError):
            return None

    def running(self, cid, tag) -> bool:
        return sandbox.unit_active(self.unit(cid, tag))

    def tail(self, cid, tag, lines: int = 6) -> str:
        try:
            return "\n".join(self._files(cid, tag)[1].read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def release_id(self) -> str:
        try:
            state = json.loads(self.state_file.read_text())
            return str(state.get("current") or "")
        except (OSError, ValueError, AttributeError):
            return ""

    def clear(self, cid, tag) -> None:
        for f in self._files(cid, tag):
            f.unlink(missing_ok=True)


# ============================================================================ the workspace
async def uv_sync(src: Path) -> tuple[bool, str]:
    """Make the workspace's .venv from the untouched export, before the agent runs (its own memory cap)."""
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    base = ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet", f"--working-directory={src}",
            "-p", "MemoryMax=1500M", "-p", "Nice=10", "-E", "UV_PROJECT_ENVIRONMENT=", uv, "sync", "--frozen", "-q"]
    code, out = await sandbox.run(base + ["--offline"], timeout=600)
    if code != 0:
        code, out = await sandbox.run(base, timeout=900)
    return code == 0, out[-500:]


class Workspaces:
    def __init__(self, repo: Path, root: Path, venv_maker=None):
        self.repo, self.root = Path(repo), Path(root)
        self.venv_maker = venv_maker or uv_sync

    def path(self, cid) -> Path:
        return self.root / str(cid)

    async def prepare(self, cid, sha: str) -> Path:
        """A fresh export of `sha` (no .git) in src/ and base/, the agent's home folder, and src/.venv."""
        work = self.path(cid)
        self.remove(cid)
        for sub in ("src", "base"):
            (work / sub).mkdir(parents=True)
            await asyncio.to_thread(self._export, sha, work / sub)
        wrapper = Path(__file__).resolve().parents[2] / sandbox.TEST_WRAPPER
        for sub in ("src", "base"):   # an older version of the code may not have the test wrapper yet
            target = work / sub / sandbox.TEST_WRAPPER
            if not target.exists() and wrapper.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(wrapper, target)
        claude_dir = work / "home" / ".claude"
        claude_dir.mkdir(parents=True)
        os.chmod(work / "home", 0o700)
        placeholder = claude_dir / ".credentials.json"   # the real file is bind-mounted over it, never copied
        placeholder.touch(mode=0o600)
        ok, out = await self.venv_maker(work / "src")
        if not ok:
            raise RuntimeError(f"couldn't make the workspace's Python environment: {out[-300:]}")
        return work

    def _export(self, sha: str, dest: Path) -> None:
        archive = subprocess.Popen(["git", "-C", str(self.repo), "archive", "--format=tar", sha],
                                   stdout=subprocess.PIPE)
        tar = subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, capture_output=True)
        archive.stdout.close()
        if archive.wait() != 0 or tar.returncode != 0:
            raise RuntimeError(f"couldn't export the code: {tar.stderr.decode(errors='replace')[-200:]}")

    def remove(self, cid) -> None:
        work = self.path(cid)
        if work.exists():
            # the agent can't make files we can't delete, but read-only folders need their write bit back
            for dirpath, dirnames, _ in os.walk(work):
                for d in dirnames:
                    with contextlib.suppress(OSError):
                        os.chmod(Path(dirpath) / d, 0o700)
            shutil.rmtree(work, ignore_errors=True)

    def existing(self) -> list[str]:
        return [p.name for p in self.root.iterdir()] if self.root.is_dir() else []

    @staticmethod
    def clean_caches(src: Path) -> None:
        """Remove bytecode and caches before checking, so only the source files count."""
        for dirpath, dirnames, filenames in os.walk(src):
            if ".venv" in dirnames:
                dirnames.remove(".venv")
            for d in list(dirnames):
                if d in SKIP_DIRS:
                    shutil.rmtree(Path(dirpath) / d, ignore_errors=True)
                    dirnames.remove(d)
            for f in filenames:
                if f.endswith(".pyc"):
                    (Path(dirpath) / f).unlink(missing_ok=True)


async def strict_check(cid, src: Path) -> tuple[int, str]:
    """The full test suite and the import check in the strict sandbox (no network)."""
    return await sandbox.run(sandbox.check_command(cid, src), timeout=1000)


# ============================================================================ the pipeline
LABELS = {
    "asked": "Waiting its turn", "planning": "Claude is planning it", "questions": "Pip has questions for you",
    "plan_ready": "Plan ready: approve it to build", "building": "Claude is building it",
    "checking": "Checking it (tests in the sandbox)", "ready": "Ready: review and approve to put it live",
    "deploying": "Putting it live", "live": "Live", "rolled_back": "Rolled back: it wasn't healthy",
    "failed": "Failed", "rejected": "Rejected", "stopped": "Stopped", "undone": "Undone",
}
MAX_QUESTION_ROUNDS = 3


class Builder:
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
        return ((c["undo_commit"] if tag == "undo" else c["commit"]) or "")[:10]

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
        self.store.update(cid, commit=commit, branch=branch, release=commit[:10])
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
        if code == 0:
            ok, why = await asyncio.to_thread(self.deployer.fast_forward_main, branch)
            self.note(cid, "main now includes it." if ok else
                      f"Kept on branch {branch} ({why}): merge it into main when ready.")
            self.releaser.clear(cid, tag)
            if undo:
                await self.set_status(cid, "undone", release=release, error="" if ok else f"Merge {branch} when ready.")
            else:
                await self.set_status(cid, "live", release=release, error="" if ok else f"Merge {branch} when ready.")
            return
        why = {1: "Pip wasn't healthy on the new version, so he went back to the one before.",
               2: "The install stopped before anything changed.",
               3: "Pip wasn't healthy and going back didn't work either: he needs you (see the release log).",
               -1: "The install job ended without saying how it went."}.get(code, f"The install ended with code {code}.")
        if undo:
            self.deployer.delete_branch(branch)
            await self.set_status(cid, "live", error=f"Undo didn't go live: {why}")
        elif code == 1:
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

    # ------------------------------------------------------------------ what people do
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

    async def shutdown(self) -> None:
        """Pip's builder is stopping: stop a running Claude or check job (it'd be marked failed at start-up)."""
        if self.job and not self.job.done() and self.job_id:
            c = self.store.get(self.job_id)
            if c and c["status"] in RUNNING:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.agent.stop, self.job_id)
                    await asyncio.to_thread(sandbox_stop_check, self.job_id)
            self.job.cancel()


def sandbox_stop_check(cid) -> None:
    with contextlib.suppress(Exception):
        sandbox.stop_unit(sandbox.check_unit(cid))
