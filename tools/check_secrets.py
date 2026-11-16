#!/usr/bin/env python3
"""Block commits that contain likely credentials, phone numbers or the Pi's Tailscale address.

Used as a git pre-commit hook (tools/install_hooks.sh) and can be run directly:
    python3 tools/check_secrets.py            # check staged changes
    python3 tools/check_secrets.py --all      # check every tracked file
    python3 tools/check_secrets.py --message FILE   # check a commit message

A line containing "secret-check: allow" is skipped (use only for documented examples).
"""
import re
import subprocess
import sys

PATTERNS = {
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "Groq key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Twilio SID": re.compile(r"\bAC[0-9a-f]{32}\b"),
    "Private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Any name ending in one of these, assigned a long quoted value: client_secret = "…", "access_secret": "…",
    # refresh_token, local_key, access_id (Google's and Tuya's keys in secrets.toml).
    "Assigned secret": re.compile(
        r"(?i)[\w-]*(api[_-]?key|token|secret|password|passwd|local[_-]?key|access[_-]?id|client[_-]?id)"
        r"['\"]?\s*[=:]\s*['\"][^'\"\s]{12,}['\"]"
    ),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Google OAuth secret": re.compile(r"\bGOCSPX-[0-9A-Za-z_-]{20,}"),
    "Google refresh token": re.compile(r"\b1//0[0-9A-Za-z_-]{30,}"),
    # The Pi's address on Tailscale (100.64.0.0/10): the repo is public (D-70). Tests use 100.64.0.x.
    "Tailscale address": re.compile(r"(?<![\d.])100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.(?!0\.\d{1,3}\b)\d{1,3}\.\d{1,3}(?![\d.])"),
    "Phone number (E.164)": re.compile(r"(?<![\w.])\+\d{10,14}\b"),
    "Phone number (US style)": re.compile(r"(?<![\w.-])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
}
ALLOW = "secret-check: allow"


def scan(text: str, where: str) -> list[str]:
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for name, pat in PATTERNS.items():
            if pat.search(line):
                problems.append(f"{where}:{n}: possible {name}")
    return problems


def staged_added_lines() -> list[tuple[str, str]]:
    """(file:line, text) for every added line in the staged diff."""
    diff = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    ).stdout
    out, current, lineno = [], "?", 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            out.append((f"{current}:{lineno}", line[1:]))
            lineno += 1
    return out


def main() -> int:
    args = sys.argv[1:]
    problems: list[str] = []
    if args[:1] == ["--message"] and len(args) == 2:
        with open(args[1], encoding="utf-8", errors="replace") as fh:
            problems = scan(fh.read(), "commit message")
    elif args[:1] == ["--all"]:
        files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    problems += scan(fh.read(), f)
            except (UnicodeDecodeError, FileNotFoundError):
                continue
    else:
        for where, line in staged_added_lines():
            problems += [p.replace(":1: ", ": ", 1) for p in scan(line, where)]
    if problems:
        print("Commit blocked: possible secret or phone number found.\n", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("\nRemove it (secrets belong in ~/pip-data/secrets.toml).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
