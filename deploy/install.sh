#!/bin/sh
# Install or update Pip's services for this user (M1a: runs straight from the repo).
# Versioned installs with automatic rollback come later (PIP-NEW-080).
set -e
cd "$(dirname "$0")/.."
~/.local/bin/uv sync -q
mkdir -p ~/.config/systemd/user
cp deploy/systemd/pip-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
for p in core ears thinker web; do systemctl --user enable -q "pip-$p.service"; systemctl --user restart "pip-$p.service"; done
echo "Pip services installed and (re)started."
