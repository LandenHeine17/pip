"""Versioned installs (PIP-NEW-080, M1b): pip_assistant/release.py with a temporary git repo and fake
uv, systemd and health check. Nothing here touches the real ~/pip-app or Pip's services."""
import json
import os
import subprocess

import pytest

from pip_assistant import release as R
from pip_assistant.common import program


def git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def commit(repo, msg, expected='["ears", "web"]'):
    (repo / "pip_assistant").mkdir(exist_ok=True)
    (repo / "pip_assistant" / "defaults.toml").write_text(f"[programs]\nexpected = {expected}\n")
    units = repo / "deploy" / "systemd"
    units.mkdir(parents=True, exist_ok=True)
    for p in ("core", "ears", "web"):
        (units / f"pip-{p}.service").write_text(f"[Service]\nExecStart=%h/pip-app/current/.venv/bin/pip-{p}\n")
    (repo / "change.txt").write_text(msg)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


class FakePi:
    """Pretends to be systemd plus Pip's /api/health: after a restart every program runs the version
    `current` points at (or "dev" with no current), unless that version is marked broken."""

    def __init__(self, rel):
        self.rel, self.t, self.calls = rel, 0.0, []
        self.running = "dev"
        self.broken: set[str] = set()
        self.state = "idle"
        self.web_up = True
        self.restarts = 0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def systemctl(self, *args):
        self.calls.append(args)
        if args[0] == "restart":
            self.restarts += 1
            self.running = self.rel.current_id() or "dev"
        return True, ""

    def fetch(self, path):
        if not self.web_up:
            return 0, b""
        if path == "/api/health":
            bad = self.running in self.broken
            progs = {p: {"state": "missing" if bad and p == "ears" else "ok", "release": self.running,
                         "uptime_s": self.t} for p in ("core", "ears", "web")}
            return 200, json.dumps({"state": self.state, "programs": progs}).encode()
        return (200, b"") if self.running not in self.broken else (500, b"")


def fake_uv(folder):
    (folder / ".venv").mkdir(exist_ok=True)
    return ""


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    shas = [commit(repo, "first")]
    lines = []
    rel = R.Releases(repo=repo, app=tmp_path / "app", data=tmp_path / "data", unit_dir=tmp_path / "units",
                     uv_sync=fake_uv, out=lines.append, port=1)
    pi = FakePi(rel)
    rel.systemctl, rel.fetch, rel.sleep, rel.clock = pi.systemctl, pi.fetch, pi.sleep, pi.clock
    return rel, pi, repo, shas, lines


def test_first_install_creates_release_and_current(setup):
    rel, pi, repo, shas, lines = setup
    assert rel.install("HEAD", units=True) == 0
    rid = shas[0][:10]
    assert rel.current_id() == rid and (rel.app / "current" / ".venv").is_dir()
    info = json.loads((rel.folder(rid) / "RELEASE").read_text())
    assert info["sha"] == shas[0] and info["short"] == rid and info["summary"] == "first"
    assert (rel.unit_dir / "pip-core.service").exists()           # units copied after the version exists
    assert ("restart", "pip-core.service", "pip-ears.service", "pip-web.service") in pi.calls
    assert any(c[0] == "enable" for c in pi.calls)
    st = rel.load_state()
    assert st["current"] == rid and st["history"][-1]["result"] == "live" and st.get("previous") is None
    assert pi.running == rid
    assert rel.install("HEAD") == 0 and "already the one running" in lines[-1]    # nothing to do


