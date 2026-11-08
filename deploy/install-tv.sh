#!/usr/bin/env bash
# Put Pip's face on the monitor (step 7, D-110): installs deploy/tv/pip-tv.service as a system service and
# switches Ziggletron's screen service (ziggletron-tv) off, as the owner chose. Ziggletron's own files are
# not touched. Needs sudo. Run it once:   sh deploy/install-tv.sh
# To undo:   sudo systemctl disable --now pip-tv && sudo rm /etc/systemd/system/pip-tv.service
#            (and, to give the screen back to Ziggletron:  sudo systemctl enable --now ziggletron-tv)
set -eu
cd "$(dirname "$0")/.."
repo="$PWD"

for tool in cage chromium curl; do
    command -v "$tool" >/dev/null || { echo "Missing $tool: it was installed for Ziggletron's screen; ask before installing packages."; exit 1; }
done

# The port pip-web uses (~/pip-data/settings.toml [web] port, else the default).
port="$(python3 -c "
import tomllib, pathlib
p = pathlib.Path.home() / 'pip-data' / 'settings.toml'
try:
    print(tomllib.loads(p.read_text()).get('web', {}).get('port', 8790))
except (OSError, ValueError):
    print(8790)")"

if systemctl list-unit-files ziggletron-tv.service >/dev/null 2>&1 && systemctl is-enabled ziggletron-tv >/dev/null 2>&1; then
    echo "Switching Ziggletron's screen off (ziggletron-tv)…"
    sudo systemctl disable --now ziggletron-tv
fi

sed -e "s|@USER@|$USER|" -e "s|@PORT@|$port|" -e "s|@REPO@|$repo|" deploy/tv/pip-tv.service |
    sudo tee /etc/systemd/system/pip-tv.service >/dev/null
sudo usermod -aG video,render,input "$USER"
sudo systemctl daemon-reload
sudo systemctl enable --now pip-tv
echo "Pip's face is on the monitor: http://127.0.0.1:$port/tv (it waits for pip-web if Pip isn't running yet)."
