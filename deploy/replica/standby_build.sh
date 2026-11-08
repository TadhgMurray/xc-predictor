#!/usr/bin/env bash
# deploy/replica/standby_build.sh -- make THIS box a hot standby of the other
# one (the primary), streaming its WAL through the tunnel.
#
#   sudo bash deploy/replica/standby_build.sh             # a new box: copy the database, stream
#   sudo bash deploy/replica/standby_build.sh --settings  # re-copy the primary's settings, reload
#   sudo bash deploy/replica/standby_build.sh --rewind    # an OLD PRIMARY after a failover or
#                                                         #   switchover: pg_rewind, then stream
#   sudo bash deploy/replica/standby_build.sh --rebuild   # throw this box's copy away, copy again
#
# Run wg_setup.sh (both boxes) and primary_prepare.sh (the primary) first.
# Re-running it on a standby that is already streaming only re-syncs the
# settings: it is safe to run any time.
#
# ★ HOW LONG. The copy is the whole data directory (~600 GB) through the
#   tunnel: about 1.5 h at a full 1 Gbit/s, 2-4 h across datacenters or with
#   RC_BB_MAX_RATE set. RUN IT IN tmux (tmux new -s standby): a dropped ssh
#   session kills pg_basebackup and the copy starts over. The WAL written
#   meanwhile streams alongside (-X stream), so the standby is current the
#   moment it starts. --rewind copies only the blocks that changed since the
#   two boxes parted: minutes.
# ★ THE COPY IS ALSO A CHECKSUM SCAN of every page of the primary
#   (pg_basebackup verifies them as it reads). On a box with a CPU fault that
#   is worth having: a failure names the file. RC_BB_NO_VERIFY=1 skips it.
# ! WHAT IT REFUSES. A local cluster that is a PRIMARY with data in it (it
#   may be the only copy) unless --rewind, or --rebuild with
#   RC_DISCARD_LOCAL_PRIMARY=yes. A peer that is itself in recovery.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
: "${RC_REPL_PASSWORD:?set RC_REPL_PASSWORD in $RC_ENV}"
: "${XCP_DB_PASSWORD:?no XCP_DB_PASSWORD in $XCP_ENV (the same postgres password as the primary)}"
MODE="${1:-build}"
case "$MODE" in build|--settings|--rewind|--rebuild) ;; *) die "unknown option $MODE" ;; esac

if [ -z "${TMUX:-}${STY:-}" ] && [ "$MODE" != --settings ]; then
  warn "not inside tmux/screen: a dropped ssh session would stop this. Ctrl-C now to start it in tmux."
  sleep 10
fi

# ---- Postgres on this box ---------------------------------------------- #
if [ ! -x "$PG_BIN/postgres" ]; then
  say "installing PostgreSQL $RC_PG_MAJOR (PGDG)"
  apt-get install -yq curl ca-certificates >/dev/null
  install -d /usr/share/postgresql-common/pgdg
  curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -yq "postgresql-${RC_PG_MAJOR}" "postgresql-client-${RC_PG_MAJOR}"
fi
if [ ! -d "$PG_CONF_DIR" ]; then
  say "creating cluster ${RC_PG_MAJOR}/${RC_PG_CLUSTER} (its data is replaced by the copy)"
  pg_createcluster "$RC_PG_MAJOR" "$RC_PG_CLUSTER" --port="$RC_PG_PORT" >/dev/null
fi

# ---- the primary ------------------------------------------------------- #
ping -c 2 -W 2 "$RC_PEER_WG" >/dev/null 2>&1 || die "no answer from $RC_PEER_WG -- run wg_setup.sh on both boxes"
peer_up || die "Postgres on $RC_PEER_WG does not answer -- run primary_prepare.sh (--restart) there"
peer_in_recovery && die "the other box is itself a standby; build from the primary"
write_pgpass

