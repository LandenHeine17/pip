"""The workspace a change is planned and built in (M1b).

One folder per change under ~/pip-data/builder/work/<id>/:
    src/    a plain copy of Pip's code (no .git) that Claude may edit, with its own .venv
    base/   an untouched copy of the same code, to compare against
    home/   the agent's home folder (only a placeholder for Claude's login, filled in by the sandbox)
It's deleted when the change ends.
"""
import asyncio
import contextlib
import os
import shutil
import subprocess
from pathlib import Path

from . import sandbox
from .checks import SKIP_DIRS


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
