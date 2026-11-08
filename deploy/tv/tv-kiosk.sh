#!/usr/bin/env bash
# What the monitor runs (deploy/tv/pip-tv.service): Pip's TV page, full screen, nothing else on the screen.
# Waits for pip-web first, so the screen never lands on a browser error page.
set -u
url="${PIP_TV_URL:-http://127.0.0.1:8790/tv}"

until curl -fsS -o /dev/null --max-time 2 "$url"; do
    sleep 2
done

browser="$(command -v chromium || command -v chromium-browser)"
# Incognito: it starts clean every time, with no "restore your pages?" after a power cut.
exec "$browser" \
    --kiosk --incognito --no-first-run --noerrdialogs --disable-infobars \
    --ozone-platform=wayland --password-store=basic --disable-pinch \
    --disable-features=Translate,MediaRouter --disable-component-update --no-default-browser-check \
    --autoplay-policy=no-user-gesture-required \
    --user-data-dir="$HOME/.cache/pip-tv" \
    "$url"
