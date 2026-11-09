"""Checking a change: what changed, and whether it's allowed (M1b, AC-SM-05).

After Claude has built a change in its workspace, these decide whether it can even be shown to the owner:
- compute_diff(): compares the untouched copy with the changed one (text files only) and makes the diff.
- forbidden(): paths a change may never touch (dependencies, service files, the commit checks, git, ...).
- risk_for(): a change touching security-sensitive code is "security" risk, whatever the plan said.
- secret_problems(): the same secret scan as the commit check, on every new file.
- strict_check(): the full test suite and an import check in the strict sandbox (no network).
Passing only makes a change reviewable; the owner still approves it.
"""
import contextlib
import difflib
import importlib.util
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox

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


async def strict_check(cid, src: Path) -> tuple[int, str]:
    """The full test suite and the import check in the strict sandbox (no network)."""
    return await sandbox.run(sandbox.check_command(cid, src), timeout=1000)


def sandbox_stop_check(cid) -> None:
    """Stop a change's strict-sandbox test job, if it's still running (Stop, or the builder shutting down)."""
    with contextlib.suppress(Exception):
        sandbox.stop_unit(sandbox.check_unit(cid))
