"""~/pip-data/secrets.toml: the only place secrets live (rule 1).

Programs read single keys with `get("groq_api_key")`. A module gets **its own section only**
(`ScopedSecrets("google")` reads and writes `[google]` and `[google.*]`), so one module can never
see another's keys. Writing replaces only that module's section and keeps everything else in the
file exactly as it was, comments included. The file stays readable by its owner only (0600).
"""
import os
import re
import tomllib
from pathlib import Path

from . import paths


def path() -> Path:
    return paths.data_dir() / "secrets.toml"


def load() -> dict:
    p = path()
    return tomllib.loads(p.read_text()) if p.exists() else {}


def get(name: str, default: str = "") -> str:
    return load().get(name, default)


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _value(k)


def _tables(prefix: str, d: dict) -> list[str]:
    """TOML text for a nested dict of plain values, as [prefix] and [prefix.sub] tables."""
    plain = {k: v for k, v in d.items() if not isinstance(v, dict)}
    out = [f"[{prefix}]"] + [f"{_key(k)} = {_value(v)}" for k, v in plain.items()] + [""]
    for k, v in d.items():
        if isinstance(v, dict):
            out += _tables(f"{prefix}.{_key(k)}", v)
    return out


class ScopedSecrets:
    """One module's own section of secrets.toml."""

    def __init__(self, section: str):
        if not re.fullmatch(r"_?[a-z][a-z0-9_]*", section):
            raise ValueError("bad secrets section name")
        self.section = section

    def all(self) -> dict:
        return load().get(self.section, {}) or {}

    def get(self, key: str, default=None):
        return self.all().get(key, default)

    def save(self, data: dict) -> None:
        """Replace this module's section with `data` (a nested dict)."""
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = p.read_text().splitlines() if p.exists() else []
        keep, mine = [], False
        header = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*(#.*)?$")
        for line in lines:
            m = header.match(line)
            if m:
                name = m.group(1).strip().strip('"')
                mine = name == self.section or name.startswith(self.section + ".")
            if not mine:
                keep.append(line)
        while keep and not keep[-1].strip():
            keep.pop()
        text = "\n".join(keep + ([""] if keep else []) + (_tables(self.section, data) if data else [])).rstrip() + "\n"
        tomllib.loads(text)  # never write a file that doesn't read back
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def set(self, key: str, value) -> None:
        data = self.all()
        data[key] = value
        self.save(data)
