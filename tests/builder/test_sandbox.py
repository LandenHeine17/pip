"""The self-change sandboxes (M1b, AC-SM-03, PIP-NEW-072).

Real checks on this machine (skipped where unshare / systemd user jobs aren't available):
- tools/sandbox-test hides the home folder (secrets, Claude's login, SSH keys, the repo) and the network;
- the agent sandbox's systemd-run flags really hide the home folder except the change's folder and the login file,
  and keep the test wrapper and the .venv read-only.
And unit checks that the command lines carry every protection (Claude itself is never started here).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import REPO

from pip_assistant.builder import agent, sandbox

WRAPPER = REPO / "tools" / "sandbox-test"
HOME = Path.home()


def _can_unshare() -> bool:
    if not shutil.which("unshare"):
        return False
    r = subprocess.run(["unshare", "-rnm", "true"], capture_output=True)
    return r.returncode == 0


def _can_systemd_run() -> bool:
    if not shutil.which("systemd-run") or not os.environ.get("XDG_RUNTIME_DIR"):
        return False
    r = subprocess.run(["systemd-run", "--user", "--wait", "--pipe", "--collect", "--quiet", "true"],
                       capture_output=True, timeout=30)
    return r.returncode == 0


PROBE_TEST = '''
import os, socket
from pathlib import Path
import pytest
H = Path({home!r})

@pytest.mark.parametrize("p", ["pip-data/secrets.toml", ".claude/.credentials.json", ".ssh", "pip"])
def test_hidden(p):
    assert not (H / p).exists()

def test_no_network():
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 443), timeout=3)

def test_not_root_and_empty_environment():
    assert os.getuid() == {uid}
    assert "XDG_RUNTIME_DIR" not in os.environ and "SSH_AUTH_SOCK" not in os.environ

def test_workspace_writable():
    Path("written.txt").write_text("ok")
'''


@pytest.fixture
def probe_project(tmp_path):
    """A tiny project with its own .venv (pytest from uv's cache, offline) and a probe test."""
    uv = shutil.which("uv") or str(HOME / ".local/bin/uv")
    if not Path(uv).exists():
        pytest.skip("uv isn't available")
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    r = subprocess.run([uv, "venv", "-q", "-p", py, str(tmp_path / ".venv")], capture_output=True, text=True)
    if r.returncode == 0:
        r = subprocess.run([uv, "pip", "install", "-q", "--offline", "-p", str(tmp_path / ".venv/bin/python"),
                            "pytest"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"couldn't make a test venv offline: {r.stderr[-200:]}")
    (tmp_path / "test_probe.py").write_text(PROBE_TEST.format(home=str(HOME), uid=os.getuid()))
    return tmp_path


@pytest.mark.skipif(not _can_unshare(), reason="unprivileged user namespaces aren't available")
def test_sandbox_test_hides_home_and_network(probe_project):
    secrets = HOME / "pip-data/secrets.toml"
    r = subprocess.run([str(WRAPPER), "test_probe.py"], cwd=probe_project,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert "7 passed" in r.stdout
    assert (probe_project / "written.txt").read_text() == "ok"     # the project folder is still there
    if secrets.exists():
        assert secrets.read_text()                                  # and the real one is untouched outside


def test_sandbox_test_needs_a_venv(tmp_path):
    r = subprocess.run([str(WRAPPER)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "project folder" in r.stderr


def test_sandbox_test_script_is_strict():
    text = WRAPPER.read_text()
    assert os.access(WRAPPER, os.X_OK)
    assert "unshare -rnm" in text and "env -i" in text and "PATH=/usr/bin:/bin" in text
    assert "/usr/bin/unshare" in text          # not looked up on a PATH the caller controls


# ---------------------------------------------------------------- the command lines
def _props(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-p"]


def test_agent_command_has_the_protections(tmp_path):
    work = tmp_path / "7"
    cmd = sandbox.agent_command("7", work, 30, ["--print"])
    props = _props(cmd)
    assert cmd[:5] == ["systemd-run", "--user", "--wait", "--pipe", "--collect"]
    assert "--unit=pip-builder-agent-7" in cmd
    assert f"--working-directory={work / 'src'}" in cmd
    for p in ("ProtectHome=tmpfs", f"BindPaths={work}", "NoNewPrivileges=yes", "PrivateTmp=yes",
              "ProtectSystem=strict", "MemoryMax=2G", "CPUQuota=200%", "RuntimeMaxSec=1800",
              f"BindReadOnlyPaths={work / 'src/tools/sandbox-test'}", f"BindReadOnlyPaths={work / 'src/.venv'}",
              f"BindReadOnlyPaths={sandbox.claude_program().parent}", f"BindReadOnlyPaths=-{sandbox.uv_python()}"):
        assert p in props, p
    # the login file is bind-mounted (never copied) into the agent's throwaway home
    assert f"BindPaths={HOME / '.claude/.credentials.json'}:{work / 'home/.claude/.credentials.json'}" in props
    assert "PrivateNetwork=yes" not in props            # Claude needs the network; the test wrapper takes it away
    assert f"HOME={work / 'home'}" in cmd and "PATH=/usr/bin:/bin" in cmd
    assert cmd[-2:] == [str(sandbox.claude_program()), "--print"]


def test_check_command_is_the_strict_m0_sandbox(tmp_path):
    src = tmp_path / "src"
    cmd = sandbox.check_command("7", src)
    props = _props(cmd)
    for p in ("ProtectHome=tmpfs", f"BindPaths={src}", "PrivateNetwork=yes", "NoNewPrivileges=yes",
              "PrivateTmp=yes", "MemoryMax=2G", "RuntimeMaxSec=900", f"BindReadOnlyPaths={src / '.venv'}"):
        assert p in props, p
    assert "pytest" in cmd[-1] and "pip_assistant.core.main" in cmd[-1]


def test_claude_arguments_planning_is_read_only(tmp_path):
    args = agent.claude_args("plan", "sonnet", "medium", "RULES", tmp_path / "src")
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep"
    assert args[args.index("--allowedTools") + 1] == "Read,Glob,Grep"
    for a in ("--strict-mcp-config", "--output-format", "--append-system-prompt"):
        assert a in args
    assert args[args.index("--setting-sources") + 1] == "project"
    assert args[args.index("--model") + 1] == "sonnet" and args[args.index("--effort") + 1] == "medium"


def test_claude_arguments_building_runs_only_the_wrapper(tmp_path):
    src = tmp_path / "src"
    args = agent.claude_args("build", "sonnet", "", "RULES", src)
    assert args[args.index("--tools") + 1] == "Read,Edit,Write,Glob,Grep,Bash"
    allowed = args[args.index("--allowedTools") + 1]
    assert allowed == f"Read,Edit,Write,Glob,Grep,Bash({src}/tools/sandbox-test:*)"   # full path only
    assert "WebFetch" not in " ".join(args) and "WebSearch" not in " ".join(args)
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "--effort" not in args


# ---------------------------------------------------------------- the agent sandbox, for real (without Claude)
AGENT_PROBE = r'''
import json, os, socket
from pathlib import Path
H = Path({home!r}); out = {{}}
def t(name, fn):
    try:
        fn(); out[name] = "allowed"
    except Exception as e:
        out[name] = "blocked"
t("secrets", lambda: open(H / "pip-data/secrets.toml").read())
t("ssh", lambda: os.listdir(H / ".ssh"))
t("repo", lambda: os.listdir(H / "pip"))
t("real claude settings", lambda: open(H / ".claude.json").read())
t("login file", lambda: open(os.environ["HOME"] + "/.claude/.credentials.json").read(1))
t("change wrapper", lambda: open("tools/sandbox-test", "a").write(""))
t("change venv", lambda: open(".venv/marker", "w").write(""))
t("write workspace", lambda: open("ok.txt", "w").write("x"))
print(json.dumps(out))
'''


@pytest.mark.skipif(not _can_systemd_run(), reason="systemd user jobs aren't available")
def test_agent_sandbox_hides_home_except_workspace_and_login(tmp_path):
    if not sandbox.credentials().exists() or not sandbox.claude_program().exists():
        pytest.skip("Claude Code isn't installed and signed in here")
    work = tmp_path / "9"
    src = work / "src"
    (src / "tools").mkdir(parents=True)
    (src / ".venv").mkdir()
    shutil.copy2(WRAPPER, src / "tools/sandbox-test")
    (work / "home/.claude").mkdir(parents=True)
    (work / "home/.claude/.credentials.json").touch(mode=0o600)
    (src / "probe.py").write_text(AGENT_PROBE.format(home=str(HOME)))
    cmd = sandbox.agent_command("test-9", work, 2, [])
    cmd = cmd[:cmd.index(str(sandbox.claude_program()))] + ["/usr/bin/python3", "-I", "probe.py"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {"secrets": "blocked", "ssh": "blocked", "repo": "blocked", "real claude settings": "blocked",
                   "login file": "allowed", "change wrapper": "blocked", "change venv": "blocked",
                   "write workspace": "allowed"}
    assert (src / "ok.txt").exists() and (work / "home/.claude/.credentials.json").stat().st_size == 0
