#!/usr/bin/env bash
# deploy/install_nightly_timer.sh -- run deploy/nightly_update.sh overnight,
# Monday to Saturday mornings.
#
# ★ NOT SATURDAY NIGHT (owner, 2026-10-22: "the full rating is done every
#   Sunday, so there should not be an overnight thing on Saturday"): the
#   Sunday full update scrapes and re-rates everything itself. About's
#   "When new results show up" says this schedule; keep the two in step.
#
#   sudo bash deploy/install_nightly_timer.sh            # 01:17 Pacific, Mon-Sat
#   sudo bash deploy/install_nightly_timer.sh 02:17      # or at this time (Pacific)
#   systemctl list-timers xcp-nightly                    # when it runs next
#   journalctl -u xcp-nightly                            # what it said (logs/nightly_*/ too)
#   sudo systemctl disable --now xcp-nightly.timer       # stop it
#
# A failed step mails the admins (scripts/notify_owner.py), when mail is set
# up. A night the full pipeline is running is skipped: same lock file.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AT="${1:-01:17}"
cat > /etc/systemd/system/xcp-nightly.service <<UNIT
[Unit]
Description=Racecast nightly light update (scrape, price new results, boards)
After=postgresql.service network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
EnvironmentFile=/etc/xc-predictor.env
ExecStart=/bin/bash $ROOT/deploy/nightly_update.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
# the scrape's own bound is 3 h; this is the backstop for everything after
TimeoutStartSec=6h
UNIT
cat > /etc/systemd/system/xcp-nightly.timer <<UNIT
[Unit]
Description=Racecast nightly light update

[Timer]
OnCalendar=Mon..Sat *-*-* $AT:00 America/Los_Angeles
Persistent=false

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now xcp-nightly.timer
systemctl list-timers xcp-nightly.timer --no-pager
