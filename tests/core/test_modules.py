"""The module system (ARCHITECTURE §7, D-96): finding modules, their settings, and their limits."""
import sqlite3
import tomllib

import pytest

from pip_assistant.common.modules import ModuleError, discover, read
from pip_assistant.common.secrets import ScopedSecrets
from pip_assistant.core import actions
from pip_assistant.core.modules import ModuleContext, Registry


def make(root, name, toml):
    d = root / name
    d.mkdir(parents=True)
    (d / "module.toml").write_text(toml)
    return d


def test_the_real_modules_are_found_in_dependency_order():
    names = [m.name for m in discover({})]
    assert names.index("google") < names.index("calendar") and names.index("google") < names.index("todos")
    assert "_template" not in names


def test_settings_are_checked_and_modules_can_be_switched_off(tmp_path):
    make(tmp_path, "dice", 'name = "dice"\nkind = "skill"\nsummary = "Dice."\n[settings.sides]\ndefault = 6\nexplain = "Sides."\n')
    make(tmp_path, "board", 'name = "board"\nkind = "skill"\nsummary = "Games."\nneeds = ["dice"]\n')
    [board, dice] = discover({"modules": {"dice": {"sides": 20}}}, root=tmp_path)[::-1]
    assert dice.settings == {"sides": 20}
    with pytest.raises(ModuleError, match="should be int"):
        discover({"modules": {"dice": {"sides": "twenty"}}}, root=tmp_path)
    with pytest.raises(ModuleError, match="Unknown setting"):
        discover({"modules": {"dice": {"colour": 1}}}, root=tmp_path)
    with pytest.raises(ModuleError, match="no such module"):
        discover({"modules": {"chess": {}}}, root=tmp_path)
    mods = {m.name: m for m in discover({"modules": {"dice": {"enabled": False}}}, root=tmp_path)}
    assert not mods["dice"].enabled and not mods["board"].enabled      # what it needs is off, so it's off too
    make(tmp_path, "bad", 'name = "wrong"\nkind = "skill"\nsummary = "x"\n')
    with pytest.raises(ModuleError, match="must be 'bad'"):
        read(tmp_path / "bad")


def test_a_module_only_sees_its_own_secrets_and_the_rest_of_the_file_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('# Pip secrets\ngroq_api_key = "gsk-1"\n\n[other]\ntoken = "keep me"\n')
    s = ScopedSecrets("google")
    s.save({"client_id": "id", "accounts": {"a1": {"refresh_token": "r1"}}})
    s.set("client_secret", "sec")
    text = (tmp_path / "secrets.toml").read_text()
    data = tomllib.loads(text)
    assert text.startswith("# Pip secrets") and data["groq_api_key"] == "gsk-1" and data["other"]["token"] == "keep me"
    assert data["google"] == {"client_id": "id", "client_secret": "sec", "accounts": {"a1": {"refresh_token": "r1"}}}
    assert ScopedSecrets("other").get("token") == "keep me" and "client_id" not in ScopedSecrets("other").all()
    assert oct((tmp_path / "secrets.toml").stat().st_mode)[-3:] == "600"


def test_a_module_can_only_write_its_own_tables(tmp_path):
    info = read(make(tmp_path, "dice", 'name = "dice"\nkind = "skill"\nsummary = "Dice."\n'))
    db = ModuleContext(info, db_path=tmp_path / "pip.db").db()
    db.execute("CREATE TABLE dice_rolls (n INTEGER)")
    db.execute("INSERT INTO dice_rolls VALUES (4)")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("CREATE TABLE people (id TEXT)")
    db.commit()
    db.execute("ALTER TABLE dice_rolls ADD COLUMN sides INTEGER")      # a later version adds a column: fine
    sqlite3.connect(tmp_path / "pip.db").execute("CREATE TABLE people (id TEXT)")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("ALTER TABLE people ADD COLUMN x TEXT")             # someone else's table: no
    # Review fixes (2026-11-16): nor read them, or reach them sideways
    for sql in ("SELECT * FROM people", "CREATE TRIGGER dice_t AFTER INSERT ON people BEGIN DELETE FROM people; END",
                "CREATE VIEW dice_v AS SELECT * FROM people", f"ATTACH '{tmp_path / 'x.db'}' AS x",
                "PRAGMA writable_schema = 1"):
        with pytest.raises(sqlite3.DatabaseError):
            db.execute(sql)
    db.execute("CREATE INDEX dice_by_n ON dice_rolls (n)")
    assert [r[0] for r in db.execute("SELECT n FROM dice_rolls")] == [4] and db.execute("PRAGMA table_info(dice_rolls)").fetchall()