# ---- settings ---------------------------------------------------------- #
# ★ COPIED FROM THE PRIMARY, NOT TYPED HERE. A hot standby refuses to run
#   with max_connections, max_worker_processes, max_wal_senders,
#   max_prepared_transactions or max_locks_per_transaction below the
#   primary's, and the site wants the same memory and planner settings on
#   either box. 95- sorts after 90-, so the primary's values win.
sync_settings() {
  local mem_kb sb_kb ecs_kb body
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  body="$(psql_peer -d postgres -c "
    SELECT name || ' = ' || quote_literal(current_setting(name)) FROM pg_settings
    WHERE name IN ('max_connections','max_worker_processes','max_wal_senders',
                   'max_prepared_transactions','max_locks_per_transaction','max_parallel_workers',
                   'max_parallel_workers_per_gather','max_parallel_maintenance_workers',
                   'work_mem','maintenance_work_mem','random_page_cost','effective_io_concurrency',
                   'jit','max_wal_size','checkpoint_timeout','default_statistics_target')
    ORDER BY name")"
  # memory sized to THIS box: the primary's, but at most 1/4 and 3/4 of RAM
  sb_kb="$(psql_peer -d postgres -c "SELECT setting::bigint * 8 FROM pg_settings WHERE name = 'shared_buffers'")"
  ecs_kb="$(psql_peer -d postgres -c "SELECT setting::bigint * 8 FROM pg_settings WHERE name = 'effective_cache_size'")"
  [ "$sb_kb" -gt $((mem_kb / 4)) ] && sb_kb=$((mem_kb / 4))
  [ "$ecs_kb" -gt $((mem_kb * 3 / 4)) ] && ecs_kb=$((mem_kb * 3 / 4))
  body="$(printf '# deploy/replica/standby_build.sh -- the primary'"'"'s settings (%s), copied %s\n%s\nshared_buffers = %sMB\neffective_cache_size = %sMB' \
          "$RC_PEER_NAME" "$(date -u +%FT%TZ)" "$body" "$((sb_kb / 1024))" "$((ecs_kb / 1024))")"
  mkdir -p "$PG_CONF_DIR/conf.d"
  grep -Eq "^\s*include_dir\s*=\s*'conf.d'" "$PG_CONF_DIR/postgresql.conf" \
    || printf "\ninclude_dir = 'conf.d'    # deploy/replica\n" >> "$PG_CONF_DIR/postgresql.conf"
  # the date line differs every run; compare the rest
  if [ "$(tail -n +2 "$PG_CONF_DIR/conf.d/95-racecast-from-primary.conf" 2>/dev/null)" != "$(printf '%s\n' "$body" | tail -n +2)" ]; then
    printf '%s\n' "$body" > "$PG_CONF_DIR/conf.d/95-racecast-from-primary.conf"
    say "settings copied from the primary (conf.d/95-racecast-from-primary.conf)"
  fi
  [ "$(psql_peer -d postgres -c 'SHOW data_checksums')" = on ] || RC_NEED_WAL_LOG_HINTS=1
  write_if_changed "$PG_CONF_DIR/conf.d/90-racecast-replication.conf" "$(replication_conf)" \
    && say "conf.d/90-racecast-replication.conf written" || true
  put_block "$PG_CONF_DIR/pg_hba.conf" "racecast replica (deploy/replica)" "$(printf '%s\n%s' \
    "host replication ${RC_REPL_USER} ${RC_PEER_WG}/32 scram-sha-256" \
    "host all         postgres       ${RC_PEER_WG}/32 scram-sha-256")"
  chown -R postgres:postgres "$PG_CONF_DIR"
}

standby_conf() {
  cat <<EOF
# deploy/replica/standby_build.sh -- this box streams from ${RC_PEER_NAME}.
# promote.sh removes this file when this box becomes the primary.
primary_conninfo = 'host=${RC_PEER_WG} port=${RC_PG_PORT} user=${RC_REPL_USER} passfile=${PG_PASSFILE} application_name=${SELF_SLOT} keepalives_idle=30'
primary_slot_name = '${SELF_SLOT}'
recovery_target_timeline = 'latest'
EOF
}

receiver_status() {
  psql_local -d postgres -c "SELECT coalesce((SELECT status FROM pg_stat_wal_receiver), 'none')" 2>/dev/null || echo down
}

