#!/usr/bin/env bash
# deploy/replica/switchover_stop_primary.sh -- step 1 of a PLANNED switchover
# (the primary is healthy; you want the other box to take over). Run on the
# CURRENT PRIMARY. Step 2 is promote.sh --planned on the standby.
#
#   sudo bash deploy/replica/switchover_stop_primary.sh
#
# 1. refuses while a pipeline holds .pipeline.lock (start the switchover
#    after the run, or stop the run first);
# 2. turns this box's writer timers off (nightly, backup, ...);
# 3. waits until the standby has replayed everything (origin_health.py
#    --wait-caught-up), so the clean stop below costs nothing;
# 4. stops this box's site and Postgres CLEANLY. A clean stop sends the
#    final WAL -- the shutdown checkpoint -- to the standby before it exits;
# 5. prints that checkpoint's position. promote.sh --planned --expect-lsn
#    <it> checks the standby has it before promoting: no write is lost.
#
# From step 4 until promote.sh restarts the site on the other box, writes
# (sign-ins, follows) fail with the 503 page and reads continue from the
# standby's site: about 1-2 minutes when both steps are run back to back.
# Postgres here stays STOPPED (and disabled) until standby_build.sh --rewind
# makes this box the new standby.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
local_up || die "the local Postgres is not running"
local_in_recovery && die "this box is a standby; run this on the primary"
pipeline_running && die "a pipeline holds .pipeline.lock -- wait for it, or stop it first"

STANDBYS="$(psql_local -d postgres -c "SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming'")"
[ "$STANDBYS" -ge 1 ] || die "no standby is streaming from this box; a switchover needs one"

writer_timers_off
say "waiting for the standby to replay everything so far"
"${XCP_PYTHON:-/srv/venv/bin/python}" "$(dirname "$0")/origin_health.py" --wait-caught-up --timeout 900 \
  || die "the standby did not catch up in 15 min; nothing was stopped"

say "stopping the site here (the standby's site keeps serving reads)"
systemctl stop xc-predictor || true
say "stopping Postgres cleanly"
systemctl disable "$PG_UNIT" >/dev/null 2>&1 || true
pg_ctlcluster "$RC_PG_MAJOR" "$RC_PG_CLUSTER" stop -m fast
LSN="$(sudo -u postgres "$PG_BIN/pg_controldata" "$PG_DATA" | awk -F': *' '/Latest checkpoint location/ {print $2}')"
STATE="$(sudo -u postgres "$PG_BIN/pg_controldata" "$PG_DATA" | awk -F': *' '/Database cluster state/ {print $2}')"
[ "$STATE" = "shut down" ] || warn "cluster state is '$STATE', not 'shut down'"
say "stopped. Final checkpoint: $LSN"
echo
echo "  NOW, on $RC_PEER_NAME:  sudo bash deploy/replica/promote.sh --planned --expect-lsn $LSN"
echo
say "then, back here: sudo bash deploy/replica/standby_build.sh --rewind"
