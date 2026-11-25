"""`tools/pip-change`: what Claude runs in a self-change's Claude Hub chat to tell Pip where the change is (D-152).

    tools/pip-change 12 building     the owner approved the plan; Claude is building it
    tools/pip-change 12 dropped      the owner decided against it
    tools/pip-change 12              show where it is

The rest Pip works out by himself: "planning" once Claude Hub opens the chat, "ready" once commits ending with
`Pip-Change: 12` are on main, "live" once they're in the version he's running (changes.py). This writes the
store directly (pip-builder notices within seconds), and uses only the standard library, so it runs with the
system python3 whatever state Pip is in.
"""
import sys
import time

from ..common import paths
from .store import ACTIVE, Store

SETTABLE = {"building": ACTIVE - {"building"}, "dropped": ACTIVE}   # stage → the statuses it may come from


def main(argv: list[str]) -> int:
    if not argv or not argv[0].lstrip("#").isdigit() or len(argv) > 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    cid = argv[0].lstrip("#")
    store = Store(paths.database())
    c = store.get(cid)
    if not c:
        print(f"There's no change #{cid}.", file=sys.stderr)
        return 1
    if len(argv) == 1:
        print(f"Change #{cid}: {c['status']} (updated {time.strftime('%Y-%m-%d %H:%M', time.localtime(c['updated']))})")
        return 0
    stage = argv[1].lower()
    if stage not in SETTABLE:
        print(f"Unknown stage {stage!r}: use {' or '.join(SETTABLE)} (ready and live are found from the commits).",
              file=sys.stderr)
        return 2
    if c["status"] == stage:
        print(f"Change #{cid} is already {stage}.")
        return 0
    if c["status"] not in SETTABLE[stage]:
        print(f"Change #{cid} is {c['status']}, so it can't be marked {stage}.", file=sys.stderr)
        return 1
    store.update(cid, status=stage)
    store.log(cid, "status", f"{stage.capitalize()} (from the Claude Hub chat)")
    print(f"Change #{cid} is now {stage}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
