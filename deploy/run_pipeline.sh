#!/usr/bin/env bash
# run_pipeline.sh -- the full pipeline on the Ubuntu server.
#
# run_pipeline.ps1 is the WORKSTATION pipeline and cannot run here: its step
# bodies are `python engine\season_year.py`, and a backslash is not a path
# separator on Linux. This is the same 26 steps in the same order, for the box.
#
#   bash deploy/run_pipeline.sh                  # everything
#   bash deploy/run_pipeline.sh --from 10        # rankings and after
#   bash deploy/run_pipeline.sh --skip-backfill  # corrections/rules unchanged
#   bash deploy/run_pipeline.sh --dry-run        # print the plan, run nothing
#
# ⚠ FOUR HOURS. Run it under tmux or it dies with your ssh session.

set -u -o pipefail

ROOT="${XCP_ROOT:-/srv/xc-predictor}"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
# ★ THE WEATHER FETCHER'S OWN VENV (2026-09-06). backfill/atmost_era5_zarr.py
#   reads ERA5 from a public cloud store and needs xarray, zarr, icechunk and
#   pandas, whose pins can move numpy under the engine -- so they live in
#   /srv/wxvenv, not in $PY's venv. Absent, step 04e says so and the run
#   goes on without new weather (as every run before it did).
WXPY="${XCP_WXPYTHON:-/srv/wxvenv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"

FROM=0; SKIP_BACKFILL=0; DRY=0; SKIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    # --skip 04_grade_sanity,04b_wheelchair : named steps to leave out of
    # this run (2026-09-06, the owner: "you're gonna need to make step 4
    # 100 times faster if we're starting there" -- nothing in 04 changed,
    # so it need not run for 04c/04d/05 to)
    --skip) SKIP=",$2,"; shift 2 ;;
    --skip-backfill) SKIP_BACKFILL=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# ---- preflight ------------------------------------------------------ #
# ★ EVERY ONE OF THESE HAS COST A RUN. A missing corrections.py does not
#   crash anything -- it silently un-applies every correction, and you find
#   out four hours later from the boards.
[ -d "$ROOT" ] || { echo "FATAL: no repo at $ROOT" >&2; exit 1; }
[ -x "$PY" ]   || { echo "FATAL: no python at $PY" >&2; exit 1; }
cd "$ROOT" || exit 1

# ★ ONE PIPELINE AT A TIME (2026-09-04). Two ran on top of each other on
#   run9 (four tmux sessions, two launches): both solves took 5.5 h instead
#   of 40 min, one go-live dropped the other's staging table, and every
#   board after was a mixture. The lock is on a file, held by this shell
#   for the whole run, released by the kernel however the run ends.
if [ "$DRY" -eq 0 ]; then
  exec 9>"$ROOT/.pipeline.lock"
  if ! flock -n 9; then
    echo "FATAL: another pipeline holds $ROOT/.pipeline.lock -- tmux ls, then" >&2
    echo "       attach to it or kill it; never run two at once." >&2
    exit 1
  fi
fi

if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
  echo "  env loaded from $ENV_FILE"
else
  echo "WARNING: $ENV_FILE missing -- DB credentials must be in the environment" >&2
fi

CORR="$ROOT/engine/corrections.py"
if [ ! -f "$CORR" ]; then
  echo "FATAL: $CORR is missing. It is NOT in git and does not crash anything" >&2
  echo "       when absent -- it silently un-applies every correction." >&2
  echo "       scp it from the workstation before running." >&2
  exit 1
fi
CORR_MB=$(( $(stat -c%s "$CORR") / 1048576 ))
if [ "$CORR_MB" -lt 100 ]; then
  echo "FATAL: corrections.py is ${CORR_MB}MB, expected ~165MB. A git pull" >&2
  echo "       or a truncated scp will do this. Re-copy it." >&2
  exit 1
fi
echo "  corrections.py present (${CORR_MB}MB)"

# ★ A FLAG LEFT BY A KILLED SWAP 503s THE WHOLE SITE UNTIL SOMEONE NOTICES.
#   siteMaintenance removes it in a `finally`, which SIGKILL does not run --
#   and killing a step whose query will not die is a normal thing to do here.
#   The app now ignores a flag older than XCP_MAINTENANCE_MAX_S, so the site
#   heals itself; this clears the debris too, and SAYS SO, because a stale
#   flag means the site was serving 503 to visitors and crawlers until now.
STALE_AGE=$("$PY" -c 'import sys; sys.path.insert(0, "engine"); import maintenance; a = maintenance.clearStale(); print("" if a is None else int(a))' 2>/dev/null)
if [ -n "${STALE_AGE:-}" ]; then
  echo "  ! cleared a STALE maintenance flag (${STALE_AGE}s old) -- the site" >&2
  echo "    was answering 503 to every request for that long. A step was" >&2
  echo "    killed mid-swap. Check search-console coverage." >&2
fi

LOGDIR="$ROOT/logs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOGDIR"
SUMMARY="$LOGDIR/summary.log"
echo "  logs -> $LOGDIR"

FAILED=""
T_START=$(date +%s)

