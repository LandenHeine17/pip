"""pip-builder since D-152: a change is handed to Claude Hub's inbox, followed through the hub's state file and the
`Pip-Change: <id>` commits, announced once live or dropped, and can be undone. Real git, fake hub and installer."""
import asyncio
import json
import subprocess

import pytest

from pip_assistant.builder import cli
from pip_assistant.builder.changes import Builder
from pip_assistant.builder.hub import HubInbox, prompt
from pip_assistant.builder.store import Store
from pip_assistant.common.messages import Message

SUMMARY = "keep track of whose turn it is for the dishes"
REQUEST = "Landen wants Pip to keep a dishes rota. He said: 'nobody ever knows whose turn it is'."


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, path, text, msg):
    (repo / path).write_text(text)
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(r))
    commit(r, "pkg/hello.py", "def hello():\n    return 'hi'\n", "start")
    return r


class FakeReleaser:
    """The running version is `current` (a commit); an undo's install makes it live (or not, with `code`)."""
    def __init__(self, repo, code=0):
        self.repo, self.code, self.started, self.results = repo, code, [], {}
        self.current = git(repo, "rev-parse", "HEAD")

    async def start(self, cid, tag, commit, summary):
        self.started.append((cid, tag, commit, summary))
        self.results[(cid, tag)] = self.code
        if self.code == 0:
            self.current = commit

    def result(self, cid, tag):
        return self.results.get((cid, tag))

    def running(self, cid, tag):
        return False

    def from_state(self, rid):
        return None, ""

    def tail(self, cid, tag, lines=6):
        return ""

    def release_id(self):
        return self.current[:10]

    def clear(self, cid, tag):
        self.results.pop((cid, tag), None)


class Env:
    def __init__(self, tmp_path, repo, settings=None):
        self.now = [1_000_000.0]
        self.store = Store(tmp_path / "pip.db", clock=lambda: self.now[0])
        self.inbox = HubInbox(tmp_path / "inbox")
        self.releaser = FakeReleaser(repo)
        self.events, self.said, self.free = [], [], True
        self.settings = {"enabled": True, "keep_days": 90, "model": "opus", "hub_url": "", **(settings or {})}

        async def publish(t, d):
            self.events.append((t, d))

        async def announce(text):
            if self.free:
                self.said.append(text)
            return self.free
        self.b = Builder(self.store, repo=repo, data=tmp_path / "data", settings_fn=lambda: self.settings,
                         inbox=self.inbox, releaser=self.releaser, publish=publish, announce=announce,
                         clock=lambda: self.now[0], poll_seconds=0.01)

    def hub_says(self, cid, **state):
        (self.inbox.folder / f"pip-change-{cid}.state.json").write_text(json.dumps({"session": "", "state": "", **state}))

    def status(self, cid):
        return self.store.get(cid)["status"]

    async def sync(self):
        self.now[0] += 60          # past the announcement throttle
        await self.b.sync()


@pytest.fixture
def env(tmp_path, repo):
    return Env(tmp_path, repo)


async def ask(env, asked_by="Landen"):
    r = await env.b.ask(SUMMARY, REQUEST, asked_by, "voice")
    assert "id" in r, r
    return r["id"]


