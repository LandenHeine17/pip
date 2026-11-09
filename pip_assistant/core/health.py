"""Watchdog: knows which programs are alive, notices stuck ones, restarts them (PIP-NEW-079)."""
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
        r = subprocess.run(["systemctl", "--user", "restart", f"pip-{name}.service"], capture_output=True, timeout=30)
        return r.returncode == 0