wait_streaming() {
  say "waiting for the standby to stream (up to ${RC_WAIT_STREAM_S:-900} s)"
  local i st
  for i in $(seq 1 "${RC_WAIT_STREAM_S:-900}"); do
    if local_up; then
      st="$(receiver_status)"
      if [ "$st" = streaming ]; then
        say "streaming. Replay lag now:"
        psql_local -d postgres -c "SELECT '  ' || coalesce(round(extract(epoch FROM now() - pg_last_xact_replay_timestamp()))::text, '?') || ' s behind the last commit replayed'"
        return 0
      fi
    fi
    [ $((i % 30)) = 0 ] && say "  still waiting (receiver: ${st:-starting}) -- journalctl -u $PG_UNIT -n 20"
    sleep 1
  done
  die "not streaming after ${RC_WAIT_STREAM_S:-900} s -- journalctl -u $PG_UNIT -n 50"
}

finish_standby() {
  systemctl unmask "$PG_UNIT" >/dev/null 2>&1 || true        # fence.sh masked an old primary
  echo auto > "$PG_CONF_DIR/start.conf"
  install -o postgres -g postgres -m 600 /dev/null "$PG_DATA/standby.signal"
  write_if_changed "$PG_CONF_DIR/conf.d/91-racecast-standby.conf" "$(standby_conf)" || true
  chown postgres:postgres "$PG_CONF_DIR/conf.d/91-racecast-standby.conf"
  systemctl enable "$PG_UNIT" >/dev/null 2>&1 || true
  systemctl start "$PG_UNIT"
  wait_streaming
  writer_timers_off                     # a standby runs no pipeline, backup or scraper
  say "done. This box is a hot standby of $RC_PEER_NAME."
  say "next: sudo bash deploy/replica/standby_site.sh (the site here), then install_services.sh"
}

ensure_slot() {   # ensure_slot [fresh] -- the slot this box streams through, on the primary
  local exists active
  exists="$(psql_peer -d postgres -c "SELECT count(*) FROM pg_replication_slots WHERE slot_name = '$SELF_SLOT'")"
  if [ "$exists" = 1 ]; then
    active="$(psql_peer -d postgres -c "SELECT active FROM pg_replication_slots WHERE slot_name = '$SELF_SLOT'")"
    [ "$active" = t ] && die "slot $SELF_SLOT on the primary is in use -- is another copy of this box streaming?"
    if [ "${1:-}" = fresh ]; then
      psql_peer -d postgres -c "SELECT pg_drop_replication_slot('$SELF_SLOT')" >/dev/null
      exists=0
    fi
  fi
  if [ "$exists" = 0 ]; then
    psql_peer -d postgres -c "SELECT pg_create_physical_replication_slot('$SELF_SLOT', true)" >/dev/null
    say "slot $SELF_SLOT made on the primary (it keeps WAL for this box, at most ${RC_MAX_SLOT_WAL_KEEP:-200GB})"
  fi
}

# ---- what is here now -------------------------------------------------- #
HAS_DATA=0; IS_STANDBY=0; LOCAL_MB=0
if [ -f "$PG_DATA/PG_VERSION" ]; then
  HAS_DATA=1
  [ -f "$PG_DATA/standby.signal" ] && IS_STANDBY=1
  if local_up && [ "$IS_STANDBY" = 0 ]; then
    LOCAL_MB="$(psql_local -d postgres -c "SELECT coalesce(sum(pg_database_size(datname)),0)/1048576 FROM pg_database WHERE datname NOT IN ('template0','template1','postgres')")"
  elif [ "$IS_STANDBY" = 0 ]; then
    LOCAL_MB="$(du -sm "$PG_DATA/base" 2>/dev/null | cut -f1)"
  fi
fi

sync_settings

if [ "$MODE" = --settings ]; then
  if local_up; then
    psql_local -d postgres -c "SELECT pg_reload_conf()" >/dev/null
    sleep 1
    PENDING="$(psql_local -d postgres -c "SELECT string_agg(name, ', ') FROM pg_settings WHERE pending_restart")"
    if [ -n "$PENDING" ]; then
      say "restarting this standby for: $PENDING (the site's reads go to the primary meanwhile)"
      systemctl restart "$PG_UNIT"
      wait_streaming
    fi
  fi
  say "settings in sync"
  exit 0
fi

if [ "$MODE" = build ] && [ "$IS_STANDBY" = 1 ] && local_up && [ "$(receiver_status)" = streaming ]; then
  psql_local -d postgres -c "SELECT pg_reload_conf()" >/dev/null
  say "already a streaming standby of $RC_PEER_NAME; settings re-synced. (--rebuild copies again.)"
  exit 0
fi

