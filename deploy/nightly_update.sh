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
# ⚠ AND SO DOES A FAILED PEOPLE STEP (sweep 2026-10-10). 04a/04a2 decide whose
#   row a new result is, 04b who is a chair athlete, 04c which copy of a race
#   is the twin -- and the normaliser resolves each new row's pool from the
#   person it belongs to. A failure there only logged, and 05 priced the
#   night's rows on unlinked persons and unflagged twins. A step turned off
#   (XCP_LINK_TFRRS=0 / XCP_LINK_TEAMLESS=0) is not a failure.
#   XCP_IGNORE_PEOPLE_FAIL=1 prices anyway.
PEOPLE_OK=1
for _s in 04a_link_tfrrs 04a2_link_teamless 04b_wheelchair 04c_twins; do
  case " $FAILED " in *" $_s "*) PEOPLE_OK=0 ;; esac
done
if [ "$PEOPLE_OK" -eq 0 ]; then
  if [ "${XCP_IGNORE_PEOPLE_FAIL:-0}" = "1" ]; then
    echo "  ⚠ a people step FAILED, and XCP_IGNORE_PEOPLE_FAIL=1 -- pricing anyway" | tee -a "$SUMMARY"
    PEOPLE_OK=1
  else
    echo "  05_normalize_new NOT RUN: a people step failed (see above); yesterday's" \
         "boards stay up. XCP_IGNORE_PEOPLE_FAIL=1 to price anyway." | tee -a "$SUMMARY"
  fi
fi
if [ "$PEOPLE_OK" -eq 1 ] && step 05_normalize_new "$PY" -u backfill/backfill_normalize.py --sport both --apply --new-only \
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
      # ★ 13c ONLY OVER A SWAPPED BOARD (sweep 2026-10-10), the order
      #   run_pipeline.sh keeps (13c after 10): after a failed finish the
      #   site still serves yesterday's boards, and a search index built for
      #   tonight's ids would list athletes those boards do not have.
      if step 10_rankings_finish "$PY" -u racecast/build_ranking_results.py --stage finish; then
        step 10g_season_ranks "$PY" -u racecast/build_season_ranks.py \
          && step 11_teams "$PY" -u racecast/build_team_season.py
        step 13c_search_index "$PY" -u racecast/search_index.py
        # ★ this week's state ranks for /movers (build_rank_snapshot.py,
        #   2026-10-10); off the && chain, so a failed snapshot never costs
        #   the team boards
        step 10g2_rank_snapshot "$PY" -u racecast/build_rank_snapshot.py
        # ★ the NCAA projection (build_ncaa_projection.py, 2026-10-10): the
        #   week's row rewritten with tonight's results; off the chain too
        step 11c_ncaa "$PY" -u racecast/build_ncaa_projection.py
      fi
      # ★ THE HOME PAGE AND /meets READ panels.py's tables (homepage_recent:
      #   "Latest results"), so without this a meet scraped tonight had a
      #   page but was on no list until the next full run. Both sports side
      #   by side, as the pipeline runs them; each writes only its own.
      if [ "$DRY" -eq 1 ]; then
        echo "  13_panels_{xc,tf} : racecast/panels.py --sport XC | TF"
      else
        ( step 13_panels_xc "$PY" -u racecast/panels.py --sport XC ) &
        pp1=$!
        ( step 13_panels_tf "$PY" -u racecast/panels.py --sport TF ) &
        pp2=$!
        wait "$pp1" || FAILED="$FAILED 13_panels_xc"
        wait "$pp2" || FAILED="$FAILED 13_panels_tf"
      fi
      # Breakouts, precomputed (build_breakouts.py): reads the boards and
      # homepage_recent's newest dates, so after both
      step 13f_breakouts "$PY" -u racecast/build_breakouts.py
      # ★ STATE ODDS (owner, 2026-10-10; racecast/build_state_odds.py): the
      #   season simulated per state/division/gender into state_odds_*.
      #   Reads the boards (athlete_season, school_unit, meet_unit), so it
      #   runs after them -- and OFF the && chain: a failed state leaves
      #   yesterday's odds standing and costs nothing else. Incremental: a
      #   division whose field has not moved is skipped.
      if [ "${XCP_STATE_ODDS:-0}" = "1" ]; then step 13i_state_odds "$PY" -u racecast/build_state_odds.py; fi
      # ★ THE FOLLOW DIGEST (owner, 2026-10-10), OPTIONAL: off unless
      #   XCP_ALERTS=1 in the env file. After the breakouts it reads; mails
      #   one digest per follower who is due, fills My page's next-meet
      #   cache (--prewarm). See racecast/follow_alerts.py.
      if [ "${XCP_ALERTS:-0}" = "1" ]; then
        step 13g_follow_alerts "$PY" -u racecast/follow_alerts.py --send --prewarm
      fi
    fi
  fi
