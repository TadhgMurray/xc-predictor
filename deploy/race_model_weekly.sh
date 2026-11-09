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
# ★ THE SITE FIRST, HERE TOO (2026-11-09). The unit's Nice=19 reaches this
#   script and Python, never the Postgres backends doing the table build's
#   1.5 h of reads -- those ran at full priority with the server's parallel
#   workers. XCP_DB_QUIET=1 is the pipeline's own cap (scripts/database.py:
#   capped workers and memory, the backend reniced). And the fit's OpenMP
#   (sklearn's HistGradientBoosting) took one thread per core by default --
#   every core, on a box that has thrown machine-check faults under full
#   load; XCP_THREADS (default 8) caps it, as for the pipeline's fit.
export XCP_DB_QUIET="${XCP_DB_QUIET:-1}"
XCP_THREADS="${XCP_THREADS:-8}"
export OMP_NUM_THREADS="$XCP_THREADS" OPENBLAS_NUM_THREADS="$XCP_THREADS" \
       MKL_NUM_THREADS="$XCP_THREADS" NUMEXPR_NUM_THREADS="$XCP_THREADS"

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
# ★ THE TABLE BUILD AND THE FITS GO THROUGH THE MEMORY LEDGER (2026-11-09,
#   scripts/jobrun.py; docs/PIPELINE-ORDER.md): each reserves its size
#   (XCP_RACE_ROWS_GB, XCP_RACE_TREES_GB) and waits for it, up to 4 h, so a
#   fit never lands on top of an engine replay or the go-live; the peak each
#   one reached is written beside its log (.mem).
JR=("$PY" -u "$ROOT/scripts/jobrun.py" --wait-min 240)
say() { echo "[race model] $(date -u +%H:%M) UTC  $*" | tee -a "$LOGDIR/summary.log"; }
fail() {
  echo "[race model] FAILED: $1 -- the served model is unchanged" | tee -a "$LOGDIR/summary.log"
  "$PY" scripts/notify_owner.py "$LOGDIR" "$1" 2>&1 | tail -3 || true
  rm -f "$ROWS_NEW" "$ROWS_NEW".records-*.npz "$PKL_NEW"
  exit 1
}

. "$ROOT/deploy/lockctl.sh"
say "waiting for the pipeline lock (up to $WAIT_MIN min)"
if ! lock_take race_model queued $(( WAIT_MIN * 60 )) "race_rows table build (about 1.5 h; xcp-race-model.service): $LOGDIR/race_rows.log"; then
  echo "[race model] the pipeline held $ROOT/.pipeline.lock for $WAIT_MIN min -- skipping this week"
  exit 0
fi
say "building the table (about 1.5 h): tail -f $LOGDIR/race_rows.log"
"${JR[@]}" --name race_rows --gb "${XCP_RACE_ROWS_GB:-20}" --peak-out "$LOGDIR/race_rows.mem" -- \
  "$PY" -u model/race_rows.py --out "$ROWS_NEW" --to "$TO" --val-from "$VAL_FROM" \
      --train-frac 1 > "$LOGDIR/race_rows.log" 2>&1 || fail race_rows
lock_release
say "table built; lock released"

say "fitting, held out then on every season (about 1.5 h): tail -f $LOGDIR/race_trees.log"
"${JR[@]}" --name race_trees --gb "${XCP_RACE_TREES_GB:-30}" --peak-out "$LOGDIR/race_trees.mem" -- \
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

# ★ AND THE NO-RATINGS MODEL (owner, 2026-11-06: "there shouldnt be any
#   hindsight"; docs/PLAN-MODEL-2026-10.md §9): the same table, nothing from
#   the engine's solve. The nightly freezes its forecasts beside the served
#   ones (13h_raw_forecasts, pred_raw_meet_forecast); the site serves it only
#   with XCP_PREDICT_BASIS=race_raw or race_mix. A failure keeps the last
#   file. XCP_RACE_RAW=0 skips it.
RAW="${XCP_RACE_MODEL_RAW:-$(dirname "$MODEL")/race_trees_raw.pkl}"
if [ "${XCP_RACE_RAW:-1}" = "1" ]; then
  say "fitting the no-ratings model (about 1.5 h): tail -f $LOGDIR/race_trees_raw.log"
  if "${JR[@]}" --name race_trees_raw --gb "${XCP_RACE_TREES_GB:-30}" --peak-out "$LOGDIR/race_trees_raw.mem" -- \
       "$PY" -u model/race_trees.py --rows "$DIR/rows_weekly.npz" --val-from "$VAL_FROM" \
       --without dist_vs_course --refit-all --no-ratings --out "$RAW.new" \
       > "$LOGDIR/race_trees_raw.log" 2>&1; then
    [ -f "$RAW" ] && cp -p "$RAW" "$RAW.prev"
    mv -f "$RAW.new" "$RAW"
    say "no-ratings model swapped in: $RAW"
  else
    rm -f "$RAW.new"
    say "no-ratings model failed, the last one kept: $LOGDIR/race_trees_raw.log"
  fi
fi

# ★ THE RANGES, ON WHAT HAPPENED: the factor that makes +-1 sigma hold 68% on
#   the last four weeks' frozen forecasts (scripts/range_calibration.py,
#   race_model.rangeScale). A failure leaves the factor in force.
"$PY" -u scripts/range_calibration.py > "$LOGDIR/range_calibration.log" 2>&1 \
  || echo "  range calibration failed: $LOGDIR/range_calibration.log"
say "ranges: $(tail -1 "$LOGDIR/range_calibration.log")"

# ★ THE QUALIFYING ROUTES, ONCE A SEASON (owner, 2026-11-02: "you can do
#   those"). scripts/qualifier_audit.py --write-routes learns each
#   championship race's rounds from the last COMPLETE season and keeps only
#   the routes that rebuild their own field (keepRoute). When the file was
#   learned from an older season, it is learned again; a failure keeps the
#   old file. racecast/qualifying.py reads it on its next mtime.
ROUTES="${XCP_QUALIFY_ROUTES:-$ROOT/model/data/qualifying_routes.json}"
SEASON=$(( M >= 8 ? Y : Y - 1 ))
FROM_SEASON="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get('from_season', 0))" \
               "$ROUTES" 2>/dev/null || echo 0)"
if [ "${FROM_SEASON:-0}" -lt $(( SEASON - 1 )) ]; then
  say "qualifying routes are from ${FROM_SEASON:-none}: learning them from $(( SEASON - 1 )) (about 20 min)"
  if lock_take qualifier_routes queued $(( WAIT_MIN * 60 )) "qualifier routes (about 20 min): $LOGDIR/qualifier_routes.log"; then
    if "$PY" -u scripts/qualifier_audit.py --season "$SEASON" --max-races 3000 \
         --write-routes "$ROUTES.new" > "$LOGDIR/qualifier_routes.log" 2>&1; then
      mv -f "$ROUTES.new" "$ROUTES"
      say "routes: $(grep -m1 '^wrote' "$LOGDIR/qualifier_routes.log")"
    else
      rm -f "$ROUTES.new"
      say "qualifying routes failed, the old file kept: $LOGDIR/qualifier_routes.log"
    fi
    lock_release
  else
    say "the pipeline held the lock: qualifying routes next week"
  fi
fi
