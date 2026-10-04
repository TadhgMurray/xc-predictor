#!/usr/bin/env bash
# nightly_update.sh -- the night's new results, scraped, priced and on the
# site by morning, without the four-hour pipeline.
#
#   bash deploy/nightly_update.sh                # scrape, then price and publish
#   bash deploy/nightly_update.sh --no-scrape    # price what is already in
#   bash deploy/nightly_update.sh --dry-run      # print the plan
#   sudo bash deploy/install_nightly_timer.sh    # run it every night
#
# ★ WHY (owner, 2026-10-04: the newest meet on the site was Sep 17 -- the data
#   moved only when someone ran the full pipeline by hand). This is the
#   light half: scrape both feeds, link and flag the new rows, normalise ONLY
#   rows nobody has normalised yet (backfill --new-only), price them AT THEIR
#   COURSE against the last full run's course numbers and scale (fill_ratings
#   --venue), and rebuild the boards and search from that.
#
# ! WHAT A NIGHTLY NUMBER IS NOT. No solve runs, so course difficulties,
#   pool means and athletes' levels are the last full run's; a new race has
#   no race-day term (it reads as a typical day) and no weather until the
#   grid reaches it. The next full pipeline re-rates every row from scratch
#   (the go-live resets the column), so nothing priced here survives it.
#   `engine/fill_ratings.py --check 3000` measures how far this pricing sits
#   from the solve's own ratings on rows the solve did rate.
#
# ! ONE AT A TIME WITH THE PIPELINE: the same lock file. A full run holding
#   it means tonight's update is skipped (exit 0, said in the log), never
#   queued behind it.
set -u -o pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
# ★ THE SCRAPE'S BOUND IS THE NIGHT, NOT A GUESS AT ITS LENGTH. Both
#   launchers stop on their own (the forward walk ends at the 404s, the
#   recent list is finite); this only stops a wedged browser from eating the
#   morning. Started at 01:17 Pacific, three hours leaves the pricing and the
#   boards (about an hour) done before people look.
SCRAPE_MIN="${XCP_NIGHTLY_SCRAPE_MIN:-180}"
SCRAPE=1; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-scrape) SCRAPE=0; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
cd "$ROOT" || exit 1

if [ "$DRY" -eq 0 ]; then
  exec 9>"$ROOT/.pipeline.lock"
  if ! flock -n 9; then
    echo "[nightly] the pipeline holds $ROOT/.pipeline.lock -- skipping tonight"
    exit 0
  fi
fi
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
# the model's settings, as the pipeline loads them (distance mode, weather ...)
[ -f "$ROOT/deploy/solve_env.sh" ] && . "$ROOT/deploy/solve_env.sh"
if [ "$DRY" -eq 0 ] && [ ! -f "$ROOT/engine/corrections.py" ]; then
  echo "[nightly] FATAL: engine/corrections.py missing (the backfill would un-apply every correction)" >&2
  exit 1
fi
export XCP_DB_QUIET="${XCP_DB_QUIET:-1}"
NICE="nice -n ${XCP_NICE:-10}"
command -v ionice >/dev/null 2>&1 && NICE="$NICE ionice -c2 -n7"

TS=$(date +%Y%m%d_%H%M%S)
LOGDIR="$ROOT/logs/nightly_$TS"
mkdir -p "$LOGDIR"
SUMMARY="$LOGDIR/SUMMARY.txt"
FAILED=""
T_START=$(date +%s)

# step <name> <command...>
step() {
  name="$1"; shift
  if [ "$DRY" -eq 1 ]; then echo "  $name : $*"; return 0; fi
  echo ""
  echo "== $name    $(date +%H:%M:%S)"
  t0=$(date +%s)
  $NICE "$@" 2>&1 | tee "$LOGDIR/$name.log"
  rc=$?
  el=$(( $(date +%s) - t0 ))
  if [ "$rc" -ne 0 ]; then
    echo "  $name FAILED (exit $rc) after ${el}s" | tee -a "$SUMMARY"
    FAILED="$FAILED $name"
  else
    echo "  $name ok (${el}s)" | tee -a "$SUMMARY"
  fi
  return "$rc"
}

