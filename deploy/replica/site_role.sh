#!/usr/bin/env bash
# deploy/replica/site_role.sh -- point THIS box's site at the right databases.
#
#   sudo bash deploy/replica/site_role.sh standby   # reads: the local replica; writes: the other box
#   sudo bash deploy/replica/site_role.sh primary   # everything local (today's single-DSN setup)
#   sudo bash deploy/replica/site_role.sh remote    # everything on the other box, no read routing
#                                                   #   (the way back if routing misbehaves)
#   sudo bash deploy/replica/site_role.sh show
#   ... --no-restart                                 # write the files, leave the site running
#
# Writes /etc/xc-predictor-role.env and a drop-in that makes the site's unit
# read it AFTER /etc/xc-predictor.env:
#
#   /etc/systemd/system/xc-predictor.service.d/50-racecast-role.conf
#       [Service]
#       EnvironmentFile=-/etc/xc-predictor-role.env
#
# ! AN EnvironmentFile, NOT Environment=. systemd lets EnvironmentFile=
#   override Environment=, so XCP_DB_HOST=127.0.0.1 from the main env file
#   would win over a drop-in's Environment= line. A second EnvironmentFile,
#   read later, wins.
# ! ONLY THE SITE READS THE ROLE FILE. The pipeline and the timers read
#   /etc/xc-predictor.env alone, so they keep talking to the local cluster
#   (and a standby box runs none of them: standby_build.sh turns them off).
# ! STANDBY: XCP_DB_MIN_CONN=0, so 24 workers do not each hold an idle
#   connection to the primary: one opens when a write needs it.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
ROLE="${1:-show}"
RESTART=1
[ "${2:-}" = "--no-restart" ] && RESTART=0
ROLE_ENV=/etc/xc-predictor-role.env
DROPIN=/etc/systemd/system/xc-predictor.service.d/50-racecast-role.conf

case "$ROLE" in
  show)
    say "$DROPIN:"; cat "$DROPIN" 2>/dev/null || echo "  (none)"
    say "$ROLE_ENV:"; cat "$ROLE_ENV" 2>/dev/null || echo "  (none)"
    exit 0 ;;
  standby)
    BODY="$(cat <<EOF
# deploy/replica/site_role.sh standby -- $(hostname): this box is a hot standby.
# Reads: the replica here. Writes (accounts, follows, claims, reports, usage):
# the primary, ${RC_PEER_NAME}, through the tunnel. scripts/read_routing.py.
XCP_DB_HOST=${RC_PEER_WG}
XCP_DB_PORT=${RC_PG_PORT}
XCP_DB_MIN_CONN=0
XCP_DB_READ_DSN=host=127.0.0.1 port=${RC_PG_PORT}
XCP_DB_READ_MAX_LAG_S=${RC_READ_MAX_LAG_S:-300}
EOF
)" ;;
  remote)
    BODY="$(cat <<EOF
# deploy/replica/site_role.sh remote -- $(hostname): reads AND writes on ${RC_PEER_NAME}
# through the tunnel; read routing off (scripts/read_routing.py unused).
XCP_DB_HOST=${RC_PEER_WG}
XCP_DB_PORT=${RC_PG_PORT}
EOF
)" ;;
  primary)
    BODY="$(cat <<EOF
# deploy/replica/site_role.sh primary -- $(hostname): this box is the primary.
# Nothing to override: /etc/xc-predictor.env's local database serves it all.
EOF
)" ;;
  *) die "usage: site_role.sh standby|primary|remote|show [--no-restart]" ;;
esac

CHANGED=0
write_if_changed "$ROLE_ENV" "$BODY" && CHANGED=1
chmod 640 "$ROLE_ENV"
chgrp xcp "$ROLE_ENV" 2>/dev/null || true
mkdir -p "$(dirname "$DROPIN")"
if write_if_changed "$DROPIN" "$(printf '# deploy/replica/site_role.sh\n[Service]\nEnvironmentFile=-%s' "$ROLE_ENV")"; then
  CHANGED=1
fi
systemctl daemon-reload
say "site role: $ROLE"
if [ "$CHANGED" = 1 ] && [ "$RESTART" = 1 ] && systemctl is-active -q xc-predictor; then
  say "restarting the site (a few seconds; the edge serves its cache meanwhile)"
  systemctl restart xc-predictor
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: racecast.co' http://127.0.0.1:8000/robots.txt || true)"
    [ "$code" = 200 ] && break
    sleep 1
  done
  say "local check: HTTP ${code:-none}"
fi
