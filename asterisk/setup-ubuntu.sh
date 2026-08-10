#!/usr/bin/env bash
# VoiceFlow AI — Asterisk + Bluetooth GSM gateway installer.
#
# Run this on the spare Ubuntu laptop, from inside the repo:
#
#     cd voiceflow-ai/asterisk
#     chmod +x setup-ubuntu.sh
#     ./setup-ubuntu.sh
#
# It installs Asterisk, drops our config in, generates real passwords, and
# stops. Bluetooth pairing is interactive and stays manual — the script prints
# exactly what to run next.
#
# Safe to run twice: existing configs are backed up, passwords are only
# generated once.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AST_DIR="/etc/asterisk"
ENV_FILE="$REPO_DIR/../backend/.env"
STAMP="$(date +%Y%m%d-%H%M%S)"

green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33m%s\033[0m\n' "$*"; }
die()   { printf '\033[0;31m%s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Do not run as root — the script calls sudo where it needs to."
command -v apt-get >/dev/null || die "This is for Ubuntu/Debian. See docs/GSM-GATEWAY.md."

# ── 1. Version check ────────────────────────────────────────────────────
VERSION_ID="$(. /etc/os-release && echo "${VERSION_ID:-unknown}")"
echo "Ubuntu $VERSION_ID"
if [[ "$VERSION_ID" == 24.* || "$VERSION_ID" == 25.* ]]; then
  warn ""
  warn "chan_mobile has known trouble with the BlueZ/DBus stack on Ubuntu 24.04+."
  warn "Pairing may succeed while audio never flows. 22.04 LTS is the safer host."
  warn ""
  read -rp "Continue anyway? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || exit 0
fi

# ── 2. Packages ─────────────────────────────────────────────────────────
green "Installing Asterisk and Bluetooth support…"
sudo apt-get update -qq
sudo apt-get install -y asterisk asterisk-mobile bluez bluez-tools python3-venv

if ! sudo asterisk -rx "module show like chan_mobile" 2>/dev/null | grep -q chan_mobile; then
  warn "chan_mobile did not load. Check that asterisk-mobile installed and that"
  warn "modules.conf does not have 'noload => chan_mobile.so'."
fi

# ── 3. Config ───────────────────────────────────────────────────────────
green "Installing configuration…"
for f in mobile.conf extensions.conf ari.conf http.conf rtp.conf logger.conf pjsip.conf; do
  [[ -f "$REPO_DIR/$f" ]] || { warn "missing $f in repo, skipping"; continue; }
  if [[ -f "$AST_DIR/$f" ]]; then
    sudo cp -a "$AST_DIR/$f" "$AST_DIR/$f.bak-$STAMP"
  fi
  sudo cp "$REPO_DIR/$f" "$AST_DIR/$f"
done
green "  existing files backed up as *.bak-$STAMP"

# ── 4. Passwords ────────────────────────────────────────────────────────
# Only touch the placeholders — never overwrite a password already set.
gen() { openssl rand -base64 18 | tr -d '/+=' | cut -c1-24; }

ARI_PASS=""
if sudo grep -q "CHANGE_ME_ARI" "$AST_DIR/ari.conf"; then
  ARI_PASS="$(gen)"
  sudo sed -i "s|CHANGE_ME_ARI|$ARI_PASS|" "$AST_DIR/ari.conf"
  green "Generated ARI password."
else
  warn "ari.conf already has a password — leaving it alone."
fi

for ext in 1001 1002; do
  if sudo grep -q "CHANGE_ME_$ext" "$AST_DIR/pjsip.conf"; then
    P="$(gen)"
    sudo sed -i "s|CHANGE_ME_$ext|$P|" "$AST_DIR/pjsip.conf"
    echo "  SIP extension $ext password: $P"
  fi
done

# ── 5. Wire the backend to ARI ──────────────────────────────────────────
if [[ -n "$ARI_PASS" && -f "$ENV_FILE" ]]; then
  if grep -q '^ASTERISK_ARI_PASSWORD=' "$ENV_FILE"; then
    sed -i "s|^ASTERISK_ARI_PASSWORD=.*|ASTERISK_ARI_PASSWORD=$ARI_PASS|" "$ENV_FILE"
  else
    printf '\nASTERISK_ARI_PASSWORD=%s\n' "$ARI_PASS" >> "$ENV_FILE"
  fi
  green "Wrote the ARI password into backend/.env."
elif [[ ! -f "$ENV_FILE" ]]; then
  warn "backend/.env not found — copy .env.example to .env and set"
  warn "ASTERISK_ARI_PASSWORD=$ARI_PASS yourself."
fi

# ── 6. Keep the laptop awake ────────────────────────────────────────────
if grep -q '^#\?HandleLidSwitch=' /etc/systemd/logind.conf; then
  sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
  sudo systemctl restart systemd-logind || true
  green "Lid-close will no longer suspend the machine."
fi

sudo systemctl enable --now asterisk >/dev/null 2>&1 || sudo systemctl restart asterisk
sleep 2
sudo asterisk -rx "core reload" >/dev/null 2>&1 || true

green ""
green "Installed. Asterisk is running."
cat <<'NEXT'

Next — pairing is interactive, so do these by hand:

  1. On the phone: Bluetooth on, name it GSM_PHONE_1, make it discoverable.

  2. Get this laptop's Bluetooth MAC (goes in mobile.conf as the *adapter*):
       hciconfig

  3. Pair:
       bluetoothctl
         power on
         agent on
         default-agent
         scan on
         pair AA:BB:CC:DD:EE:FF
         trust AA:BB:CC:DD:EE:FF
         connect AA:BB:CC:DD:EE:FF
         info AA:BB:CC:DD:EE:FF
         quit

     In `info`, confirm "Handsfree Audio Gateway" appears. No HFP, no gateway.

  4. Let Asterisk find the phone — this prints the RFCOMM port you need:
       sudo asterisk -rx "mobile search"

  5. Put the adapter MAC, the phone MAC and that port into:
       sudo nano /etc/asterisk/mobile.conf
       sudo asterisk -rx "module reload chan_mobile"
       sudo asterisk -rx "mobile show devices"     # expect State: Free

  6. Call the SIM from another phone, watching:
       sudo asterisk -rvvv

Full walkthrough and troubleshooting: docs/GSM-GATEWAY.md
NEXT
