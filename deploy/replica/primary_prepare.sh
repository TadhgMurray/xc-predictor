#!/usr/bin/env bash
# deploy/replica/primary_prepare.sh -- let the other box stream this box's
# WAL. Run on the PRIMARY, after wg_setup.sh.
#
#   sudo bash deploy/replica/primary_prepare.sh            # write config, reload, report
#   sudo bash deploy/replica/primary_prepare.sh --restart  # ... and restart Postgres if needed
#
# What it does (each step only if not already so):
#   - the replication role ($RC_REPL_USER) with RC_REPL_PASSWORD;
#   - pg_hba.conf: that role (replication) and postgres (the standby site's
#     writes, pg_rewind) from the other box's TUNNEL address only;
#   - conf.d/90-racecast-replication.conf (common.sh replication_conf):
#     listen on localhost + the tunnel address, slots, WAL kept for a slot
#     at most RC_MAX_SLOT_WAL_KEEP, zstd WAL compression;
#   - reload; and say whether a RESTART is still needed (listen_addresses,
#     wal_log_hints, max_wal_senders... take effect only on a restart).
#
# ! THE RESTART IS THE ONE INTERRUPTION. With --restart it runs a CHECKPOINT
#   first (so the restart itself is seconds, not a minute of flushing 16 GB
#   of shared buffers) and then restarts the cluster: the site answers 503
#   for ~10-30 s and the edge keeps serving what it has cached. It refuses
#   while a pipeline holds .pipeline.lock. Without --restart, pick your own
#   moment:  sudo -u postgres psql -c CHECKPOINT && systemctl restart postgresql@18-main
# ! NOT ON A STANDBY: it stops if this cluster is in recovery.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
: "${RC_REPL_PASSWORD:?set RC_REPL_PASSWORD in $RC_ENV}"
RESTART=0
[ "${1:-}" = "--restart" ] && RESTART=1

local_up || die "the local Postgres ($PG_UNIT) is not answering"
local_in_recovery && die "this cluster is a standby; run this on the primary"

say "checks"
WAL_LEVEL="$(psql_local -d postgres -c 'SHOW wal_level')"
[ "$WAL_LEVEL" = minimal ] && warn "wal_level is minimal: a standby needs replica (set below; restart needed)"
CHECKSUMS="$(psql_local -d postgres -c 'SHOW data_checksums')"
if [ "$CHECKSUMS" != on ]; then
  warn "data checksums are off: turning on wal_log_hints so pg_rewind can rebuild this box later"
  RC_NEED_WAL_LOG_HINTS=1
fi
TS="$(psql_local -d postgres -c "SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')")"
[ "$TS" = 0 ] || warn "$TS extra tablespace(s): standby_build.sh copies only the data directory; map them by hand"
UNLOGGED="$(psql_local -d "$RC_DB_NAME" -c "SELECT string_agg(relname, ', ' ORDER BY relname) FROM pg_class
             WHERE relpersistence = 'u' AND relkind IN ('r','p','m') AND relnamespace <> 'pg_toast'::regnamespace")"
if [ -n "$UNLOGGED" ]; then
  say "unlogged tables (empty on a standby; a site read of one is re-run on the primary): $UNLOGGED"
fi

say "replication role $RC_REPL_USER"
if [ "$(psql_local -d postgres -c "SELECT count(*) FROM pg_roles WHERE rolname = '$RC_REPL_USER'")" = 0 ]; then
  psql_local -d postgres -c "CREATE ROLE \"$RC_REPL_USER\" WITH REPLICATION LOGIN"
fi
# ! THE PASSWORD NEVER GOES ON A COMMAND LINE (ps shows those to every
#   user): psql reads it from the environment (\getenv) in a script on stdin.
: "${XCP_DB_PASSWORD:?no XCP_DB_PASSWORD in $XCP_ENV}"
PGPASSWORD="$XCP_DB_PASSWORD" psql -X -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$RC_PG_PORT" \
  -U postgres -d postgres >/dev/null <<'SQL'
\getenv pw RC_REPL_PASSWORD
\getenv u RC_REPL_USER
ALTER ROLE :"u" WITH REPLICATION LOGIN PASSWORD :'pw';
SQL

