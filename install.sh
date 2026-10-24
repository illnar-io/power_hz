#!/bin/sh
# Install as a per-user service. No root, nothing written to /sys.
# Requires sudo NOPASSWD for tuned-adm profile (one-time setup).
set -eu
here=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$here/power_hz.py" "$HOME/.local/bin/power-hz"
cp "$here/power-hz.service" "$HOME/.config/systemd/user/"

# Sudoers rule for tuned-adm profile (safe: only the configured profile name runs)
if ! sudo -n true 2>/dev/null; then
    echo "This script needs sudo to install the tuned-adm profile rule."
fi
SUDOERS_FILE="/etc/sudoers.d/power-hz"
SUDOERS_LINE="$(id -un) ALL=(ALL) NOPASSWD: /usr/bin/tuned-adm profile powersave, /usr/bin/tuned-adm profile throughput-performance, /usr/bin/tuned-adm profile balanced"
if [ ! -f "$SUDOERS_FILE" ] || ! grep -q "tuned-adm profile" "$SUDOERS_FILE" 2>/dev/null; then
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    echo "Installed $SUDOERS_FILE"
fi

systemctl --user daemon-reload
systemctl --user enable --now power-hz.service
systemctl --user --no-pager status power-hz.service || true
