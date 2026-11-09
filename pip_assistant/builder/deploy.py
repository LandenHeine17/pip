"""Putting a checked change into git (M1b): Pip's own commits in ~/pip, never pushed.

- commit_change(): a git worktree on a new branch (pip/change-<id>) from the code the change was planned on,
  the checked files written in, and one commit by "Pip" made from the approved summary (never the request,
  AC-SM-07), with the normal commit checks. If main moved meanwhile, the commit is put on top of it.
- revert(): undoing a live change is a new commit that takes it back out (branch pip/undo-<id>).
- fast_forward_main(): once a change is live, main moves to it, but only if ~/pip is clean and on main.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from .checks import Diff, forbidden


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
