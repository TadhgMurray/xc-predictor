#!/usr/bin/env bash
# deploy/install_race_model_timer.sh -- run deploy/race_model_weekly.sh once
# a week: rebuild the race model on the week's results, swap it in only if
# it still beats the ratings.
#
#   sudo bash deploy/install_race_model_timer.sh          # Mondays 14:17 UTC
#   sudo bash deploy/install_race_model_timer.sh 15:47    # or at this time (UTC)
#   systemctl list-timers xcp-race-model                  # when it runs next
#   journalctl -u xcp-race-model                          # what it said (logs/race_model_*/ too)
#   sudo systemctl disable --now xcp-race-model.timer     # stop it
#
# ★ MONDAY AFTERNOON UTC: after Sunday's full re-rating and the Monday
#   nightly (01:17 Pacific, done by about 07:00 Pacific), and clear of the
#   Postgres restart (06:00-07:00 UTC). About 2.5 hours, at the lowest CPU
#   and disk priority, so the site comes first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AT="${1:-14:17}"
cat > /etc/systemd/system/xcp-race-model.service <<UNIT
[Unit]
Description=Racecast weekly race model rebuild
After=postgresql.service

[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
ExecStart=/bin/bash $ROOT/deploy/race_model_weekly.sh
Nice=19
IOSchedulingClass=best-effort
IOSchedulingPriority=7
TimeoutStartSec=8h
UNIT
cat > /etc/systemd/system/xcp-race-model.timer <<UNIT
[Unit]
Description=Racecast weekly race model rebuild

[Timer]
OnCalendar=Mon *-*-* $AT:00 UTC
Persistent=false

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-race-model.timer
systemctl list-timers xcp-race-model.timer --no-pager
