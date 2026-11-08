#!/usr/bin/env bash
# deploy/replica/install_services.sh -- the replica's three small services.
# Run on BOTH boxes (each acts on its role at run time, so nothing changes
# at a switchover).
#
#   sudo bash deploy/replica/install_services.sh
#
#   rc-origin-health.service   origin_health.py --serve on :RC_HEALTH_PORT (8099):
#                              200 when the site and this box's Postgres answer,
#                              503 otherwise. Cloudflare's monitor reads it.
#                              Runs as xcp, read-only filesystem.
#   rc-replica-alert.timer     every 5 min: origin_health.py --alert (mails the
#                              admins when replication breaks, a slot is idle
#                              or lost, the disk runs low)
#   rc-sync.timer              every 10 min: sync_from_primary.sh (only acts on
#                              the standby)
#
# Firewall: the health port is opened to Cloudflare's published ranges only
# (as deploy/origin_firewall.sh does for 80/443). Re-run monthly with them.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
PORT="${RC_HEALTH_PORT:-8099}"
[ -x "$PY" ] || die "no $PY -- the site's environment (standby_site.sh) comes first"

cat > /etc/systemd/system/rc-origin-health.service <<UNIT
[Unit]
Description=Racecast: origin health for Cloudflare (deploy/replica)
After=network-online.target

[Service]
User=xcp
EnvironmentFile=$RC_ENV
EnvironmentFile=/etc/xc-predictor.env
Environment=RC_ENV=/nonexistent XCP_ENV=/nonexistent
ExecStart=$PY -u $HERE/origin_health.py --serve --port $PORT
Restart=always
RestartSec=2
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/rc-replica-alert.service <<UNIT
[Unit]
Description=Racecast: replication check, mail on a new problem (deploy/replica)

[Service]
Type=oneshot
WorkingDirectory=$RC_ROOT
ExecStart=$PY -u $HERE/origin_health.py --alert
SuccessExitStatus=1
UNIT
cat > /etc/systemd/system/rc-replica-alert.timer <<UNIT
[Unit]
Description=Racecast: replication check every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/rc-sync.service <<UNIT
[Unit]
Description=Racecast: standby follows the primary's files (deploy/replica)

[Service]
Type=oneshot
ExecStart=/bin/bash $HERE/sync_from_primary.sh
Nice=10
UNIT
cat > /etc/systemd/system/rc-sync.timer <<UNIT
[Unit]
Description=Racecast: standby file sync every 10 minutes

[Timer]
OnBootSec=4min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now rc-origin-health.service rc-replica-alert.timer rc-sync.timer >/dev/null
systemctl restart rc-origin-health.service

say "firewall: port $PORT for Cloudflare's monitors only"
if ufw status | grep -q "Status: active"; then
  V4="$(curl -fsS https://www.cloudflare.com/ips-v4 || true)"
  V6="$(curl -fsS https://www.cloudflare.com/ips-v6 || true)"
  if [ -n "$V4" ] && [ -n "$V6" ]; then
    for n in $V4 $V6; do
      ufw allow proto tcp from "$n" to any port "$PORT" comment 'racecast origin health (cloudflare)' >/dev/null
    done
  else
    warn "could not fetch Cloudflare's ranges; port $PORT left closed -- re-run later"
  fi
else
  warn "ufw is not active; port $PORT is reachable by anyone (it only says up or down)"
fi

sleep 2
say "health here: $(curl -s -m 10 "http://127.0.0.1:$PORT/" || echo 'no answer')"
"$PY" "$HERE/origin_health.py" --report || true
