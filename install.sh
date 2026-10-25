#!/bin/sh
# Install as a per-user service. No root, nothing written to /sys.
set -eu
here=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$here/power_hz.py" "$HOME/.local/bin/power-hz"
cp "$here/power-hz.service" "$HOME/.config/systemd/user/"

systemctl --user daemon-reload
systemctl --user enable --now power-hz.service
systemctl --user --no-pager status power-hz.service || true
