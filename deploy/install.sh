#!/bin/sh
# Install or update Pip's services for this user (M1a: runs straight from the repo).
# Versioned installs with automatic rollback come later (PIP-NEW-080).
set -e
cd "$(dirname "$0")/.."
~/.local/bin/uv sync -q
# Voice recognition model (step 5, D-87): downloaded once into the data folder, never into the repo.
SPK=~/pip-data/models/speaker/nemo_en_titanet_small.onnx
if [ ! -f "$SPK" ]; then
  mkdir -p ~/pip-data/models/speaker
  curl -sSfL -o "$SPK.part" https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx && mv "$SPK.part" "$SPK"
fi
# Pip's voice (Piper, D-73): the one chosen in settings ([ears] voice), downloaded once from the Piper
# voices collection. A name like en_US-lessac-medium lives at en/en_US/lessac/medium/ there.
VOICE=$(~/.local/bin/uv run -q python -c 'from pip_assistant.common import settings; print(settings.load()["ears"]["voice"])')
VDIR=~/pip-data/models/voices
if [ ! -f "$VDIR/$VOICE.onnx" ] || [ ! -f "$VDIR/$VOICE.onnx.json" ]; then
  mkdir -p "$VDIR"
  LANG_REGION=${VOICE%%-*}; REST=${VOICE#*-}; NAME=${REST%-*}; QUALITY=${REST##*-}
  URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${LANG_REGION%%_*}/$LANG_REGION/$NAME/$QUALITY/$VOICE"
  for f in "$VOICE.onnx" "$VOICE.onnx.json"; do
    curl -sSfL -o "$VDIR/$f.part" "$URL${f#$VOICE}" && mv "$VDIR/$f.part" "$VDIR/$f"
  done
fi
mkdir -p ~/.config/systemd/user
cp deploy/systemd/pip-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
for p in core ears thinker web; do systemctl --user enable -q "pip-$p.service"; systemctl --user restart "pip-$p.service"; done
echo "Pip services installed and (re)started."
