#!/usr/bin/env bash
# deploy/install_ovh_watch_timer.sh -- check OVH US Rise stock every 30 minutes
# and mail the admins when a server we would move to comes into stock
# (scripts/ovh_stock_watch.py says which, and why).
#
#   sudo bash deploy/install_ovh_watch_timer.sh           # install / refresh
#   systemctl list-timers xcp-ovh-watch                   # when it runs next
#   journalctl -u xcp-ovh-watch -n 20                     # what it found
#   sudo systemctl disable --now xcp-ovh-watch.timer      # stop it (moved, or gave up)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
cat > /etc/systemd/system/xcp-ovh-watch.service <<UNIT
[Unit]
Description=Racecast: OVH Rise stock watch

[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
ExecStart=$PY -u $ROOT/scripts/ovh_stock_watch.py
Nice=10
UNIT
cat > /etc/systemd/system/xcp-ovh-watch.timer <<UNIT
[Unit]
Description=Racecast: OVH Rise stock watch, every 30 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-ovh-watch.timer
systemctl start xcp-ovh-watch.service || true
journalctl -u xcp-ovh-watch -n 12 --no-pager
