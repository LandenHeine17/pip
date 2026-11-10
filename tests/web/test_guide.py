"""The Guide (/guide, web/static/guide.html) and its live code (web/source.py): every code link in the guide points at
something that exists (so a rename fails here, not as a dead link), and only Pip's own files can be read."""
import re

import pytest
from helpers import REPO
from starlette.testclient import TestClient

from pip_assistant.web import source
from pip_assistant.web.main import State, build_app

PAGE = (REPO / "pip_assistant/web/static/guide.html").read_text()
CONTENT = PAGE[PAGE.index("const CHAPTERS = ["):PAGE.index("// ================================================================ the page itself")]
FOLDERS = ("pip_assistant/", "tests/", "tools/", "deploy/", "docs/")


def guide_refs() -> set[str]:
    """Every code reference in the guide: [[path#symbol|label]] in the text, ref: "…" in tours, "ref:…" in diagrams,
    and data-ref="…" written by hand."""
    found = set()
    for pat in (r"\[\[([^|\]]+)\|", r'ref: "([^"]+)"', r'"ref:([^"]+)"', r'data-ref="([^"$]+)"'):
        found |= set(re.findall(pat, CONTENT))
    return found


def norm(path: str) -> str:
    return path if path.startswith(FOLDERS) else "pip_assistant/" + path


def test_the_guide_has_many_code_links():
    assert len(guide_refs()) > 120


@pytest.mark.parametrize("ref", sorted(guide_refs()))
def test_every_code_link_in_the_guide_exists(ref):
    path, _, symbol = ref.partition("#")
    got = source.read(norm(path), symbol, root=REPO)
    assert got["code"].strip() and got["start"] >= 1


def test_every_chapter_link_goes_to_a_chapter():
    ids = set(re.findall(r'\{id: "([a-z]+)", title:', CONTENT))
    assert len(ids) == 15
    for target in re.findall(r"\{\{([a-z]+)\|", CONTENT) + re.findall(r'"go:([a-z]+)"', CONTENT):
        assert target in ids, target


@pytest.mark.parametrize("path", ["../pip-data/pip.db", "pip_assistant/../../pip-data/settings.toml", "/etc/passwd",
                                  "docs/archive/README.md", "conftest.py", "pyproject.toml", "pip_assistant/web/static/missing.py",
                                  "tests/../uv.lock"])
def test_only_pips_own_files_can_be_read(path):
    with pytest.raises(source.NotAllowed):
        source.read(path, root=REPO)


def test_a_function_inside_a_class_is_found_by_name():
    got = source.read("pip_assistant/core/turns.py", "TurnTaking.addressed", root=REPO)
    assert got["code"].lstrip().startswith("async def addressed") and got["doc"].startswith("Someone said something to Pip")
    with pytest.raises(source.NotAllowed):
        source.read("pip_assistant/core/turns.py", "TurnTaking.nothing_like_this", root=REPO)


def test_the_file_index_lists_every_python_file():
    listed = {f["path"] for f in source.index(root=REPO)}
    want = {str(p.relative_to(REPO)) for p in (REPO / "pip_assistant").rglob("*.py")
            if "__pycache__" not in p.parts and not p.name.startswith("test_")}
    assert listed == want
    assert all(f["about"] for f in source.index(root=REPO) if not f["path"].endswith("__init__.py"))


def test_the_page_and_the_route():
    client = TestClient(build_app(None, State(), {}))
    assert "How Pip works" in client.get("/guide").text
    r = client.get("/api/source", params={"path": "pip_assistant/core/states.py", "symbol": "situation"})
    assert r.status_code == 200 and "def situation" in r.json()["code"]
    assert client.get("/api/source", params={"path": "../pip-data/pip.db"}).status_code == 404
    assert client.get("/api/source", params={"path": "pip_assistant/core/states.py\x00"}).status_code == 404
    assert len(client.get("/api/source", params={"index": "1"}).json()["files"]) > 50
