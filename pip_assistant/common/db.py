"""Opening Pip's database (~/pip-data/pip.db) the same way everywhere.

Several programs use the one file: pip-core (its tables and each module's), pip-thinker and pip-builder (the
usage meter, `usage`) and pip-builder (`builder_changes`). WAL lets them read while another writes; the
timeout makes a writer wait up to 10 s for another instead of failing at once.

    db = connect(path)               # rows come back as sqlite3.Row (row["name"])
    db = connect(path, rows=False)   # plain tuples
"""
import sqlite3
from pathlib import Path

TIMEOUT_S = 10


def connect(path: str | Path, rows: bool = True) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=TIMEOUT_S)
    if rows:
        db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db
