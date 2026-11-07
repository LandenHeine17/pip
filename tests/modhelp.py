"""Plugging the real modules into a test, the way pip-core does (core/modules.py)."""
from pip_assistant.common.modules import discover
from pip_assistant.core.modules import Registry


def registry(tmp_path, schedule=None, clock=None, people=None, names=("google", "calendar", "todos"), transport=None,
             settings=None):
    infos = [m for m in discover({"modules": settings or {}}) if m.name in names]
    kw = {"schedule": schedule, "people": people, "db_path": tmp_path / "pip.db", "transport": transport}
    if clock is not None:
        kw["clock"] = clock
    return Registry.load({}, infos=infos, **kw)