fi

# ★ 13c0 RUNS WHATEVER PUBLISHING DID (sweep 2026-10-10). Its only inputs are
#   tonight's 01a probe and the result rows' person ids -- 04a/04a2/04c moved
#   them whether or not anything was priced or published -- and tomorrow's
#   01a OVERWRITES the probe. Gated on the boards, a failed night lost its
#   redirects for good: every id merged away tonight would 404.
step 13c0_person_redirects "$PY" -u scripts/person_redirects.py --resolve

# ★ "WHAT WE SAID", FROZEN BEFORE EACH MEET, AND SCORED AFTER (owner,
#   2026-10-10: the weekly "how we did" loop). The week's posted XC meets
#   get their forecast stored (once, refreshed the night before), and every
#   stored meet that has run is scored for /meet/recap and /recaps. See
#   racecast/build_meet_forecasts.py.
# ! OFF THE && CHAIN AND AFTER THE BOARDS: it reads the ratings as they
#   stand, publishes nothing the boards need, and a failure only says so in
#   the summary. OFF until XCP_FORECASTS=1 is in the env file: run it by
#   hand once first (the catch-up predicts every posted meet) to see its
#   time on the server.
if [ "${XCP_FORECASTS:-0}" = "1" ]; then
  step 13h_meet_forecasts "$PY" -u racecast/build_meet_forecasts.py
fi

# ★ THE SITEMAP AND INDEXNOW, NIGHTLY TOO (SEO pass, 2026-10-10). Until now
#   only the full pipeline rebuilt them, so a meet scraped tonight had a page
#   but no sitemap line and no IndexNow ping for days. OFF THE && CHAIN, and
#   after 13c0 on purpose: the sitemap lists canonical ids only (it skips
#   every id person_redirect sends elsewhere), and it reads whatever boards
#   are live -- tonight's after a good publish, yesterday's after a failed
#   one, a correct list either way. The files are written beside the old
#   ones and swapped (build_sitemap.writeSitemaps), so nginx never serves a
#   half-built set. 13e posts only what changed (indexnow_submit.pick).
step 13d_sitemap "$PY" -u racecast/build_sitemap.py
step 13e_indexnow "$PY" -u scripts/indexnow_submit.py


# ★ THE DATA WATCHDOG (owner, 2026-10-10), LAST AND OFF THE && CHAIN: it
#   reads what tonight changed -- rows scraped, rows a linker moved, the
#   boards as swapped (or not) -- and reports; it fixes nothing. It runs
#   whatever publishing did, because a failed night is exactly when its
#   pipeline section matters. Mails the admins only on NEW items, and only
#   when no step failed (then notify_owner's mail carries WATCHDOG.txt).
#   --ensure-indexes builds its three BRIN scope indexes once. XCP_WATCHDOG=0
#   turns it off. See racecast/watchdog.py.
if [ "${XCP_WATCHDOG:-1}" != "0" ]; then
  step 13k_watchdog "$PY" -u racecast/watchdog.py --log-dir "$LOGDIR" --kind nightly \
       --step-name 13k_watchdog --failed "$FAILED" --send --ensure-indexes
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
