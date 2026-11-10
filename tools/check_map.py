#!/usr/bin/env python3
"""Two checks on the repo, run before every commit (tools/install_hooks.sh):

1. Every top-level file or folder is mentioned in docs/PROJECT-MAP.md.
2. Modules really plug in (ARCHITECTURE §7): Pip's programs (pip_assistant/core, ears, thinker, web, builder,
   common) never name a module in their code, so a module can be added, changed or switched off without touching
   them. Pip asks every module through the hooks in core/modules.py instead. Checked: Python string values (not
   docstrings or comments), and the dashboard's and monitor's pages outside comments: a module's name in
   quotes, or its address /m/<name>. tests/core/test_plugins.py runs this check too, so the builder's sandbox
   (which has no git) catches it.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------- 1. the project map
def map_missing(root: Path) -> list[str]:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, text=True, cwd=root).stdout.split()
    top = sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in files} - {".gitignore"})
    text = (root / "docs" / "PROJECT-MAP.md").read_text()
    return [t for t in top if f"`{t}" not in text]


# ---------------------------------------------------------------- 2. no module names in Pip's programs
def python_strings(path: Path):
    """(line, text) of every string value in a Python file that isn't a docstring."""
    tree = ast.parse(path.read_text())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def page_lines(path: Path):
    """(line, text) of a page or script, without its comments."""
    text = re.sub(r"<!--.*?-->|/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), path.read_text(), flags=re.S)
    for i, line in enumerate(text.splitlines(), 1):
        yield i, re.sub(r"(^|[^:\\])//.*$", r"\1", line)   # a // comment (not the // in http://)


def modules_named(root: Path) -> list[str]:
    """Every place in Pip's programs that names a module ("path:line: text"); [] is good."""
    pkg = root / "pip_assistant"
    names = sorted(p.name for p in (pkg / "modules").iterdir() if p.is_dir() and (p / "module.toml").exists()
                   and not p.name.startswith("_"))
    if not names:
        return []
    exact = re.compile(rf"^({'|'.join(names)})$")
    address = re.compile(rf"/m/({'|'.join(names)})\b")
    quoted = re.compile(rf"""(?<![=(])(["'`])({'|'.join(names)})\1""")   # not an attribute (id="screen") or $("…")
    named = []
    for folder in (pkg / d for d in ("core", "ears", "thinker", "web", "builder", "common")):
        for f in sorted(folder.rglob("*")):
            if "__pycache__" in f.parts or not f.is_file():
                continue
            if f.suffix == ".py" and not f.name.startswith("test_"):
                hits = [(n, s) for n, s in python_strings(f) if exact.match(s) or address.search(s)]
            elif f.suffix in (".html", ".js"):
                hits = [(n, s) for n, s in page_lines(f) if quoted.search(s) or address.search(s)]
            else:
                continue
            named += [f"{f.relative_to(root)}:{n}: {s.strip()[:100]}" for n, s in hits]
    return named


if __name__ == "__main__":
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())
    failed = False
    if missing := map_missing(root):
        print("docs/PROJECT-MAP.md doesn't mention: " + ", ".join(missing), file=sys.stderr)
        print("Add them to the map (what it is, and whether it may be changed).", file=sys.stderr)
        failed = True
    if named := modules_named(root):
        print("Pip's programs name a module (they should ask every module through core/modules.py's hooks instead):",
              file=sys.stderr)
        print("\n".join("  " + x for x in named), file=sys.stderr)
        failed = True
    sys.exit(1 if failed else 0)