# step <name> <command...>
skipped() {                      # is step $1 on the --skip list?
  case "$SKIP" in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

failed() {                       # did step $1 already fail this run?
  case " $FAILED " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# summarise <exit-code> -- the closing banner, from here or from the end of
# the file. Factored out so an early abort reports exactly like a full run.
summarise() {
  TOTAL=$(( $(date +%s) - T_START ))
  echo ""
  echo "======================================================================"
  echo "  finished in $((TOTAL / 3600))h $(((TOTAL % 3600) / 60))m"
  if [ -n "$FAILED" ]; then
    echo "  FAILED STEPS:$FAILED"
    echo "  logs: $LOGDIR"
    exit 1
  fi
  echo "  all steps ok -- logs: $LOGDIR"
  echo "======================================================================"
  exit "${1:-0}"
}

# ★ THE STEPS --from DOES NOT SKIP. Both are cheap, both are read-only over
#   the corpus, and both are facts a LATER step consults rather than work a
#   later step redoes -- so "start at 8" must not silently drop them.
#     02_drop_old   the go-live refuses to start with a stale <table>_old.
#     04b_wheelchair wheelchair_person is the list fill_ratings and the board
#                   builds anti-join. Skipped, it holds whatever the last run
#                   that built it found, so every chair athlete who has
#                   arrived since is priced and ranked -- issue 14, twice
#                   (2026-09-01 the step did not exist; 2026-09-08 the
#                   standard --from 8 recipe left it out). Run after the
#                   pack it cannot un-rate them in the SOLVE, but it does
#                   keep them out of the fill and off every board, which is
#                   where they show.
# ⚠ 05b_anchor_repair IS HERE BECAUSE --from 7 IS THE STANDARD RECIPE AND
#   IT SKIPPED THE REPAIR ENTIRELY (owner's run20:
#   "05b_anchor_repair_xc + 05b_anchor_repair_tf skipped (--from 7)").
#   Placing a fix at step 5 and then never running step 5 is not a fix.
#   It is cheap once the corpus is clean -- one scan, idempotent, no
#   writes when nothing is wrong -- so it runs on every --from.
_ALWAYS="02_drop_old 04b_wheelchair 05b_anchor_repair_xc 05b_anchor_repair_tf"

step() {
  name="$1"; shift
  num=$(echo "$name" | sed 's/^0*\([0-9]*\).*/\1/')
  case " $_ALWAYS " in *" $name "*) always=1 ;; *) always=0 ;; esac
  if [ "${num:-0}" -lt "$FROM" ] && [ "$always" -eq 0 ]; then
    echo "  $name skipped (--from $FROM)"
    return 0
  fi
  if skipped "$name"; then
    echo "  $name skipped (--skip)"
    # ⚠ AND SAY WHAT IT COSTS. --skip is explicit, so it is honoured -- but
    #   the one step whose omission is invisible until the boards are wrong
    #   does not get to go quietly.
    if [ "$always" -eq 1 ]; then
      echo "  ⚠⚠ $name IS ON THE --skip LIST. wheelchair_person will not be" \
           "rebuilt, so chair athletes who arrived since it was last built" \
           "will be rated and ranked. run_checklist cannot catch this: it" \
           "consults the same stale list. Drop it from --skip."
    fi
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "  $name : $*"
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $name    $(date +%H:%M:%S)"
  echo "======================================================================"
  t0=$(date +%s)
  # ! -u SO PYTHON DOES NOT BUFFER. Without it a four-hour step shows nothing
  #   until it finishes, and you cannot tell a slow step from a hung one.
  if "$@" 2>&1 | tee "$LOGDIR/$name.log"; then rc=0; else rc=1; fi
  el=$(( $(date +%s) - t0 ))
  if [ "$rc" -ne 0 ]; then
    echo "  $name FAILED after ${el}s" | tee -a "$SUMMARY"
    FAILED="$FAILED $name"
  else
    echo "  $name ok (${el}s)" | tee -a "$SUMMARY"
  fi
}

# steps2 <nameA> "<cmdA>" <nameB> "<cmdB>"  -- two INDEPENDENT steps at
# once, one log each, one summary line each. For the pairs that read and
# write nothing in common (the two rowguard diags: 10 minutes each,
# sequential for no reason).
steps2() {
  nameA="$1"; cmdA="$2"; nameB="$3"; cmdB="$4"
  num=$(echo "$nameA" | sed 's/^0*\([0-9]*\).*/\1/')
  if [ "${num:-0}" -lt "$FROM" ]; then
    echo "  $nameA + $nameB skipped (--from $FROM)"
    return 0
  fi
  if skipped "$nameA"; then
    echo "  $nameA + $nameB skipped (--skip)"
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "  $nameA : $cmdA   (with $nameB : $cmdB)"
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $nameA + $nameB    $(date +%H:%M:%S)   (in parallel)"
  echo "======================================================================"
  t0=$(date +%s)
  sh -c "$cmdA" > "$LOGDIR/$nameA.log" 2>&1 &
  pa=$!
  sh -c "$cmdB" > "$LOGDIR/$nameB.log" 2>&1 &
  pb=$!
  wait "$pa"; ra=$?
  wait "$pb"; rb=$?
  el=$(( $(date +%s) - t0 ))
  tail -n 3 "$LOGDIR/$nameA.log" "$LOGDIR/$nameB.log"
  for pair in "$nameA:$ra" "$nameB:$rb"; do
    nm="${pair%%:*}"; rc="${pair##*:}"
    if [ "$rc" -ne 0 ]; then
      echo "  $nm FAILED after ${el}s" | tee -a "$SUMMARY"
      FAILED="$FAILED $nm"
    else
      echo "  $nm ok (${el}s, in parallel)" | tee -a "$SUMMARY"
    fi
  done
}

