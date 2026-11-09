#!/bin/sh
# Install or update Pip (M1b, versioned installs, PIP-NEW-080).
#
# Pip runs from a separate live copy, ~/pip-app/current, never straight from this repo. This script:
#   1. refuses if tracked files in ~/pip have uncommitted changes (only committed code is installed;
#      --allow-dirty installs the last commit anyway and leaves your changes out);
#   2. downloads the models Pip needs into ~/pip-data (once);
#   3. installs the last commit (HEAD) with pip_assistant/release.py: makes the version, waits for a quiet
#      moment, copies the service files, switches Pip over, restarts him and checks his health. If he isn't
#      healthy it switches back by itself. It runs as its own systemd job so the restarts can't cut it off.
# The exit code says how it went: 0 healthy, 1 rolled back, 2 stopped before changing anything, 3 needs you.
set -e
cd "$(dirname "$0")/.."
REPO=$(pwd)
ALLOW_DIRTY=0
NOW=""
for a in "$@"; do
  case "$a" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --now) NOW="--now" ;;
    *) echo "Unknown option: $a (use --allow-dirty and/or --now)"; exit 2 ;;
  esac
done

# 1. Only committed code goes live.
if ! DIRTY=$(python3 -m pip_assistant.release dirty); then
  if [ "$ALLOW_DIRTY" = 1 ]; then
    echo "Warning: these files have uncommitted changes, and they will NOT be installed (only the last commit is):"
    echo "$DIRTY" | sed 's/^/  /'
  else
    echo "Stopped: these files have uncommitted changes:"
    echo "$DIRTY" | sed 's/^/  /'
    echo "Commit them first (only committed code is installed), or run with --allow-dirty to install the last commit without them."
    exit 2
  fi
fi

# 2. Models, downloaded once into the data folder, never into the repo.
# Voice recognition model (step 5, D-87).
SPK=~/pip-data/models/speaker/nemo_en_titanet_small.onnx
if [ ! -f "$SPK" ]; then
  mkdir -p ~/pip-data/models/speaker
  curl -sSfL -o "$SPK.part" https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx && mv "$SPK.part" "$SPK"
fi
# Pip's voice (Piper, D-73): the one chosen in settings ([ears] voice), downloaded once from the Piper
# voices collection into ~/pip-data/models/voices (common/voices.py, the same code the Settings page uses).
# This uses the repo's own .venv (uv run), which is also what Pip falls back to if the very first
# versioned install isn't healthy.
~/.local/bin/uv run -q python -c 'from pip_assistant.common import settings, voices; print("Voice:", voices.ensure_voice(settings.load()["ears"]["voice"]).name)'

# 3. Install the last commit. The service files are copied (and enabled) by release.py only once the new
# version exists, so Pip is never pointed at an empty ~/pip-app/current.
set +e
systemd-run --user --unit="pip-release-$(date +%s)" --wait --pipe --collect --quiet \
  -p MemoryMax=2G -p TimeoutStopSec=15min --working-directory="$REPO" --setenv=PYTHONPATH="$REPO" --setenv=PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
  /usr/bin/python3 -m pip_assistant.release install HEAD --install-units $NOW
CODE=$?
case $CODE in
  0) echo "Pip is installed, running the new version, and healthy." ;;
  1) echo "The new version wasn't healthy, so Pip went back to the one before (see above, and ~/pip-app/release.log)." ;;
  2) echo "Stopped before anything changed (see above)." ;;
  *) echo "PROBLEM: Pip may not be running properly. See above and ~/pip-app/release.log." ;;
esac
exit $CODE
