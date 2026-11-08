#!/usr/bin/env bash
# deploy/install_static_timer.sh -- rebuild the static pages every five minutes
# (racecast/build_static_pages.py; nginx side: deploy/nginx_static.sh).
#
#   sudo bash deploy/install_static_timer.sh            # install / update
#   sudo bash deploy/install_static_timer.sh --remove   # stop it (the files run out within 20 min)
#   systemctl list-timers xcp-static                    # when it runs next
#   journalctl -u xcp-static                            # what it said
#
# ★ FIVE MINUTES BECAUSE OF THE WINDOWS. A build links the ten-minute window
#   it runs in and the next; nginx reads only the current one. Every five
#   minutes keeps a file at most ~5 minutes older than a fresh render (the
#   front page's own shared copy is 300 s old at worst); a stopped timer
#   stops serving files within twenty. A build is ~50 renders in a fresh
#   process (seconds), at Nice=10.
# ! THE SWITCH IS XCP_STATIC_PAGES=1 in /etc/xc-predictor.env. Without it each
#   run clears the windows and builds nothing, so the timer can be installed
#   ahead of the switch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
SITE_USER="${XCP_SITE_USER:-xcp}"

if [ "${1:-}" = "--remove" ]; then
  systemctl disable --now xcp-static.timer 2>/dev/null || true
  rm -f /etc/systemd/system/xcp-static.service /etc/systemd/system/xcp-static.timer
  systemctl daemon-reload
  echo "removed the timer; the last files stop being served within twenty minutes"
  exit 0
fi

cat > /etc/systemd/system/xcp-static.service <<UNIT
[Unit]
Description=Racecast static pages (racecast/build_static_pages.py)
After=postgresql.service xc-predictor.service

[Service]
Type=oneshot
User=$SITE_USER
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
# the site's database settings (deploy/server_setup.sh's unit), a smaller pool
Environment=XCP_DB_APP=site XCP_DB_MIN_CONN=1 XCP_DB_MAX_CONN=2 XCP_PREWARM=0
Environment=XCP_DB_LOCK_TIMEOUT_MS=5000 XCP_DB_STATEMENT_TIMEOUT_MS=55000
ExecStart=$PY -u $ROOT/racecast/build_static_pages.py
TimeoutStartSec=240
Nice=10
UNIT
cat > /etc/systemd/system/xcp-static.timer <<UNIT
[Unit]
Description=Racecast static pages, every five minutes

[Timer]
OnBootSec=3min
OnCalendar=*:0/5
AccuracySec=15s

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-static.timer
systemctl list-timers xcp-static.timer --no-pager