# stepsN <nameA> <cmdA> <nameB> <cmdB> ...  -- any number of steps in
# parallel, one log each, one summary line each. Same --from and --dry-run
# rules as steps2; the --from test uses the FIRST name's number.
stepsN() {
  first="$1"
  num=$(echo "$first" | sed 's/^0*\([0-9]*\).*/\1/')
  if [ "${num:-0}" -lt "$FROM" ]; then
    echo "  $first (+ $(( $# / 2 - 1 )) more) skipped (--from $FROM)"
    return 0
  fi
  if skipped "$first"; then
    echo "  $first (+ $(( $# / 2 - 1 )) more) skipped (--skip)"
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    while [ $# -ge 2 ]; do echo "  $1 : $2"; shift 2; done
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $first (+ $(( $# / 2 - 1 )) more)    $(date +%H:%M:%S)   (in parallel)"
  echo "======================================================================"
  t0=$(date +%s)
  names=""; pids=""
  while [ $# -ge 2 ]; do
    sh -c "$2" > "$LOGDIR/$1.log" 2>&1 &
    pids="$pids $!"; names="$names $1"; shift 2
  done
  rcs=""
  for pid in $pids; do wait "$pid"; rcs="$rcs $?"; done
  el=$(( $(date +%s) - t0 ))
  set -- $names
  for rc in $rcs; do
    nm="$1"; shift
    tail -n 2 "$LOGDIR/$nm.log"
    if [ "$rc" -ne 0 ]; then
      echo "  $nm FAILED after ${el}s" | tee -a "$SUMMARY"
      FAILED="$FAILED $nm"
    else
      echo "  $nm ok (${el}s, in parallel)" | tee -a "$SUMMARY"
    fi
  done
}

# shards <name> <n> <command...>  -- run <command> --shard k/n for k in
# 0..n-1 in parallel, one log each, one summary line. Same --from and
# --dry-run rules as step.
shards() {
  name="$1"; n="$2"; shift 2
  num=$(echo "$name" | sed 's/^0*\([0-9]*\).*/\1/')
  if [ "${num:-0}" -lt "$FROM" ] && [ "$name" != "02_drop_old" ]; then
    echo "  $name skipped (--from $FROM)"
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "  $name : $* --shard k/$n  (x$n in parallel)"
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $name    $(date +%H:%M:%S)   ($n shards in parallel)"
  echo "======================================================================"
  t0=$(date +%s)
  pids=""
  k=0
  while [ "$k" -lt "$n" ]; do
    "$@" --shard "$k/$n" > "$LOGDIR/${name}_shard$k.log" 2>&1 &
    pids="$pids $!"
    k=$((k + 1))
  done
  rc=0
  for pid in $pids; do
    wait "$pid" || rc=1
  done
  tail -n 2 "$LOGDIR/${name}"_shard*.log
  el=$(( $(date +%s) - t0 ))
  if [ "$rc" -ne 0 ]; then
    echo "  $name FAILED after ${el}s" | tee -a "$SUMMARY"
    FAILED="$FAILED $name"
  else
    echo "  $name ok (${el}s)" | tee -a "$SUMMARY"
  fi
}

# ---- verdicts ------------------------------------------------------- #
# ! unlink.py IS NOT HERE ON PURPOSE -- it is the one non-idempotent step.
step 01_season_year   "$PY" -u engine/season_year.py
step 02_drop_old      "$PY" -u engine/drop_old.py
step 03_pro_flag      "$PY" -u engine/pro_flag.py --skip-dist --write
# ⚠ BEFORE grade_sanity: it decides whether "11-12" is grades or ages, and
#   every rule downstream reads that answer.
step 03b_age_bands    "$PY" -u engine/age_band_grades.py --write
step 04_grade_sanity  "$PY" -u engine/grade_sanity.py --write

# ⚠ THIS STEP WAS MISSING, AND THAT IS HOW CHAIR ATHLETES CAME BACK (owner,
#   2026-09-01: "somehow wheelchair athletes snuck back into the engine").
#   speed_ratings_db._chairFilter() DEGRADES rather than crashes when
#   wheelchair_person is absent -- it prints one line and rates them -- which
#   is right for an old database and lethal for a pipeline that never builds
#   the table. Nothing else creates it, so every run since it was written has
#   rated chair athletes. It belongs BEFORE the pack: it is a fact about
#   people that the pack reads.
step 04b_wheelchair   "$PY" -u engine/wheelchair_flag.py --write
# issue 94: one physical race stored twice is flagged once, by result_id,
# and every reader (engine, pricer, boards, page) anti-joins result_twin
step 04c_twins        "$PY" -u engine/twin_flag.py --write
# gender by the divisions raced under; a person who raced both ways enough
# is two athletes to the pack and the boards (issue 164)
step 04d_gender       "$PY" -u engine/person_gender.py --write
# ★ THE WEATHER GRID FOLLOWS THE SEASON (2026-09-06). The grid stopped at
#   2025-12-31 because nothing in the pipeline ever extended it, so the
#   whole 2026 season was rated with no weather. The fetcher is resumable:
#   it lists every venue-day the meets need, skips what the grid has, and
#   pulls the rest (27,212 cell-days for 2026 on 2026-09-06). ERA5 lags real
#   time by weeks, so the last few weeks are always missing and are picked
#   up by a later run.
# ⚠ THE STORE IS PCODEC-COMPRESSED (run16, 2026-09-07): every tile failed
#   with "codec not available: 'pcodec'" and the step still exited 0, so
#   the run went on with no 2026 weather. The venv needs the codec:
#       /srv/wxvenv/bin/pip install pcodec
#   and the fetcher now fails the step when every tile failed.
if [ -x "$WXPY" ]; then
  step 04e_weather_grid "$WXPY" -u backfill/atmost_era5_zarr.py
else
  echo "  04e_weather_grid skipped ($WXPY not found; see deploy/run_pipeline.sh)" | tee -a "$SUMMARY"
fi
# ! THE REFIT IS OPT-IN. fit_weather_correction is the heavy stage (hours)
#   and its answer only changes when the grid's numbers do -- after
#   scripts/recompute_apparent_temp.py, or a new season's worth of rows.
#   XCP_WEATHER_FIT=1 runs both sports side by side; the artifacts it
#   writes are what 05_backfill applies.
if [ "${XCP_WEATHER_FIT:-0}" = "1" ]; then
  # --refresh: the fitter caches its corpus by SQL text, and the recompute
  # changes the numbers without changing the SQL
  steps2 04f_weather_fit_xc "$PY -u engine/fit_weather_correction.py --sport XC --refresh" \
         04f_weather_fit_tf "$PY -u engine/fit_weather_correction.py --sport TF --refresh"
else
  echo "  04f_weather_fit skipped (XCP_WEATHER_FIT=1 to refit)" | tee -a "$SUMMARY"
fi

# ! THE BACKFILL RUNS AFTER grade_sanity, NOT BEFORE -- it resolves pools from
#   grade_fix, and a disagreement writes normalized_time on the wrong SCALE
#   (measured at a 64% rating error, frozen into the row).
if [ "$SKIP_BACKFILL" -eq 1 ]; then
  echo "  05_backfill skipped (--skip-backfill)"
else
  # the two sports write two tables and share nothing: in parallel
  # (2026-09-06, the owner: "make those steps faster")
  # XCP_BACKFILL_XC=changed re-normalises only the XC rows of the people
  # 04d moved (nothing else on the XC side changed since the last full
  # pass); unset, the full XC pass. TF is always full here (geometry off,
  # the 600, the event names: every track row).
  if [ "${XCP_BACKFILL_XC:-full}" = "changed" ]; then
    steps2 05_backfill_xc "$PY -u backfill/backfill_normalize.py --sport XC --apply --only-changed" \
           05_backfill_tf "$PY -u backfill/backfill_normalize.py --sport TF --apply"
  else
    steps2 05_backfill_xc "$PY -u backfill/backfill_normalize.py --sport XC --apply" \
           05_backfill_tf "$PY -u backfill/backfill_normalize.py --sport TF --apply"
  fi
fi

# ★★ THE ANCHOR REPAIR, IMMEDIATELY AFTER THE BACKFILL AND BEFORE THE PACK
#    (2026-09-09). The backfill picks the pool that sets the SCALE
#    normalized_time is written on; the solve picks the pool that sets the
#    ANCHOR it is divided by. For an athlete who raced across levels in one
#    season those two disagree -- backfill_normalize reads season_level,
#    which only speaks when a season's verdict is UNANIMOUS, so a runner who
#    did mostly non-HS races and a few HS ones falls back to
#    poolFor(grade=12) -> hs_m and is written at the 5000m anchor, while the
#    engine's majority rule calls the season college_m and divides by a mean
#    on the 8000m anchor. About 1.61x, for free, aimed at exactly the
#    athletes good enough to be invited up a level.
#
# ★ THE ORDER IS THE WHOLE VALUE. After 05, or the backfill would overwrite
#   the repair with the same mismatch. BEFORE the pack, so THIS run's solve
#   reads the corrected times rather than the next one -- run it after
#   go-live instead and the fix is always one run behind.
#
# ! IT NEEDS results*.rating_pool, WHICH A PREVIOUS GO-LIVE WROTE. On a
#   database that has never packed there is nothing to repair and the script
#   says so and exits; `|| true` keeps that from stopping the run.
#
# ⚠ IT RESCALES, IT DOES NOT RECOMPUTE. Only the pool factor is divided out
#   and the right one multiplied in, so the era, weather, geometry and course
#   corrections already inside the stored value survive exactly. It is
#   idempotent: a repaired row reads as already right on the next pass, so
#   running it every pipeline costs one scan and changes nothing once clean.
if [ "${XCP_SKIP_ANCHOR_REPAIR:-0}" = "1" ]; then
  echo "  05b_anchor_repair skipped (XCP_SKIP_ANCHOR_REPAIR=1)"
else
  steps2 05b_anchor_repair_xc "$PY -u engine/anchor_repair.py --sport XC --apply" \
         05b_anchor_repair_tf "$PY -u engine/anchor_repair.py --sport TF --apply"
fi

# ---- pack and solve ------------------------------------------------- #
# ! BOTH caches must go: packed_XC_TF.npz is checked for existence only, and
#   pair_solve_cache.npz fingerprints on sum(y), which does not notice a pool
#   reassignment that leaves the sum intact.
#
# ★ THE GATE IS 7, NOT 6, AND THAT IS THE POINT (2026-09-08). Step 07 runs
#   `speed_ratings.py --cache --pack-only`, and --cache means "reuse
#   engine/data/packed_XC_TF.npz if the file is there" -- its own help says
#   DELETE THE FILE AFTER ANY DATA OR QUERY CHANGE. So `--from 7` used to
#   skip the clear at 6 and then hand step 07 a pack it would simply reuse:
#   the step ran, printed, took no time and rebuilt nothing. There is no
#   reason to run 07 against a valid cached pack -- that is a no-op wearing
#   a step's name -- so the clear now happens whenever 07 is going to run.
#   06 and 07 are one unit; only `--from 8` and later keep the pack.
#
# ⚠ AND `wheelchair_flag --write` IS A QUERY CHANGE. _chairFilter() is
#   interpolated into both pack queries (speed_ratings_db, the XC and TF
#   builders), so the pack's ROW SET depends on wheelchair_person. Rebuild
#   the list without rebuilding the pack and the chair athletes stay in the
#   solve with their abilities intact -- off every board, because the fill
#   and the board builds anti-join the list directly, but still perturbing
#   everyone they raced. Out of the RATINGS needs --from 7 or lower.
if [ "$FROM" -le 7 ]; then
  if [ "$DRY" -eq 1 ]; then
    echo "  06_clear_cache : rm engine/data/{packed_XC_TF,pair_solve_cache}.npz"
  else
    rm -f engine/data/packed_XC_TF.npz engine/data/pair_solve_cache.npz
    echo "  06_clear_cache : caches cleared (07_pack will rebuild the pack)"
  fi
else
  echo "  06_clear_cache skipped (--from $FROM): 07_pack would reuse the" \
       "cached pack anyway. The pack keeps whatever wheelchair_person held" \
       "when it was built -- see 04b_wheelchair."
fi

# ★ THE PACK CARRIES dist_m SINCE 2026-09-03 (issue 148): the joint solve
#   fits one offset per (pool, track distance) from it. A pack from before
#   that runs with the block OFF (08 prints "track distance offsets: OFF"),
#   so the first run after the change is --from 6, which clears the cache
#   above and rebuilds here. --from 8 keeps whatever pack is on disk.
# ★ EVERY tfrrs TRACK ROW GETS ITS MEET'S NAME AND VENUE IN meets_tf
#   (2026-09-06): the pages, the pack's venue keys and the boards all read
#   meets_tf on the row's own keys, and tfrrs rows had rows there only
#   where a geometry stamp existed, unnamed. Idempotent, seconds.
step 06_tfrrs_meets   "$PY" -u scripts/land_tfrrs_meet_names.py --apply
step 07_pack          "$PY" -u engine/speed_ratings.py --sport merged --cache --pack-only

# ★ TWO SOLVERS, ONE SWITCH.
#   XCP_JOINT_LIVE=1  (THE DEFAULT since 2026-09-08: two runs went live on
#   the old engine because the flag was left off the command line, and a
#   day was spent reading the old engine's ratings through the joint's
#   tables; issue 310. XCP_JOINT_LIVE=0 asks for the old engine.)
#   the joint solve IS step 08: it writes course_difficulties,
#                     athlete_ratings, results.speed_rating and
#                     pair_difficulty.npz (issue 116). The tilt is inside its
#                     ratings, so 09_tilt is skipped -- running it would tilt
#                     twice -- and 10c_gap runs WITHOUT --emit: the sport
#                     level is a parameter of the solve and the bbar loop has
#                     nothing to steer, so the gap is measured for telemetry
#                     and written to no json. (It is not skipped; this
#                     comment said so until 2026-09-08 and the code below
#                     never did.)
#   XCP_JOINT=1       the sequential solve stays live; the joint solve runs
#                     beside it as a shadow and writes joint_difficulty.npz only.
if [ "${XCP_JOINT_LIVE:-1}" = "1" ]; then
  # ! NO --holdout ON THE LIVE STEP: it is a second full solve (three hours
  #   on 59M rows) that scores a split and publishes nothing. The shadow
  #   step keeps it; that is what the shadow is for.
  # --probes 0: the probes size per-cell uncertainty for the report and
  #   nothing the site reads; four took 35 minutes on run12 and printed
  #   a cell SE nobody could use. XCP_PROBES=4 puts them back.
  # XCP_WINTER_GAIN=0.03 states the average athlete's fall-to-spring gain
  # (issue 143); unset, the level carries the whole change.
  # ★★ ALTITUDE IS ON BY DEFAULT NOW (2026-09-10). It was gated behind
  #    XCP_ALTITUDE=1, which was never set -- so the term has been OFF in
  #    every run since issue 172 landed, and BYU (1,400 m) has been reading
  #    slow because the model had no way to know why (owner: "BYU course
  #    too slow (altitude)"). venue_elevation exists on the box, so the
  #    only thing keeping the term off was an env var nobody set.
  #
  #  ! IT FAILS SOFT. run_joint prints "venue_elevation is absent ... the
  #    term is OFF" and carries on if the table is ever missing, so
  #    defaulting this on cannot break a run. XCP_ALTITUDE=0 turns it off.
  # XCP_WINTER_GAIN_BANDS=0.03,0.02,0.03 states it per rating band
  # (low / middle / top) and the go-live shifts the track rows to it
  # (issue 194); unset, no shift.
  # --outer 5: the sixth outer moved sigma by 0.00003 and the level by
  #   nothing on run12; XCP_OUTER=6 puts it back.
  # ★ XCP_MERGE_SPORTS=1 -- ONE SCALE THAT MEANS FITNESS (2026-09-09).
  #   Sport is season, so the XC/TF level is not identified by any data this
  #   corpus contains; --merge-sports asserts it is zero instead of
  #   estimating it, and the form curve carries autumn-to-spring movement as
  #   FITNESS. It turns off beta, the winter-gain pin, the curve-gap penalty
  #   and the go-live band shift by itself -- so setting XCP_WINTER_GAIN
  #   alongside it is harmless but pointless, and run_joint says so.
  # ★ THE 2026-09-11 TERMS. XCP_SPORT_LEVEL=0.0583 ASSERTS the XC/TF level
  #   at the stated grass cost (joint_solve.XC_TRACK_GAP) instead of
  #   estimating what the data cannot identify; unset, the level is
  #   estimated as before. The field-strength term (the race's front, from
  #   the model's own ratings; XCP_IMPORTANCE=season-end for the calendar
  #   share instead), the indoor level ASSERTED at the NCAA factor
  #   (XCP_INDOOR_LEVEL=fit to estimate it) and the tables' prior on event
  #   offsets are ON; XCP_NO_IMPORTANCE=1, XCP_NO_INDOOR=1,
  #   XCP_NO_DIST_TABLE=1 switch each off. XCP_ERA_YEARS=2 splits every
  #   course into two-year eras tied by a random walk (the go-live
  #   publishes each venue's latest era under its bare key);
  #   XCP_ERA_DRIFT sets the walk's step (log-time sd per era, default
  #   0.01): with a few race days per era that number, against
  #   sigma_u / sqrt(days), decides how far a venue can move. The holdout
  #   carries the same.
  # ★ XCP_DIFFICULTY=bracket publishes the bracket engine's course numbers
  #   (run_joint.bracketDifficulties): the solve still fits everything
  #   else, the courses come from the owner's method, the abilities are
  #   recomputed to match. Default joint. The holdout and the ladder score
  #   the joint solve either way; 08d compares the two on the same rows.
  step 08_golive        "$PY" -u engine/run_joint.py --golive --probes "${XCP_PROBES:-0}" \
      --outer "${XCP_OUTER:-5}" \
      ${XCP_DIFFICULTY:+--difficulty "$XCP_DIFFICULTY"} \
      ${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"} \
      ${XCP_IMPORTANCE:+--importance "$XCP_IMPORTANCE"} \
      ${XCP_NO_IMPORTANCE:+--no-importance} \
      ${XCP_NO_INDOOR:+--no-indoor} \
      ${XCP_INDOOR_LEVEL:+--indoor-level "$XCP_INDOOR_LEVEL"} \
      ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} \
      ${XCP_ERA_DRIFT:+--era-drift "$XCP_ERA_DRIFT"} \
      ${XCP_NO_DIST_TABLE:+--no-dist-table} \
      ${XCP_MERGE_SPORTS:+--merge-sports} \
      ${XCP_CENTRE_CURVE:+--centre-curve} \
      ${XCP_TAU_MAX:+--tau-max "$XCP_TAU_MAX"} \
      ${XCP_SPLIT_ABILITY:+--split-ability} \
      ${XCP_WINTER_GAIN:+--winter-gain "$XCP_WINTER_GAIN"} \
      ${XCP_WINTER_GAIN_BANDS:+--winter-gain-bands "$XCP_WINTER_GAIN_BANDS"} \
      $([ "${XCP_ALTITUDE:-1}" != "0" ] && echo --altitude)
  # ★★ THE SCOREBOARD, EVERY RUN (2026-09-10). Until now nothing in this
  #    pipeline produced a number that said whether a change helped, so
  #    modelling questions were settled by argument. 08a scores the shipped
  #    model on races it never saw, and 08b runs the same score across the
  #    ablations -- including a Slaney-shaped rung with a fraction of our
  #    parameters, which is the honest test of whether our extra terms earn
  #    their keep.
  #
  #  ! ON A SAMPLE, AND IT REPORTS ONLY. The ladder writes no board and no
  #    difficulty; a human reads the table and decides what the NEXT run
  #    carries. Automatic model selection on one number, computed once, on
  #    a sample, is how you get a model that is excellent at the holdout
  #    and wrong about Foot Locker.
  #
  #  ! `|| true` on both: an evidence step must never fail a pipeline that
  #    has already produced good boards.
  #  ! --holdout-only, NOT --holdout. Plain --holdout scores the held-out
  #    races and then solves the full model on the sample and writes it
  #    over engine/data/joint_difficulty.npz -- the file the diagnostics
  #    read -- for a solve nobody looks at. It cost this step half its
  #    wall clock (3073s of 6371s) and would have shipped a quarter-sample
  #    difficulty file to explain_joint_row.
  step 08a_holdout      "$PY" -u engine/run_joint.py --holdout-only \
      --holdout-kind race --sample-pct "${XCP_HOLDOUT_PCT:-25}" \
      --outer "${XCP_OUTER:-5}" --probes 0 --altitude \
      ${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"} \
      ${XCP_IMPORTANCE:+--importance "$XCP_IMPORTANCE"} \
      ${XCP_NO_IMPORTANCE:+--no-importance} \
      ${XCP_NO_INDOOR:+--no-indoor} \
      ${XCP_INDOOR_LEVEL:+--indoor-level "$XCP_INDOOR_LEVEL"} \
      ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} \
      ${XCP_ERA_DRIFT:+--era-drift "$XCP_ERA_DRIFT"} \
      ${XCP_NO_DIST_TABLE:+--no-dist-table} || true
  # ! A RUNG IS A SOLVE (2026-09-12: "08b takes over 6 hours ... gets
  #   stuck"). Each rung solves XCP_LADDER_PCT of the athletes, the era
  #   rungs on three times the cells, and until today nothing was printed
  #   while one ran. The ladder now runs its CORE set (seven rungs) by
  #   default, streams each rung to engine/data/ladder_logs/<rung>.log with
  #   a heartbeat in this log, and kills a rung past XCP_RUNG_TIMEOUT
  #   seconds (default 7200). XCP_LADDER_ALL=1 runs every rung;
  #   XCP_LADDER_ONLY=base,no-importance runs a named subset.
  step 08b_ladder       "$PY" -u scripts/ablation_ladder.py \
      --pct "${XCP_LADDER_PCT:-15}" --outer "${XCP_OUTER:-5}" \
      --rung-timeout "${XCP_RUNG_TIMEOUT:-7200}" \
      ${XCP_LADDER_ALL:+--all} \
      ${XCP_LADDER_ONLY:+--only "$XCP_LADDER_ONLY"} || true
  # ★ THE SECOND ENGINE AND THE PACK DIAGNOSTICS, ONE PROCESS (2026-09-12).
  #   scripts/diagnose.py loads the pack and the solve file once and runs
  #   the bracket engine on the ladder's athlete sample and race split (so
  #   its "error sd" and the base rung's are one question asked of two
  #   engines), indoor against outdoor, and why tracks differ. Reports go
  #   to $LOGDIR/{bracket_holdout,indoor,tracks}.txt; the venue brackets
  #   need names from the database and are run by hand (--venue). Three
  #   minutes on the corpus, measured. XCP_BRACKET=0 skips it.
  if [ "${XCP_BRACKET:-1}" != "0" ]; then
    step 08d_diagnose     "$PY" -u scripts/diagnose.py \
        --only indoor,tracks,holdout --out-dir "$LOGDIR" \
        --pct "${XCP_LADDER_PCT:-15}" --seed 11 \
        ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} || true
  fi

  # ★ THE ANCHOR AUDIT, EVERY RUN (2026-09-09). anchor_check recomputes each
  #   row's normalized_time on the pool it is RATED in and reports the ones
  #   that disagree -- rows normalised as hs_m and rated as college_m come
  #   out 60% inflated, which is how two athletes rated 230 in a race whose
  #   other six finishers rated 141-144.
  #
  # ⚠ IT IS A REPORT, NOT A GATE. It writes nothing and cannot fail the run;
  #   `|| true` keeps a diagnostic from ever taking a pipeline down. Read the
  #   percentages: they should be a fraction of a percent, and a jump means
  #   the two stages have drifted apart again.
  step 08c_anchor_check "$PY" -u engine/anchor_check.py --sport TF --pct 1 \
      || true
  echo "  08b_joint_shadow: the joint solve is live (XCP_JOINT_LIVE=1)"
  echo "  09_tilt skipped: the joint ratings carry the tilt"
else
  step 08_golive        "$PY" -u engine/linkage_check.py --golive --split
  if [ "${XCP_JOINT:-0}" = "1" ]; then
    # --holdout scores 10% of rows first (a second solve); --probes 16 keeps
    # the posterior-variance pass to minutes rather than hours on a first run.
    step 08b_joint_shadow "$PY" -u engine/run_joint.py --holdout --probes 16 \
        ${XCP_WINTER_GAIN:+--winter-gain "$XCP_WINTER_GAIN"}
  else
    echo "  08b_joint_shadow skipped (set XCP_JOINT=1 to run)"
  fi
  step 09_tilt          "$PY" -u engine/apply_tilt.py --refresh --write
fi

# ★ A FAILED GO-LIVE STOPS THE RUN HERE (2026-09-08, issue 310 again).
#   step() records a failure and carries on, which is right for a diagnostic
#   or a board -- and wrong for this one step. Everything below republishes
#   the site FROM results.speed_rating: 09b_fill prices the unrated rows
#   against it, 10_* rebuild the boards from it, 13c/13d/13e push it to the
#   search index, the sitemap and Bing. When 08 fails, that column is
#   whatever the LAST run left there, so the run spends four more hours
#   dressing stale ratings up as today's and the summary's one FAILED line
#   is the only sign. That is how the old engine's ratings served the site
#   for two days (310), and how run 20260908_030942 published a rating set
#   whose go-live had crashed.
#
#   The rebuild is safe to lose: the ratings are still in the database and
#   the pack is still on disk, so the fix is `--from 8` again once the
#   go-live's own failure is understood -- reading 08_golive.log FIRST.
#
#   XCP_IGNORE_GOLIVE_FAIL=1 continues anyway. That is for the one case
#   where you have decided the ratings on disk are the ones you want
#   published -- never for "let's see if the rest works".
if failed 08_golive; then
  if [ "${XCP_IGNORE_GOLIVE_FAIL:-0}" = "1" ]; then
    echo ""
    echo "  ⚠ 08_golive FAILED, and XCP_IGNORE_GOLIVE_FAIL=1 -- continuing." \
         "The boards below will be built from the ratings the PREVIOUS run" \
         "left in results.speed_rating." | tee -a "$SUMMARY"
  else
    echo "" | tee -a "$SUMMARY"
    echo "  ABORTING: 08_golive failed, so results.speed_rating still holds" \
         "the PREVIOUS run's ratings. Everything after this step republishes" \
         "that column to the site, so the run stops instead of shipping" \
         "stale ratings as new ones (310)." | tee -a "$SUMMARY"
    echo "  read: $LOGDIR/08_golive.log" | tee -a "$SUMMARY"
    echo "  then: bash deploy/run_pipeline.sh --from 8 ...   (the pack is" \
         "still current; nothing before 8 needs to rerun)" | tee -a "$SUMMARY"
    echo "  or:   XCP_IGNORE_GOLIVE_FAIL=1 ... to publish the ratings as" \
         "they stand." | tee -a "$SUMMARY"
    summarise
  fi
fi

step 09b_fill         "$PY" -u engine/fill_ratings.py


# ---- boards and pages ----------------------------------------------- #
# the two sports stream side by side (each is a Python row walk of ~30M
# rows); prepare makes the shadow once, finish indexes and swaps
step 10_rankings_prepare "$PY" -u racecast/build_ranking_results.py --stage prepare
# each sport in two halves on a date seam (XCP_RANK_SEAM), four streams
# into one shadow: the row walk is Python per row and was 52 minutes
# for two (2026-09-06)
stepsN 10_rankings_xc_a "$PY -u racecast/build_ranking_results.py --stage stream --sport XC --until ${XCP_RANK_SEAM:-2018-01-01}" \
       10_rankings_xc_b "$PY -u racecast/build_ranking_results.py --stage stream --sport XC --since ${XCP_RANK_SEAM:-2018-01-01}" \
       10_rankings_tf_a "$PY -u racecast/build_ranking_results.py --stage stream --sport TF --until ${XCP_RANK_SEAM:-2018-01-01}" \
       10_rankings_tf_b "$PY -u racecast/build_ranking_results.py --stage stream --sport TF --since ${XCP_RANK_SEAM:-2018-01-01}"
step 10_rankings_finish "$PY" -u racecast/build_ranking_results.py --stage finish
step 10b_school_ids   "$PY" -u racecast/build_school_identity.py
if [ "${XCP_JOINT_LIVE:-1}" = "1" ]; then
  # measured for telemetry only: the joint level is not steered by the json
  step 10c_gap        "$PY" -u scripts/measure_sport_gap.py
else
  step 10c_gap        "$PY" -u scripts/measure_sport_gap.py --emit
fi

step 10d_school_units "$PY" -u racecast/build_school_units.py
# issue 34: which meets are championships, and of what; then the units
# reach the site search (a partial index rebuild, cheap)
step 10e_meet_units   "$PY" -u racecast/build_meet_units.py
step 11_teams         "$PY" -u racecast/build_team_season.py
# ! THE PAGE INDEXES COME BEFORE THE COURSE PAGES. They ran AFTER them
#   (step 14), so every course page of a first run was built without the
#   indexes the course queries need; 12b took 41,047 s on 2026-09-02.
step 11b_indexes      "$PY" -u scripts/add_page_indexes.py
step 12_courses       "$PY" -u racecast/build_course_rank.py
# ★ COURSE PAGES IN THREE SHARDS (issue 140). One process building 1,200
#   courses in a row was mostly waiting on the database; three workers on
#   every third course cut the wall time to about a third. --prepare and
#   --finish bracket them so the swap happens once, after all three.
step 12b_prepare      "$PY" -u racecast/build_course_boards.py --prepare
shards 12b_course_pages 3 "$PY" -u racecast/build_course_boards.py --limit 1200
step 12b_finish       "$PY" -u racecast/build_course_boards.py --finish
# the two sports' full passes side by side (14 minutes for both in one
# process on run12); each writes only its own sport's panels
steps2 13_panels_xc "$PY -u racecast/panels.py --sport XC" \
       13_panels_tf "$PY -u racecast/panels.py --sport TF"
step 13b_pool_consts  "$PY" -u scripts/warm_pool_constants.py
# ★ THE SEARCH INDEX, REBUILT EVERY RUN (issue 140). It was never in the
#   pipeline, so new athletes and meets stayed unsearchable until someone
#   rebuilt it by hand. Built into a shadow table and swapped, so search
#   never goes dark.
step 13c_search_index "$PY" -u racecast/search_index.py
# the sitemap for Google: every ranked athlete, school, course and meet
step 13d_sitemap      "$PY" -u racecast/build_sitemap.py
# IndexNow: the URLs that changed, to Bing and friends (needs XCP_INDEXNOW_KEY)
step 13e_indexnow     "$PY" -u scripts/indexnow_submit.py

# ---- rowguard ------------------------------------------------------- #
# 2000-row rail; anything past it diverts to .OVER-CAP for a human.
steps2 15_rowguard_diag_xc "$PY -u scripts/diag_suspects.py --sport XC" \
       15_rowguard_diag_tf "$PY -u scripts/diag_suspects.py --sport TF"
step 15_rowguard_triage_xc "$PY" -u scripts/triage_suspects.py --sport XC
step 15_rowguard_triage_tf "$PY" -u scripts/triage_suspects.py --sport TF
step 16_rowguard_apply     "$PY" -u scripts/apply_triage.py

# ---- the owner's go/no-go ------------------------------------------- #
step 17_checklist     "$PY" -u scripts/run_checklist.py

# ---- summary -------------------------------------------------------- #
summarise
