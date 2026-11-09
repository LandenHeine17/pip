"""Installing a change as a new version of Pip, as its own job (M1b).

The builder doesn't install anything itself: it starts `python3 -m pip_assistant.release install <commit>`
(release.py, the same installer deploy/install.sh uses) as a separate systemd job, then watches for the
small file that job writes its exit code to. The install restarts pip-builder too, so after a restart the
builder picks up where it left off; if the exit file is missing, ~/pip-app/release-state.json says how
it went.

release.py is the one place that says what a release id is, what its exit codes mean and how to read its
state file; this file uses its names (release_id, EXIT_..., install_outcome) instead of copying them.
"""
import asyncio
import subprocess
from pathlib import Path

from ..release import Releases, install_outcome
from . import sandbox


class ReleaseRunner:
    """Runs `python3 -m pip_assistant.release install <commit> --summary …` from ~/pip as its own systemd job
    (pip-release-<id>, detached), writing its exit code to a file; release-state.json is the fallback.
    What the exit codes mean: release.py's EXIT_LIVE, EXIT_ROLLED_BACK, EXIT_STOPPED and EXIT_NEEDS_OWNER."""

    def __init__(self, repo: Path, folder: Path, app: Path | None = None):
        self.repo, self.folder = Path(repo), Path(folder)
        # only used to read ~/pip-app/release-state.json (app = the folder holding it)
        self.releases = Releases(repo=repo, app=Path(app) if app else Path.home() / "pip-app")

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
        return install_outcome(self.releases.load_state(), rid)

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
        """The release that's current, as release-state.json last recorded it ("" if there's none)."""
        return str(self.releases.load_state().get("current") or "")

    def clear(self, cid, tag) -> None:
        for f in self._files(cid, tag):
            f.unlink(missing_ok=True)
