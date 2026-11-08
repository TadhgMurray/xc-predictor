# deploy/replica/common.sh -- sourced by every script in deploy/replica.
# Not run on its own.
#
# Reads /etc/racecast-replica.env (replica.env.example says what goes in it)
# and /etc/xc-predictor.env (the postgres password), and gives the scripts
# one way to talk to the local cluster and to the other box's.

RC_ENV="${RC_ENV:-/etc/racecast-replica.env}"
XCP_ENV="${XCP_ENV:-/etc/xc-predictor.env}"
RC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

say()  { printf '[replica] %s\n' "$*"; }
warn() { printf '[replica] ! %s\n' "$*" >&2; }
die()  { printf '[replica] STOP: %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" = 0 ] || die "run as root (sudo bash $0 ...)"; }

load_env() {
  [ -f "$RC_ENV" ] || die "no $RC_ENV -- copy deploy/replica/replica.env.example there and fill it in"
  set -a; . "$RC_ENV"; set +a
  if [ -f "$XCP_ENV" ]; then
    # only the password: the rest of that file is the site's and the pipeline's
    XCP_DB_PASSWORD="$(grep -oP '^XCP_DB_PASSWORD=\K.*' "$XCP_ENV" | tail -1 | tr -d "'\"")"
    export XCP_DB_PASSWORD
  fi
  : "${RC_SELF_NAME:?set RC_SELF_NAME in $RC_ENV}" "${RC_PEER_NAME:?}" \
    "${RC_SELF_WG:?}" "${RC_PEER_WG:?}" "${RC_PG_MAJOR:?}"
  RC_PG_CLUSTER="${RC_PG_CLUSTER:-main}"
  RC_PG_PORT="${RC_PG_PORT:-5432}"
  RC_DB_NAME="${RC_DB_NAME:-xc_predictor}"
  RC_REPL_USER="${RC_REPL_USER:-replicator}"
  RC_WG_IF="${RC_WG_IF:-wg0}"
  RC_WG_PORT="${RC_WG_PORT:-51820}"
  PG_BIN="/usr/lib/postgresql/${RC_PG_MAJOR}/bin"
  PG_CONF_DIR="/etc/postgresql/${RC_PG_MAJOR}/${RC_PG_CLUSTER}"
  PG_DATA="/var/lib/postgresql/${RC_PG_MAJOR}/${RC_PG_CLUSTER}"
  PG_UNIT="postgresql@${RC_PG_MAJOR}-${RC_PG_CLUSTER}"
  PG_PASSFILE="/var/lib/postgresql/.pgpass"
  SELF_SLOT="rc_${RC_SELF_NAME}"     # the slot THIS box streams through, on the primary
  PEER_SLOT="rc_${RC_PEER_NAME}"
}

# psql on this box's cluster, as postgres, through the socket
psql_local() {
  sudo -u postgres psql -X -q -At -v ON_ERROR_STOP=1 -p "$RC_PG_PORT" "$@"
}

# psql on the other box's cluster, over the tunnel, as postgres
psql_peer() {
  [ -n "${XCP_DB_PASSWORD:-}" ] || die "no XCP_DB_PASSWORD (from $XCP_ENV) to reach the other box's Postgres"
  PGPASSWORD="$XCP_DB_PASSWORD" PGCONNECT_TIMEOUT=5 psql -X -q -At -v ON_ERROR_STOP=1 \
    -h "$RC_PEER_WG" -p "$RC_PG_PORT" -U postgres "$@"
}

local_up()  { sudo -u postgres "$PG_BIN/pg_isready" -q -p "$RC_PG_PORT" 2>/dev/null; }
peer_up()   { "$PG_BIN/pg_isready" -q -t 5 -h "$RC_PEER_WG" -p "$RC_PG_PORT" 2>/dev/null; }
local_in_recovery() { [ "$(psql_local -d postgres -c 'SELECT pg_is_in_recovery()')" = t ]; }
peer_in_recovery()  { [ "$(psql_peer -d postgres -c 'SELECT pg_is_in_recovery()')" = t ]; }

# put_block FILE NAME BODY -- the lines between "# >>> NAME >>>" and
# "# <<< NAME <<<" become BODY; the rest of FILE is left alone. Sets
# BLOCK_CHANGED=1 when the file changed. Keeps the file's owner and mode.
put_block() {
  local f="$1" name="$2" body="$3" tmp
  BLOCK_CHANGED=0
  [ -f "$f" ] || { touch "$f"; }
  tmp="$(mktemp)"
  awk -v b="# >>> $name >>>" -v e="# <<< $name <<<" \
      '$0==b{skip=1; next} $0==e{skip=0; next} !skip{print}' "$f" > "$tmp"
  printf '%s\n%s\n%s\n' "# >>> $name >>>" "$body" "# <<< $name <<<" >> "$tmp"
  if ! cmp -s "$tmp" "$f"; then
    cat "$tmp" > "$f"
    BLOCK_CHANGED=1
  fi
  rm -f "$tmp"
}

# write_if_changed FILE CONTENT -- returns 0 when it wrote, 1 when unchanged
write_if_changed() {
  local f="$1" content="$2"
  if [ -f "$f" ] && [ "$(cat "$f")" = "$content" ]; then
    return 1
  fi
  printf '%s\n' "$content" > "$f"
  return 0
}

# the .pgpass the postgres OS user reads: the walreceiver's password for the
# replication user, and the superuser for pg_rewind and the checks
write_pgpass() {
  : "${RC_REPL_PASSWORD:?set RC_REPL_PASSWORD in $RC_ENV}"
  local body
  body="$(printf '%s\n' \
    "${RC_PEER_WG}:${RC_PG_PORT}:replication:${RC_REPL_USER}:${RC_REPL_PASSWORD}" \
    "${RC_PEER_WG}:${RC_PG_PORT}:*:postgres:${XCP_DB_PASSWORD:-}")"
  install -o postgres -g postgres -m 600 /dev/null "$PG_PASSFILE.new"
  printf '%s\n' "$body" > "$PG_PASSFILE.new"
  mv "$PG_PASSFILE.new" "$PG_PASSFILE"
}

# The replication settings. ONE file, the same on both boxes and in both
# roles, so a switchover changes no configuration: a primary ignores the
# standby settings and a standby the primary ones.
replication_conf() {
  local hints=""
  if [ "${RC_NEED_WAL_LOG_HINTS:-0}" = 1 ]; then
    hints="wal_log_hints = on                # pg_rewind needs it when data checksums are off"
  fi
  cat <<EOF
# deploy/replica -- written by primary_prepare.sh / standby_build.sh; edit
# those, not this file (docs/REPLICA-PLAN.md).
listen_addresses = 'localhost,${RC_SELF_WG}'   # the tunnel only; never the public address
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
max_slot_wal_keep_size = ${RC_MAX_SLOT_WAL_KEEP:-200GB}
wal_keep_size = 4GB              # a short gap without the slot (pg_rewind, a slot rebuilt) needs no base backup
wal_compression = zstd           # full-page images in the WAL, a third of their size on the wire
wal_sender_timeout = 60s
${hints}
# as a standby
hot_standby = on
hot_standby_feedback = on        # the site's queries (55 s at most) hold vacuum back that long, no longer
max_standby_streaming_delay = 60s   # a replayed swap waits out a site query (statement_timeout 55 s) rather than kill it
max_standby_archive_delay = 60s
wal_receiver_timeout = 60s
idle_in_transaction_session_timeout = 10min
EOF
}

# stop the writers this repo installs (the pipeline, backups, the stock
# watch, the recorders): only ever on the box that is the primary
WRITER_TIMERS="xcp-nightly xcp-backup xcp-ovh-watch xcp-kailo-record xcp-race-model"
writer_timers_off() {
  local t
  for t in $WRITER_TIMERS; do
    if systemctl list-unit-files "$t.timer" >/dev/null 2>&1 && systemctl is-enabled "$t.timer" >/dev/null 2>&1; then
      systemctl disable --now "$t.timer" >/dev/null 2>&1 && say "timer off: $t"
    fi
  done
}

pipeline_running() {
  # the pipeline holds .pipeline.lock (deploy/run_pipeline.sh, backup_db.sh)
  local lock="${RC_ROOT}/.pipeline.lock"
  [ -f "$lock" ] || return 1
  ! flock -n "$lock" true
}