# ---- 1. scrape: both feeds at once (different sites, separate browsers) -- #
# ! A scrape that hits the bound (timeout's 124) is "stopped for the night",
#   not a failure: whatever it saved is priced below.
scrape() {
  name="$1"; shift
  if [ "$DRY" -eq 1 ]; then echo "  $name : timeout ${SCRAPE_MIN}m $*"; return 0; fi
  t0=$(date +%s)
  NO_VPN=1 timeout "${SCRAPE_MIN}m" xvfb-run -a "$@" > "$LOGDIR/$name.log" 2>&1
  rc=$?
  el=$(( $(date +%s) - t0 ))
  # ! run in the background: FAILED cannot be set from here, so the
  #   verdict goes to a file the caller reads after `wait`
  if [ "$rc" -eq 124 ]; then
    echo "  $name stopped at the ${SCRAPE_MIN}-minute bound (${el}s); the rest waits for tomorrow" | tee -a "$SUMMARY"
  elif [ "$rc" -eq 1 ] && grep -q "NOTHING IS DUE" "$LOGDIR/$name.log"; then
    # both launchers exit 1 on an empty queue -- a quiet night, not a fault
    echo "  $name: nothing new to scrape (${el}s)" | tee -a "$SUMMARY"
  elif [ "$rc" -ne 0 ]; then
    echo "  $name FAILED (exit $rc) after ${el}s -- tail of $name.log:" | tee -a "$SUMMARY"
    tail -n 5 "$LOGDIR/$name.log" | tee -a "$SUMMARY"
    touch "$LOGDIR/$name.failed"
  else
    echo "  $name ok (${el}s)" | tee -a "$SUMMARY"
  fi
}
if [ "$SCRAPE" -eq 1 ]; then
  echo "== scrape (anet + tfrrs, at most ${SCRAPE_MIN} min)    $(date +%H:%M:%S)"
  export PER_MEET_DELAY="${PER_MEET_DELAY:-3,6}"
  scrape scrape_anet "$PY" -u scripts/launcher.py &
  pa=$!
  scrape scrape_tfrrs "$PY" -u tfrrs/driver/launch_tfrrs.py &
  pb=$!
  wait "$pa"; wait "$pb"
  for nm in scrape_anet scrape_tfrrs; do
    [ -f "$LOGDIR/$nm.failed" ] && FAILED="$FAILED $nm"
  done
fi

# ---- 2. who each new row is, and what it is (the pipeline's own steps) --- #
step 00_meet_dates    "$PY" -u engine/meet_date_fix.py --write --show 20
# the ids alive now, so 13c0 can 301 any a link below merges away
step 01a_person_probe "$PY" -u scripts/person_redirects.py --snapshot
step 02b_tf_venues    "$PY" -u scripts/backfill_tf_venues.py --write
step 03b_age_bands    "$PY" -u engine/age_band_grades.py --write
[ "${XCP_LINK_TFRRS:-1}" = "0" ] || \
  step 04a_link_tfrrs "$PY" -u scripts/link_tfrrs_rows.py --apply
[ "${XCP_LINK_TEAMLESS:-1}" = "0" ] || \
  step 04a2_link_teamless "$PY" -u scripts/link_teamless.py --apply
step 04b_wheelchair   "$PY" -u engine/wheelchair_flag.py --write
step 04c_twins        "$PY" -u engine/twin_flag.py --write
step 06_tfrrs_meets   "$PY" -u scripts/land_tfrrs_meet_names.py --apply
step 06b_course_canonical "$PY" -u scripts/build_course_canonical.py --incremental --apply

# ---- 3. price the new rows ---------------------------------------------- #
# ! A FAILED NORMALISATION STOPS HERE: pricing and publishing half a night is
#   worse than publishing yesterday's boards again.
if step 05_normalize_new "$PY" -u backfill/backfill_normalize.py --sport both --apply --new-only \
   && step 09b_fill_venue "$PY" -u engine/fill_ratings.py --venue; then

  # ---- 4. publish: boards, rank lines, teams, search -------------------- #
  if step 10_rankings_prepare "$PY" -u racecast/build_ranking_results.py --stage prepare; then
    SEAM="${XCP_RANK_SEAM:-2018-01-01}"
    if [ "$DRY" -eq 1 ]; then
      echo "  10_rankings_{xc,tf}_{a,b} : four streams on the $SEAM seam"
    else
      pids=""
      for spec in "xc_a XC --until" "xc_b XC --since" "tf_a TF --until" "tf_b TF --since"; do
        set -- $spec
        ( step "10_rankings_$1" "$PY" -u racecast/build_ranking_results.py \
               --stage stream --sport "$2" "$3" "$SEAM" ) &
        pids="$pids $!"
      done
      STREAMS_OK=1
      for p in $pids; do wait "$p" || STREAMS_OK=0; done
      [ "$STREAMS_OK" -eq 1 ] || FAILED="$FAILED 10_rankings_stream"
    fi
    # ! the swap only over four complete streams: a missing half is a board
    #   missing half its rows
    if [ "${STREAMS_OK:-1}" -eq 1 ]; then
      step 10_rankings_finish "$PY" -u racecast/build_ranking_results.py --stage finish \
        && step 10g_season_ranks "$PY" -u racecast/build_season_ranks.py \
        && step 11_teams "$PY" -u racecast/build_team_season.py
      step 13c0_person_redirects "$PY" -u scripts/person_redirects.py --resolve
      step 13c_search_index "$PY" -u racecast/search_index.py
    fi
  fi
fi

# ---- summary ------------------------------------------------------------ #
TOTAL=$(( $(date +%s) - T_START ))
echo ""
echo "== nightly update finished in $((TOTAL / 60)) min -- logs: $LOGDIR"
if [ -n "$FAILED" ]; then
  echo "   FAILED:$FAILED"
  [ "$DRY" -eq 1 ] || "$PY" scripts/notify_owner.py "$LOGDIR" $FAILED 2>&1 | tail -3 || true
  exit 1
fi
exit 0
