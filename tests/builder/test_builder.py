"""pip-builder's pipeline (M1b, PIP-NEW-069–076, AC-SM-01–07) with a fake Claude, a fake test run and a fake
release install, on a throwaway git repo. The real sandboxes are tested in test_sandbox.py."""
import asyncio
import json
import subprocess

import pytest

from pip_assistant.builder import checks
from pip_assistant.builder.agent import RunResult, StreamParser, parse_plan
from pip_assistant.builder.deploy import commit_message
from pip_assistant.builder.pipeline import Builder
from pip_assistant.builder.releasejob import ReleaseRunner
from pip_assistant.builder.store import Store
from pip_assistant.common.messages import Message

REQUEST = "make pip say good morning to the whole flat every day at nine"   # private: never in git or events
PLAN = {"summary": "Add a daily good-morning greeting", "why": "They want a greeting.", "files": ["pkg/hello.py"],
        "risk": "behaviour", "tests": "tests/test_hello.py", "rollback": "Undo the change.", "questions": []}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg/hello.py").write_text("def hello():\n    return 'hi'\n")
    (r / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (r / "README.md").write_text("A repo.\n")
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    git(r, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
    git(r, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "start")
    return r


class FakeAgent:
    """Plans with PLAN (or a queue of plans), builds by calling `edit(src)`."""

    def __init__(self):
        self.plans = []
        self.plan_result = None
        self.edit = lambda src: (src / "pkg/hello.py").write_text("def hello():\n    return 'good morning'\n")
        self.build_result = None
        self.calls = []
        self.block = None            # an asyncio.Event a build waits on (to test Stop)
        self.stopped = []

    async def plan(self, change, work, cfg, on_step):
        self.calls.append(("plan", dict(change)))
        assert (work / "src/pkg/hello.py").exists() and not (work / "src/.git").exists()
        await on_step("tool", "Read pkg/hello.py")
        if self.plan_result:
            return self.plan_result
        plan = self.plans.pop(0) if self.plans else PLAN
        return RunResult(ok=True, text=json.dumps(plan), cost_usd=0.05)

    async def build(self, change, work, cfg, on_step):
        self.calls.append(("build", dict(change)))
        if self.block:
            await self.block.wait()
        if self.build_result:
            return self.build_result
        self.edit(work / "src")
        return RunResult(ok=True, text="Changed the greeting.", cost_usd=0.2)

    def stop(self, cid):
        self.stopped.append(cid)
        if self.block:
            self.block.set()


class FakeReleaser:
    def __init__(self, code=0):
        self.code, self.started, self.results = code, [], {}

    async def start(self, cid, tag, commit, summary):
        self.started.append((cid, tag, commit, summary))
        self.results[(cid, tag)] = self.code

    def result(self, cid, tag):
        return self.results.get((cid, tag))

    def running(self, cid, tag):
        return (cid, tag) in self.results and self.results[(cid, tag)] is None   # started, not finished

    def from_state(self, rid):
        return None, ""

    def tail(self, cid, tag, lines=6):
        return "Pip is running version x and healthy."

    def release_id(self):
        return "abc123"

    def clear(self, cid, tag):
        self.results.pop((cid, tag), None)


async def fake_venv(src):
    (src / ".venv").mkdir()
    return True, ""


class Env:
    def __init__(self, tmp_path, repo, settings=None, check_code=0):
        self.agent, self.releaser = FakeAgent(), FakeReleaser()
        self.events, self.usage = [], []
        self.now = [1_000_000.0]
        self.settings = {"enabled": True, "model": "sonnet", "effort": "medium", "plan_minutes": 10,
                         "build_minutes": 30, "keep_days": 90, **(settings or {})}
        self.check_code = check_code
        self.checked = []
        self.store = Store(tmp_path / "pip.db", clock=lambda: self.now[0])
        self.data = tmp_path / "builder"

        async def publish(t, d):
            self.events.append((t, d))

        async def checker(cid, src):
            self.checked.append(cid)
            return self.check_code, "12 passed\nall modules import" if self.check_code == 0 else "1 failed"
        self.b = Builder(self.store, self.agent, repo=repo, data=self.data, settings_fn=lambda: self.settings,
                         publish=publish, checker=checker, venv_maker=fake_venv, releaser=self.releaser,
                         usage_fn=lambda c, e: self.usage.append((c, e)), clock=lambda: self.now[0],
                         poll_seconds=0.01)

    async def settle(self):
        for _ in range(500):
            await asyncio.sleep(0)
            if self.b.job and not self.b.job.done():
                await asyncio.wait([self.b.job])
                continue
            await asyncio.sleep(0.005)
            if not (self.b.job and not self.b.job.done()):
                return
        raise AssertionError("the builder never settled")

    def status(self, cid):
        return self.store.get(cid)["status"]

    def work(self, cid):
        return self.data / "work" / str(cid)


@pytest.fixture
def env(tmp_path, repo):
    return Env(tmp_path, repo)


async def ask(env, text=REQUEST):
    r = await env.b.ask(text, "landen", "voice")
    await env.settle()
    return r["id"]


# ---------------------------------------------------------------- the happy path, with both approvals
async def test_a_change_needs_both_approvals_and_goes_live(env, repo):
    await env.b.start()
    cid = await ask(env)
    assert env.status(cid) == "plan_ready"                      # planned, then waits: nothing is built yet
    assert [c[0] for c in env.agent.calls] == ["plan"]
    assert env.agent.calls[0][1]["request"] == REQUEST          # Claude sees the request…
    c = env.store.get(cid)
    assert c["summary"] == PLAN["summary"] and c["plan"]["files"] == ["pkg/hello.py"]
    assert env.work(cid).joinpath("src").is_dir()               # the workspace waits with it

    assert (await env.b.page("approve_plan", {"id": cid}))["ok"]
    await env.settle()
    assert env.status(cid) == "ready"                           # built and checked, then waits again
    assert env.releaser.started == []
    c = env.store.get(cid)
    assert c["files"] == ["pkg/hello.py"] and "+    return 'good morning'" in c["diff"]
    assert "12 passed" in c["test_report"] and env.checked == [cid]

    assert (await env.b.page("approve", {"id": cid, "read_diff": False}))["ok"]
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "live" and c["release"] == "abc123"
    assert env.releaser.started == [(cid, "install", c["commit"], PLAN["summary"])]
    # committed by Pip, message from the summary, never the request (AC-SM-07); main fast-forwarded
    msg = git(repo, "log", "-1", "--format=%B", "main")
    assert msg.startswith(PLAN["summary"]) and f"Pip self-change #{cid}" in msg
    assert REQUEST not in msg and "good morning to the whole flat" not in msg
    assert git(repo, "log", "-1", "--format=%an <%ae>", "main") == "Pip <pip@localhost>"
    assert git(repo, "rev-parse", "main") == c["commit"]
    assert (repo / "pkg/hello.py").read_text().endswith("'good morning'\n")
    assert not env.work(cid).exists()                           # workspace gone
    assert git(repo, "worktree", "list").count("\n") == 0       # the deploy worktree is gone too
    # status events never carry the words
    assert all(t == "builder.changed" and set(d) == {"id", "status"} for t, d in env.events)
    assert [d["status"] for t, d in env.events if d["id"] == cid][-1] == "live"
    assert REQUEST not in json.dumps(env.events)
    assert env.usage == [(0.05, False), (0.2, False)]
    assert env.store.get(cid)["cost_usd"] == pytest.approx(0.25)


async def test_approvals_only_in_the_right_state(env):
    await env.b.start()
    cid = await ask(env)
    assert "error" in await env.b.page("approve", {"id": cid, "read_diff": True})     # not before it's built
    assert "error" in await env.b.page("undo", {"id": cid})
    assert "error" in await env.b.page("approve_plan", {"id": "999"})
    assert "error" in await env.b.page("frobnicate", {"id": cid})


async def test_the_page_list_and_detail(env):
    await env.b.start()
    cid = await ask(env)
    lst = await env.b.page("list", {})
    assert lst["busy"] == cid and lst["paused"] is None
    row = lst["changes"][0]
    assert row["id"] == cid and row["status"] == "plan_ready" and row["waiting"] is True
    assert "request" not in row
    d = (await env.b.page("get", {"id": cid}))["change"]
    assert d["request"] == REQUEST and d["plan"]["summary"] == PLAN["summary"]
    assert any(s["text"] == "Read pkg/hello.py" for s in d["log"])
    r = await env.b.page("ask", {"text": "another idea"}, asked_by="landen")
    assert set(r) == {"id"}


# ---------------------------------------------------------------- questions
async def test_questions_are_answered_on_the_dashboard_then_planned_again(env):
    env.agent.plans = [{**PLAN, "questions": ["Which room?"]}, PLAN]
    await env.b.start()
    cid = await ask(env)
    c = env.store.get(cid)
    assert c["status"] == "questions" and c["questions"] == ["Which room?"]
    assert "error" in await env.b.page("answer", {"id": cid, "answers": []})
    assert (await env.b.page("answer", {"id": cid, "answers": ["The kitchen"]}))["ok"]
    await env.settle()
    assert env.status(cid) == "plan_ready"
    second = env.agent.calls[-1][1]
    assert second["answers"] == ["The kitchen"] and second["questions"] == ["Which room?"]


# ---------------------------------------------------------------- refusals
async def test_forbidden_paths_are_refused(env):
    def edit(src):
        (src / "pkg/hello.py").write_text("x = 1\n")
        (src / "pyproject.toml").write_text("[project]\nname = 'x'\ndependencies = ['evil']\n")
    env.agent.edit = edit
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "failed" and "pyproject.toml" in c["error"]
    assert env.checked == []                                   # refused before any generated code runs
    assert not env.work(cid).exists()


@pytest.mark.parametrize("path", ["deploy/systemd/pip-x.service", "tools/check_secrets.py", ".gitignore",
                                  ".claude/settings.json", "uv.lock", "tools/sandbox-test", ".git/hooks/pre-commit",
                                  "setup.py"])
def test_forbidden_list(path):
    assert checks.forbidden(path)


@pytest.mark.parametrize("path", ["pip_assistant/core/main.py", "docs/PIP-SPEC.md", "tests/test_x.py",
                                  "pip_assistant/modules/todos/module.py"])
def test_allowed_paths(path):
    assert checks.forbidden(path) is None


async def test_a_secret_in_the_change_is_refused(env):
    key = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    env.agent.edit = lambda src: (src / "pkg/hello.py").write_text(f"KEY = '{key}'\n")
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "failed" and "Groq key" in c["error"] and env.checked == []


async def test_binary_files_are_refused(env):
    env.agent.edit = lambda src: (src / "pkg/blob.bin").write_bytes(b"\x00\x01\x02")
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    assert env.status(cid) == "failed" and "only text files" in env.store.get(cid)["error"]


async def test_failing_tests_fail_the_change(tmp_path, repo):
    env = Env(tmp_path, repo, check_code=1)
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "failed" and "1 failed" in c["test_report"] and not env.work(cid).exists()


async def test_security_changes_need_the_diff_read(env):
    def edit(src):
        (src / "pip_assistant/builder").mkdir(parents=True)
        (src / "pip_assistant/builder/x.py").write_text("y = 2\n")
    env.agent.edit = edit
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "ready" and c["risk"] == "security"   # the plan said "behaviour"; the files decide
    r = await env.b.page("approve", {"id": cid, "read_diff": False})
    assert "read the diff" in r["error"] and env.status(cid) == "ready"
    assert (await env.b.page("approve", {"id": cid, "read_diff": True}))["ok"]
    await env.settle()
    assert env.status(cid) == "live"


def test_risk_from_files():
    assert checks.risk_for(["pip_assistant/web/security.py"], "cosmetic") == "security"
    assert checks.risk_for(["deploy/install.sh"], "cosmetic") == "security"
    assert checks.risk_for(["pip_assistant/common/secrets.py"], "data") == "security"
    assert checks.risk_for(["docs/PIP-SPEC.md"], "cosmetic") == "cosmetic"


# ---------------------------------------------------------------- commit messages
def test_commit_message_never_contains_the_request():
    c = {"id": "4", "request": REQUEST, "files": ["pkg/hello.py"], "plan": {"summary": PLAN["summary"]}}
    assert commit_message(c) == f"{PLAN['summary']}\n\nPip self-change #4 (approved on the dashboard)"
    quoting = {**c, "plan": {"summary": "Do this: " + REQUEST}}
    msg = commit_message(quoting)
    assert REQUEST not in msg and msg.startswith("Self-change #4: pkg/hello.py")


# ---------------------------------------------------------------- stop, restart, one at a time, pause
async def test_stop_while_building(env):
    env.agent.block = asyncio.Event()
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await asyncio.sleep(0.02)
    assert env.status(cid) == "building"
    assert (await env.b.page("stop", {"id": cid}))["ok"]
    await env.settle()
    assert env.status(cid) == "stopped" and env.agent.stopped == [cid]
    assert not env.work(cid).exists() and env.checked == []


async def test_restart_fails_changes_that_were_mid_way(tmp_path, repo):
    env = Env(tmp_path, repo)
    await env.b.start()
    a = await ask(env)                                   # plan_ready: waiting, keeps its workspace
    b = env.store.add("second", "sam", "dashboard")
    env.store.update(b, status="building")               # as if Pip died while building
    (env.data / "work" / b / "src").mkdir(parents=True)
    (env.data / "work" / "77").mkdir(parents=True)       # an orphan
    env2 = Env(tmp_path, repo)
    await env2.b.start()
    await env2.settle()
    assert env2.status(b) == "failed" and "restarted" in env2.store.get(b)["error"]
    assert env2.status(a) == "plan_ready" and env2.work(a).exists()
    assert not env2.work(b).exists() and not (env2.data / "work/77").exists()


async def test_restart_during_install_picks_up_the_result(tmp_path, repo):
    env = Env(tmp_path, repo)
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    env.releaser.code = None                             # the install doesn't finish in this process
    await env.b.page("approve", {"id": cid, "read_diff": False})
    for _ in range(20):
        await asyncio.sleep(0.01)
    env.b.job.cancel()                                   # pip-builder restarted by the install
    assert env.status(cid) == "deploying"
    env2 = Env(tmp_path, repo)
    env2.releaser.results[(cid, "install")] = 1          # it rolled back
    await env2.b.start()
    await env2.settle()
    c = env2.store.get(cid)
    assert c["status"] == "rolled_back" and "went back" in c["error"]


async def test_restart_during_install_reads_the_release_state(tmp_path, repo):
    env = Env(tmp_path, repo)
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    env.releaser.code = None
    await env.b.page("approve", {"id": cid, "read_diff": False})
    for _ in range(20):
        await asyncio.sleep(0.01)
    env.b.job.cancel()
    commit = env.store.get(cid)["commit"]
    env2 = Env(tmp_path, repo)
    env2.releaser.from_state = lambda rid: (0, "") if rid == commit[:10] else (None, "")
    await env2.b.start()
    await env2.settle()
    assert env2.status(cid) == "live"


def test_release_state_file(tmp_path):
    state = tmp_path / "release-state.json"
    r = ReleaseRunner(tmp_path, tmp_path / "rel", app=tmp_path)
    assert r.from_state("abc") == (None, "")
    state.write_text(json.dumps({"current": "abc", "previous": "old", "last_problem": None}))
    assert r.from_state("abc") == (0, "")
    state.write_text(json.dumps({"current": "old", "last_problem": {"release": "abc", "reason": "pip-web died"}}))
    assert r.from_state("abc") == (1, "pip-web died")


async def test_one_change_at_a_time(env):
    await env.b.start()
    a = await ask(env)
    r = await env.b.ask("a second idea", "sam", "dashboard")
    await env.settle()
    b = r["id"]
    assert r["queued"] is True
    assert env.status(a) == "plan_ready" and env.status(b) == "asked"    # waits its turn
    await env.b.page("reject", {"id": a})
    await env.settle()
    assert env.status(a) == "rejected" and not env.work(a).exists()
    assert env.status(b) == "plan_ready"


async def test_paused_by_usage_limit_then_resumes(env):
    await env.b.start()
    env.b.pause(env.now[0] + 600)
    cid = await ask(env)
    assert env.status(cid) == "asked" and env.agent.calls == []
    lst = env.b.listing()
    assert lst["paused"]["until"] == env.now[0] + 600 and "limit" in lst["paused"]["why"]
    env.now[0] += 601
    env.b.kick()
    await env.settle()
    assert env.status(cid) == "plan_ready" and env.b.listing()["paused"] is None


async def test_hitting_the_limit_while_planning_pauses_and_requeues(env):
    told = []
    env.b.on_limit = told.append
    env.agent.plan_result = RunResult(ok=False, is_error=True, error="usage limit", limited_until=env.now[0] + 3600)
    await env.b.start()
    cid = await ask(env)
    assert env.status(cid) == "asked" and env.b.is_paused() and told == [env.now[0] + 3600]
    assert not env.work(cid).exists()
    assert env.usage == [(None, True)]


async def test_switched_off(tmp_path, repo):
    env = Env(tmp_path, repo, settings={"enabled": False})
    r = await env.b.ask("x", "landen", "voice")
    assert "switched off" in r["error"]


# ---------------------------------------------------------------- after it's live
async def test_main_not_moved_when_repo_is_dirty(env, repo):
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    (repo / "README.md").write_text("Owner's unfinished edit.\n")
    start_main = git(repo, "rev-parse", "main")
    await env.b.page("approve", {"id": cid, "read_diff": False})
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "live" and git(repo, "rev-parse", "main") == start_main
    assert git(repo, "rev-parse", c["branch"]) == c["commit"] and "Merge" in c["error"]
    # a second change can't go live until the first is merged into main
    (repo / "README.md").write_text("A repo.\n")
    cid2 = await ask(env, "another")
    await env.b.page("approve_plan", {"id": cid2})
    await env.settle()
    r = await env.b.page("approve", {"id": cid2, "read_diff": False})
    assert "isn't in main" in r["error"]


async def test_rolled_back_when_unhealthy(env, repo):
    env.releaser.code = 1
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    start_main = git(repo, "rev-parse", "main")
    await env.b.page("approve", {"id": cid, "read_diff": False})
    await env.settle()
    assert env.status(cid) == "rolled_back" and git(repo, "rev-parse", "main") == start_main


async def test_undo(env, repo):
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    await env.b.page("approve", {"id": cid, "read_diff": False})
    await env.settle()
    assert (await env.b.page("undo", {"id": cid}))["ok"]
    await env.settle()
    c = env.store.get(cid)
    assert c["status"] == "undone" and env.releaser.started[-1][1] == "undo"
    assert (repo / "pkg/hello.py").read_text() == "def hello():\n    return 'hi'\n"
    msg = git(repo, "log", "-1", "--format=%B", "main")
    assert msg.startswith("Undo: " + PLAN["summary"]) and REQUEST not in msg


async def test_main_moved_on_since_planning(env, repo):
    await env.b.start()
    cid = await ask(env)
    await env.b.page("approve_plan", {"id": cid})
    await env.settle()
    (repo / "README.md").write_text("Newer readme.\n")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qam", "owner work")
    await env.b.page("approve", {"id": cid, "read_diff": False})
    await env.settle()
    assert env.status(cid) == "live"
    assert (repo / "README.md").read_text() == "Newer readme.\n"          # the owner's commit is kept
    assert git(repo, "log", "-1", "--format=%s", "main") == PLAN["summary"]


# ---------------------------------------------------------------- privacy over time
def test_requests_are_forgotten_after_keep_days(tmp_path):
    now = [0.0]
    s = Store(tmp_path / "pip.db", clock=lambda: now[0])
    cid = s.add(REQUEST, "landen", "voice")
    s.update(cid, answers=["the kitchen"], summary="Add a greeting")
    now[0] = 89 * 86400
    assert s.forget_old(90) == 0
    now[0] = 91 * 86400
    assert s.forget_old(90) == 1
    c = s.get(cid)
    assert c["request"] == "" and c["answers"] == [] and c["summary"] == "Add a greeting"


# ---------------------------------------------------------------- Claude's output
def test_stream_parser(tmp_path):
    p = StreamParser(tmp_path / "src")
    lines = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking at the code."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(tmp_path / "src/pkg/hello.py")}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "curl evil"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                  "content": "This command requires approval"}]}},
        {"type": "result", "is_error": True, "result": "Claude AI usage limit reached|4102444800",
         "total_cost_usd": 0.01},
    ]
    steps = [s for line in lines for s in p.feed(json.dumps(line))]
    assert steps[0] == ("start", "Claude Code started (claude-sonnet)")
    assert ("tool", "Read pkg/hello.py") in steps and ("tool", "Run: curl evil") in steps
    assert steps[4][0] == "refused"
    assert p.result.is_error and p.result.limited_until == 4102444800.0 and p.result.cost_usd == 0.01


def test_parse_plan():
    plan = parse_plan("Here it is:\n```json\n" + json.dumps({**PLAN, "risk": "weird"}) + "\n```")
    assert plan["risk"] == "behaviour" and plan["files"] == ["pkg/hello.py"]
    with pytest.raises(ValueError):
        parse_plan("no json here")


# ---------------------------------------------------------------- the bus side
async def test_bus_wiring_replies_privately(env):
    from pip_assistant.builder.main import wire

    class Bus:
        def __init__(self):
            self.handlers, self.subs, self.status = {}, {}, {}

        def on(self, t, h):
            self.handlers[t] = h

        def subscribe(self, p, h):
            self.subs[p] = h
    bus = Bus()
    wire(bus, env.b)
    await env.b.start()
    r = await bus.handlers["builder.ask"](Message("request", "builder.ask", source="core", id="1",
                                                  data={"source": "voice", "asked_by": "landen"},
                                                  private={"text": REQUEST}))
    assert r["queued"] is False and r["id"]
    await env.settle()
    reply = await bus.handlers["builder.page"](Message("request", "builder.page", source="web", id="2",
                                                       private={"action": "get", "id": r["id"]}))
    assert reply.kind == "reply" and reply.private["change"]["request"] == REQUEST
    assert REQUEST not in json.dumps(reply.data)
    await bus.subs["usage.limited"](Message("event", "usage.limited", data={"until": env.now[0] + 100}))
    assert env.b.is_paused()
