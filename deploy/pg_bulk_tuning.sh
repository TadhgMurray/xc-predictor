#!/usr/bin/env bash
# deploy/pg_bulk_tuning.sh -- Postgres settings for the pipeline's bulk
# rewrites, applied with a RELOAD (no Postgres restart, nobody disconnected).
#
#   bash deploy/pg_bulk_tuning.sh --show     # current values + checkpoint counters
#   bash deploy/pg_bulk_tuning.sh --apply    # set them, reload
#   bash deploy/pg_bulk_tuning.sh --revert   # back to postgresql.conf's values, reload
#
# ★ WHY (2026-11-22, owner: "get really experimental"). Every big step
#   rewrites a 20-70 GB table (05's merges, 08's results rebuilds, the
#   rankings). server_setup.sh set max_wal_size = 8GB, so Postgres must
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
# ! wal_level STAYS replica (docs/REPLICA-PLAN.md plans a hot standby).
#   Every setting here is reload-only; nothing here restarts Postgres or the
#   site. --revert puts every one back.
set -euo pipefail
PSQL=(runuser -u postgres -- psql -X -q -d "${XCP_DB_NAME:-xc_predictor}" -v ON_ERROR_STOP=1)
SETTINGS=(max_wal_size checkpoint_timeout checkpoint_completion_target wal_compression
          max_parallel_workers)

show() {
  for s in "${SETTINGS[@]}" max_worker_processes wal_level server_version; do
    printf '  %-30s %s\n' "$s" "$("${PSQL[@]}" -tAc "SHOW $s")"
  done
  if [ "$("${PSQL[@]}" -tAc "SELECT current_setting('server_version_num')::int >= 170000")" = t ]; then
    "${PSQL[@]}" -c "SELECT num_timed AS timed, num_requested AS forced_by_wal FROM pg_stat_checkpointer"
  else
    "${PSQL[@]}" -c "SELECT checkpoints_timed AS timed, checkpoints_req AS forced_by_wal FROM pg_stat_bgwriter"
  fi
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
  *) echo "usage: $0 --show | --apply | --revert" >&2; exit 2 ;;
esac