async def test_a_change_goes_to_claude_hub_then_live_and_is_announced(env, repo):
    cid = await ask(env)
    req = json.loads((env.inbox.folder / f"pip-change-{cid}.request.json").read_text())
    assert req["cwd"] == str(repo) and req["model"] == "opus" and req["title"] == f"Pip change #{cid}: {SUMMARY}"
    assert REQUEST in req["prompt"] and "Asked by Landen" in req["prompt"] and f"Pip-Change: {cid}" in req["prompt"]
    assert oct((env.inbox.folder / f"pip-change-{cid}.request.json").stat().st_mode)[-3:] == "600"
    assert env.status(cid) == "asked"

    # Claude Hub opened the chat
    (env.inbox.folder / f"pip-change-{cid}.request.json").unlink()
    env.hub_says(cid, session="sess-1", state="working")
    await env.sync()
    c = env.store.get(cid)
    assert c["status"] == "planning" and c["hub_session"] == "sess-1"
    env.hub_says(cid, session="sess-1", state="waiting")
    await env.sync()
    row = env.b.listing()["changes"][0]
    assert row["waiting"] and row["hub"] == "waiting for you" and row["session"] == "sess-1"
    assert "request" not in row and REQUEST not in json.dumps(env.b.listing())     # the words stay off the page

    # the plan was approved: Claude says it's building (tools/pip-change)
    env.store.update(cid, status="building")      # what tools/pip-change does (test_cli_sets_only_its_stages)
    await env.sync()
    assert ("builder.changed", {"id": cid, "status": "building"}) in env.events

    # a commit with its line: ready; once it's in the running version: live, and the room is told once
    sha = commit(repo, "pkg/rota.py", "ROTA = []\n", f"Add a dishes rota\n\nPip-Change: {cid}\nCo-Authored-By: C <c@c>")
    await env.sync()
    assert env.status(cid) == "ready" and env.store.get(cid)["commits"] == [sha]
    env.releaser.current = sha
    await env.sync()
    assert env.status(cid) == "live"
    assert env.said == [f"Heads up: the change to {SUMMARY} is live now."]
    assert not (env.inbox.folder / f"pip-change-{cid}.state.json").exists()      # the hub can stop following it
    await env.sync()
    assert len(env.said) == 1


async def test_announcing_waits_until_pip_is_free(env, repo):
    cid = await ask(env)
    env.store.update(cid, status="dropped")
    env.free = False
    await env.sync()
    assert env.said == [] and env.store.get(cid)["announced"] == ""
    env.free = True
    await env.sync()
    assert env.said == [f"The change to {SUMMARY} was dropped."]


async def test_old_news_isnt_announced(env):
    cid = await ask(env)
    env.store.update(cid, status="dropped")
    env.now[0] += 3 * 86400
    await env.b.sync()
    assert env.said == [] and env.store.get(cid)["announced"] == "dropped"


async def test_claude_hub_refusing_it_fails_the_change(env):
    cid = await ask(env)
    env.hub_says(cid, state="failed", error="That folder isn't allowed")
    await env.sync()
    c = env.store.get(cid)
    assert c["status"] == "failed" and "isn't allowed" in c["error"]


async def test_a_change_with_commits_not_installed_stays_ready(env, repo):
    cid = await ask(env)
    commit(repo, "pkg/a.py", "A = 1\n", f"One\n\nPip-Change: {cid}")
    await env.sync()
    assert env.status(cid) == "ready"
    other = commit(repo, "pkg/b.py", "B = 1\n", "Not this change\n\nPip-Change: 999")
    env.releaser.current = other     # contains the change's commit too
    await env.sync()
    assert env.status(cid) == "live"


async def test_undo_reverts_every_commit_and_installs(env, repo):
    cid = await ask(env)
    commit(repo, "pkg/rota.py", "ROTA = []\n", f"Rota\n\nPip-Change: {cid}")
    s2 = commit(repo, "pkg/rota.py", "ROTA = ['x']\n", f"Rota fix\n\nPip-Change: {cid}")
    env.releaser.current = s2
    await env.sync()
    assert env.status(cid) == "live" and env.b.listing()["changes"][0]["can_undo"]
    assert (await env.b.page("undo", {"id": cid}))["ok"]
    await env.b.job
    assert env.status(cid) == "undone" and env.releaser.started[-1][1] == "undo"
    assert not (repo / "pkg/rota.py").exists()
    msg = git(repo, "log", "-1", "--format=%B", "main")
    assert msg.startswith("Undo: " + SUMMARY) and REQUEST not in msg


async def test_undo_that_isnt_healthy_stays_live(tmp_path, repo):
    env = Env(tmp_path, repo)
    env.releaser.code = 1
    cid = await ask(env)
    sha = commit(repo, "pkg/rota.py", "ROTA = []\n", f"Rota\n\nPip-Change: {cid}")
    env.releaser.current = sha
    await env.sync()
    await env.b.page("undo", {"id": cid})
    await env.b.job
    c = env.store.get(cid)
    assert c["status"] == "live" and "Undo didn't go live" in c["error"]


