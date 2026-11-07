"""Finding Pip's modules (ARCHITECTURE §7, D-96). Read-only: every program may use it.

Each feature that plugs into Pip is a folder in pip_assistant/modules/:

    modules/calendar/
    ├── module.toml   what it is, what it may do, its settings (the part people read and edit)
    ├── module.py     setup(ctx) → the code (pip-core runs it; see core/modules.py)
    ├── static/       its dashboard page, if it has one (served at /m/calendar/)
    └── test_*.py     its tests

Folders starting with "_" (like _template) are examples and never loaded.

A module's settings come from its module.toml defaults, overridden by [modules.<name>] in
~/pip-data/settings.toml, checked the same way as Pip's other settings. `enabled = false` there
switches a module off.
"""
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "modules"
KINDS = ("skill", "widget", "provider", "device", "plugin")


class ModuleError(ValueError):
    pass


@dataclass
class ModuleInfo:
    name: str
    kind: str
    summary: str
    path: Path
    says_examples: list = field(default_factory=list)
    offline: bool = False
    permissions: list = field(default_factory=list)
    needs: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)      # merged values
    explain: dict = field(default_factory=dict)       # setting → plain-language explanation
    claude: str = ""                                  # instructions added to Claude's system prompt
    page: str = ""                                    # dashboard page (in static/), if any
    label: str = ""                                   # its button on the main dashboard
    enabled: bool = True

    @property
    def hosts(self) -> list[str]:
        """Internet hosts it may reach ("network:googleapis.com" → googleapis.com and its subdomains)."""
        return [p.split(":", 1)[1] for p in self.permissions if p.startswith("network:")]


def _check_value(name: str, default, value):
    ok = isinstance(value, type(default)) or (isinstance(default, float) and isinstance(value, int))
    if isinstance(default, bool) != isinstance(value, bool) or not ok:
        raise ModuleError(f"'{name}' should be {type(default).__name__}, like {default!r}; got {value!r}.")
    return float(value) if isinstance(default, float) else value


def read(folder: Path, user: dict | None = None) -> ModuleInfo:
    """Read and check one module.toml, merged with the user's [modules.<name>] settings."""
    f = folder / "module.toml"
    try:
        t = tomllib.loads(f.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ModuleError(f"{f}: {e}")
    name = t.get("name")
    if name != folder.name:
        raise ModuleError(f"{f}: name must be '{folder.name}' (the folder's name)")
    if not re.fullmatch(r"_?[a-z][a-z0-9_]*", name):
        raise ModuleError(f"{f}: a module name is lowercase letters, digits and _")
    if t.get("kind") not in KINDS:
        raise ModuleError(f"{f}: kind must be one of {', '.join(KINDS)}")
    if not t.get("summary"):
        raise ModuleError(f"{f}: a one-line summary is needed")
    defaults, explain = {}, {}
    for key, s in (t.get("settings") or {}).items():
        if not isinstance(s, dict) or "default" not in s or "explain" not in s:
            raise ModuleError(f"{f}: [settings.{key}] needs a default and an explain line")
        defaults[key], explain[key] = s["default"], s["explain"]
    user = dict(user or {})
    enabled = user.pop("enabled", True)
    if not isinstance(enabled, bool):
        raise ModuleError(f"'modules.{name}.enabled' should be true or false")
    merged = dict(defaults)
    for key, value in user.items():
        if key not in defaults:
            raise ModuleError(f"Unknown setting 'modules.{name}.{key}'. Check {f}.")
        merged[key] = _check_value(f"modules.{name}.{key}", defaults[key], value)
    dash = t.get("dashboard") or {}
    return ModuleInfo(name=name, kind=t["kind"], summary=t["summary"], path=folder,
                      says_examples=list(t.get("says_examples") or []), offline=bool(t.get("offline", False)),
                      permissions=list(t.get("permissions") or []), needs=list(t.get("needs") or []),
                      settings=merged, explain=explain, claude=(t.get("claude") or {}).get("instructions", "").strip(),
                      page=dash.get("page", ""), label=dash.get("label", ""), enabled=enabled)


def discover(cfg: dict | None = None, root: Path | None = None) -> list[ModuleInfo]:
    """Every module (enabled or not), in an order where a module comes after the ones it needs."""
    root = root or ROOT
    user = (cfg or {}).get("modules", {}) or {}
    found = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
                         and (p / "module.toml").exists()):
        found[folder.name] = read(folder, user.get(folder.name))
    for name in user:
        if name not in found:
            raise ModuleError(f"Settings mention module '{name}', but there's no such module.")
    order, seen = [], set()

    def visit(name, chain=()):
        if name in seen:
            return
        if name in chain:
            raise ModuleError(f"Modules need each other in a loop: {' → '.join(chain + (name,))}")
        info = found.get(name)
        if info is None:
            raise ModuleError(f"Module '{chain[-1]}' needs '{name}', which doesn't exist.")
        for dep in info.needs:
            visit(dep, chain + (name,))
        seen.add(name)
        order.append(info)
    for name in found:
        visit(name)
    for info in order:  # a module whose needs are switched off can't run either
        if info.enabled and any(not found[d].enabled for d in info.needs):
            info.enabled = False
    return order


def enabled(cfg: dict | None = None) -> list[ModuleInfo]:
    return [m for m in discover(cfg) if m.enabled]
