"""The two sandboxes self-changes run in (M1b, AC-SM-03, PIP-NEW-072). Both are transient systemd user jobs.

1. The **agent sandbox**: Claude Code edits the workspace copy. The home folder is an empty, read-only tmpfs
   except the change's own folder; the Claude program and uv's Python are visible read-only; the Claude login
   file (~/.claude/.credentials.json) is bind-mounted in (never copied) so Claude can sign in and refresh it.
   The network is allowed, because Claude needs it; the only command Claude may run is tools/sandbox-test,
   which takes the network and the home folder away again before any generated code runs.
2. The **strict sandbox** (the M0 recipe, m0/sandbox_test.py): the builder's own checks run the generated code
   here, with no network at all, no home folder and only the workspace's code visible.

Layout of a change's folder, ~/pip-data/builder/work/<id>/:
    src/     the plain export the agent edits (no .git), with its .venv made before the agent runs
    base/    the same export, untouched, to diff against
    home/    the agent's throwaway home folder (Claude's settings, sessions; the login file is mounted here)
"""
import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("pip-builder")

PATH = "/usr/bin:/bin"
TEST_WRAPPER = "tools/sandbox-test"


def home() -> Path:
    return Path.home()


def claude_program() -> Path:
    """The real Claude Code program (~/.local/bin/claude is a link into ~/.local/share/claude/versions/)."""
    found = shutil.which("claude") or str(home() / ".local/bin/claude")
    return Path(os.path.realpath(found))


def uv_python() -> Path:
    return home() / ".local/share/uv/python"


def credentials() -> Path:
    return home() / ".claude/.credentials.json"


def agent_unit(cid) -> str:
    return f"pip-builder-agent-{cid}"


def check_unit(cid) -> str:
    return f"pip-builder-check-{cid}"


def common_limits(minutes: float) -> list[str]:
    return ["-p", "NoNewPrivileges=yes", "-p", "PrivateTmp=yes", "-p", "ProtectSystem=strict",
            "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=200%",
            "-p", f"RuntimeMaxSec={int(minutes * 60)}", "-p", "Nice=10"]


def agent_command(cid, work: Path, minutes: float, claude_args: list[str]) -> list[str]:
    """systemd-run for one Claude Code run in the agent sandbox. claude_args come after the program path."""
    work = Path(work)
    src, agent_home = work / "src", work / "home"
    program = claude_program()
    return ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet", f"--unit={agent_unit(cid)}",
            f"--working-directory={src}",
            "-p", "ProtectHome=tmpfs",
            "-p", f"BindPaths={work}",
            # the test wrapper and the venv can't be changed by the agent (they run outside its edits' reach)
            "-p", f"BindReadOnlyPaths={src / TEST_WRAPPER}",
            "-p", f"BindReadOnlyPaths={src / '.venv'}",
            "-p", f"BindReadOnlyPaths={program.parent}",
            "-p", f"BindReadOnlyPaths=-{uv_python()}",
            "-p", f"BindPaths={credentials()}:{agent_home / '.claude/.credentials.json'}",
            *common_limits(minutes),
            "-E", f"HOME={agent_home}", "-E", f"PATH={PATH}", "-E", "LANG=C.UTF-8",
            "-E", "DISABLE_AUTOUPDATER=1", "-E", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            "-E", "DISABLE_TELEMETRY=1",
            str(program), *claude_args]


SMOKE = ("import importlib, importlib.util as u; "
         "[importlib.import_module(m) for m in ('pip_assistant.core.main', 'pip_assistant.web.main', "
         "'pip_assistant.thinker.main', 'pip_assistant.ears.main', 'pip_assistant.builder.main') "
         "if m.startswith(('pip_assistant.core', 'pip_assistant.web', 'pip_assistant.thinker', 'pip_assistant.ears')) "
         "or u.find_spec(m.rsplit('.', 1)[0])]")


def check_command(cid, src: Path, minutes: float = 15) -> list[str]:
    """systemd-run for the full test suite plus an import smoke run in the strict sandbox (no network)."""
    src = Path(src)
    script = (".venv/bin/python -m pytest -q -p no:cacheprovider && echo 'Start-up check:' && "
              f".venv/bin/python -c '{SMOKE}' && echo 'all modules import'")
    return ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet", f"--unit={check_unit(cid)}",
            f"--working-directory={src}",
            "-p", "ProtectHome=tmpfs", "-p", f"BindPaths={src}", "-p", f"BindReadOnlyPaths={src / '.venv'}",
            "-p", f"BindReadOnlyPaths=-{uv_python()}", "-p", "PrivateNetwork=yes", *common_limits(minutes),
            "-E", "HOME=/tmp", "-E", f"PATH={PATH}", "-E", "LANG=C.UTF-8", "-E", "PYTHONDONTWRITEBYTECODE=1",
            "/bin/sh", "-c", script]


async def run(cmd: list[str], stdin: bytes = b"", on_line=None, timeout: float | None = None) -> tuple[int, str]:
    """Run a command, feeding it stdin, calling on_line(text) for each output line. Returns (exit code, the
    last 20 kB of output). stderr is merged into the output."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, limit=16 * 1024 * 1024)
    tail: list[str] = []

    async def feed():
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write(stdin)
            await proc.stdin.drain()
            proc.stdin.close()

    async def read():
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip("\n")
            tail.append(line)
            if len(tail) > 400:
                del tail[:100]
            if on_line:
                await on_line(line)
    try:
        await asyncio.wait_for(asyncio.gather(feed(), read(), proc.wait()), timeout)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    return proc.returncode, "\n".join(tail)[-20000:]


def stop_unit(unit: str) -> None:
    """Stop a sandbox job (the Stop button, or Pip shutting down a job)."""
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=30)


def unit_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() in ("active", "activating", "deactivating")
