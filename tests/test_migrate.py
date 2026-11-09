"""Bringing memories and the personality over from Ziggletron (step 8, plan §3). Fake data only."""
import asyncio
import hashlib
import json
import time

import pytest

from pip_assistant.common import personality
from pip_assistant.core.memory import Memory
from pip_assistant.core.migrate import Migration, convert_personality
from pip_assistant.core.people import People

MEMORIES = [
    {"id": "z1", "fact": "Robin likes jazz on Sundays", "saved": "2026-10-20", "kind": "person", "about": ["robin", "jazz"]},
    {"id": "z2", "fact": "The bins go out Tuesday night", "saved": "2026-10-21", "kind": "household", "about": []},
    {"id": "z3", "fact": "Never play music before ten", "saved": "2026-10-22", "kind": "rule", "about": []},
    {"id": "z4", "fact": "Robin and Kestrel are building a robot", "saved": "2026-10-23", "kind": "note",
     "about": ["robin", "kestrel", "robot"]},
    {"id": "z5", "fact": "The wifi password is hunter2", "saved": "2026-10-24", "kind": "household", "about": []},
    {"id": "z6", "fact": "The plant by the window is called Fern", "saved": "bad", "kind": "odd", "about": []},
    {"id": "z7", "fact": "The bins go out Tuesday night.", "saved": "2026-10-25", "kind": "household", "about": []},
]

BEHAVIOR = """<!--
  Header for the editor. {name} is replaced.
-->

# Who you are

You're Ziggletron. You live here and you're friendly. {mood}

# Pictures

You can draw pictures and show images on the TV.

# How you talk

- Keep it short.
- When someone asks, send them a text message.
- Be kind.

You can change your own code when asked to.
"""


class FakeEmbedder:
    def available(self):
        return False


@pytest.fixture
def zig(tmp_path):
    src = tmp_path / "ziggletron"
    (src / "data").mkdir(parents=True)
    (src / "data" / "household.json").write_text(json.dumps({"memories": MEMORIES, "alerts": [], "events": []}))
    (src / "behavior.md").write_text(BEHAVIOR)
    return src


@pytest.fixture
def setup(tmp_path, monkeypatch, zig):
    monkeypatch.setenv("PIP_DATA", str(tmp_path / "pip-data"))
    personality._cache["key"] = None
    db = tmp_path / "pip.db"
    memory, people = Memory(db), People(db, embedder=FakeEmbedder())
    robin = people.add_person("Robin")
    return Migration(memory, people, source=zig), memory, robin


def sums(src):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in (src / "data" / "household.json", src / "behavior.md")]


def run(mig, action):
    return asyncio.run(mig.handle({"action": action}))


def test_dry_run_counts_and_changes_nothing(setup, zig):
    mig, memory, _ = setup
    before = sums(zig)
    got = run(mig, "get")
    assert got == {"available": True, "memories": {"new": 5, "duplicate": 1, "refused": 1, "total": 7},
                   "personality": True, "done_at": None}
    assert memory.items() == [] and personality.read()[1] is True
    assert sums(zig) == before


def test_import(setup, zig):
    mig, memory, robin = setup
    before = sums(zig)
    t0 = time.time()
    got = run(mig, "import")
    assert got == {"memories": {"imported": 5, "duplicate": 1, "refused": 1}, "personality": "imported"}
    assert sums(zig) == before
    rows = {r["text"]: r for r in memory.items()}
    assert len(rows) == 5 and all(r["status"] == "pending" for r in rows.values())
    assert all(abs(r["created"] - t0) < 60 for r in rows.values())        # dated now: not pruned after 14 days
    memory.prune()
    assert len(memory.items()) == 5
    assert rows["Robin likes jazz on Sundays"]["category"] == "personal"
    assert rows["Robin likes jazz on Sundays"]["about"] == [robin["id"]]
    assert rows["Robin likes jazz on Sundays"]["source"] == "Imported from Ziggletron (saved 2026-10-20)"
    assert rows["The bins go out Tuesday night"]["category"] == "household"
    assert rows["Never play music before ten"]["category"] == "rule"
    robot = rows["Robin and Kestrel are building a robot"]
    assert robot["category"] == "household" and robot["about"] == [robin["id"]]
    assert robot["why_pending"] == "from Ziggletron, about Kestrel"      # not "Robot": not a name in the text
    fern = rows["The plant by the window is called Fern"]
    assert fern["category"] == "household" and fern["source"] == "Imported from Ziggletron"
    assert not any("hunter2" in t for t in rows)


def test_personality_conversion_and_undo(setup):
    mig, _, _ = setup
    run(mig, "import")
    text, builtin = personality.read()
    assert not builtin
    assert text.startswith("# Who you are\n\nYou're {name}. You live here and you're friendly. mood")
    for gone in ("<!--", "Header", "Ziggletron", "draw pictures", "Pictures", "text message", "own code", "{mood}"):
        assert gone not in text
    assert "- Keep it short.\n- Be kind." in text
    assert "The room:" in text                       # Pip's transcript conventions are explained
    h = personality.history()
    assert h[0]["label"] == "From Ziggletron" and h[0]["active"]
    builtin_entry = next(e for e in h if e["label"] == "Built-in")
    personality.use(builtin_entry["id"])             # one-click undo
    assert personality.read() == (personality.BUILTIN, True)


def test_convert_keeps_only_name_and_location():
    out = convert_personality("Hi Ziggletron {name} {location} {weather} { } {{x}}")
    assert "{name} {name} {location}" in out
    assert personality.validate(out)


def test_rerun_is_harmless(setup, zig):
    mig, memory, _ = setup
    run(mig, "import")
    first = {r["id"] for r in memory.items()}
    for r in memory.items():
        memory.delete(r["id"])                       # even rejected ones don't come back
    personality.use("builtin")
    got = run(mig, "import")
    assert got == {"memories": {"imported": 0, "duplicate": 6, "refused": 1}, "personality": "skipped"}
    assert memory.items() == [] and first
    assert personality.read()[1] is True
    dry = run(mig, "get")
    assert dry["memories"]["new"] == 0 and dry["personality"] is False and dry["done_at"] > 0


def test_existing_memory_is_a_duplicate(setup):
    mig, memory, _ = setup
    memory.add("The bins go out Tuesday night", "household")
    got = run(mig, "get")
    assert got["memories"] == {"new": 4, "duplicate": 2, "refused": 1, "total": 7}


def test_missing_ziggletron(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path / "pip-data"))
    db = tmp_path / "pip.db"
    mig = Migration(Memory(db), People(db, embedder=FakeEmbedder()), source=tmp_path / "nowhere")
    assert run(mig, "get") == {"available": False, "memories": {"new": 0, "duplicate": 0, "refused": 0, "total": 0},
                               "personality": False, "done_at": None}
    assert "error" in run(mig, "import")


def test_files_changing_during_import_is_an_error(setup, zig, monkeypatch):
    mig, memory, _ = setup
    real = mig._read

    def read_then_change():
        out = real()
        (zig / "behavior.md").write_text(BEHAVIOR + "\nchanged")
        return out
    monkeypatch.setattr(mig, "_read", read_then_change)
    got = run(mig, "import")
    assert "error" in got and memory.items() == []


def test_never_writes_under_ziggletron(setup, zig):
    mig, _, _ = setup
    files = sorted(p.name for p in zig.rglob("*"))
    run(mig, "import")
    run(mig, "get")
    assert sorted(p.name for p in zig.rglob("*")) == files
