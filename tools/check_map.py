#!/usr/bin/env python3
"""Fail if a top-level file or folder in the repo isn't mentioned in docs/PROJECT-MAP.md."""
import subprocess
import sys
from pathlib import Path

root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())
files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, text=True, cwd=root).stdout.split()
top = sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in files} - {".gitignore"})
text = (root / "docs" / "PROJECT-MAP.md").read_text()
missing = [t for t in top if f"`{t}" not in text]
if missing:
    print("docs/PROJECT-MAP.md doesn't mention: " + ", ".join(missing), file=sys.stderr)
    print("Add them to the map (what it is, and whether it may be changed).", file=sys.stderr)
    sys.exit(1)
