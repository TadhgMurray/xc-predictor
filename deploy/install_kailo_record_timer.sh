#!/usr/bin/env bash
# deploy/install_kailo_record_timer.sh -- every 6 hours, save OUR predictions
# for Kailo's upcoming college races (every basis), and both systems'
# ratings, BEFORE the races are run: the only comparison neither side can
# have seen the answer to (scripts/kailo_compare.py --record,
# scripts/kailo_ratings.py --snapshot; files under engine/data/kailo/).
#
#   sudo bash deploy/install_kailo_record_timer.sh       # install / refresh
#   journalctl -u xcp-kailo-record -n 30                 # what it saved
#   sudo systemctl disable --now xcp-kailo-record.timer  # stop it
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
cat > /etc/systemd/system/xcp-kailo-record.service <<UNIT
[Unit]
Description=Racecast: record our predictions and ratings before Kailo's races

[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
ExecStart=$PY -u $ROOT/scripts/kailo_compare.py --record
ExecStart=$PY -u $ROOT/scripts/kailo_ratings.py --snapshot
Nice=10
UNIT
cat > /etc/systemd/system/xcp-kailo-record.timer <<UNIT
[Unit]
Description=Racecast: Kailo recording, every 6 hours

[Timer]
OnCalendar=*-*-* 00/6:17
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-kailo-record.timer
systemctl start xcp-kailo-record.service || true
journalctl -u xcp-kailo-record -n 20 --no-pager
