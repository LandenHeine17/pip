"""Where Pip keeps things. Personal data lives only in the data folder, never in the repo (PIP-NEW-096)."""
import os
from pathlib import Path


def data_dir() -> Path:
    """The data folder: ~/pip-data, or $PIP_DATA (used by tests and fresh installs)."""
    return Path(os.environ.get("PIP_DATA", Path.home() / "pip-data"))


def run_dir() -> Path:
    """Where the programs' connection socket lives."""
    d = data_dir() / "run"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def core_socket() -> Path:
    return run_dir() / "core.sock"


def database() -> Path:
    return data_dir() / "pip.db"


def user_settings() -> Path:
    return data_dir() / "settings.toml"
