#!/bin/sh
# Give Pip's dashboard an https address on the owner's Tailscale devices (D-116, D-149):
#     https://<this machine>.<tailnet>.ts.net:8791  →  Pip's dashboard (http://127.0.0.1:8790)
# Tailscale makes and renews the certificate; only devices on the owner's tailnet can open it.
# A phone needs this to talk to Pip (/phone, D-149): phones only let a page use the microphone on https.
#
# It uses its own port ([web] https_port, 8791), so whatever already has https on 443 (Immich on this Pi) is left
# alone. It doesn't touch Ziggletron. Run it once:   sudo sh ~/pip/deploy/install-https.sh
# To undo:   sudo tailscale serve --https=8791 off
set -eu

command -v tailscale >/dev/null || { echo "Tailscale isn't installed here."; exit 1; }

# The ports pip-web uses (~/pip-data/settings.toml [web] port and https_port, else the defaults). Under sudo, "~"
# is root's home, so look in the home of the person who ran sudo.
owner="${SUDO_USER:-$(id -un)}"
home="$(getent passwd "$owner" | cut -d: -f6)"
ports="$(python3 -c "
import tomllib, pathlib, sys
p = pathlib.Path(sys.argv[1]) / 'pip-data' / 'settings.toml'
try:
    web = tomllib.loads(p.read_text()).get('web', {})
except (OSError, ValueError):
    web = {}
print(web.get('port', 8790), web.get('https_port', 8791))" "$home")"
port="${ports% *}"
https_port="${ports#* }"

echo "What Tailscale serves now (before):"
tailscale serve status || true
echo

echo "Pointing https port $https_port at Pip's dashboard on http://127.0.0.1:$port …"
tailscale serve --bg --https="$https_port" "http://127.0.0.1:$port"
echo

echo "What Tailscale serves now (after):"
tailscale serve status
echo

name="$(tailscale status --json | python3 -c "import json, sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")"
echo "Open Pip's dashboard at:  https://$name:$https_port"
echo "Talk to Pip on your phone: https://$name:$https_port/phone"
echo "(Only your Tailscale devices can reach it. The monitor keeps using http://127.0.0.1:$port/tv.)"
