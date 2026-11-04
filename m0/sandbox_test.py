"""M0 sandbox test: can the Pi isolate a self-change job without installing anything? (AC-SM-03, PIP-NEW-072)

Runs an untrusted "probe" script inside a systemd user sandbox (home hidden, only a workspace visible,
no network, memory/CPU capped) and checks that every forbidden action fails while allowed ones work.

    python3 ~/pip/m0/sandbox_test.py
"""
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

DATA = Path.home() / "pip-data/m0"
PROBE = r'''
import json, os, socket, sys
from pathlib import Path
home = Path(sys.argv[1]); ws = Path(sys.argv[2]); out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "allowed"
    except Exception as e:
        out[name] = "blocked (" + type(e).__name__ + ")"
attempt("read secrets file", lambda: open(home / "pip-data/secrets.toml").read())
attempt("read SSH keys", lambda: os.listdir(home / ".ssh"))
attempt("read recordings", lambda: os.listdir(home / "pip-data/m0/recordings"))
attempt("read Pip repo", lambda: os.listdir(home / "pip"))
attempt("write outside workspace", lambda: open(home / "sandbox-escape.txt", "w").write("x"))
attempt("internet access", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
attempt("write inside workspace", lambda: (ws / "ok.txt").write_text("ok"))
attempt("read inside workspace", lambda: (ws / "ok.txt").read_text())
print(json.dumps(out))
'''
SHOULD_FAIL = ["read secrets file", "read SSH keys", "read recordings", "read Pip repo", "write outside workspace", "internet access"]
SHOULD_WORK = ["write inside workspace", "read inside workspace"]


def dashboard_latency():
    t0 = time.perf_counter()
    try:
        urllib.request.urlopen("http://100.118.171.118:8780/api/status", timeout=5).read()
        return round(time.perf_counter() - t0, 3)
    except Exception:
        return None


def main():
    home = Path.home()
    ws = Path(tempfile.mkdtemp(prefix="pip-sandbox-", dir=home / "pip-data/m0"))
    (ws / "probe.py").write_text(PROBE)
    unit = f"pip-sandbox-test-{int(time.time())}"
    cmd = ["systemd-run", "--user", "--wait", "--pipe", "--collect", f"--unit={unit}",
           "-p", "ProtectHome=tmpfs", "-p", f"BindPaths={ws}", "-p", "PrivateNetwork=yes",
           "-p", "NoNewPrivileges=yes", "-p", "PrivateTmp=yes", "-p", "MemoryMax=2G", "-p", "CPUQuota=200%",
           "-p", "RuntimeMaxSec=120", "/usr/bin/python3", "-I", str(ws / "probe.py"), str(home), str(ws)]
    before = dashboard_latency()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    after = dashboard_latency()
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        out = {"error": (p.stderr or p.stdout)[-400:]}
    escaped = (home / "sandbox-escape.txt").exists()
    if escaped:
        (home / "sandbox-escape.txt").unlink()
    ok = ("error" not in out and all(out.get(k, "").startswith("blocked") for k in SHOULD_FAIL)
          and all(out.get(k) == "allowed" for k in SHOULD_WORK) and not escaped)
    leaks = [k for k in SHOULD_FAIL if out.get(k) == "allowed"]
    result = {"id": "sandbox", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": ok,
              "summary": ("Sandbox works with no installs (systemd user sandbox): secrets, SSH keys, recordings, the repo, "
                          "writing outside the workspace and the internet were all blocked; the workspace was usable."
                          if ok else f"Sandbox problems: {', '.join(leaks) or out.get('error', 'unexpected result')}")
                         + f" Dashboard response before/after: {before}/{after} s. (The real coding agent also needs a "
                           "controlled route to the Claude service; that's designed in the architecture phase.)",
              "details": {"checks": out, "escape_file_created": escaped}}
    for f in ws.iterdir():
        f.unlink()
    ws.rmdir()
    (DATA / "results").mkdir(parents=True, exist_ok=True)
    (DATA / "results/sandbox.json").write_text(json.dumps(result, indent=1))
    print(result["summary"])


if __name__ == "__main__":
    main()