say "pg_hba.conf"
HBA="$(psql_local -d postgres -c 'SHOW hba_file')"
put_block "$HBA" "racecast replica (deploy/replica)" "$(printf '%s\n%s' \
  "host replication ${RC_REPL_USER} ${RC_PEER_WG}/32 scram-sha-256" \
  "host all         postgres       ${RC_PEER_WG}/32 scram-sha-256")"
[ "$BLOCK_CHANGED" = 1 ] && say "  added the other box's tunnel address"

say "conf.d/90-racecast-replication.conf"
CONF_D="$PG_CONF_DIR/conf.d"
mkdir -p "$CONF_D"
grep -Eq "^\s*include_dir\s*=\s*'conf.d'" "$PG_CONF_DIR/postgresql.conf" \
  || printf "\ninclude_dir = 'conf.d'    # deploy/replica\n" >> "$PG_CONF_DIR/postgresql.conf"
if write_if_changed "$CONF_D/90-racecast-replication.conf" "$(replication_conf)"; then
  chown postgres:postgres "$CONF_D/90-racecast-replication.conf"
  say "  written"
fi

psql_local -d postgres -c "SELECT pg_reload_conf()" >/dev/null
sleep 1
ERRS="$(psql_local -d postgres -c "SELECT string_agg(sourcefile || ':' || sourceline || ' ' || error, '; ') FROM pg_file_settings WHERE error IS NOT NULL")"
[ -z "$ERRS" ] || die "Postgres rejected a setting: $ERRS"

# ! A LATER LINE WINS. server_setup.sh appends its tuning block AFTER the
#   include_dir line, and ALTER SYSTEM (postgresql.auto.conf) beats both. If
#   anything there sets one of ours, say so instead of silently losing it.
OVERRIDDEN="$(psql_local -d postgres -c "
  SELECT string_agg(name || ' (from ' || sourcefile || ')', ', ')
  FROM pg_file_settings
  WHERE applied AND name IN ('listen_addresses','wal_level','max_wal_senders','max_replication_slots',
                             'max_slot_wal_keep_size','hot_standby','hot_standby_feedback',
                             'max_standby_streaming_delay','wal_compression')
    AND sourcefile NOT LIKE '%90-racecast-replication.conf'")"
[ -z "$OVERRIDDEN" ] || warn "set elsewhere, and that wins over conf.d: $OVERRIDDEN"

PENDING="$(psql_local -d postgres -c "SELECT string_agg(name, ', ') FROM pg_settings WHERE pending_restart")"
if [ -n "$PENDING" ]; then
  if [ "$RESTART" = 1 ]; then
    pipeline_running && die "a pipeline holds .pipeline.lock; restart when it is done"
    say "restart needed for: $PENDING -- checkpoint, then restart"
    psql_local -d postgres -c "CHECKPOINT"
    systemctl restart "$PG_UNIT"
    for _ in $(seq 1 60); do local_up && break; sleep 1; done
    local_up || die "Postgres did not come back -- journalctl -u $PG_UNIT -n 50"
    say "  back up"
  else
    warn "a RESTART is needed for: $PENDING"
    warn "  run again with --restart at a quiet moment (no pipeline running), or by hand:"
    warn "  sudo -u postgres psql -c CHECKPOINT && systemctl restart $PG_UNIT"
  fi
fi

say "now"
psql_local -d postgres -c "SELECT 'listen_addresses = ' || current_setting('listen_addresses'),
                                  'wal_level = ' || current_setting('wal_level'),
                                  'max_slot_wal_keep_size = ' || current_setting('max_slot_wal_keep_size'),
                                  'max_connections = ' || current_setting('max_connections')" | tr '|' '\n' | sed 's/^/  /'
psql_local -d postgres -c "SELECT '  slot ' || slot_name || ': ' || coalesce(wal_status,'?') || CASE WHEN active THEN ', streaming' ELSE ', idle' END
                           FROM pg_replication_slots" || true
USED="$(psql_local -d postgres -c "SELECT count(*) FROM pg_stat_activity")"
say "  connections in use now: $USED (the standby site adds up to 24 idle + a few for writes; keep headroom)"
say "done. Next, on the other box: sudo bash deploy/replica/standby_build.sh"
