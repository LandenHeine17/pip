"""Pip's own code, read live for the Guide page (/guide, web/static/guide.html). Read-only.

The Guide shows the real code of each step it explains, read from the version that's running
(~/pip-app/current), so its examples never go out of date:

    read("pip_assistant/core/turns.py", "TurnTaking.addressed")  →  {"code": "async def addressed(…", "start": 32, …}
    index()  →  every Python file in pip_assistant/, with the first line of what it says it does

Only the project's own files: inside pip_assistant/, tests/, tools/, deploy/ and docs/ (not docs/archive/), and only
code and text (.py .toml .html .js .md .sh). Never ~/pip-data, never a path with "..", never a link leading out. The
repo holds no secrets (the commit check enforces it), and the dashboard is only reachable on the owner's devices.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the project folder (a release in ~/pip-app, or ~/pip)
FOLDERS = ("pip_assistant", "tests", "tools", "deploy", "docs")
SUFFIXES = (".py", ".toml", ".html", ".js", ".md", ".sh", ".service", ".css")
MAX_LINES = 400


class NotAllowed(ValueError):
    pass


def resolve(path: str, root: Path = ROOT) -> Path:
    """The file, if the Guide may show it. Raises NotAllowed otherwise."""
    rel = Path(str(path or "").strip().lstrip("/"))
    if not rel.parts or ".." in rel.parts or rel.parts[0] not in FOLDERS or rel.parts[:2] == ("docs", "archive"):
        raise NotAllowed("not one of Pip's own files")
    f = (root / rel).resolve()
    if root.resolve() not in f.parents or f.suffix not in SUFFIXES or not f.is_file():
        raise NotAllowed("not one of Pip's own files")
    return f


def _python(text: str, symbol: str) -> tuple[int, int, str] | None:
    """Where a function or class is ("Class.method" for one inside a class): (first line, last line, docstring)."""
    def named(n, part: str) -> bool:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return n.name == part
        if isinstance(n, ast.Assign):
            return any(isinstance(t, ast.Name) and t.id == part for t in n.targets)
        return isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == part
    nodes = ast.parse(text).body
    found = None
    for part in symbol.split("."):
        found = next((n for n in nodes if named(n, part)), None)
        if found is None:
            return None
        nodes = getattr(found, "body", [])
    start = min([d.lineno for d in getattr(found, "decorator_list", [])] + [found.lineno])
    doc = ast.get_docstring(found) if isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
    return start, found.end_lineno, (doc or "").strip().split("\n")[0]


def _script(lines: list[str], symbol: str) -> tuple[int, int, str] | None:
    """A function or constant in a page's script or a .js file: from where it starts to where its braces close."""
    name = re.escape(symbol)
    pat = re.compile(rf"(?:function\s+{name}\s*\(|(?:const|let|var)\s+{name}\b|^\s*{name}\s*\(.*\)\s*\{{|^\s*(?:async\s+)?{name}\s*:)")
    for i, line in enumerate(lines):
        if pat.search(line):
            depth, seen = 0, False
            for j in range(i, min(len(lines), i + 200)):
                depth += lines[j].count("{") + lines[j].count("[") - lines[j].count("}") - lines[j].count("]")
                seen = seen or "{" in lines[j] or "[" in lines[j]
                if seen and depth <= 0:
                    return i + 1, j + 1, ""
            return i + 1, min(len(lines), i + 40), ""
    return None


def _toml(lines: list[str], symbol: str) -> tuple[int, int, str] | None:
    """A [table] in a .toml file, up to the next table."""
    for i, line in enumerate(lines):
        if line.split("#")[0].strip() == f"[{symbol}]":   # (a comment may follow the table's name)
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("[")), len(lines))
            return i + 1, end, ""
    return None


def read(path: str, symbol: str = "", root: Path = ROOT) -> dict:
    """A file (its first MAX_LINES lines), or one function, class or table in it."""
    f = resolve(path, root)
    text = f.read_text(errors="replace")
    lines = text.split("\n")
    where = None
    if symbol:
        try:
            where = _python(text, symbol) if f.suffix == ".py" else _toml(lines, symbol) if f.suffix == ".toml" \
                else _script(lines, symbol)
        except SyntaxError:
            where = None
        if where is None:
            raise NotAllowed(f"there's no {symbol} in {path}")
    start, end, doc = where or (1, min(len(lines), MAX_LINES), "")
    return {"path": str(f.relative_to(root.resolve())), "symbol": symbol, "start": start, "end": end, "total": len(lines),
            "doc": doc, "code": "\n".join(lines[start - 1:end])}


def index(root: Path = ROOT) -> list[dict]:
    """Every Python file in pip_assistant/ (tests left out), with the first line of its docstring: what it's for."""
    out = []
    for f in sorted((root / "pip_assistant").rglob("*.py")):
        if "__pycache__" in f.parts or f.name.startswith("test_"):
            continue
        text = f.read_text(errors="replace")
        try:
            doc = ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            doc = ""
        out.append({"path": str(f.relative_to(root)), "about": doc.strip().split("\n")[0][:200], "lines": text.count("\n")})
    return out
