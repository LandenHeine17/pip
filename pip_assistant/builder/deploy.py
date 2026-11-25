"""Pip's self-changes in git (D-152): finding a change's commits, and undoing them. Never pushes.

- change_commits(): the commits on main that end with a `Pip-Change: <id>` line (Claude adds it in the Claude
  Hub chat), by change, oldest first.
- in_release(): whether a commit is in the version Pip is running.
- revert(): undoing a live change is a new commit that takes all its commits back out (branch pip/undo-<id>).
- fast_forward_main(): once the undo is live, main moves to it, but only if ~/pip is clean and on main.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path



class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True, env_extra: dict | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env_extra or {})}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300, env=env)
    if check and r.returncode != 0:
        raise GitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


PIP_AUTHOR = ["-c", "user.name=Pip", "-c", "user.email=pip@localhost"]


class Deployer:
    """Git work in ~/pip: finding a change's commits, an undo branch with a commit by "Pip", fast-forwarding main.
    Never pushes."""

    def __init__(self, repo: Path, scratch: Path):
        self.repo, self.scratch = Path(repo), Path(scratch)

    def main_sha(self) -> str:
        return git(self.repo, "rev-parse", "--verify", "main^{commit}")

    def change_commits(self, days: float = 120) -> dict[str, list[str]]:
        """{change id: [commit, …]} for the commits on main with a "Pip-Change: <id>" line, oldest first."""
        out = git(self.repo, "log", "main", f"--since={int(days)} days ago", "--reverse",
                  "--format=%H %(trailers:key=Pip-Change,valueonly,separator=%x2C)")
        found: dict[str, list[str]] = {}
        for line in out.splitlines():
            sha, _, ids = line.partition(" ")
            for cid in re.findall(r"\d+", ids):
                found.setdefault(str(int(cid)), []).append(sha)
        return found

    def in_release(self, sha: str, release: str) -> bool:
        """Is this commit in release `release` (a release id is the start of its commit's sha)?"""
        return bool(release) and self.is_ancestor(sha, release)

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

    def revert(self, change: dict) -> tuple[str, str]:
        """A new commit taking all of the change's commits back out, on a branch from main. Returns (sha, branch)."""
        commits = list(change.get("commits") or []) or ([change["commit"]] if change.get("commit") else [])
        if not commits:
            raise GitError("this change has no commits to take out")
        start = "main" if all(self.is_ancestor(c, "main") for c in commits) else commits[-1]
        branch = f"pip/undo-{change['id']}"
        wt = self._worktree(f"undo-{change['id']}", branch, start)
        try:
            r = subprocess.run(["git", "-C", str(wt), "revert", "--no-commit", *reversed(commits)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                git(wt, "revert", "--abort", check=False)
                raise GitError("later changes touched the same lines, so it can't be taken out automatically")
            summary = re.sub(r"\s+", " ", change.get("summary") or "").strip()
            git(wt, *PIP_AUTHOR, "commit", "-q", "-m",
                f"Undo: {summary[:90]}\n\nPip self-change #{change['id']} undone (Undo on the Changes page)")
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