def test_second_install_switches_and_records_previous(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    second = commit(repo, "second")
    assert rel.install(second, now=True, summary="Pip says hi") == 0
    assert rel.current_id() == second[:10] and pi.running == second[:10]
    st = rel.load_state()
    assert st["previous"] == shas[0][:10]
    assert st["history"][-1]["summary"] == "Pip says hi"
    assert len(git(repo, "worktree", "list").splitlines()) == 3   # main + two versions


def test_unhealthy_install_rolls_back_with_reason(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    bad = commit(repo, "bad")
    pi.broken.add(bad[:10])
    assert rel.install(bad, now=True) == 1
    assert rel.current_id() == shas[0][:10] and pi.running == shas[0][:10]
    last = rel.load_state()["history"][-1]
    assert last["result"] == "rolled_back" and last["back_to"] == shas[0][:10] and last["back_ok"]
    assert "pip-ears is missing" in last["reason"]
    assert rel.load_state()["last_problem"]["release"] == bad[:10]
    assert pi.t >= R.HEALTH_WAIT_S                                    # it gave the new version the full 2 min
    assert any("Rolled back" in line for line in lines)


def test_restart_during_settling_counts_as_unhealthy(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    flaky = commit(repo, "flaky")
    real_fetch = pi.fetch
    started = {}

    def fetch(path):                      # healthy at first, then ears crashes and comes back (uptime resets)
        status, body = real_fetch(path)
        if path == "/api/health" and pi.running == flaky[:10]:
            started.setdefault("t", pi.t)
            d = json.loads(body)
            if pi.t - started["t"] > 10:
                d["programs"]["ears"]["uptime_s"] = 1
            body = json.dumps(d).encode()
        return status, body
    rel.fetch = fetch
    assert rel.install(flaky, now=True) == 1
    assert "pip-ears restarted" in rel.load_state()["history"][-1]["reason"]
    assert rel.current_id() == shas[0][:10]


def test_quiet_wait(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    second = commit(repo, "second")
    pi.state = "speaking"
    restarts = pi.restarts

    def sleep(s):
        pi.t += s
        if pi.t > 20 and pi.restarts == restarts:
            pi.state = "idle"
    rel.sleep = sleep
    assert rel.install(second) == 0
    assert any("waiting for a quiet moment" in line for line in lines)
    # Nobody answering: no waiting at all.
    third = commit(repo, "third")
    pi.web_up = False
    lines.clear()
    rel.wait_quiet()
    assert "isn't answering" in lines[-1]
    pi.web_up = True
    pi.state = "ringing"                      # busy forever: switches after 2 minutes anyway
    rel.sleep = pi.sleep
    t0 = pi.t
    rel.wait_quiet()
    assert pi.t - t0 >= R.QUIET_WAIT_S and "switching anyway" in lines[-1]
    assert rel.install(third) == 0 and rel.current_id() == third[:10]


def test_prune_keeps_three_plus_current_and_previous(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    for n in range(5):
        commit(repo, f"v{n}")
        assert rel.install("HEAD", now=True) == 0
    ids = sorted(f.name for f in rel.releases.iterdir())
    assert len(ids) == 3 and rel.current_id() in ids and rel.load_state()["previous"] in ids
    # Roll back twice (to an older one, then back), so current is not among the newest: it still stays.
    newest = rel.current_id()
    assert rel.rollback("testing") == 0
    old = rel.current_id()
    commit(repo, "w0")
    assert rel.install("HEAD", now=True) == 0
    ids = {f.name for f in rel.releases.iterdir()}
    assert old in ids and rel.load_state()["previous"] == old and newest in ids
    assert len(git(repo, "worktree", "list").splitlines()) == len(ids) + 1   # removed ones are gone from git too


def test_rollback_command(setup):
    rel, pi, repo, shas, lines = setup
    assert rel.rollback() == 2                                 # nothing to go back to
    rel.install("HEAD", units=True)
    second = commit(repo, "second")
    rel.install(second, now=True)
    assert rel.rollback("the owner asked") == 0
    assert rel.current_id() == shas[0][:10] and pi.running == shas[0][:10]
    last = rel.load_state()["history"][-1]
    assert last["action"] == "rollback" and last["reason"] == "the owner asked" and last["from"] == second[:10]
    assert rel.rollback() == 0 and rel.current_id() == second[:10]   # and back again


def test_first_run_failure_goes_back_to_old_units(setup):
    rel, pi, repo, shas, lines = setup
    rel.unit_dir.mkdir(parents=True)
    (rel.unit_dir / "pip-core.service").write_text("[Service]\nExecStart=%h/pip/.venv/bin/pip-core\n")
    pi.broken.add(shas[0][:10])
    assert rel.install("HEAD", units=True) == 1
    assert rel.current_id() is None and not (rel.app / "current").exists()
    assert "%h/pip/.venv" in (rel.unit_dir / "pip-core.service").read_text()   # old units are back
    assert pi.running == "dev"
    last = rel.load_state()["history"][-1]
    assert last["result"] == "rolled_back" and last["back_to"] == "~/pip" and last["back_ok"]
    # Fixed and run again: the backup is kept, the new version goes live.
    pi.broken.clear()
    assert rel.install("HEAD", units=True) == 0 and rel.current_id() == shas[0][:10]
    assert "pip-app/current" in (rel.unit_dir / "pip-core.service").read_text()


def test_first_run_failure_with_nothing_to_go_back_to(setup):
    rel, pi, repo, shas, lines = setup
    pi.broken.add(shas[0][:10])
    assert rel.install("HEAD", units=True) == 3
    assert any("nothing to go back to" in line for line in lines)
    assert rel.load_state()["history"][-1]["result"] == "failed"


def test_bad_commit_changes_nothing(setup):
    rel, pi, repo, shas, lines = setup
    assert rel.install("no-such-commit") == 2
    assert rel.current_id() is None and pi.calls == []


def test_uv_failure_changes_nothing(setup):
    rel, pi, repo, shas, lines = setup
    rel.install("HEAD", units=True)
    commit(repo, "second")

    def bad_uv(folder):
        raise R.ReleaseError("uv sync failed: no network")
    rel.uv_sync = bad_uv
    calls = len(pi.calls)
    assert rel.install("HEAD", now=True) == 2
    assert rel.current_id() == shas[0][:10] and len(pi.calls) == calls


def test_expected_programs_from_release_and_user_settings(setup):
    rel, pi, repo, shas, lines = setup
    commit(repo, "with builder", expected='["ears", "thinker", "web", "builder"]')
    rid, folder = rel.make("HEAD", None)
    assert rel.programs(folder) == ["core", "ears", "thinker", "web", "builder"]
    rel.data.mkdir(parents=True, exist_ok=True)
    (rel.data / "settings.toml").write_text('[programs]\nexpected = ["ears", "web"]\n')
    assert rel.programs(folder) == ["core", "ears", "web"]


def test_dirty_tracked_files(setup):
    rel, pi, repo, shas, lines = setup
    (repo / "untracked.txt").write_text("new")
    assert rel.dirty() == []                                  # new files don't count
    (repo / "change.txt").write_text("edited")
    assert rel.dirty() == ["change.txt"]
    r = subprocess.run(["python3", "-m", "pip_assistant.release", "dirty"], capture_output=True, text=True,
                       env={**os.environ, "PIP_REPO": str(repo)}, cwd=os.path.dirname(os.path.dirname(R.__file__)))
    assert r.returncode == 1 and "change.txt" in r.stdout


def test_release_runs_with_system_python_stdlib_only(tmp_path):
    """release.py must work with plain python3 (no venv): list/status on an empty app folder."""
    root = os.path.dirname(os.path.dirname(R.__file__))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PIP_APP": str(tmp_path / "app"),
           "PYTHONPATH": root}
    r = subprocess.run(["/usr/bin/python3", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "from pip_assistant import release; sys.exit(release.main(['list']))", root],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "No versions installed" in r.stdout


def test_programs_report_their_release(tmp_path, monkeypatch):
    program.release_info.cache_clear()
    try:
        assert program.release_id() == "dev"                   # the repo itself has no RELEASE file
        fake = tmp_path / "rel" / "pip_assistant" / "common"
        fake.mkdir(parents=True)
        (tmp_path / "rel" / "RELEASE").write_text(json.dumps({"short": "abc1234567", "time": "t"}))
        monkeypatch.setattr(program, "__file__", str(fake / "program.py"))
        program.release_info.cache_clear()
        assert program.release_info() == {"release": "abc1234567", "release_time": "t"}
    finally:
        program.release_info.cache_clear()


def test_core_health_reports_release():
    from pip_assistant.core.health import Health
    h = Health(["ears"], restart=False, release="abc")
    h.heartbeat("ears", {"release": "abc", "rss_mb": 1, "uptime_s": 2})
    snap = h.snapshot()
    assert snap["core"]["release"] == "abc" and snap["ears"]["release"] == "abc"


def test_prune_never_removes_current_or_previous(setup):
    rel, pi, repo, shas, lines = setup
    made = []
    for n in range(6):
        commit(repo, f"p{n}")
        rid, _ = rel.make("HEAD", None)
        pi.t += 10                                            # each one made later than the one before
        made.append(rid)
    rel.point_current(made[0])
    rel.save_state({"previous": made[1]})
    rel.prune()
    assert sorted(f.name for f in rel.releases.iterdir()) == sorted([made[0], made[1], *made[-3:]])


def test_status_json_for_programs(setup):
    """`status --json` is the one-line, machine-readable answer other programs read; the exit codes and
    release ids pip-builder relies on come from release.py too."""
    rel, pi, repo, shas, lines = setup
    assert rel.install("HEAD", units=True) == R.EXIT_LIVE
    second = commit(repo, "second")
    pi.broken.add(R.release_id(second))
    assert rel.install(second) == R.EXIT_ROLLED_BACK
    r = subprocess.run(["/usr/bin/python3", "-m", "pip_assistant.release", "status", "--json"],
                       capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(R.__file__)),
                       env={**os.environ, "PIP_APP": str(rel.app), "PIP_REPO": str(repo)})
    assert r.returncode == 0 and len(r.stdout.strip().splitlines()) == 1
    st = json.loads(r.stdout)
    assert st["current"] == R.release_id(shas[0]) == shas[0][:10]
    assert st["last_problem"]["release"] == R.release_id(second) and st["last"]["result"] == "rolled_back"
    assert set(st) == {"current", "previous", "running_from", "last", "last_problem"}
    # how pip-builder reads the same state when an install's exit file is missing
    state = rel.load_state()
    assert R.install_outcome(state, R.release_id(second))[0] == R.EXIT_ROLLED_BACK
    assert R.install_outcome(state, R.release_id(shas[0])) == (R.EXIT_LIVE, "")
    assert R.install_outcome({}, "abc") == (None, "")
