#!/usr/bin/env bash
# deploy/replica/fence.sh -- an OLD PRIMARY that the other box replaced while
# it was down (promote.sh --failover) must not take a single write. Run it on
# the old box the moment it is reachable, before anything else.
#
#   sudo bash deploy/replica/fence.sh
#
# Stops and disables its site, its writer timers and its Postgres, and masks
# Postgres so a reboot cannot start it. standby_build.sh --rewind unmasks it
# and brings it back as a standby of the new primary.
#
# ! IT REFUSES TO FENCE THE ONLY PRIMARY: when the OTHER box is not a running
#   primary, this one may be the only copy taking writes.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
if ! { peer_up && ! peer_in_recovery; }; then
  [ "${1:-}" = "--force" ] || die "the other box is not a running primary -- fencing this one would leave no primary.
           (--force if you are sure.)"
fi
systemctl stop xc-predictor 2>/dev/null || true
systemctl disable xc-predictor >/dev/null 2>&1 || true
writer_timers_off
systemctl stop "$PG_UNIT" 2>/dev/null || true
systemctl disable "$PG_UNIT" >/dev/null 2>&1 || true
systemctl mask "$PG_UNIT" >/dev/null
echo manual > "$PG_CONF_DIR/start.conf"          # and pg_ctlcluster/the generator leave it alone
say "fenced: site, timers and Postgres stopped; Postgres masked."
say "next: sudo bash deploy/replica/standby_build.sh --rewind"
