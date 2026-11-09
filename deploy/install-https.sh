#!/bin/sh
# Give Pip's dashboard an https address on the owner's Tailscale devices (D-116):
#     https://<this machine>.<tailnet>.ts.net  →  Pip's dashboard (http://127.0.0.1:8790)
# Tailscale makes and renews the certificate; only devices on the owner's tailnet can open it.
#
# This replaces Ziggletron's old https entry, which still points at Ziggletron's dashboard port 8765
# (nothing listens there since Ziggletron was switched off, D-82). It doesn't touch Ziggletron itself:
# no files, no services. Run it once, over SSH:   sudo sh ~/pip/deploy/install-https.sh
# To undo:   sudo tailscale serve --https=443 off
set -eu

command -v tailscale >/dev/null || { echo "Tailscale isn't installed here."; exit 1; }

# The port pip-web uses (~/pip-data/settings.toml [web] port, else the default). Under sudo, "~" is
# root's home, so look in the home of the person who ran sudo.
owner="${SUDO_USER:-$(id -un)}"
home="$(getent passwd "$owner" | cut -d: -f6)"
port="$(python3 -c "
import tomllib, pathlib, sys
p = pathlib.Path(sys.argv[1]) / 'pip-data' / 'settings.toml'
try:
    print(tomllib.loads(p.read_text()).get('web', {}).get('port', 8790))
except (OSError, ValueError):
    print(8790)" "$home")"

echo "What Tailscale serves now (before):"
tailscale serve status || true
echo

echo "Pointing https (port 443) at Pip's dashboard on http://127.0.0.1:$port …"
tailscale serve --bg --https=443 "http://127.0.0.1:$port"
echo

echo "What Tailscale serves now (after):"
tailscale serve status
echo

name="$(tailscale status --json | python3 -c "import json, sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")"
echo "Open Pip's dashboard at:  https://$name"
echo "(Only your Tailscale devices can reach it. The monitor keeps using http://127.0.0.1:$port/tv.)"
