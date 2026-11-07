"""Settings: defaults from the repo, overridden by ~/pip-data/settings.toml (PIP-NEW-093).

Each user setting must exist in defaults.toml and have the same type, so typos and wrong
values are caught at startup with a clear message instead of failing later.
"""
import tomllib
from importlib import resources
from pathlib import Path

from . import paths


class SettingsError(ValueError):
    pass


def _defaults() -> dict:
    return tomllib.loads(resources.files("pip_assistant").joinpath("defaults.toml").read_text())


def _merge(defaults: dict, user: dict, where: str = "") -> dict:
    out = dict(defaults)
    for key, value in user.items():
        name = f"{where}{key}"
        if key not in defaults:
            raise SettingsError(f"Unknown setting '{name}'. Check the spelling against defaults.toml.")
        default = defaults[key]
        if isinstance(default, dict):
            if not isinstance(value, dict):
                raise SettingsError(f"'{name}' should be a section like [{name}].")
            out[key] = _merge(default, value, f"{name}.")
        else:
            ok = isinstance(value, type(default)) or (isinstance(default, float) and isinstance(value, int))
            if isinstance(default, bool) != isinstance(value, bool):
                ok = False
            if not ok:
                raise SettingsError(f"'{name}' should be {type(default).__name__}, like {default!r}; got {value!r}.")
            out[key] = float(value) if isinstance(default, float) else value
    return out


def load(path: Path | None = None) -> dict:
    """Return the merged settings. A missing user file is fine: defaults are used (fresh clone)."""
    path = path or paths.user_settings()
    user = tomllib.loads(path.read_text()) if path.exists() else {}
    modules = user.pop("modules", {}) or {}   # [modules.<name>]: checked against each module's own module.toml
    if not isinstance(modules, dict):
        raise SettingsError("'modules' should be sections like [modules.calendar].")
    out = _merge(_defaults(), user)
    out["modules"] = modules
    return out