def test_claudes_action_reaches_the_module_that_declares_it(tmp_path):
    from pip_assistant.common.modules import ROOT
    reg = Registry.load({}, infos=[read(ROOT / "_template")], db_path=tmp_path / "pip.db")

    class Cmds:
        class s:
            @staticmethod
            def find(ref):
                return None

        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 11, 7, 9, 0)
    said, n = actions.apply(Cmds, 'ACTION {"do": "roll", "sides": 6}', {"modules": reg})
    assert n == 1 and said.startswith("It's a ")
    assert actions.apply(Cmds, 'ACTION {"do": "roll", "sides": 1}', {"modules": reg}) == (actions.FAILED, 0)
    assert actions.apply(Cmds, 'ACTION {"do": "fly"}', {"modules": reg}) == (actions.FAILED, 0)


def test_the_dashboard_serves_module_pages_and_passes_requests_privately():
    from starlette.testclient import TestClient

    from pip_assistant.common.messages import Message
    from pip_assistant.web.main import State, build_app

    class Bus:
        sent = []

        class connected:
            @staticmethod
            def is_set():
                return True

        async def request(self, target, kind, data=None, private=None, timeout=5):
            self.sent.append((kind, data, private))
            return Message("reply", kind, data={"ok": True}, private={"echo": private})

    bus = Bus()
    client = TestClient(build_app(bus, State(), {}))
    # every module with a dashboard page gets a button, whichever modules are installed (no fixed list, so a new
    # module's page doesn't need this test changed)
    from pip_assistant.common.modules import enabled
    with_page = [m.label or m.name.title() for m in sorted(enabled({}), key=lambda m: m.label or m.name) if m.page]
    labels = [m["label"] for m in client.get("/api/modules").json()]
    assert labels == with_page and {"Calendar", "To-dos"} <= set(labels)
    page = client.get("/m/calendar/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert client.get("/m/calendar/../module.py").status_code == 404           # nothing outside static/
    assert client.get("/m/calendar/%2e%2e/module.toml").status_code == 404
    r = client.post("/api/m/todos", json={"action": "add", "title": "milk"}).json()
    kind, data, private = bus.sent[-1]
    assert kind == "module.request" and data == {"module": "todos", "action": "add"} and private == {"title": "milk"}
    assert r == {"echo": {"title": "milk"}}
    assert client.get("/pip.css").status_code == 200


async def test_a_failed_background_job_is_started_again(monkeypatch):
    """Review fixes (2026-11-16): one error in a sync loop used to stop it until pip-core restarted."""
    import asyncio

    from pip_assistant.core.modules import Registry

    runs = []

    class Flaky:
        def tasks(self):
            return [self.loop(), self.other()]

        async def loop(self):
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("database is locked")
            await asyncio.sleep(10)

        async def other(self):
            await asyncio.sleep(10)

    monkeypatch.setattr(Registry, "RESTART_AFTER", (0.01, 0.02))
    m = Flaky()
    first, second = m.tasks()
    second.close()
    task = asyncio.create_task(Registry._guard("flaky", first, m, 0))
    await asyncio.sleep(0.1)
    assert len(runs) == 2 and not task.done()
    task.cancel()