if [ "$IS_STANDBY" = 0 ] && [ "$LOCAL_MB" -gt 100 ]; then
  case "$MODE" in
    --rewind) ;;
    --rebuild) [ "${RC_DISCARD_LOCAL_PRIMARY:-}" = yes ] \
                 || die "this box holds a ${LOCAL_MB} MB database that is NOT a standby. If it is the old
           primary, use --rewind. To throw it away: RC_DISCARD_LOCAL_PRIMARY=yes ... --rebuild" ;;
    *) die "this box holds a ${LOCAL_MB} MB database that is not a standby (the old primary?).
           Use --rewind for an old primary, or see --rebuild." ;;
  esac
fi

# A primary that is about to become a standby must not run the pipeline
# against itself while it does, nor serve writes.
writer_timers_off

# ---- --rewind: an old primary rejoins ----------------------------------- #
if [ "$MODE" = --rewind ]; then
  [ "$HAS_DATA" = 1 ] || die "nothing here to rewind; run without --rewind"
  say "stopping the local cluster"
  systemctl stop "$PG_UNIT"
  ensure_slot
  say "pg_rewind from $RC_PEER_NAME (only the blocks that changed since the two parted)"
  if ! sudo -u postgres "$PG_BIN/pg_rewind" --target-pgdata="$PG_DATA" \
        --source-server="host=$RC_PEER_WG port=$RC_PG_PORT user=postgres dbname=postgres passfile=$PG_PASSFILE" \
        --config-file="$PG_CONF_DIR/postgresql.conf" --progress; then
    die "pg_rewind failed (see above). Fall back to a full copy:
           RC_DISCARD_LOCAL_PRIMARY=yes bash $0 --rebuild"
  fi
  finish_standby
  exit 0
fi

# ---- build / --rebuild: a full copy ------------------------------------ #
[ "$(psql_peer -d postgres -c "SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')")" = 0 ] \
  || die "the primary has extra tablespaces; this script copies the data directory only"
PEER_GB="$(psql_peer -d postgres -c "SELECT ceil(sum(pg_database_size(datname)) / 1073741824.0) FROM pg_database")"
FREE_GB="$(df -BG --output=avail "$(dirname "$PG_DATA")" | tail -1 | tr -dc 0-9)"
NEED_GB=$(( PEER_GB * 13 / 10 + 50 ))          # + 30% for a pipeline's shadow tables, + WAL
say "the primary holds ${PEER_GB} GB; free here: ${FREE_GB} GB (want ${NEED_GB} GB)"
[ "$FREE_GB" -ge "$NEED_GB" ] || die "not enough disk: a pipeline swap briefly needs room for a second copy of its biggest tables"

say "stopping the local cluster and emptying $PG_DATA"
systemctl stop "$PG_UNIT" 2>/dev/null || true
case "$PG_DATA" in /var/lib/postgresql/*/*) ;; *) die "unexpected data directory $PG_DATA" ;; esac
find "$PG_DATA" -mindepth 1 -delete
ensure_slot fresh

say "pg_basebackup from $RC_PEER_NAME -- the long part ($(date))"
BB_ARGS=(-d "host=$RC_PEER_WG port=$RC_PG_PORT user=$RC_REPL_USER passfile=$PG_PASSFILE application_name=${SELF_SLOT}_copy keepalives_idle=30"
         -D "$PG_DATA" -X stream -S "$SELF_SLOT" -c "${RC_BB_CHECKPOINT:-fast}" -P -v)
[ -n "${RC_BB_MAX_RATE:-}" ] && BB_ARGS+=(-r "$RC_BB_MAX_RATE")
[ -n "${RC_BB_COMPRESS:-}" ] && BB_ARGS+=(--compress="$RC_BB_COMPRESS")
[ "${RC_BB_NO_VERIFY:-0}" = 1 ] && BB_ARGS+=(--no-verify-checksums)
t0=$(date +%s)
if ! sudo -u postgres "$PG_BIN/pg_basebackup" "${BB_ARGS[@]}"; then
  die "pg_basebackup failed (see above). A 'checksum verification failed' line names a damaged page ON THE PRIMARY:
           send it to the owner before anything else. Otherwise re-run:  bash $0 --rebuild"
fi
say "copied in $(( ($(date +%s) - t0) / 60 )) min"
finish_standby
