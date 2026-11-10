#!/usr/bin/env bash
# deploy/rr_archive.sh -- the OnlineRaceResults archive on Race Roster
# (about 600 school cross country meets, 2019-2025), walked once, through the
# pipeline lock. Resumable: run it again and it carries on where it stopped.
#
#   bash deploy/rr_archive.sh            # up to 240 minutes, then stops
#   MINUTES=120 bash deploy/rr_archive.sh
#
# ! QUEUED, like the backup: it takes the lock only when nothing holds it,
#   and it stops by itself. Do not start it within MINUTES of the nightly
#   (01:17 Pacific): the nightly would wait for it.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
MINUTES="${MINUTES:-240}"
cd "$ROOT" || exit 1
. "$ROOT/deploy/lockctl.sh"
LOG="$ROOT/logs/rr_archive_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$ROOT/logs"
if ! lock_take rr_archive queued 600 "rr_archive.sh: Race Roster archive walk, up to ${MINUTES} min ($LOG)"; then
  echo "[rr archive] the lock is busy -- try again later"; exit 1
fi
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
"$PY" -u -m sources.raceroster archive --write --minutes "$MINUTES" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
"$PY" -u scripts/link_uploads.py --source timer --apply 2>&1 | tail -3 | tee -a "$LOG"
lock_release
exit $rc
