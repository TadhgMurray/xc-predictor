#!/usr/bin/env bash
# deploy/race_model_weekly.sh -- rebuild the race model (model/race_rows.py
# then model/race_trees.py) on this week's results, and swap it in only if
# it still beats the ratings. Installed by deploy/install_race_model_timer.sh.
#
#   sudo bash deploy/race_model_weekly.sh            # by hand
#   sudo bash deploy/race_model_weekly.sh --dry-run  # say what it would do
#
# ★ WHY WEEKLY (review 2026-10). The page's race model reads each runner's,
#   team's and course's record as of the table it was fitted on
#   (race_trees own / team / ctx / course_level). A runner's three races
#   since then are invisible to it until the table is rebuilt.
#
# ★ THE SPLIT ROLLS WITH THE SEASONS: the trees train on every season before
#   the last completed August; the race level, the ranges and the spread are
#   fitted on everything since (in October 2026: before 2025-08-01, then
#   2025-08-01 to yesterday). That is the setup the sealed 2025-26 check
#   scored (docs/PLAN-MODEL-2026-10.md: +1.29 points over the ratings).
#   The gate below reads that held-out score; then --refit-all refits the
#   served trees on every season, the held-out one too (owner, 2026-10-28:
#   "why not everything?"), keeping the held-out ranges.
#
# ! NEVER A WORSE MODEL ON THE PAGE. The new pickle replaces the served one
#   only when its race-morning gain over the ratings is at least
#   XCP_RACE_MODEL_MIN_GAIN points (default 1.0); otherwise the old one
#   stays and the admins get the log (scripts/notify_owner.py). The swap is
#   a rename, and race_model.loadModel reloads on the new mtime: no restart.
#
# ! ONE AT A TIME WITH THE PIPELINE: the table build reads ratings, so it
#   waits (up to XCP_RACE_MODEL_WAIT_MIN, default 180) for the pipeline lock
#   and holds it only while it reads the database (about 1.5 h). The fit
#   after it is offline.
set -u -o pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
cd "$ROOT" || exit 1
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

DIR="${XCP_PRED_DIR:-/srv/pred}"
MODEL="${XCP_RACE_MODEL:-$DIR/race_trees.pkl}"
MIN_GAIN="${XCP_RACE_MODEL_MIN_GAIN:-1.0}"
WAIT_MIN="${XCP_RACE_MODEL_WAIT_MIN:-180}"
TO="$(date -u -d yesterday +%F)"
Y=$(date -u +%Y); M=$(date -u +%-m)
VAL_FROM="$(( M >= 8 ? Y - 1 : Y - 2 ))-08-01"
TS="$(date -u +%Y%m%d_%H%M)"
LOGDIR="$ROOT/logs/race_model_$TS"
ROWS_NEW="$DIR/rows_weekly.new.npz"
PKL_NEW="$MODEL.new"

echo "[race model] table to $TO, trees before $VAL_FROM; served file $MODEL"
if [ "$DRY" -eq 1 ]; then
  echo "  would build $ROWS_NEW, fit $PKL_NEW, and swap it in at +$MIN_GAIN points or more"
  exit 0
fi
mkdir -p "$LOGDIR" "$DIR"
fail() {
  echo "[race model] FAILED: $1 -- the served model is unchanged" | tee -a "$LOGDIR/summary.log"
  "$PY" scripts/notify_owner.py "$LOGDIR" "$1" 2>&1 | tail -3 || true
  rm -f "$ROWS_NEW" "$ROWS_NEW".records-*.npz "$PKL_NEW"
  exit 1
}

exec 9>"$ROOT/.pipeline.lock"
if ! flock -w $(( WAIT_MIN * 60 )) 9; then
  echo "[race model] the pipeline held $ROOT/.pipeline.lock for $WAIT_MIN min -- skipping this week"
  exit 0
fi
"$PY" -u model/race_rows.py --out "$ROWS_NEW" --to "$TO" --val-from "$VAL_FROM" \
      --train-frac 1 > "$LOGDIR/race_rows.log" 2>&1 || fail race_rows
flock -u 9
exec 9>&-

"$PY" -u model/race_trees.py --rows "$ROWS_NEW" --val-from "$VAL_FROM" \
      --without dist_vs_course --refit-all --out "$PKL_NEW" > "$LOGDIR/race_trees.log" 2>&1 \
      || fail race_trees
GAIN="$(grep -m1 'model - ratings, pairs:' "$LOGDIR/race_trees.log" \
        | sed -E 's/.*pairs: ([+-][0-9.]+).*/\1/')"
echo "[race model] race-morning gain over the ratings: ${GAIN:-none} points" \
  | tee -a "$LOGDIR/summary.log"
awk -v g="${GAIN:-x}" -v m="$MIN_GAIN" 'BEGIN { exit !(g + 0 == g && g + 0 >= m + 0) }' \
  || { echo "  under +$MIN_GAIN" > "$LOGDIR/gate.log"; fail gate; }

[ -f "$MODEL" ] && cp -p "$MODEL" "$MODEL.prev"
mv -f "$PKL_NEW" "$MODEL"
mv -f "$ROWS_NEW" "$DIR/rows_weekly.npz"
# the records cache (race_trees loadTable) belongs to the old table
rm -f "$DIR/rows_weekly.npz".records-*.npz "$ROWS_NEW".records-*.npz
echo "[race model] swapped in $MODEL (the last one is $MODEL.prev)" | tee -a "$LOGDIR/summary.log"
