#!/bin/sh
# The smart plugs' own Wi-Fi network (D-139): the Pi reaches the internet through its Ethernet cable, and its
# Wi-Fi hosts a small 2.4 GHz network, "pip-plugs", that only the plugs (and a phone, while pairing one) join.
# On it, Pip switches the plugs directly (modules/home_control), which the campus Wi-Fi doesn't allow (test tuya-plug).
#
#     sh ~/pip/deploy/plug-network.sh             set it up (once; again is harmless)
#     sh ~/pip/deploy/plug-network.sh --dry-run   only show what it would do
#     sh ~/pip/deploy/plug-network.sh --undo      the Wi-Fi back on the campus network; pip-plugs kept, switched off
#
# No sudo: NetworkManager lets the Pi's own user change its connections. What it does:
#   - checks the cable first: an address, the internet and Tailscale over it (else it stops: Pip would lose
#     the internet, as the Wi-Fi can't be on the campus network and host one at the same time);
#   - makes the network: 2.4 GHz, channel 6, WPA2 (the plugs can't use WPA3), 10.47.0.1/24, sharing the cable's
#     internet (the plugs need it to pair, and the Smart Life app keeps working);
#   - its name and password: ~/pip-data/secrets.toml [plugs_wifi] (made once, never printed by this script);
#   - the campus Wi-Fi profile is kept (no longer joined by itself), so --undo puts it straight back.
set -eu
NET=pip-plugs
ADDR=10.47.0.1/24
CAMPUS="${CAMPUS:-NU-IoT-Ziggletron}"   # the campus Wi-Fi profile (nmcli connection show)
cd "$(dirname "$0")/.."
repo="$(pwd)"
dry=0
case "${1:-}" in
    --dry-run) dry=1 ;;
    --undo)
        nmcli connection down "$NET" 2>/dev/null || true
        nmcli connection modify "$NET" connection.autoconnect no 2>/dev/null || true
        nmcli connection modify "$CAMPUS" connection.autoconnect yes
        nmcli connection up "$CAMPUS"
        echo "The Wi-Fi is back on the campus network; pip-plugs is switched off (kept). The plugs on it are offline."
        exit 0 ;;
    "") ;;
    *) echo "Usage: sh deploy/plug-network.sh [--dry-run | --undo]"; exit 1 ;;
esac
if [ "$(id -u)" = 0 ]; then
    echo "Run this without sudo:  sh ~/pip/deploy/plug-network.sh"
    exit 1
fi

# 1. The cable has to carry everything first.
ip -4 -br addr show eth0 | grep -q "[0-9]\.[0-9]" || { echo "No address on the Ethernet cable (eth0): plug it in first."; exit 1; }
ping -c2 -W3 -I eth0 1.1.1.1 >/dev/null 2>&1 || { echo "No internet over the cable: ask campus IT whether the port needs registering."; exit 1; }
curl -s -o /dev/null --interface eth0 --max-time 8 https://openapi.tuyaus.com/ || { echo "The cable can't reach the web."; exit 1; }
tailscale status --self --peers=false >/dev/null 2>&1 || { echo "Tailscale isn't up: fix that first, or you'd lose remote access."; exit 1; }
echo "The cable works (internet and Tailscale)."

# 2. Its name and password, made once and kept in secrets.toml (letters and digits only: easy to type in Smart Life).
if [ "$dry" = 1 ]; then
    echo "would make (once) the network's name and password in ~/pip-data/secrets.toml [plugs_wifi]"
    psk="(the password)"
else
    psk="$(PYTHONPATH="$repo" python3 -c "
import secrets
from pip_assistant.common.secrets import ScopedSecrets
s = ScopedSecrets('plugs_wifi')
if not s.get('password'):
    s.save({'ssid': '$NET', 'password': ''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(12))})
print(s.get('password'))")"
fi

run() {
    if [ "$dry" = 1 ]; then echo "would run: $*"; else "$@"; fi
}

# 3. The network (made once; its settings are put right again on every run).
if ! nmcli -t -f NAME connection show | grep -qx "$NET"; then
    run nmcli connection add type wifi ifname wlan0 con-name "$NET" ssid "$NET"
fi
run nmcli connection modify "$NET" 802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
    wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf disable \
    wifi-sec.psk "$psk" ipv4.method shared ipv4.addresses "$ADDR" ipv6.method disabled \
    connection.autoconnect yes connection.autoconnect-priority 200

# 4. Switch: the campus Wi-Fi isn't joined by itself any more (kept for --undo), and the Wi-Fi hosts pip-plugs.
run nmcli connection modify "$CAMPUS" connection.autoconnect no
run nmcli connection up "$NET"
[ "$dry" = 1 ] && exit 0

ping -c2 -W3 1.1.1.1 >/dev/null 2>&1 && echo "Pip still has the internet (through the cable)." \
    || echo "Pip has no internet now! Undo:  sh ~/pip/deploy/plug-network.sh --undo"
echo "pip-plugs is up at ${ADDR%/*}. Its password: ~/pip-data/secrets.toml, [plugs_wifi]."
echo "Next: join pip-plugs on your phone and open a web page. If it doesn't load, Docker's firewall is in the way:"
echo "  see deploy/network/50-pip-plugs (two sudo commands)"
