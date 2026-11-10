"""Shared by every test, in tests/ and in each module's folder: no test may touch the real ~/pip-data.

Each test gets its own throwaway data folder ($PIP_DATA, read by common/paths.py), so a test that forgets to
pass a database path writes there, not into Pip's live pip.db. Only the downloaded models and the owner's M0
recordings are linked in (some tests use them, read-only); tests that need them skip themselves where they
aren't there, like in pip-builder's sandbox."""
import os
from pathlib import Path

import pytest

REAL = Path(os.environ.get("PIP_DATA", Path.home() / "pip-data"))
READ_ONLY = ("models", "m0")


@pytest.fixture(autouse=True)
def no_real_pip_data(monkeypatch, tmp_path_factory):
    data = tmp_path_factory.mktemp("pip-data")
    for name in READ_ONLY:
        if (REAL / name).is_dir():
            (data / name).symlink_to(REAL / name, target_is_directory=True)
    monkeypatch.setenv("PIP_DATA", str(data))
