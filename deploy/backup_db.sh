#!/usr/bin/env bash
# deploy/backup_db.sh -- dump the database, and the files git does not hold,
# to /srv/backups, and (optionally) copy them off this server.
#
#   sudo bash deploy/backup_db.sh              # one backup now
#   sudo bash deploy/backup_db.sh --verify     # check the newest dump reads back
#   sudo bash deploy/install_backup_timer.sh   # run it every day
#
# ★ WHY (sweep, 2026-09-26). The server was the ONLY copy of the database,
#   of engine/corrections.py (~165 MB, gitignored) and of the fitted files
#   in engine/data/. One disk failure or one bad DROP lost all of it; a
#   rebuild from the scrapers is weeks.
#
# WHAT IT WRITES  /srv/backups/<stamp>/
#   db/           pg_dump directory format, 4 jobs in parallel (restore with
#                 pg_restore -j 4 -d xc_predictor db/ -- see server_restore.sh)
#   files.tar.gz  engine/corrections.py and engine/data/*.pkl *.json
#   MANIFEST      sizes and the dump's table list
# and keeps the newest XCP_BACKUP_KEEP (default 3) locally.
#
# OFF THE SERVER. A copy on the same disk survives a bad DROP, not a dead
# disk. Set XCP_BACKUP_REMOTE in /etc/xc-predictor.env to an rclone remote
# (e.g. b2:racecast-backups, s3:bucket/racecast, gdrive:racecast) after
# `rclone config`; each backup is copied there and remote copies older than
# XCP_BACKUP_REMOTE_DAYS (default 30) are deleted.
#
# ! NEVER DURING A PIPELINE RUN. pg_dump holds a read lock on every table
#   for its whole run, and a pipeline swap (DROP + RENAME) cannot take its
#   lock past that. So this takes the pipeline's own lock file first: it
#   waits up to XCP_BACKUP_WAIT_H hours (default 6) for a running pipeline
#   to finish, and skips the day otherwise. While it runs, a pipeline
#   started by hand stops at once with "another pipeline holds
#   .pipeline.lock" -- start it again when the backup is done.
# ! /etc/xc-predictor.env IS NOT COPIED OFF THE SERVER: it holds the
#   passwords and keys. Keep your own copy of it somewhere safe.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
DEST="${XCP_BACKUP_DIR:-/srv/backups}"
KEEP="${XCP_BACKUP_KEEP:-3}"
WAIT_H="${XCP_BACKUP_WAIT_H:-6}"
DB="${XCP_DB_NAME:-xc_predictor}"
export PGHOST="${XCP_DB_HOST:-127.0.0.1}" PGPORT="${XCP_DB_PORT:-5432}" \
       PGUSER="${XCP_DB_USER:-postgres}" PGPASSWORD="${XCP_DB_PASSWORD:-}"

if [ "${1:-}" = "--verify" ]; then
  last=$(ls -1d "$DEST"/2* 2>/dev/null | tail -1)
  [ -n "$last" ] || { echo "no backups in $DEST"; exit 1; }
  n=$(pg_restore -l "$last/db" | grep -c " TABLE DATA ")
  echo "$last: dump reads back, $n tables of data"
  tar -tzf "$last/files.tar.gz" >/dev/null && echo "$last: files.tar.gz reads back"
  exit 0
fi

exec 9>"$ROOT/.pipeline.lock"
if ! flock -w $((WAIT_H * 3600)) 9; then
  echo "backup: a pipeline held .pipeline.lock for ${WAIT_H}h -- skipped today" >&2
  exit 1
fi

STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$DEST/$STAMP"
mkdir -p "$OUT"
chmod 700 "$DEST"
t0=$(date +%s)
echo "backup: dumping $DB to $OUT/db ..."
nice -n 10 pg_dump -Fd -j 4 -Z 5 -f "$OUT/db" "$DB"
echo "backup: files ..."
( cd "$ROOT" && tar -czf "$OUT/files.tar.gz" \
    engine/corrections.py $(ls engine/data/*.pkl engine/data/*.json 2>/dev/null) \
    2>/dev/null || true )
{
  echo "stamp:    $STAMP"
  echo "database: $DB  ($(pg_restore -l "$OUT/db" | grep -c ' TABLE DATA ') tables)"
  echo "size:     $(du -sh "$OUT/db" | cut -f1) dump, $(du -sh "$OUT/files.tar.gz" | cut -f1) files"
  echo "took:     $(( $(date +%s) - t0 ))s"
  echo "commit:   $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)"
} > "$OUT/MANIFEST"
cat "$OUT/MANIFEST"

# the newest $KEEP stay on this disk
ls -1d "$DEST"/2* | head -n -"$KEEP" | xargs -r rm -rf

flock -u 9                                  # the pipeline may start now

if [ -n "${XCP_BACKUP_REMOTE:-}" ]; then
  if command -v rclone >/dev/null; then
    echo "backup: copying to $XCP_BACKUP_REMOTE/$STAMP ..."
    rclone copy "$OUT" "$XCP_BACKUP_REMOTE/$STAMP" --transfers 4
    rclone delete "$XCP_BACKUP_REMOTE" --min-age "${XCP_BACKUP_REMOTE_DAYS:-30}d" || true
    rclone rmdirs "$XCP_BACKUP_REMOTE" --leave-root || true
    echo "backup: off-server copy done"
  else
    echo "backup: XCP_BACKUP_REMOTE is set but rclone is not installed" >&2
    exit 1
  fi
else
  echo "backup: ! only on this server -- set XCP_BACKUP_REMOTE for an off-server copy"
fi
