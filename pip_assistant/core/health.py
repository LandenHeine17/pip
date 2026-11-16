"""Watchdog: knows which programs are alive, notices stuck ones, restarts them (PIP-NEW-079).

If pip-core itself was held up (the whole Pi stalled, say), every program's last heartbeat looks old at once
although they're fine: that check is skipped, and they're judged again once their heartbeats have caught up.
"""
import logging
import os
import subprocess
import time

from ..common.client import rss_mb

OK, STUCK, MISSING = "ok", "stuck", "missing"


class Health:
    def __init__(self, expected: list[str], stuck_after: float = 10.0, restart: bool = True,
                 restart_fn=None, clock=time.time, release: str = "dev"):
        self.expected = expected
        self.release = release  # which installed version pip-core is (common/program.release_id)
        self.stuck_after = stuck_after
        # Restarting only makes sense when systemd runs Pip (INVOCATION_ID is set by systemd).
        self.restart = restart and bool(os.environ.get("INVOCATION_ID"))
        self.restart_fn = restart_fn or self._systemd_restart
        self.clock = clock
        self.programs: dict[str, dict] = {}
        self.last_restart: dict[str, float] = {}
        self.started = clock()
        self.last_check = self.started

    def heartbeat(self, name: str, data: dict) -> None:
        self.programs[name] = {**self.programs.get(name, {}), **data, "last_seen": self.clock(), "connected": True}

    def disconnected(self, name: str) -> None:
        if name in self.programs:
            self.programs[name]["connected"] = False

    def state(self, name: str) -> str:
        p = self.programs.get(name)
        if not p:
            return MISSING
        if self.clock() - p["last_seen"] > self.stuck_after:
            return STUCK if p.get("connected") else MISSING
        return OK

    def snapshot(self) -> dict:
        now = self.clock()
        names = list(dict.fromkeys(self.expected + list(self.programs)))
        out = {"core": {"state": OK, "uptime_s": round(now - self.started), "rss_mb": rss_mb(),
                        "release": self.release}}
        for n in names:
            p = self.programs.get(n, {})
            out[n] = {"state": self.state(n), "rss_mb": p.get("rss_mb"), "uptime_s": p.get("uptime_s"),
                      "seen_s_ago": round(now - p["last_seen"], 1) if p else None,
                      **{k: v for k, v in p.items() if k not in ("last_seen", "connected", "rss_mb", "uptime_s", "state")}}
        return out

    def check(self) -> list[dict]:
        """Return actions taken: restarts of programs that are stuck (or missing after start-up grace)."""
        actions = []
        now = self.clock()
        held_up = now - self.last_check > self.stuck_after   # pip-core didn't get to check: its own stall
        self.last_check = now
        if held_up:
            return actions
        for n in self.expected:
            st = self.state(n)
            grace_over = now - self.started > 3 * self.stuck_after
            if st == STUCK or (st == MISSING and grace_over):
                if self.restart and now - self.last_restart.get(n, 0) > 60:
                    self.last_restart[n] = now
                    ok = self.restart_fn(n)
                    actions.append({"program": n, "state": st, "restarted": ok})
        return actions

    @staticmethod
    def _systemd_restart(name: str) -> bool:
        """Ask systemd to restart it, without waiting for the restart (--no-block): a stuck program can take
        90 s to stop, and pip-core's whole event loop would wait with it."""
        try:
            r = subprocess.run(["systemctl", "--user", "--no-block", "restart", f"pip-{name}.service"],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError) as e:
            logging.getLogger(__name__).warning("couldn't restart pip-%s: %s", name, type(e).__name__)
            return False