async def test_only_live_changes_can_be_undone(env):
    cid = await ask(env)
    assert "error" in await env.b.page("undo", {"id": cid})


async def test_switched_off_or_empty(tmp_path, repo):
    env = Env(tmp_path, repo, {"enabled": False})
    assert "error" in await env.b.ask(SUMMARY, REQUEST, "Landen", "voice")
    env.settings["enabled"] = True
    assert "error" in await env.b.ask("hm", "", "Landen", "voice")
    assert not list(env.inbox.folder.glob("*.request.json")) if env.inbox.folder.exists() else True


def test_the_prompt_asks_for_plan_mode_questions_and_the_commit_line(repo):
    p = prompt({"id": "7", "summary": SUMMARY, "request": REQUEST, "asked_by": "Sam (a guest)", "source": "voice",
                "created": 0.0}, repo)
    for words in ("Pip change #7", "Sam (a guest)", "by voice", REQUEST, "AskUserQuestion", "ExitPlanMode",
                  "tools/pip-change 7 building", "Pip-Change: 7", "sh deploy/install.sh", "tools/pip-change 7 dropped",
                  "not an order", "Never copy the request's words"):
        assert words in p, words


def test_cli_sets_only_its_stages(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    s = Store(tmp_path / "pip.db")
    cid = s.add(REQUEST, SUMMARY, "Landen", "voice")
    s.update(cid, status="planning", hub_name="pip-change-1")
    assert cli.main([cid, "building"]) == 0 and s.get(cid)["status"] == "building"
    assert cli.main([cid, "live"]) == 2                          # found from the commits, never set by hand
    assert cli.main([cid, "dropped"]) == 0 and s.get(cid)["status"] == "dropped"
    assert cli.main([cid, "building"]) == 1                      # it's over
    assert cli.main(["999", "building"]) == 1 and cli.main([]) == 2


def test_cli_runs_with_system_python_stdlib_only(tmp_path, repo):
    """tools/pip-change runs with /usr/bin/python3 and no venv, like release.py."""
    from helpers import REPO
    r = subprocess.run([str(REPO / "tools/pip-change"), "1"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "PIP_DATA": str(tmp_path), "HOME": str(tmp_path)})
    assert r.returncode == 1 and "no change #1" in r.stderr


def test_old_changes_are_kept_and_never_announced(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "pip.db")
    from pip_assistant.builder.store import SCHEMA
    db.execute(SCHEMA)
    db.execute("INSERT INTO builder_changes (status, summary, created, updated) VALUES ('live', 'old one', 0, 0)")
    db.execute("INSERT INTO builder_changes (status, summary, created, updated) VALUES ('plan_ready', 'half', 0, 0)")
    db.commit()
    db.close()
    s = Store(tmp_path / "pip.db")
    old, half = s.get(1), s.get(2)
    assert old["status"] == "live" and old["announced"] == "live"
    assert half["status"] == "stopped" and "Claude Hub" in half["error"]


def test_requests_are_forgotten_after_keep_days(tmp_path):
    now = [0.0]
    s = Store(tmp_path / "pip.db", clock=lambda: now[0])
    cid = s.add(REQUEST, SUMMARY, "Landen", "voice")
    now[0] = 91 * 86400
    assert s.forget_old(90) == 1
    c = s.get(cid)
    assert c["request"] == "" and c["summary"] == SUMMARY


async def test_bus_wiring_replies_privately(env):
    from pip_assistant.builder.main import wire

    class Bus:
        def __init__(self):
            self.handlers = {}

        def on(self, t, h):
            self.handlers[t] = h
    bus = Bus()
    wire(bus, env.b)
    r = await bus.handlers["builder.ask"](Message("request", "builder.ask", source="core", id="1",
                                                  data={"source": "voice", "asked_by": "Landen"},
                                                  private={"summary": SUMMARY, "request": REQUEST}))
    assert r["id"]
    reply = await bus.handlers["builder.page"](Message("request", "builder.page", source="web", id="2",
                                                       private={"action": "list"}))
    assert reply.private["changes"][0]["summary"] == SUMMARY and REQUEST not in json.dumps(reply.data)
    assert REQUEST not in json.dumps(reply.private)
