#!/usr/bin/env bash
# deploy/pg_bulk_tuning.sh -- Postgres settings for the pipeline's bulk
# rewrites, applied with a RELOAD (no Postgres restart, nobody disconnected).
#
#   bash deploy/pg_bulk_tuning.sh --show     # current values + checkpoint counters
#   bash deploy/pg_bulk_tuning.sh --apply    # set them, reload
#   bash deploy/pg_bulk_tuning.sh --revert   # back to postgresql.conf's values, reload
#   bash deploy/pg_bulk_tuning.sh --minimal  # wal_level minimal: RESTARTS POSTGRES
#   bash deploy/pg_bulk_tuning.sh --replica  # wal_level replica again: RESTARTS POSTGRES
#
# ★ WHY (2026-11-22, owner: "get really experimental"). Every big step
#   rewrites a 20-70 GB table (05's merges, 08's results rebuilds, the
#   rankings). ! MEASURED 2026-11-22 on production BEFORE --apply:
#   max_wal_size was already 32GB, checkpoint_timeout 15min, wal_compression
#   lz4, and only 7 of 565 checkpoints had been forced by WAL size -- so
#   --apply's gain there is small; wal_level minimal (--minimal) is the
#   bigger lever. The original reasoning, for the record: with 8GB Postgres must
#   checkpoint after every 8 GB of WAL -- several times inside one rewrite --
#   and after each checkpoint the first change to every page writes the WHOLE
#   8 kB page into the WAL again (full_page_writes). The pipeline's summary
#   now prints the run's checkpoints, forced against timed, and its WAL
#   (scripts/checkpoint_stats.py): many forced ones means this helps.
#
#   max_wal_size 96GB     checkpoints by the timer, not every 8 GB. The box
#                         has ~1 TB free; WAL is recycled after the next
#                         checkpoint. A crash replays more WAL on restart
#                         (minutes, not seconds) -- nothing is lost.
#   checkpoint_timeout 30min, checkpoint_completion_target 0.9
#                         fewer, smoother checkpoints
#   wal_compression zstd  the full-page images that remain are compressed
#                         (Postgres 15+; CPU for less disk and less to ship
#                         to the planned standby)
#   max_parallel_workers 22 (max_worker_processes is 24; that one needs a
#                         restart, this one does not): more workers for the
#                         index builds and parallel sorts. Left at 16 when
#                         max_worker_processes is lower than 24.
#
# ! wal_level: --apply/--revert never touch it; they are reload-only and
#   restart nothing. --minimal / --replica change it and RESTART POSTGRES
#   (a few seconds; the site's pool reconnects; the site's own service is
#   NOT restarted).
#
# ★ --minimal (2026-11-22, owner: the standby is not running; backups are
#   pg_dump, deploy/backup_db.sh, so there is no WAL archive or PITR to
#   lose). At wal_level minimal a table or index created in a transaction
#   and filled in that same transaction writes NO WAL: the file is fsynced
#   at commit instead. The pipeline's big writes already qualify, checked
#   2026-11-22: merge_column's CREATE TABLE AS (05), the go-live's
#   DROP/CREATE/COPY/CREATE INDEX in one transaction (pair_write*.py), the
#   rankings' UNLOGGED load + SET LOGGED and the ordered CREATE+INSERT
#   rewrite (build_ranking_results.py), and every CREATE INDEX. So their
#   data is written once instead of twice. Crash safety is unchanged.
# ! BEFORE BUILDING THE STANDBY run --replica: streaming replication needs
#   replica (deploy/replica/primary_prepare.sh warns about minimal and sets
#   replica, but only takes effect after a restart).
#   Refuses while a pipeline session is running (a restart would kill it);
#   XCP_PG_RESTART_FORCE=1 overrides.
set -euo pipefail
PSQL=(runuser -u postgres -- psql -X -q -d "${XCP_DB_NAME:-xc_predictor}" -v ON_ERROR_STOP=1)
SETTINGS=(max_wal_size checkpoint_timeout checkpoint_completion_target wal_compression
          max_parallel_workers)

show() {
  for s in "${SETTINGS[@]}" max_worker_processes wal_level max_wal_senders server_version; do
    printf '  %-30s %s\n' "$s" "$("${PSQL[@]}" -tAc "SHOW $s")"
  done
  if [ "$("${PSQL[@]}" -tAc "SELECT current_setting('server_version_num')::int >= 170000")" = t ]; then
    "${PSQL[@]}" -c "SELECT num_timed AS timed, num_requested AS forced_by_wal FROM pg_stat_checkpointer"
  else
    "${PSQL[@]}" -c "SELECT checkpoints_timed AS timed, checkpoints_req AS forced_by_wal FROM pg_stat_bgwriter"
  fi
}

_restartWith() {               # $1 = wal_level, $2 = max_wal_senders
  local busy
  busy=$("${PSQL[@]}" -tAc "SELECT count(*) FROM pg_stat_activity
           WHERE application_name = 'xcp-pipeline' AND pid <> pg_backend_pid()")
  if [ "${busy:-0}" -gt 0 ] && [ "${XCP_PG_RESTART_FORCE:-0}" != 1 ]; then
    echo "  $busy pipeline session(s) connected: wait for the run to finish" >&2
    echo "  (or XCP_PG_RESTART_FORCE=1 to restart anyway)" >&2
    exit 1
  fi
  if [ "$1" = minimal ] && [ "$("${PSQL[@]}" -tAc "SHOW archive_mode")" != off ]; then
    echo "  archive_mode is on: minimal needs it off (a WAL archive exists?)" >&2
    exit 1
  fi
  "${PSQL[@]}" <<SQL
ALTER SYSTEM SET wal_level = '$1';
ALTER SYSTEM SET max_wal_senders = $2;
SQL
  echo "  restarting Postgres for wal_level=$1 (the site reconnects on its own)"
  systemctl restart postgresql
  for _ in $(seq 60); do "${PSQL[@]}" -tAc "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
  show
  [ "$("${PSQL[@]}" -tAc "SHOW wal_level")" = "$1" ] || { echo "  wal_level is NOT $1" >&2; exit 1; }
}

case "${1:---show}" in
  --show) show ;;
  --apply)
    ver=$("${PSQL[@]}" -tAc "SELECT current_setting('server_version_num')::int")
    comp=on; [ "$ver" -ge 150000 ] && comp=zstd
    mwp=$("${PSQL[@]}" -tAc "SHOW max_worker_processes")
    par=16; [ "$mwp" -ge 24 ] && par=22
    "${PSQL[@]}" <<SQL
ALTER SYSTEM SET max_wal_size = '${XCP_PG_MAX_WAL:-96GB}';
ALTER SYSTEM SET checkpoint_timeout = '30min';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_compression = '$comp';
ALTER SYSTEM SET max_parallel_workers = $par;
SELECT pg_reload_conf();
SQL
    sleep 1; show ;;
  --revert)
    for s in "${SETTINGS[@]}"; do "${PSQL[@]}" -c "ALTER SYSTEM RESET $s"; done
    "${PSQL[@]}" -c "SELECT pg_reload_conf()"
    sleep 1; show ;;
  --minimal) _restartWith minimal 0 ;;
  --replica) _restartWith replica 10 ;;
  *) echo "usage: $0 --show | --apply | --revert | --minimal | --replica" >&2; exit 2 ;;
esac
