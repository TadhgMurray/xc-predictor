#!/usr/bin/env bash
# deploy/install_backup_timer.sh -- run deploy/backup_db.sh every day.
#
#   sudo bash deploy/install_backup_timer.sh            # daily at 11:07
#   sudo bash deploy/install_backup_timer.sh 03:37      # or at this time
#   systemctl list-timers xcp-backup                    # when it runs next
#   journalctl -u xcp-backup                            # what it said
#
# A failed backup is mailed to the admins like a failed pipeline run
# (scripts/notify_owner.py), when mail is set up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AT="${1:-11:07}"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
cat > /etc/systemd/system/xcp-backup.service <<UNIT
[Unit]
Description=Racecast database backup
After=postgresql.service

[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
ExecStart=/bin/bash $ROOT/deploy/backup_db.sh
# a failed (or skipped) backup mails the admins; never fails the unit itself
ExecStopPost=/bin/sh -c '[ "\$\$SERVICE_RESULT" = success ] || $PY $ROOT/scripts/notify_owner.py --backup-failed || true'
Nice=10
IOSchedulingClass=idle
UNIT
cat > /etc/systemd/system/xcp-backup.timer <<UNIT
[Unit]
Description=Racecast database backup, daily

[Timer]
OnCalendar=*-*-* $AT:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-backup.timer
systemctl list-timers xcp-backup.timer --no-pager
