#!/usr/bin/env bash
# deploy/replica/promote.sh -- THIS standby becomes the primary.
#
#   PLANNED (both boxes healthy; after switchover_stop_primary.sh on the other):
#     sudo bash deploy/replica/promote.sh --planned --expect-lsn 1A2/3B4C5D60
#
#   FAILOVER (the primary is dead and will not be back soon):
#     sudo bash deploy/replica/promote.sh --failover --old-primary-is-fenced
#
# ★ DO NOT PROMOTE FOR A REBOOT. While the primary reboots, this box keeps
#   serving the site read-only with no action at all (read_routing). The
#   primary comes back in 2-60 minutes and replication resumes by itself.
#   Promote only when the primary is gone for hours (a dead disk, a board
#   swap) -- and then FENCE it first.
# ! FENCING (--failover). Two primaries is the one unrecoverable mistake: the
#   old one, booting, would start Postgres and take writes nobody can merge.
#   Before --old-primary-is-fenced is true, ALL of:
#     - Cloudflare: the old box is out of the load-balancer pool (or the
#       DNS record no longer points at it);
#     - the old box is powered off (OVH panel), OR its Postgres is stopped and
#       masked (fence.sh on it, if you can reach it);
#     - you will run fence.sh on it the moment it is reachable again.
#   The script also refuses while the old primary's Postgres still answers.
#
# What it does: checks; pg_promote(); removes the standby settings (91-...);
# points this box's site at its own database (site_role.sh primary); prints
# what to turn on here (timers) and what to do on the old box.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
MODE=""; EXPECT=""; FENCED=0
while [ $# -gt 0 ]; do
  case "$1" in
    --planned) MODE=planned ;;
    --failover) MODE=failover ;;
    --expect-lsn) EXPECT="${2:?}"; shift ;;
    --old-primary-is-fenced) FENCED=1 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ -n "$MODE" ] || die "say --planned (after switchover_stop_primary.sh) or --failover --old-primary-is-fenced"
local_up || die "the local Postgres is not running"
local_in_recovery || die "this box is already a primary"

if [ "$MODE" = planned ]; then
  [ -n "$EXPECT" ] || die "--planned needs --expect-lsn (switchover_stop_primary.sh printed it)"
  peer_up && die "the other box's Postgres still answers; run switchover_stop_primary.sh there first"
  say "waiting for this box to have replayed $EXPECT"
  ok=0
  for _ in $(seq 1 120); do
    if [ "$(psql_local -d postgres -c "SELECT pg_last_wal_replay_lsn() >= '$EXPECT'::pg_lsn")" = t ]; then ok=1; break; fi
    sleep 1
  done
  [ "$ok" = 1 ] || die "this box has not replayed $EXPECT -- do not promote; check replication (origin_health.py --report)"
else
  [ "$FENCED" = 1 ] || die "--failover needs --old-primary-is-fenced: read the FENCING note at the top of this script"
  if peer_up; then
    die "the old primary's Postgres ANSWERS on $RC_PEER_WG. It is not dead: wait for it (this box serves
           reads meanwhile), or switch over properly with switchover_stop_primary.sh on it."
  fi
  say "replication position at promotion (anything the old primary wrote after this is lost):"
  psql_local -d postgres -c "SELECT '  received ' || pg_last_wal_receive_lsn() || ', replayed ' || pg_last_wal_replay_lsn()
                             || ', last commit ' || coalesce(pg_last_xact_replay_timestamp()::text, '?')"
fi

say "promoting"
[ "$(psql_local -d postgres -c "SELECT pg_promote(true, 120)")" = t ] || die "pg_promote did not finish in 120 s"
local_in_recovery && die "still in recovery after pg_promote"
rm -f "$PG_CONF_DIR/conf.d/91-racecast-standby.conf"
psql_local -d postgres -c "SELECT pg_reload_conf()" >/dev/null
systemctl enable "$PG_UNIT" >/dev/null 2>&1 || true
say "this box is the primary (timeline $(psql_local -d postgres -c "SELECT timeline_id FROM pg_control_checkpoint()"))"

# a slot for the old primary to stream through once it is rebuilt
psql_local -d postgres -c "SELECT pg_create_physical_replication_slot('$PEER_SLOT', false)
                           WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = '$PEER_SLOT')" >/dev/null

bash "$(dirname "$0")/site_role.sh" primary

cat <<EOF

[replica] NEXT, in this order:
  1. Cloudflare: this box is now where writes work. Make it the first
     origin (docs/REPLICA-PLAN.md, "Cloudflare"); the old box stays out of
     the pool until it is a standby again.
  2. Here, the writers the old box ran:
       sudo bash deploy/install_nightly_timer.sh
       sudo bash deploy/install_backup_timer.sh
       sudo bash deploy/install_kailo_record_timer.sh
       sudo bash deploy/install_ovh_watch_timer.sh      # if it was on
     and the edge purge keys (XCP_CF_*) are already in /etc/xc-predictor.env.
  3. On the OLD box, when it is reachable:
       sudo bash deploy/replica/fence.sh                 # failover only: first thing
       sudo bash deploy/replica/standby_build.sh --rewind
       sudo bash deploy/replica/standby_site.sh
     then put it back in the Cloudflare pool as the second origin.
EOF
