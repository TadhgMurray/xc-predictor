#!/usr/bin/env bash
# run_pipeline.sh -- the full pipeline on the Ubuntu server.
#
# run_pipeline.ps1 is the WORKSTATION pipeline and cannot run here: its step
# bodies are `python engine\season_year.py`, and a backslash is not a path
# separator on Linux. This is the same 26 steps in the same order, for the box.
#
#   bash deploy/run_pipeline.sh                  # everything
#   bash deploy/run_pipeline.sh --from 10        # rankings and after
#   bash deploy/run_pipeline.sh --fast           # ratings + boards, no reports (iteration)
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

# ★ THE PIPELINE YIELDS TO THE SITE (owner, 2026-09-13: "some parts of
#   pipeline make website super slow (this is a must fix)"). Every step
#   runs under nice and, where it exists, ionice idle-ish, so gunicorn and
#   Postgres win the CPU and the disk when a page is being served; and the
#   numpy solve's BLAS is capped at XCP_THREADS (cores less two) instead of
#   one thread per core, which starved the eight workers for hours.
#   XCP_NICE=0 XCP_THREADS=<n> override both.
XCP_NICE="${XCP_NICE:-10}"
NICE="nice -n $XCP_NICE"
if command -v ionice >/dev/null 2>&1; then NICE="$NICE ionice -c2 -n7"; fi
_cores=$(nproc 2>/dev/null || echo 4)
XCP_THREADS="${XCP_THREADS:-$(( _cores > 3 ? _cores - 2 : 1 ))}"
export OMP_NUM_THREADS="$XCP_THREADS" OPENBLAS_NUM_THREADS="$XCP_THREADS" \
       MKL_NUM_THREADS="$XCP_THREADS" NUMEXPR_NUM_THREADS="$XCP_THREADS"
# ★ AND INSIDE POSTGRES (owner, 2026-09-14: "especially the steps past the
#   solve"). nice never reached the Postgres backends that do the boards'
#   COPY, the heap rebuilds and the index builds; XCP_DB_QUIET=1 caps every
#   pipeline connection -- see scripts/database.py.
# ★★ BALANCED, NOT STRICT, SINCE 2026-09-26 (owner: "speed up every step in
#    pipeline possible"). XCP_DB_QUIET=1 is now 2 parallel workers, 512MB
#    work_mem, 2GB maintenance_work_mem, two builder jobs, and every cursor
#    planned to read to the end; the site is served from Cloudflare's cache
#    and the scraper is blocked, so the strict caps (XCP_DB_QUIET=strict,
#    with XCP_STREAMS=2 XCP_COURSE_SHARDS=2) were buying little. The board
#    row walks run four at a time again and the course pages in three.
export XCP_DB_QUIET="${XCP_DB_QUIET:-1}"
XCP_STREAMS="${XCP_STREAMS:-4}"
# ★ SIX COURSE SHARDS ON THE 24-CORE BOX (2026-10-25: 12b was 1,541 s at
#   three, and the shards wait on the database, not the CPU). Each holds one
#   pooled connection until it needs more (XCP_DB_MIN_CONN=1 at 12b).
XCP_COURSE_SHARDS="${XCP_COURSE_SHARDS:-6}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"

_ORIG_ARGS="$*"
FROM=0; SKIP_BACKFILL=0; DRY=0; SKIP=""; FAST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    # --fast: the ratings and the boards, nothing that only reports on them
    #   (see FAST_SKIP below)
    --fast) FAST=1; shift ;;
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

# ★ --fast: AN ITERATION RUN (owner, 2026-11-06: "if we're gonna iterate
#   like this we gotta do better"; the last full run took ~10 h). Everything
#   that changes a rating or a board still runs -- links, twins, distances,
#   normalise, pack, solve, go-live, fill, boards, search, panels. Left out:
#   the checks and reports nothing on the site reads, and the slow
#   background jobs whose last output stands in for a run:
#     evidence holdouts/ladder (XCP_EVIDENCE), backtest, LACCTiC, Kailo,
#     scoreboard, qualified fields, the raw forecast, social cards, the
#     rowguard diagnostics, the course pages (served with live ratings), the
#     recruit projection, the level graph (the next full run restages it),
#     the weather fetch (the nightly's provisional weather covers new meets),
#     and the pro flags (last run's stand: they change when a pro appears).
#   An env var set by hand still wins over these defaults.
# ! THE COURSE PAGES STAY UP, WITH TODAY'S RATINGS (owner, 2026-11-09: Tom
#   Rankin 160.7 on the Hereford course page, 145.8 on his profile). A board
#   built before the go-live has its ratings re-read when it is served
#   (app._boardFresh); who is on its lists waits for the next full run.
FAST_SKIP="01c_level_graph,04e_weather_grid,04e2_weather_recent,03_pro_flag,05c_pro_ability"
FAST_SKIP="$FAST_SKIP,10f2_projection,12b_course_pages,13j_record_books"
FAST_SKIP="$FAST_SKIP,15_rowguard_diag_xc,15_rowguard_diag_tf,15_rowguard_triage_xc,15_rowguard_triage_tf"
if [ "$FAST" -eq 1 ]; then
  SKIP="${SKIP:-,}${FAST_SKIP},"
  for v in XCP_EVIDENCE XCP_BACKTEST XCP_LACCTIC XCP_KAILO XCP_SCOREBOARD \
           XCP_QUALIFIED_BUILD XCP_SOCIAL XCP_FORECAST_RAW XCP_RECORD_BOOKS; do
    eval "[ -n \"\${$v+x}\" ] || export $v=0"
  done
  echo "  --fast: skipping ${FAST_SKIP//,/, } and the evidence/report steps"
fi

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
# ★ AND A QUEUED JOB DOES NOT TURN A RUN AWAY (2026-11-09, deploy/lockctl.sh):
#   when the backup or the race-model build holds the lock, the run says so
#   and waits for it (XCP_LOCK_WAIT_MIN, default 120) with a want posted, so
#   the next queued job cannot slip in first. A pipeline or the nightly
#   holding it is still a FATAL. The holder is written to
#   .pipeline.lock.holder; scripts/who_holds_the_lock.sh reads it.
if [ "$DRY" -eq 0 ]; then
  . "$ROOT/deploy/lockctl.sh"
  if ! lock_take pipeline exclusive $(( ${XCP_LOCK_WAIT_MIN:-120} * 60 )) \
       "run_pipeline.sh ${_ORIG_ARGS:-} (tmux: tmux ls; logs: newest logs/2*)"; then
    echo "       tmux ls, then attach to it or stop it; never run two at once." >&2
    exit 1
  fi
fi

if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
  echo "  env loaded from $ENV_FILE"
else
  echo "WARNING: $ENV_FILE missing -- DB credentials must be in the environment" >&2
fi

# ★★ THE MODEL'S SETTINGS, LOADED HERE, EVERY RUN (2026-10-02). They live in
#    deploy/solve_env.sh (XCP_DIFFICULTY=bracket, the sport level, 2-year
#    eras, altitude, the indoor level, the gauge, the 30-day window ...), and
#    only scripts/overnight_fit_pool_solve.sh sourced it. The run of
#    2026-10-01 was started as `bash deploy/run_pipeline.sh --from 7` and
#    published WITHOUT them: the joint model's course numbers instead of the
#    bracket engine's (0 [bracket] lines in its 08_golive log against 70 the
#    run before), and every other default -- Mt. SAC 5000 +9.6% -> +4.6%,
#    the owner's track ratings down. solve_env.sh only fills what is unset
#    (`: "${VAR:=value}"`), so a value given on the command line still wins.
SOLVE_ENV="$ROOT/deploy/solve_env.sh"
if [ -f "$SOLVE_ENV" ]; then
  . "$SOLVE_ENV"
  echo "  model settings from $SOLVE_ENV: difficulty=${XCP_DIFFICULTY:-?} era_years=${XCP_ERA_YEARS:-?}" \
       "sport_level=${XCP_SPORT_LEVEL:-?} altitude=${XCP_ALTITUDE:-?} window=${XCP_BRACKET_WINDOW:-?}" \
       "gauge=${XCP_GAUGE:-?}"
else
  echo "FATAL: $SOLVE_ENV is missing -- the model's settings would silently fall to defaults" >&2
  exit 1
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

# ★ AN INTERRUPTED RUN STOPS ITS DATABASE WORK TOO (owner, 2026-09-15: "even
#   after I ctrl-c'd the script the website is slow as shit"). Ctrl-C ends
#   this shell and the foreground Python; it does NOT end the statement
#   Postgres is running for them (a CREATE TABLE AS runs to completion with
#   nobody to send the rows to), and the parallel children started with &
#   ignore SIGINT. So on INT or TERM: terminate every backend labelled
#   xcp-pipeline (quiet mode names them), then the whole process group.
_cleanup() {
  trap - INT TERM
  echo ""
  echo "  interrupted: ending the pipeline's database backends and children" | tee -a "$SUMMARY"
  "$PY" scripts/db_activity.py --kill-pipeline --quiet 2>/dev/null || true
  _pgid=$(ps -o pgid= $$ 2>/dev/null | tr -d ' ')
  [ -n "$_pgid" ] && kill -TERM -- -"$_pgid" 2>/dev/null
  exit 130
}
trap _cleanup INT TERM
SUMMARY="$LOGDIR/summary.log"
echo "  logs -> $LOGDIR"

# ★ A SOLVE IS AHEAD: NO ENGINE REPLAY STARTS UNTIL IT IS DONE (2026-11-09;
#   docs/PIPELINE-ORDER.md). A replay is a ~60 GB solve; one started during
#   steps 00-07 was still running at 08_golive, and two of them beside the
#   go-live left 1.5 GB on the box. This posts a standing want in
#   scripts/jobrun.py's ledger from now until the evidence chain is
#   collected: engine_replay.sh will not start a cutoff meanwhile, and one
#   already running is stopped (and retried later) the moment the go-live
#   asks for memory it cannot have. It dies with this shell.
# ★ "THE RESULTS TABLES ARE STABLE" (2026-11-10): a run from 9 on never
#   rewrites results / results_tf, so a nightly waiting on the lock may
#   scrape new rows into them now (deploy/nightly_update.sh). A run from 8
#   or lower says so after its go-live (below).
if [ "$DRY" -eq 0 ] && [ "$FROM" -gt 8 ]; then lock_note "results_stable=1"; fi
if [ "$DRY" -eq 0 ] && [ "$FROM" -le 8 ]; then
  "$PY" "$ROOT/scripts/jobrun.py" --want-standing $$ --gb "${XCP_GOLIVE_GB:-60}" \
        --name "pipeline $(basename "$LOGDIR") (a solve is ahead)" 2>/dev/null || true
fi

FAILED=""
T_START=$(date +%s)

# _stampTo <file> -- copy stdin to stdout unchanged, and to <file> with the
# wall-clock time in front of every line.
# ★ WHY (2026-10-05, owner: "speed up first"): 08_golive took 2.6 h of a
#   17h44m run and its log could not say which part -- a handful of its
#   lines carry a duration, the rest none. With every line stamped, the
#   gap between two lines IS the time that part took, for every step, with
#   no change to any Python. Only the log file carries the stamp; nothing
#   parses these logs by line start (notify_owner tails them, run_report
#   searches them).
_stampTo() {
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s\n' "$line"
    printf '%(%H:%M:%S)T %s\n' -1 "$line" >> "$1"
  done
}

# ★ EVERY STEP'S MEMORY, BESIDE ITS TIME (2026-11-09, the back-end track:
#   "wall-clock time and peak memory per step"). Each step's command runs
#   under scripts/jobrun.py, which follows its whole process tree and writes
#   the peak RSS and the lowest MemAvailable seen while it ran to
#   $LOGDIR/.mem/<name>; the summary line carries both, and
#   scripts/pipeline_profile.py tabulates a run (or two side by side).
#   STEP_MEM="--gb N --priority ..." in front of a step admits it through
#   jobrun's memory ledger (the go-live; see docs/PIPELINE-ORDER.md).
#   XCP_JOBRUN=0 runs the commands bare.
_jr() {   # _jr <name> -- the wrapper's argv, or nothing
  if [ "${XCP_JOBRUN:-1}" != "0" ] && [ "$DRY" -eq 0 ]; then
    echo "$PY -u $ROOT/scripts/jobrun.py --name $1 --peak-out $LOGDIR/.mem/$1 ${STEP_MEM:-} --"
  fi
}
_peak() { # _peak <name...> -- ", peak 4.2 GB, box down to 31 GB" (shards: summed)
  "$PY" - "$LOGDIR/.mem" "$@" <<'PYEOF' 2>/dev/null
import os, re, sys
d, names = sys.argv[1], sys.argv[2:]
pk, lo = 0.0, None
for n in names:
    try:
        t = open(os.path.join(d, n)).read()
    except OSError:
        continue
    m = re.search(r"peak_gb=([0-9.]+) min_avail_gb=([0-9.inf]+)", t)
    if m:
        pk += float(m.group(1))
        a = float(m.group(2))
        lo = a if lo is None else min(lo, a)
if lo is not None:
    print(f", peak {pk:.1f} GB, box down to {lo:.0f} GB free", end="")
PYEOF
}

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
  # ★ WHAT TO DO NOW (owner, 2026-10-11: "remind me of the thing to do
  #   after the pipeline finished from now on"): deploy/AFTER_PIPELINE.md,
  #   printed above the banner on every finish, failed or not. Edit the
  #   file as items are done or added.
  rm -f "$LOGDIR"/.club_cache.*            # the run's club tables (07_pack), done with
  if [ -s deploy/AFTER_PIPELINE.md ]; then
    echo ""
    echo "==== AFTER THIS RUN (deploy/AFTER_PIPELINE.md) ===="
    cat deploy/AFTER_PIPELINE.md
  fi
  echo ""
  echo "======================================================================"
  echo "  finished in $((TOTAL / 3600))h $(((TOTAL % 3600) / 60))m" | tee -a "$SUMMARY"
  # ★ THE RUN'S OWN DIAGNOSIS (owner, 2026-11-13: "diagnose and look over
  #   every step after each run"): every step against the last finished run,
  #   what each background step held the run, and the longest pauses inside
  #   every step that ran 5 minutes or more -- one file to read afterwards.
  _prev=$(ls -dt "$ROOT"/logs/2*/ 2>/dev/null | grep -v "$(basename "$LOGDIR")" \
          | while read -r d; do grep -q "finished in" "$d/summary.log" 2>/dev/null && { echo "$d"; break; }; done)
  "$PY" "$ROOT/scripts/pipeline_profile.py" ${_prev:+"$_prev"} "$LOGDIR" --gaps 300 \
      > "$LOGDIR/profile.txt" 2>&1 || true
  echo "  step-by-step diagnosis: $LOGDIR/profile.txt"
  if [ -n "$FAILED" ]; then
    echo "  FAILED STEPS:$FAILED"
    echo "  logs: $LOGDIR"
    # ★ AND TELL THE OWNER (sweep, 2026-09-26): a failure used to be found
    #   whenever someone next read this terminal. Never fails the run itself.
    if [ "$DRY" -eq 0 ]; then
      "$PY" scripts/notify_owner.py "$LOGDIR" $FAILED 2>&1 | tail -3 || true
    fi
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
# ★ 04a_link_tfrrs TOO (owner, 2026-09-25: "yes add it to the pipeline").
#   Not one 2026 tfrrs row had a person_id -- no name, no gender, no rating
#   -- because the linking passes ran by hand after past seasons and never
#   since. Who a row belongs to is a fact every later step reads
#   (grade_sanity, twins, the pack, the boards), so it runs on every --from.
#   XCP_LINK_TFRRS=0 turns it off; every stamp is logged in person_link_log
#   and `scripts/link_tfrrs_rows.py --undo fanout|freshman|mint` reverses it.
# ★ 04a2_link_teamless TOO, for the same reason: it moves ids (2026-09-29).
# ★ 04a2b_feed_twins TOO: it moves ids (2026-10-15).
# ★ 04a3_pins TOO: it keeps the owner's detaches where he put them (2026-10-10).
# ★ 00_integrity TOO: a damaged page is damaged whichever step reads it first.
_ALWAYS="00_integrity 00b_run_snapshot 01a_person_probe 02_drop_old 04a_link_tfrrs 04a2_link_teamless 04a2b_feed_twins 04a4_unsplit 04a3_pins 04a3b_merges 04a3c_gender_split 04b_wheelchair 05b_anchor_repair_xc 05b_anchor_repair_tf"

# ⚠ A SKIPPED STEP SAYS WHAT IT COSTS. --skip is explicit, so it is
#   honoured -- but the one step whose omission is invisible until the
#   boards are wrong does not get to go quietly.
_skipCost() {
  if [ "$1" = 04b_wheelchair ]; then
    echo "  ⚠⚠ $1 IS ON THE --skip LIST. wheelchair_person will not be" \
         "rebuilt, so chair athletes who arrived since it was last built" \
         "will be rated and ranked. run_checklist cannot catch this: it" \
         "consults the same stale list. Drop it from --skip."
  fi
}

# beforeFrom NAME: true when NAME is before --from and not on _ALWAYS.
# ★ ONE RULE FOR EVERY KIND OF STEP (complexity review, 2026-10-24): only
#   step() read _ALWAYS, and 05b_anchor_repair runs through steps2, so
#   `--from 7` -- the standard recipe -- still skipped the repair the list
#   exists to keep (run20). steps2, stepsN, shards and bgstep ask here now,
#   and --skip is honoured per name, not only for a batch's first.
beforeFrom() {
  case " $_ALWAYS " in *" $1 "*) return 1 ;; esac
  _n=$(echo "$1" | sed 's/^0*\([0-9]*\).*/\1/')
  [ "${_n:-0}" -lt "$FROM" ]
}

# willRun NAME: would a step of this name run (not before --from, not on
# --skip)? For a group whose parts must run or skip together (12b, 12c).
willRun() { ! beforeFrom "$1" && ! skipped "$1"; }

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
    _skipCost "$name"
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
  : > "$LOGDIR/$name.log"
  if $NICE $(_jr "$name") "$@" 2>&1 | _stampTo "$LOGDIR/$name.log"; then rc=0; else rc=1; fi
  el=$(( $(date +%s) - t0 ))
  if [ "$rc" -ne 0 ]; then
    echo "  $name FAILED after ${el}s${STEP_NOTE:+, $STEP_NOTE}$(_peak "$name")" | tee -a "$SUMMARY"
    FAILED="$FAILED $name"
  else
    echo "  $name ok (${el}s${STEP_NOTE:+, $STEP_NOTE}$(_peak "$name"))" | tee -a "$SUMMARY"
  fi
}

# _live <name> <log> <command...>  -- run the command with its output in
# <log> AND on the console, every line prefixed "[name]"; the exit code is
# the COMMAND's (pipefail, in the function's own subshell).
# ★ PARALLEL STEPS PRINT TO THE CONSOLE TOO (owner, 2026-09-28: "don't only
#   print it to a file, print to console too"). steps2, stepsN, shards and
#   bgstep used to write only to their logs and show a tail at the end, so
#   an hours-long step (04f, 05) was silent until it finished.
#   XCP_LIVE=0 goes back to logs only.
_live() (
  set -o pipefail
  nm="$1"; log="$2"; shift 2
  : > "$log"
  if [ "${XCP_LIVE:-1}" = "0" ]; then
    $(_jr "$nm") "$@" 2>&1 | _stampTo "$log" > /dev/null
  else
    $(_jr "$nm") "$@" 2>&1 | _stampTo "$log" | sed -u "s/^/  [$nm] /"
  fi
)

# steps2 <nameA> "<cmdA>" <nameB> "<cmdB>"  -- two INDEPENDENT steps at
# once, one log each, one summary line each. For the pairs that read and
# write nothing in common (the two rowguard diags: 10 minutes each,
# sequential for no reason).
steps2() {
  nameA="$1"; cmdA="$2"; nameB="$3"; cmdB="$4"
  runA=1; runB=1
  if beforeFrom "$nameA"; then echo "  $nameA skipped (--from $FROM)"; runA=0
  elif skipped "$nameA"; then echo "  $nameA skipped (--skip)"; runA=0; fi
  if beforeFrom "$nameB"; then echo "  $nameB skipped (--from $FROM)"; runB=0
  elif skipped "$nameB"; then echo "  $nameB skipped (--skip)"; runB=0; fi
  if [ "$runA" -eq 0 ] && [ "$runB" -eq 0 ]; then return 0; fi
  # one of the pair left: it runs alone, as a step
  if [ "$runB" -eq 0 ]; then step "$nameA" sh -c "$cmdA"; return 0; fi
  if [ "$runA" -eq 0 ]; then step "$nameB" sh -c "$cmdB"; return 0; fi
  if [ "$DRY" -eq 1 ]; then
    echo "  $nameA : $cmdA   (with $nameB : $cmdB)"
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $nameA + $nameB    $(date +%H:%M:%S)   (in parallel)"
  echo "======================================================================"
  t0=$(date +%s)
  _live "$nameA" "$LOGDIR/$nameA.log" $NICE sh -c "$cmdA" &
  pa=$!
  _live "$nameB" "$LOGDIR/$nameB.log" $NICE sh -c "$cmdB" &
  pb=$!
  wait "$pa"; ra=$?
  wait "$pb"; rb=$?
  el=$(( $(date +%s) - t0 ))
  tail -n 3 "$LOGDIR/$nameA.log" "$LOGDIR/$nameB.log"
  for pair in "$nameA:$ra" "$nameB:$rb"; do
    nm="${pair%%:*}"; rc="${pair##*:}"
    if [ "$rc" -ne 0 ]; then
      echo "  $nm FAILED after ${el}s$(_peak "$nm")" | tee -a "$SUMMARY"
      FAILED="$FAILED $nm"
    else
      echo "  $nm ok (${el}s, in parallel$(_peak "$nm"))" | tee -a "$SUMMARY"
    fi
  done
}

# stepsN <nameA> <cmdA> <nameB> <cmdB> ...  -- any number of steps in
# parallel, one log each, one summary line each. Same --dry-run rule as
# steps2.
# ★ EACH NAME ANSWERS --from, --skip AND _ALWAYS FOR ITSELF (2026-10-25).
#   It used to test the FIRST name only, which was right while every group
#   was one step's streams. The people verdicts and the board waves mix
#   step numbers and an _ALWAYS step: `--from 11` must still build the
#   teams, `--from 7` must still rebuild wheelchair_person, and `--skip
#   04d_gender` must skip gender and nothing else.
stepsN() {
  # each pair judged on its own name: --from (with _ALWAYS) and --skip
  _keep=()
  while [ $# -ge 2 ]; do
    if beforeFrom "$1"; then echo "  $1 skipped (--from $FROM)"
    elif skipped "$1"; then echo "  $1 skipped (--skip)"; _skipCost "$1"
    else _keep+=("$1" "$2"); fi
    shift 2
  done
  [ "${#_keep[@]}" -eq 0 ] && return 0
  set -- "${_keep[@]}"
  first="$1"
  if [ "$DRY" -eq 1 ]; then
    while [ $# -ge 2 ]; do echo "  $1 : $2"; shift 2; done
    return 0
  fi
  echo ""
  echo "======================================================================"
  echo "  $first (+ $(( $# / 2 - 1 )) more)    $(date +%H:%M:%S)   ($XCP_STREAMS at a time)"
  echo "======================================================================"
  t0=$(date +%s)
  # ★ XCP_STREAMS AT A TIME, not all at once (2026-09-14): four row walks
  #   were four backends scanning results side by side under the site.
  names=""; rcs=""; batch_pids=""; batch_names=""; inflight=0
  while [ $# -ge 2 ]; do
    _live "$1" "$LOGDIR/$1.log" $NICE sh -c "$2" &
    batch_pids="$batch_pids $!"; batch_names="$batch_names $1"; inflight=$((inflight + 1)); shift 2
    if [ "$inflight" -ge "$XCP_STREAMS" ] || [ $# -lt 2 ]; then
      for pid in $batch_pids; do wait "$pid"; rcs="$rcs $?"; done
      names="$names$batch_names"; batch_pids=""; batch_names=""; inflight=0
    fi
  done
  el=$(( $(date +%s) - t0 ))
  set -- $names
  for rc in $rcs; do
    nm="$1"; shift
    tail -n 2 "$LOGDIR/$nm.log"
    if [ "$rc" -ne 0 ]; then
      echo "  $nm FAILED after ${el}s$(_peak "$nm")" | tee -a "$SUMMARY"
      FAILED="$FAILED $nm"
    else
      echo "  $nm ok (${el}s, in parallel$(_peak "$nm"))" | tee -a "$SUMMARY"
    fi
  done
}

# shards <name> <n> <command...>  -- run <command> --shard k/n for k in
# 0..n-1 in parallel, one log each, one summary line. Same --from and
# --dry-run rules as step.
shards() {
  name="$1"; n="$2"; shift 2
  if beforeFrom "$name"; then
    echo "  $name skipped (--from $FROM)"
    return 0
  fi
  if skipped "$name"; then
    echo "  $name skipped (--skip)"
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
    _live "${name}_shard$k" "$LOGDIR/${name}_shard$k.log" $NICE "$@" --shard "$k/$n" &
    pids="$pids $!"
    k=$((k + 1))
  done
  rc=0
  for pid in $pids; do
    wait "$pid" || rc=1
  done
  tail -n 2 "$LOGDIR/${name}"_shard*.log
  el=$(( $(date +%s) - t0 ))
  _sh=(); k=0; while [ "$k" -lt "$n" ]; do _sh+=("${name}_shard$k"); k=$((k + 1)); done
  if [ "$rc" -ne 0 ]; then
    echo "  $name FAILED after ${el}s$(_peak "${_sh[@]}")" | tee -a "$SUMMARY"
    FAILED="$FAILED $name"
  else
    echo "  $name ok (${el}s$(_peak "${_sh[@]}"))" | tee -a "$SUMMARY"
  fi
}

# bgstep <name> <command...> -- a LEAF step, started in the background.
# bgwait                      -- wait for every one of them, and report.
#
# ★ FOR A STEP NOTHING LATER IN THE RUN READS (2026-09-22). 10f2_projection
#   is 50 minutes of model inference whose table only the recruiting page
#   joins; run in line, every step after it waited for it. Same --from,
#   --skip and --dry-run rules as step; its summary line is written when
#   bgwait collects it, so a failure still lands in FAILED and the exit code.
# ! ONLY FOR LEAVES. A step whose output another step reads must stay a
#   plain `step`, or the reader races the writer.
BG_PIDS=(); BG_NAMES=(); BG_T0=()
bgstep() {
  name="$1"; shift
  if beforeFrom "$name"; then
    echo "  $name skipped (--from $FROM)"
    return 0
  fi
  if skipped "$name"; then
    echo "  $name skipped (--skip)"
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "  $name : $*   (in the background)"
    return 0
  fi
  echo ""
  echo "  $name    $(date +%H:%M:%S)   started IN THE BACKGROUND -> $LOGDIR/$name.log"
  _live "$name" "$LOGDIR/$name.log" $NICE "$@" &
  BG_PIDS+=("$!"); BG_NAMES+=("$name"); BG_T0+=("$(date +%s)")
}
# bgjoin <name> -- wait for ONE background step now (its reader is next),
#   write its summary line; bgwait then skips it. A name never started (by
#   --from, --skip or --dry-run) is a no-op.
bgjoin() {
  local i rc el
  for i in "${!BG_PIDS[@]}"; do
    [ "${BG_NAMES[$i]}" = "$1" ] && [ -n "${BG_PIDS[$i]}" ] || continue
    echo "  waiting for background step $1..."
    local tw; tw=$(date +%s)
    if wait "${BG_PIDS[$i]}"; then rc=0; else rc=1; fi
    el=$(( $(date +%s) - ${BG_T0[$i]} ))
    # ★ HOW LONG THE RUN STOOD STILL FOR IT (2026-11-12): a background
    #   step's own time says nothing about what it cost the run
    tw=$(( $(date +%s) - tw ))
    tail -n 2 "$LOGDIR/$1.log"
    if [ "$rc" -ne 0 ]; then
      echo "  $1 FAILED after ${el}s (background, held the run ${tw}s$(_peak "$1"))" | tee -a "$SUMMARY"
      FAILED="$FAILED $1"
    else
      echo "  $1 ok (${el}s, background, held the run ${tw}s$(_peak "$1"))" | tee -a "$SUMMARY"
    fi
    BG_PIDS[$i]=""
  done
}
bgwait() {
  local i rc el
  for i in "${!BG_PIDS[@]}"; do
    [ -n "${BG_PIDS[$i]}" ] || continue          # already collected by bgjoin
    echo "  waiting for background step ${BG_NAMES[$i]}..."
    local tw; tw=$(date +%s)
    if wait "${BG_PIDS[$i]}"; then rc=0; else rc=1; fi
    el=$(( $(date +%s) - ${BG_T0[$i]} ))
    tw=$(( $(date +%s) - tw ))                   # the run stood still this long (bgjoin)
    tail -n 2 "$LOGDIR/${BG_NAMES[$i]}.log"
    if [ "$rc" -ne 0 ]; then
      echo "  ${BG_NAMES[$i]} FAILED after ${el}s (background, held the run ${tw}s$(_peak "${BG_NAMES[$i]}"))" | tee -a "$SUMMARY"
      FAILED="$FAILED ${BG_NAMES[$i]}"
    else
      echo "  ${BG_NAMES[$i]} ok (${el}s, background, held the run ${tw}s$(_peak "${BG_NAMES[$i]}"))" | tee -a "$SUMMARY"
    fi
  done
  BG_PIDS=(); BG_NAMES=(); BG_T0=()
}

# bgchain <label> <function> -- a FUNCTION of ordinary steps (step, steps2,
#   stepsN), run in order in one background subshell, its lines prefixed
#   "[label]". The subshell keeps its own FAILED and writes it to a file;
#   bgchainjoin <label> waits for the chain and adds those failures to
#   FAILED, so a failed step in a chain fails the run as it did in line.
#   --from, --skip and --dry-run are each step's own, as in line (a dry run
#   prints the chain in place, in order).
# ★ FOR A RUN OF STEPS WHOSE OUTPUT NOTHING READS UNTIL A KNOWN POINT
#   (2026-11-07, owner-approved): the distance checks (04g0..04g1b) beside
#   the people verdicts, the rowguard diagnostics beside the boards. The
#   join goes immediately before the first reader.
# ! A CHAIN THAT DIES WITHOUT WRITING ITS FILE COUNTS AS FAILED (its label).
# rankSeamsOk "<d1 d2 ...>" -- the seams back when they are YYYY-MM-DD
#   dates, strictly increasing, strictly between 1990-01-01 and 2100-01-01
#   (the stream's default --since / --until); empty otherwise. A seam outside
#   that range would add or drop rows; inside it, any seams give the same.
rankSeamsOk() {
  local d prev="1990-01-01"
  [ -n "$1" ] || return 0
  for d in $1; do
    echo "$d" | grep -Eq '^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$' || return 0
    [[ "$d" > "$prev" ]] || return 0
    prev="$d"
  done
  echo "$1"
}
BGC_PIDS=(); BGC_NAMES=()
bgchain() {
  local label="$1" fn="$2"
  if [ "$DRY" -eq 1 ]; then "$fn"; return 0; fi
  rm -f "$LOGDIR/.chain_$label.failed"
  echo ""
  echo "  $label chain    $(date +%H:%M:%S)   started IN THE BACKGROUND; its lines carry [$label]"
  (
    FAILED=""
    "$fn"
    echo "$FAILED" > "$LOGDIR/.chain_$label.failed"
  ) 2>&1 | sed -u "s/^/  [$label] /" &
  BGC_PIDS+=("$!"); BGC_NAMES+=("$label")
}
bgchainjoin() {
  local i _ef
  for i in "${!BGC_PIDS[@]}"; do
    [ "${BGC_NAMES[$i]}" = "$1" ] && [ -n "${BGC_PIDS[$i]}" ] || continue
    echo "  waiting for the $1 chain..."
    local tw; tw=$(date +%s)
    wait "${BGC_PIDS[$i]}"
    echo "  $1_chain held the run $(( $(date +%s) - tw ))s" | tee -a "$SUMMARY"
    if [ -f "$LOGDIR/.chain_$1.failed" ]; then
      _ef=$(tr -s ' \n' ' ' < "$LOGDIR/.chain_$1.failed" | sed 's/^ *//; s/ *$//')
      [ -n "$_ef" ] && FAILED="$FAILED $_ef"
    else
      echo "  $1 chain FAILED: it ended without reporting its steps" | tee -a "$SUMMARY"
      FAILED="$FAILED $1"
    fi
    echo "  $1 chain collected"
    BGC_PIDS[$i]=""
  done
}

# ---- verdicts ------------------------------------------------------- #
# ! unlink.py --write IS NOT HERE ON PURPOSE -- it is the one non-idempotent
#   step (its idempotent --rejoin half is 04a4_unsplit).
# ★ AN anet MEET UNDER THE WRONG YEAR IS PUT BACK FIRST (2026-09-29). Meet
#   227716, a 2023 Middlesex League race stored as 2025-10-26, put four
#   runners' own high school race on their college seasons. Every step after
#   this one reads the season off results.date, so the date is corrected in
#   place before any of them runs: the athletes' grades say which year it is
#   (anet meet ids are not a clock -- the first cut's dry run), and every
#   fix is a row of meet_date_fix (`engine/meet_date_fix.py --undo <meet>
#   --sport XC` puts one back). Idempotent: a second run moves nothing; a re-scrape that
#   writes the wrong year back is moved again. NOT on the always-run list: a
#   --from run keeps the verdicts steps 01-04 made on the old dates, and a
#   date moved under them would split the pack from them.
#   `engine/meet_date_fix.py` alone is the dry-run report.
# ★ EVERY PAGE READS CLEAN, BEFORE ANYTHING READS IT (owner, 2026-09-30:
#   "THis is the 2nd time I've had data get messed up on this server").
#   Block 491889 of results_tf went bad and fifteen steps failed on
#   DataCorrupted four hours into a run. The server's RAM has no error
#   correction, so it can happen again: amcheck reads every page of every
#   table once (read-only; the site keeps serving), and a damaged one STOPS
#   the run here, with the table, the block and the repair commands. On
#   every --from. The daily backup's pg_dump is the other reader of every
#   page, and it emails on failure.
step 00_integrity     "$PY" -u scripts/integrity_check.py
# ★ WHAT THE SITE SHOWED BEFORE THIS RUN (owner, 2026-10-22: "say what
#   changed in the recomputed ratings vs the last pipeline run"): this
#   season's athlete ratings and every XC course number, kept so 17c0 can
#   tell readers what the recompute moved (What's new). On every --from.
step 00b_run_snapshot "$PY" -u scripts/run_changes.py --snapshot || true
# ★ LAST RUN'S LEVEL GRAPH GOES LIVE HERE, BEFORE ANYTHING READS IT
#   (2026-10-25: "the tables nothing builds"). school_level_graph,
#   race_level and race_top_level were built by hand, now and then; 01c
#   rebuilds them every run in the background as <name>_next, and this
#   swaps a complete set in, all three in one transaction, in seconds. A
#   run with nothing staged changes nothing.
step 00c_level_swap   "$PY" -u engine/level_graph.py --swap-staged
if failed 00_integrity; then
  echo "" | tee -a "$SUMMARY"
  echo "FATAL: 00_integrity found damaged pages (or could not check). Nothing" | tee -a "$SUMMARY"
  echo "       else ran. The repair is in $LOGDIR/00_integrity.log." | tee -a "$SUMMARY"
  summarise 1
fi
step 00_meet_dates    "$PY" -u engine/meet_date_fix.py --write --show 20
step 01_season_year   "$PY" -u engine/season_year.py
# ★ tfrrs XC MEETS PLACED BEFORE THE WEATHER GRID READS THEIR PLACE
#   (2026-10-25). 04e fetches weather for the meets that have gps, and
#   tfrrs meets only get gps from 06a, which ran after it: a meet 06a placed
#   tonight got no weather until the next full run. The same script runs
#   here first (tiers 1-2 on the links that already exist; idempotent,
#   seconds to minutes) and again at 06a, which still catches the meets the
#   linking steps between make placeable.
step 01b_tfrrs_location "$PY" -u scripts/backfill_tfrrs_location.py --apply --out "$LOGDIR"
# ★ THE WEATHER GRID IN THE BACKGROUND FROM HERE (2026-10-18, 23 min off
#   the critical path). It reads the meets' dates and venues (corrected by
#   the two steps above) and writes only the weather grid, which nothing
#   reads until 04f/05; bgjoin collects it right before them. The comments
#   on why it exists sit where it used to run, above 04f.
if [ -x "$WXPY" ]; then
  # ★ BOUNDED TOO (2026-11-12; run 20261111_200338: 5,991 s, and the run
  #   stood at its bgjoin, before 04f/05, for the half hour past the ~70 min
  #   of steps 02-04 in line). It commits every WRITE_BATCH rows and skips
  #   what is stored, so a stop keeps what it fetched and the next run
  #   carries on from there.
  #   XCP_WX_GRID_MIN (60) sets it; the stop is reported as a bound.
  bgstep 04e_weather_grid sh -c 'timeout -k 60 "$1m" "$2" -u backfill/atmost_era5_zarr.py; rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
      echo "04e: stopped at its $1-minute bound (exit $rc) -- what it fetched is stored; the rest waits for the next run"
      exit 0
    fi
    exit "$rc"' sh "${XCP_WX_GRID_MIN:-60}" "$WXPY"
  # ★ AND THE MONTHS THAT STORE HAS NOT REACHED (2026-10-25: the grid
  #   stopped at 2026-06-30 -- the free store runs 3-4 months behind). The
  #   CDS's ERA5T, ~5 days behind, for the days after the store's last one;
  #   no CDSAPI_KEY in the env file and it says so and exits 0.
  # ! BOUNDED (2026-11-03: it sat in the CDS queue for 10 hours, the run
  #   waited for it at bgwait, held .pipeline.lock all that time, and the
  #   nightly skipped two nights). Whatever it fetched in time is kept; the
  #   next run asks for the rest.
  # ★ ITS BOUND IS NOT A FAILURE (2026-11-10; run 20261109_033652 marked it
  #   FAILED: month 1 of 5 came at 03:53, month 2 sat in the CDS queue until
  #   the 90 minutes ran out -- timeout's 124, by design, with the month kept
  #   for the next run). Stopped at the bound, the step says so and passes;
  #   any other exit is still a failure. (Each run asks for the same next
  #   month first, so a queue longer than the bound never gets past it --
  #   noted for the scrapers: submit and collect later, not wait.)
  bgstep 04e2_weather_recent sh -c 'timeout -k 60 "$1m" "$2" -u backfill/era5_cds_recent.py; rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
      echo "04e2: stopped at its $1-minute bound (exit $rc) -- what it fetched is in the grid; the rest waits for the next run"
      exit 0
    fi
    exit "$rc"' sh "${XCP_WX_RECENT_MIN:-90}" "$WXPY"
else
  echo "  04e_weather_grid skipped ($WXPY not found; see deploy/run_pipeline.sh)" | tee -a "$SUMMARY"
fi
# ★ THE LEVEL GRAPH, REBUILT EVERY RUN, BESIDE IT (2026-10-25). About two and
#   a half hours of graph propagation over the race/team graph, written as
#   <name>_next for the NEXT run's 00c to swap in: this run reads one
#   version throughout. It commits after reading results, so it holds no
#   lock on the live tables while it propagates. Collected at the end (bgwait).
bgstep 01c_level_graph "$PY" -u engine/level_graph.py --stage
# ★ WHERE EVERY ATHLETE'S ROWS ARE, BEFORE ANYTHING MOVES THEM (2026-09-26).
#   Twins, tfrrs links and mints all change person ids, and the old id's page
#   404s. 13c0 compares against this and 301s each vanished id to its
#   successor. Two result ids per person per sport; on every --from, because
#   04a (always) moves ids too.
step 01a_person_probe "$PY" -u scripts/person_redirects.py --snapshot
step 02_drop_old      "$PY" -u engine/drop_old.py
# ! BEFORE ANYTHING LABELS A VENUE. meets_tf.venue_name is NULL on a lot of
#   anet track meets, and the engine then prints the LOCATION ID as if it
#   were a venue name. The names are already in meets_tf_meta, or on another
#   meet at the same location -- no scrape needed. Idempotent; a second run
#   finds nothing. database.backfillMeetsTFVenueNames existed for weeks and
#   was called by nothing, which is why the ids kept appearing.
# ⚠ 03b BEFORE grade_sanity: it decides whether "11-12" is grades or ages,
#   and every rule downstream reads that answer.
# ★ 02b, 03 AND 03b SIDE BY SIDE (2026-11-07, owner-approved). Disjoint
#   writes -- meets_tf.venue_name (02b); pro_athlete_season, ncaa_first_season
#   (03); age_band_div, age_band_result (03b) -- and none reads another's:
#   03 reads results*, meets, meets_tf's division / event_short (never
#   venue_name), last run's grade_fix / athlete_season / athlete_named /
#   pro_athlete_season and team_pool (03a's, which stays AFTER 03); 03b reads
#   results*, meets and meets_tfrrs. Their private tables are TEMP (03) or
#   their own (age_band_div). Each keeps its own --from / --skip / summary
#   line (stepsN), and a failed 03 or 03b still stops the run at the people
#   gate. XCP_STREAMS=1 runs them one after another.
# ★ 03 BESIDE THE 04a LINKING CHAIN, ON A SNAPSHOT TAKEN BEFORE IT (2026-11-13;
#   ~25 min off the line). 03, 03b and 03a must read the people as they were
#   before 04a moves ids; scripts/snapshot_hold.py exports a REPEATABLE READ
#   snapshot and every connection those three open imports it
#   (XCP_PG_SNAPSHOT_FILE, scripts/database.py). They write only their own
#   tables (pro_athlete_season, ncaa_first_season, age_band_*, team_pool),
#   none of which the 04a chain reads or writes. Collected after 04a3c,
#   before grade_sanity (age_band_result), 04h1 (ncaa_first_season) and the
#   04g chain. 02b stays in line first: it and 04a both write results_tf
#   rows. XCP_SNAPSHOT_03=0, or a holder that cannot start: today's order.
#   tests/test_snapshot_03_beside_04a.py.
step 02b_tf_venues "$PY" -u scripts/backfill_tf_venues.py --write
_SNAP03=""; _SNAP03_FILE="$LOGDIR/.snapshot_03"; _SNAP03_REL="$LOGDIR/.snapshot_03.release"
if [ "${XCP_SNAPSHOT_03:-1}" != "0" ] && [ "$DRY" -eq 0 ] \
   && { willRun 03_pro_flag || willRun 03b_age_bands || willRun 03a_team_pool; }; then
  rm -f "$_SNAP03_FILE" "$_SNAP03_REL"
  "$PY" -u scripts/snapshot_hold.py "$_SNAP03_FILE" --release "$_SNAP03_REL" \
       --max-seconds "${XCP_SNAPSHOT_03_MAX:-14400}" --parent $$ > "$LOGDIR/03_snapshot_hold.log" 2>&1 9>&- &
  _SNAP03_PID=$!
  for _i in $(seq 1 120); do [ -s "$_SNAP03_FILE" ] && break; kill -0 "$_SNAP03_PID" 2>/dev/null || break; sleep 0.5; done
  if [ -s "$_SNAP03_FILE" ]; then _SNAP03="$_SNAP03_FILE"
  else echo "  03 snapshot not taken (see 03_snapshot_hold.log): 03 runs before 04a" | tee -a "$SUMMARY"
       kill "$_SNAP03_PID" 2>/dev/null; wait "$_SNAP03_PID" 2>/dev/null; fi
fi
_group03() {
  local _e=""; [ -n "$_SNAP03" ] && _e="XCP_PG_SNAPSHOT_FILE='$_SNAP03' "
  stepsN 03_pro_flag "${_e}$PY -u engine/pro_flag.py --skip-dist --write" \
         03b_age_bands "${_e}$PY -u engine/age_band_grades.py --write"
  # ★ THE TEAM POOLS, EVERY RUN (pooling review P2, 2026-10-26). team_pool
  #   (a club with a pro is pro, a team of under 15 athletes ever is pro) was
  #   built only by overnight_fit_pool_solve.sh, so the pack and the fill read
  #   a table as old as that run, keyed on team ids and pros since changed.
  #   After 03 because it reads pro_athlete_season; the pack reads it at 08.
  if [ -n "$_SNAP03" ]; then
    step 03a_team_pool env XCP_PG_SNAPSHOT_FILE="$_SNAP03" "$PY" -u engine/build_team_pool.py --write
  else
    step 03a_team_pool "$PY" -u engine/build_team_pool.py --write
  fi
}
if [ -n "$_SNAP03" ]; then bgchain 03_group _group03; else _group03; fi
# every new tfrrs row gets a person: its athlete id's existing person, its own
# high school career (a freshman), or one minted from the id -- see the script
step 04a_link_tfrrs   "$PY" -u scripts/link_tfrrs_rows.py --apply
# ★ A TEAMLESS anet PROFILE JOINS THE SCHOOL ATHLETE IT IS (owner,
#   2026-09-28/29: Tadhg Murray's Foot Locker West Regional sat on a second
#   profile, "Unknown (WA)", /athlete/13642378; "a Foot-Locker-only rule is
#   too narrow"). A profile whose every row has no team (team 0, Unknown,
#   Unattached, a "Danville CA" hometown) joins the ONE same-name, same-gender
#   school athlete racing within 60 days, when two of rating / region /
#   qualifier agree -- see the script's header for every guard. AFTER 04a
#   (a profile a tfrrs row was just linked to is not a stray) and BEFORE
#   grade_sanity, twins and the pack, which all read who a row belongs to.
#   Always, like 04a: it is also what re-homes a merged profile's NEXT
#   teamless row, which a re-scrape seeds with the old id. Between 01a and
#   13c0, so the old id 301s (it writes the redirect itself as well).
#   XCP_LINK_TEAMLESS=0 turns it off; every move is in person_link_log rule
#   'teamless'; `scripts/link_teamless.py --undo <id>|all` reverses it.
step 04a2_link_teamless "$PY" -u scripts/link_teamless.py --apply
# ★ ONE RUNNER IN BOTH FEEDS IS ONE PERSON (owner, 2026-10-15: "do those
#   two" -- Arkansas's Allen Bonnessen/Bonnesen, Emma Chepngetich and
#   Makenna Herbst, each an anet person beside a tfrrs person 04a minted).
#   A tfrrs person and an anet person join on a run both feeds carried (same
#   day, same time to the printed tenth, same school, one partner) or, for a
#   minted tfrrs person with no shared run, the same name, school and season
#   with exactly one candidate each way; names, sexes, a second time on one
#   day and two careers all refuse. AFTER 04a/04a2 (the persons it joins
#   exist), BEFORE grade_sanity, twins and the pack. Always: it moves ids.
#   XCP_LINK_FEED_TWINS=0 turns it off; every move is in person_link_log
#   rule 'feed_twin'; `scripts/link_feed_twins.py --undo <id>|all` reverses.
[ "${XCP_LINK_FEED_TWINS:-1}" = "0" ] || \
  step 04a2b_feed_twins "$PY" -u scripts/link_feed_twins.py --write
# ★ THE OWNER'S DETACHES, AFTER EVERY LINKER (2026-10-10). A result an
#   athlete reported "not mine" and the owner approved (racecast/fixes.py)
#   sits on a fresh person (result_detach, scripts/person_pins.py). The
#   linkers skip it; this re-applies each decision, so a re-scrape or a
#   relink can never put the row back. Logged in person_link_log rule
#   'detach'; `scripts/person_pins.py --undo-detach <result id>|all`. The
#   grade and school pins need no step: grade_sanity (next) and
#   build_ranking_results read them themselves.
# ★ A SPLIT THE RULES NO LONGER MAKE IS PUT BACK (owner, 2026-10-12, Jack
#   Falkowski split off his own high school career by an unattached "9").
#   unlink.py --write stays by hand; its --rejoin half re-judges every split
#   on record with today's rules and undoes the ones they would not make.
#   Idempotent. Before the pins, so an owner's decision is re-applied last.
#   Logged in person_link_log rule 'unsplit'; `engine/unlink.py --rejoin --undo`.
step 04a4_unsplit     "$PY" -u engine/unlink.py --rejoin --write
step 04a3_pins        "$PY" -u scripts/person_pins.py --apply
# ★ AND THE APPROVED MERGES, AGAIN (owner, 2026-10-18: one merge by hand moved
#   29k rows that had drifted back onto merged-away ids -- an anet row is seeded
#   person_id = athlete_id, and nothing re-applied the merges between approvals)
step 04a3b_merges     "$PY" -u scripts/link_profile_school.py --reapply
# ★ ONE PAGE, A BOY'S RACES AND A GIRL'S (owner, 2026-11-06 Coogan; 2026-11-09
#   Tom Rankin, whose 1986 boys' race was rated in the girls' pool and led the
#   Hereford course's boys' list at 160.7). Was by hand only; now every run,
#   before the backfill, so the moved rows are rated on their own page.
#   Detaches go through person_pins (04a3 re-applies them); undo one with
#   scripts/person_pins.py --undo-detach RESULT_ID.
step 04a3c_gender_split "$PY" -u scripts/gender_split_people.py --write --show 40
# the 03 group (on the pre-04a snapshot), collected; the snapshot released
if [ -n "$_SNAP03" ]; then
  bgchainjoin 03_group
  touch "$_SNAP03_REL"
  wait "$_SNAP03_PID" 2>/dev/null || echo "  03 snapshot hold ended early (03_snapshot_hold.log)" | tee -a "$SUMMARY"
  tail -n 1 "$LOGDIR/03_snapshot_hold.log"; rm -f "$_SNAP03_FILE" "$_SNAP03_REL"
fi

# ★★ THE DISTANCE CHECKS BESIDE THE PEOPLE VERDICTS (2026-11-07, owner-
#    approved: data_checks ~30 min and wrong_distance 20-60 min used to run
#    after the 04 group, in line). 04g0, 04g1, 04g and 04g1b read results*
#    (person ids, times, last run's ratings and pools), meets*, athletes,
#    course_canonical, last run's dist_override and distance_pin, and write
#    engine/corrections.py, data_issue and distance_pin. None of them reads
#    grade_fix, grade_untrusted, ntschool, wheelchair_*, person_gender or
#    result_twin (the people steps' tables), and no people step reads
#    corrections.py, data_issue or distance_pin -- so the four run in order,
#    as one background chain, from the moment the last id mover (04a3b) is
#    done. 04g2_dump_overrides stays where it was, after grade_sanity (which
#    reads dist_override) and after this chain (it reads corrections.py and
#    distance_pin); the chain is collected before the people gate.
#  ! On a people-step abort the chain has already rewritten corrections.py,
#    data_issue and distance_pin. Each is re-judged from scratch every run on
#    the same inputs (results are untouched until 05), so the rerun repeats
#    the same verdicts; dist_override, which the backfill reads, is not
#    rebuilt until 04g2.
_DIST_CHECKS=0
if [ "${XCP_AUDIT_OVERRIDES:-1}" != "0" ] && [ -f engine/corrections.py ]; then
  _DIST_CHECKS=1
fi
_distanceChecks() {
  # ★ AND A MEN'S RACE FILED AT THE WOMEN'S DISTANCE IS PINNED FIRST (owner,
  #   2026-10-13: Al Shirley 2026, men 35:40 vs women 23:17 at "5000 m" --
  #   the junior college men's 8K; engine/gender_pace_pin.py)
  step 04g0_gender_pin "$PY" -u engine/gender_pace_pin.py --write
  # ★ THE DATA CHECKS (owner, 2026-10-13: "so many small data leaks ...
  #   just a pipeline step that can be fixed"): each race against its own
  #   runners (pins a corroborated distance), the two event-name parsers,
  #   one person on both genders -- data_issue, ranked by impact
  step 04g1_data_checks "$PY" -u engine/data_checks.py --write
  step 04g_audit_overrides "$PY" -u engine/audit_overrides.py --rebuild --write
  # ★ THE ONE WRITER OF DISTANCE FIXES (engine/wrong_distance.py, 2026-10-26;
  #   docs/WRONG-DISTANCE-2026-10-25.md). Raw times against each runner's own
  #   season, re-judged every run, into distance_pin; dump_overrides reads it
  #   first. Its circuit breaker writes nothing past 300 adds or retires in
  #   one run (the step still succeeds and says so in its log).
  step 04g1b_wrong_distance "$PY" -u engine/wrong_distance.py --write --top 20 \
       --csv "$LOGDIR/wrong_distance.csv"
}
if [ "${_DIST_CHECKS:-0}" -eq 1 ]; then
  bgchain 04g_checks _distanceChecks
fi

# ⚠ THIS STEP WAS MISSING, AND THAT IS HOW CHAIR ATHLETES CAME BACK (owner,
#   2026-09-01: "somehow wheelchair athletes snuck back into the engine").
#   speed_ratings_db._chairFilter() DEGRADES rather than crashes when
#   wheelchair_person is absent -- it prints one line and rates them -- which
#   is right for an old database and lethal for a pipeline that never builds
#   the table. Nothing else creates it, so every run since it was written has
#   rated chair athletes. It belongs BEFORE the pack: it is a fact about
#   people that the pack reads.
# issue 94: one physical race stored twice is flagged once, by result_id,
# and every reader (engine, pricer, boards, page) anti-joins result_twin
# ★ AND A HIGH SCHOOL RACE ON A COLLEGE SEASON IS FLAGGED HERE TOO (the
#   NESCAC review, 2026-09-29: four college runners carrying one 2025
#   Middlesex League high school race, and a published season 3 points high
#   because of it). Rule 'level_conflict', engine/level_conflict.py. AFTER
#   04a, because 04a's links are what make the conflicts; BEFORE the pack,
#   because the pack is what must not see them. `--from 7` keeps the last
#   full run's flags; `engine/level_conflict.py --write` refreshes just
#   these ones when a run starts past this step.
# gender by the divisions raced under; a person who raced both ways enough
# is two athletes to the pack and the boards (issue 164)
# ★ THREE PEOPLE VERDICTS SIDE BY SIDE (2026-10-25, the step times: 4,292 s
#   + the two small ones, in a row). grade_sanity, wheelchair and gender
#   read the person ids 04a3b leaves and write disjoint tables (grade_fix /
#   grade_untrusted, wheelchair_*, person_gender; docs/PIPELINE-
#   DEPENDENCIES.md). Twins stays AFTER grade_sanity: grade_sanity reads the
#   PREVIOUS run's result_twin on purpose and 04c swaps it. stepsN asks
#   --from per name, so 04b keeps its run-on-every---from rule.
# ★★ AND THE TWINS BUILT BESIDE THEM, SWAPPED AFTER (2026-11-07, owner-
#    approved: the group was 3,762 s and 04c ~31-45 min after it). 04c's
#    rules read results*, meets*, person_link_log, college_directory and
#    person_gender (dup_cross_gender) -- nothing grade_sanity or wheelchair
#    writes -- so its BUILD runs beside them, after 04d (person_gender), and
#    leaves a complete result_twin_new; the SWAP waits for grade_sanity,
#    which reads the previous result_twin throughout, exactly as when 04c
#    ran after the group. A failed (or --skip'd) build is never swapped:
#    04c_twins_swap runs only after a build that succeeded this run, and
#    twin_flag --swap-only refuses a result_twin_new not marked complete.
#    grade_sanity and wheelchair go in the background (bgstep: --from,
#    --skip and _ALWAYS per name, as stepsN), joined before the swap.
skipped 04b_wheelchair && _skipCost 04b_wheelchair
bgstep 04_grade_sanity "$PY" -u engine/grade_sanity.py --write
# ★ COLLEGE FLAGS BESIDE GRADE_SANITY (2026-11-12; run 20261112_173033: 04h
#   held the run 1,589 s -- started after grade_sanity's join and collected
#   21 s later). college_flag reads results' school and grade, the
#   ncaa_first_season of 03 and pair_athlete_season; nothing from here to 05
#   writes those (grade_sanity writes grade_fix, 04c/04d/04b their own
#   tables). season_level reads grade_fix, so it alone waits (below).
bgstep 04h1_college_flags "$PY" -u engine/college_flag.py --write
bgstep 04b_wheelchair  "$PY" -u engine/wheelchair_flag.py --write
step 04d_gender "$PY" -u engine/person_gender.py --write
step 04c_twins        "$PY" -u engine/twin_flag.py --write --no-swap
bgjoin 04_grade_sanity
bgjoin 04b_wheelchair
if ! skipped 04c_twins && ! failed 04c_twins; then
  step 04c_twins_swap "$PY" -u engine/twin_flag.py --swap-only
fi
# ★ THE SEASON LEVELS, REBUILT EVERY RUN (2026-10-25: "the tables nothing
#   builds"). college_first_season / upperclass_first_season (college_flag)
#   and athlete_season_level (season_level, from race_level and tonight's
#   grade_fix) were built by hand; the backfill, the pack and the boards
#   read them. After the person steps (both read person ids and dates) and
#   04_grade_sanity, in the background while 04g runs (which reads neither),
#   and collected before 05, their first reader.
bgjoin 04h1_college_flags                 # season_level reads college_first_season
if failed 04h1_college_flags; then
  bgstep 04h_season_levels sh -c "\"$PY\" -u engine/college_flag.py --write && \"$PY\" -u engine/season_level.py --write"
else
  bgstep 04h_season_levels "$PY" -u engine/season_level.py --write
fi
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
# (started in the background after 01_season_year; collected here, before
#  the refit and the backfill read the grid)
bgjoin 04e_weather_grid
# ★ NOT 04e2 (2026-10-29: one month's CDS request took 6.6 h -- 23,739 s,
#   nearly all of it the CDS queue -- and the backfill waited on it). The
#   recent weather is collected at the end (bgwait) instead: whatever it has
#   written by the time 05 reads the grid is used, the rest on the next run.
#   Rows go in with ON CONFLICT DO NOTHING, so a write beside the backfill's
#   read is safe.
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

# ⚠⚠ A FAILED PEOPLE STEP STOPS THE RUN BEFORE THE BACKFILL (sweep
#    2026-10-10). 03_pro_flag, 03b_age_bands, 04_grade_sanity, 04b_wheelchair,
#    04c_twins and 04d_gender each write a table the backfill and the pack
#    read to decide WHO a row is and WHICH pool it sits in -- and a failure
#    only logged, so 05 normalised every row on last run's verdicts (or on
#    none: a half-written grade_fix) and the solve published them. Same
#    shape as the 05_backfill gate below. XCP_IGNORE_PEOPLE_FAIL=1 carries
#    on anyway -- only when you have read the log and last run's tables are
#    the ones you want.
# the distance checks (04g0..04g1b, started after 04a3b): collected here, so
# the gate and 04g2 see their verdicts and failures
bgchainjoin 04g_checks
_PEOPLE_FAILED=""
for _s in 03_pro_flag 03b_age_bands 04_grade_sanity 04b_wheelchair 04c_twins 04c_twins_swap 04d_gender; do
  failed "$_s" && _PEOPLE_FAILED="$_PEOPLE_FAILED $_s"
done
if [ "$DRY" -eq 0 ] && [ -n "$_PEOPLE_FAILED" ]; then
  if [ "${XCP_IGNORE_PEOPLE_FAIL:-0}" = "1" ]; then
    echo "  ⚠ FAILED:$_PEOPLE_FAILED, and XCP_IGNORE_PEOPLE_FAIL=1 --" \
         "continuing on the tables the PREVIOUS run wrote." | tee -a "$SUMMARY"
  else
    echo "" | tee -a "$SUMMARY"
    echo "  ABORTING:$_PEOPLE_FAILED failed, so the backfill and the pack" \
         "would read stale or half-written people tables." | tee -a "$SUMMARY"
    for _s in $_PEOPLE_FAILED; do
      echo "  read: $LOGDIR/$_s.log" | tee -a "$SUMMARY"
    done
    echo "  then: rerun from the failed step, or" \
         "XCP_IGNORE_PEOPLE_FAIL=1 ... to continue on last run's tables" | tee -a "$SUMMARY"
    summarise 1
  fi
fi

# ! THE BACKFILL RUNS AFTER grade_sanity, NOT BEFORE -- it resolves pools from
#   grade_fix, and a disagreement writes normalized_time on the wrong SCALE
#   (measured at a 64% rating error, frozen into the row).
# ★ XCP_DISTANCE_BY=ability (2026-09-29; the full backfill's default from
#   2026-11-08 to 11-09, pool again now, see 05 below) normalises every row
#   with the curve by ability (engine/data/distance_ability.pkl, fitted by
#   engine/fit_distance_ability.py) onto 5000 m, whatever its pool. The
#   backfill records the mode it wrote (engine/data/distance_applied_*.json)
#   and every later step and the site read that record, so the switch is
#   set on the run that CHANGES it -- `--from 5` or earlier, both sports in
#   full -- and XCP_DISTANCE_BY=pool the same way reverts it. The backfill
#   refuses a partial (--only-changed) write in a new mode, and refuses
#   'ability' without its artifact. Restart the site afterwards.
# ★ AND THE PER-POOL RESIDUAL COMES WITH IT (owner, 2026-09-29: "adopt the
#   ability curve with the per-pool residual applied"): by ability a row in
#   pool p also gets p's extra exponent at equal ability, delta_p *
#   log(d/5000) (high school 0; only the pools the fitter found significant
#   with support -- distance_ability.pkl's residual_applied). It needs an
#   artifact fitted with the joint fit (fit_distance_ability.py since
#   2026-09-29; the backfill refuses an older one). XCP_DISTANCE_RESIDUAL=0
#   on the same `--from 5` run writes the curve alone instead; the record
#   carries which ("residual" in distance_applied_*.json), so the site and
#   the solve follow it with nothing in /etc/xc-predictor.env.
# ★ EVERY DISTANCE OVERRIDE IS RE-JUDGED BEFORE THE BACKFILL APPLIES IT
#   (owner, 2026-10-13: Nike Hole in the Wall 2022, the varsity 5K "corrected"
#   to 4506 m -- 2.8 miles, the JV races' distance -- and all 302 runners
#   then read 8-12% under their own level). audit_overrides removes an
#   override that is an outlier for its distance class AND whose removal
#   brings the field back to its runners' normal (judged on last run's rows,
#   which carry the override's effect); dump_overrides rebuilds dist_override
#   from what is left, and 05 re-normalises with it. It ran only by hand.
#   ! Needs engine/corrections.py (travels out of band, .gitignore); without
#     it both are skipped. XCP_AUDIT_OVERRIDES=0 skips them.
#   ⚠ XCP_BACKFILL_XC=changed does not re-normalise a division whose
#     override was removed; the next full XC pass does.
if [ "${_DIST_CHECKS:-0}" -eq 1 ]; then
  # (04g0, 04g1, 04g and 04g1b ran in the background from after 04a3b: the
  #  _distanceChecks chain above, collected before the people gate)
  if ! failed 04g_audit_overrides; then
    step 04g2_dump_overrides "$PY" -u engine/dump_overrides.py
  fi
else
  echo "  04g_audit_overrides skipped (XCP_AUDIT_OVERRIDES=0 or no engine/corrections.py)" | tee -a "$SUMMARY"
fi

bgjoin 04h_season_levels
# a race a report moved to another course (scripts/set_race_course.py), put
# back on it before the backfill prices every row
step 04y_course_pins "$PY" -u scripts/set_race_course.py --apply
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
    # ★ THE CURVE BY ABILITY IS THE DEFAULT NOW (owner, 2026-11-08: "if it's
    #   better please just make it regular"). On 1.7M held-out pairs
    #   (scripts/diag_one_scale.py --holdout) it beat the pool curves for
    #   high school and middle school in both sports, and cut the high
    #   school girls' XC bias from -2.7% to -0.3%; college about tied. Only
    #   the FULL backfill writes it -- a partial write in a new mode is
    #   refused -- and records it, so every later step, the nightly and the
    #   site follow the record. XCP_DISTANCE_BY=pool on a run reverts.
    # ! BACK TO THE POOL CURVES BY DEFAULT (owner, 2026-11-09: "undo the
    #   model changes that caused these issues so I can do a deep ratings
    #   dive"). The ability run lifted every level -- middle school +3.95,
    #   college +1.53 on average, the college women's board reading 14:11
    #   5Ks -- with the per-pool residual (ms_m -0.096, k ~0.99) as the
    #   largest piece. XCP_DISTANCE_BY=ability on a run still writes it.
    _DBY="${XCP_DISTANCE_BY:-pool}"
    steps2 05_backfill_xc "env XCP_DISTANCE_BY=$_DBY $PY -u backfill/backfill_normalize.py --sport XC --apply" \
           05_backfill_tf "env XCP_DISTANCE_BY=$_DBY $PY -u backfill/backfill_normalize.py --sport TF --apply"
  fi
fi

# ⚠⚠ A FAILED BACKFILL STOPS THE RUN (2026-09-22). 05_backfill_xc could not
#    take the lock to swap results in, failed after 50 minutes -- and the run
#    carried on for ELEVEN MORE HOURS: packed, solved and published boards on
#    XC times normalised by the OLD curve and the OLD pools, which is the
#    exact thing the chain above the backfill exists to prevent (curve ->
#    backfill -> solve). One FAILED line in the summary was the only sign.
#
#    Same shape as the 08_golive abort below. <table>_new survives a failed
#    swap, so the rerun resumes at the swap rather than rebuilding.
#    XCP_IGNORE_BACKFILL_FAIL=1 carries on anyway -- only when you have
#    decided the times already in results are the ones you want solved.
if [ "$DRY" -eq 0 ] && { failed 05_backfill_xc || failed 05_backfill_tf; }; then
  if [ "${XCP_IGNORE_BACKFILL_FAIL:-0}" = "1" ]; then
    echo "  ⚠ 05_backfill FAILED, and XCP_IGNORE_BACKFILL_FAIL=1 -- continuing" \
         "on the normalized_time the PREVIOUS backfill wrote." | tee -a "$SUMMARY"
  else
    echo "" | tee -a "$SUMMARY"
    echo "  ABORTING: 05_backfill failed, so results.normalized_time is not" \
         "what this run was meant to solve. Nothing after this step is worth" \
         "running on it." | tee -a "$SUMMARY"
    echo "  read: $LOGDIR/05_backfill_xc.log / 05_backfill_tf.log (the swap" \
         "prints the sessions holding the table)" | tee -a "$SUMMARY"
    echo "  then: bash deploy/run_pipeline.sh --from 5 ..." | tee -a "$SUMMARY"
    summarise
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
#   (2026-09-26: two set-based passes now -- a GROUP BY, then only the rows
#   outside the band -- instead of ~300 batches through Python.)
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
# ★ THE PROFESSIONAL ABILITY GATE (owner, 2026-09-22: "if they're sub
#   14:00? for men, or sub 15:30? for women put in pro, otherwise trust
#   grade"). pool_resolve reads pro_ability_season to decide who belongs in
#   the pro pool; only the old Windows chain (run_pipeline.ps1) ever built
#   it, so on this server the gate saw last season's table or none at all
#   (sweep, 2026-09-26).
# ⚠ AFTER 05, AND BEFORE 07 -- the ps1's own reasoning: it streams through
#   the loader whose WHERE needs normalized_time (05 writes it), and 07_pack
#   resolves the pools, so a table built after 07 is read a run late.
step 05c_pro_ability  "$PY" -u engine/build_pro_ability.py --write
# ★ THE CLUB TABLES IN THE BACKGROUND FROM HERE (2026-11-13, the 05-08
#   review): 07_pack built them in line before it forked, ~7-10 min. Their
#   inputs -- the rows' person, date, team, school and grade,
#   pro_athlete_season (03) and anet_team's levels -- are not written by
#   05c, 06*, or 06c (05c writes pro_ability_season; 06 meet names and
#   places; 06c impossible_*). Saved to the run's copy (_runCached) for 07
#   and 09b; collected before 07. --from 7 skips this and 07 builds them.
export XCP_CLUB_CACHE="$LOGDIR/.club_cache"
bgstep 05d_club_tables "$PY" -u -c 'import sys; sys.path.insert(0, "engine"); import speed_ratings as S; S.loadClubPros(); S.loadClubMajority()'

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
# ★ EVERY tfrrs XC MEET GETS ITS COURSE'S GPS FIRST (owner, 2026-10-22:
#   all 1,994 college races of 2026 had none -- TFRRS pages carry no
#   coordinates -- so none found its course and every college prediction
#   converted at difficulty 0.0). The same meet on athletic.net, or a known
#   course by its exact name and state; never a guess (the fuzzy pass stays
#   by hand). Idempotent; the leftovers list goes to the run's log folder.
step 06a_tfrrs_location "$PY" -u scripts/backfill_tfrrs_location.py --apply --out "$LOGDIR"
# ★ EVERY COURSE GETS A CANONICAL ID BEFORE THE PACK (2026-09-26: Penrose
#   Park had no difficulty -- this table was last built by hand, before the
#   course was first raced, and the build was never in the pipeline).
#   --incremental keeps every existing course's id, so course_difficulties
#   still points at the right courses while the solve runs.
# a race with a course name but no coordinates takes its course's place
# (scripts/place_unplaced_meets.py), so it matches a course, gets weather and
# joins the solve -- before the canonical ids are built from (name, place)
step 06a2_place_by_name "$PY" -u scripts/place_unplaced_meets.py --write
step 06b_course_canonical "$PY" -u scripts/build_course_canonical.py --incremental --apply
# ★ THE RACES THE RECORD CONDEMNED (owner, 2026-09-14): a time faster than
#   the world record allows, outside the college pools, marks its whole
#   race; the pack, the fill and the boards all anti-join the table.
step 06c_impossible   "$PY" -u engine/impossible_race.py --write
# ★★ FOUR LATE STEPS THAT READ NO RATING, BESIDE THE PACK (2026-11-07,
#    owner-approved). Each reads only what is final by now -- person ids
#    (the last mover is 04a3b), names, schools, dates, teams and the meet
#    tables (06, 06a, 06a2 are done) -- and writes a table nothing before
#    its old place reads:
#      13c0_person_redirects  person_probe, results*, athletes -> person_redirect
#                             (read by the sitemap step and the athlete page)
#      13c0_search_helpers    results* (person, date, school, name), athletes
#                             -> athlete_named / athlete_agg / athlete_school
#                             (read by the search index step)
#      10b0_tfrrs_link        results*, anet_team, school_team_link
#                             -> school_team_link (read by the school identity step)
#      10e_meet_units         meets, meets_tf, meets_tfrrs -> meet_unit
#                             (read by the site and 13i only)
#    Nothing from here to their old places writes results*' ids, names,
#    schools or dates, athletes, anet_team or the meet tables (08 and 09b
#    write ratings only), so each builds the same table it built there.
#  ! JOINED BEFORE 08_golive: its heap rebuild of results ends in a swap
#    siege that terminates whatever still reads results.
# athlete ids that vanished since 01a -> the id their rows went to (the
# athlete page 301s them; see scripts/person_redirects.py)
bgstep 13c0_person_redirects "$PY" -u scripts/person_redirects.py --resolve
# ★ THE SEARCH INDEX'S THREE SOURCE TABLES, REBUILT FIRST (owner,
#   2026-10-11: a college-only page "doesn't show up on search results").
#   athlete_named / athlete_agg / athlete_school were built once by hand and
#   never again, so nobody made since could be found. One scan of the
#   results; racecast/build_search_helpers.py.
bgstep 13c0_search_helpers "$PY" -u racecast/build_search_helpers.py
# ! 10b0 BEFORE 10b, BECAUSE 10b READS IT (see the board steps below)
bgstep 10b0_tfrrs_link "$PY" -u scripts/link_tfrrs_to_anet.py --write
# 10e marks which meets are championships (issue 34)
bgstep 10e_meet_units "$PY" -u racecast/build_meet_units.py
# ★ THE CLUB TABLES ONCE FOR THE RUN (2026-11-12; speed_ratings._runCached):
#   07_pack builds them and 09b_fill / 09b_fill_tf read its copy. Named here,
#   after every step that writes their inputs (03 pro seasons, 04 links,
#   05 backfill), so no earlier step can save a copy the later ones outdate.
export XCP_CLUB_CACHE="$LOGDIR/.club_cache"
bgjoin 05d_club_tables
step 07_pack          "$PY" -u engine/speed_ratings.py --sport merged --cache --pack-only
bgjoin 13c0_person_redirects
bgjoin 13c0_search_helpers
bgjoin 10b0_tfrrs_link
bgjoin 10e_meet_units

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
# the published ratings' distance mode before the go-live (see 'restarting
# the site' after 08_golive)
_ratedKey() {
  "$PY" - "$ROOT/engine/data" <<'PYEOF' 2>/dev/null || echo "?"
import json, os, sys
out = []
for sp in ("XC", "TF"):
    try:
        r = json.load(open(os.path.join(sys.argv[1], f"distance_rated_{sp}.json")))
    except (OSError, ValueError):
        r = {}
    m = r.get("distance_by") or "pool"
    out.append(m + ("+residual" if m == "ability" and r.get("residual") else ""))
print(",".join(out))
PYEOF
}
[ "$DRY" -eq 0 ] && _RATED_AT_START=$(_ratedKey)

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
  # ★ XCP_BRACKET_WINDOW (days, default 21): how far either side of a race
  #   the engine looks for the athlete's OTHER rows when forming their level.
  #   run_joint has taken --bracket-window since it existed and this file has
  #   never passed it, so the knob was unreachable from a pipeline run
  #   (2026-09-19). MEASURED on the same held-out rows, which is the only fair
  #   way to compare it since a wider window also changes COVERAGE:
  #
  #       571,290 rows covered by all, against a 21-day baseline:
  #         21  0.041938 | 30  0.041401 | 45  0.041434 | 60  0.041603
  #         90  0.041658
  #
  #   All four beat 21 and the curve TURNS OVER: an interior optimum at 30-45
  #   (a tie at 0.08% apart), declining after. solve_env.sh sets 30.
  #   Separately, coverage rises 63.1% -> 69.3% at 45.
  #   Read the headline sds and 45 looks WORSE (0.042507 against 0.041938),
  #   because it is scoring 6.2 points more of the corpus and the rows it adds
  #   are the ones with no sibling race within 21 days -- the hardest there
  #   are. Use scripts/bracket_holdout.py --dump/--compare, never the
  #   headlines, to judge this one.
  # ★ XCP_BRACKET_PRIOR: the bracket engine's course prior in races, per
  #   group (XC, outdoor track, indoor track). Default "fit": read from
  #   the courses with 2+ races and printed; "XC=1,TF:out=2.5,TF:in=1"
  #   states them; one number states every group (bracket_engine.parsePrior).
  # ★ XCP_DIST_WALK: sd per unit log-distance of the random walk tying each
  #   event offset to its neighbouring distance classes (default 0.02,
  #   js.DIST_WALK_SD; 0 off). The 1000, 2000 and 6000 no longer wander alone.
  # ★ XCP_BRACKET_PLACE_RADIUS (metres, default 400; 0 off) and
  #   XCP_BRACKET_PLACE_PRIOR (races, default 2): the place prior -- courses
  #   of one kind within the radius rest on each other before the sport's
  #   average (bracket_engine.placeClusters). Needs a pack built with the
  #   course coordinates (07_pack from 2026-09-14 on).
  # ★ XCP_BRACKET_PLACE_VENUE (1 default, 0 off; 2026-09-29): one canonical
  #   XC course's cells at other distances are a place too, so a new layout
  #   with one meet (Mt. SAC's 4828 m) is shrunk toward its venue, not the
  #   average course (bracket_engine VENUE PRIOR).
  # ★ XCP_BRACKET_SIBLING_TOL (fraction, default off; 2026-09-29): one
  #   course's cells at listed distances within this fraction share one
  #   history, each keeping its own cell (bracket_engine.SIBLING_DIST_TOL).
  #   Off because Mt. SAC's 4715 m and 4828 m are different layouts.
  # ★ XCP_COURSE_SCALE (2026-09-15): the per-sport multiplier on the bracket
  #   engine's course effects, 'fit' by default (since 2026-09-28 from the
  #   tilt-by-races rows of courses with 4+ races, where the prior's in-sample
  #   signature does not pose as a scale; 'bands' is the old all-voter
  #   reading that put XC at x1.11), 'off', or 'XC=1.1,TF=1'. XCP_SPORT_LEVEL_POOLS='college=0,hs=0.008,
  #   ms=0.012,elem=0.015' states the fall-to-spring gain per pool level
  #   that the go-live shifts the track rows to show; scripts/
  #   sport_level_fit.py measures it from the boards.
  # ★ XCP_BRACKET_INDOOR_MODE=geometry (2026-09-29, a flag; pin stays the
  #   default): each indoor geometry class -- under 200m, flat 200m, banked
  #   200m, 300m+, unknown -- is pinned on its MEASURED level from
  #   engine/data/indoor_geometry_levels.json (XCP_BRACKET_INDOOR_LEVELS for
  #   another file). Measure first:
  #     scripts/indoor_outdoor_check.py --by geometry --write-levels
  #   run_joint refuses to start without the file, and replaces
  #   XCP_INDOOR_LEVEL with the class levels' row-weighted mean so the joint
  #   solve and the engine assert one indoor level (bracket_engine.
  #   geometryJointLevel).
  # ★ XCP_RACE_EFFECT_SPORTS (2026-09-28): unset = the race-day term is in
  #   no rating (2026-09-06). 'XC:fast' puts a FAST XC day into the rating
  #   and leaves slow days out (joint_golive.dayModes says why).
  # ★ THE RACE-DAY TRIAL (owner, 2026-10-01: "throw race day on, then we
  #   can decide if this is final methodology"). Three switches, all off
  #   unless set:
  #     XCP_RACE_KEY=venue          one day effect per VENUE and day, every
  #                                 race there (varsity, JV, boys, girls, every
  #                                 distance), so one tactical race cannot move
  #                                 it. Changes the solve; 08a and the
  #                                 scorecard fit it too.
  #     XCP_RACE_EFFECT_SPORTS=XC   the day in XC ratings (track stays out)
  #     XCP_RACE_EFFECT_OWN         leave-out (default): each runner gets the
  #                                 day as the REST of the field ran it, so no
  #                                 one's own time moves their own day
  #                                 (js.raceEffectLeaveOneOut); 'in' = as fitted
  #   The day is the residual AFTER measured weather (the normalisation takes
  #   the weather off first), shrunk toward zero, so it starts from the
  #   weather. The go-live prints "day-term consistency": whether crediting
  #   the day makes an athlete's races agree with each other (lower scatter)
  #   or not. Set XCP_RACE_DAY_SPORTS=XC for the site too (the hover's words).
  # ★ XCP_TRACK_LEVEL_BY_POOL=0 leaves the college-only and high-school-only
  #   tracks at the level the linkage gave them; default 1 recentres each host
  #   population's outdoor tracks to the same zero (run_joint.trackPopulationShift).
  # ★ THE TWO 2026-09-29 SWITCHES (owner: "try to be safe and test it"),
  #   both OFF unless set, both carried by 08a so the holdout scores the
  #   model that ships:
  #   XCP_SEASON_TIE=1   ties consecutive athlete-seasons of one person by a
  #                      random walk fitted per transition (joint_solve.
  #                      SEASON_TIE_BANDS); a two-race season leans on its
  #                      neighbours, a full one barely moves.
  #   XCP_TILT_SCALE=hs  the course tilt reads the HS-equivalent rating
  #                      (joint_solve.TILT_SCALES). The site reads the same
  #                      variable (racecast/tilt.py, conversions._tilt), so
  #                      set it in /etc/xc-predictor.env and restart the site.
  #   Score them first: scripts/ablation_ladder.py --only base,season-tie,
  #   tilt-hs, then scripts/switch_scorecard.py on the rungs' dumps.
  # ★ XCP_DIFFICULTY=bracket publishes the bracket engine's course numbers
  #   (run_joint.bracketDifficulties): the solve still fits everything
  #   else, the courses come from the owner's method, the abilities are
  #   recomputed to match. Default joint. The holdout and the ladder score
  #   the joint solve either way; 08d compares the two on the same rows.
  # the flags: deploy/joint_flags.sh (one definition with the replays)
  . "$ROOT/deploy/joint_flags.sh"
  jointFlags
  # ★ ADMITTED THROUGH THE MEMORY LEDGER (scripts/jobrun.py, 2026-11-09):
  #   the go-live reserves XCP_GOLIVE_GB (60) and waits until that and
  #   XCP_MEM_RESERVE_GB (16) for the site are free after everyone else's
  #   reservations. A running engine replay in its way stops itself at once
  #   (it is retried later); anything else is waited for, up to
  #   XCP_GOLIVE_MEM_WAIT_MIN (90), and then the go-live runs anyway.
  STEP_MEM="--gb ${XCP_GOLIVE_GB:-60} --priority --wait-min ${XCP_GOLIVE_MEM_WAIT_MIN:-90}" \
  step 08_golive        "$PY" -u engine/run_joint.py --golive "${JOINT_FLAGS[@]}"
  # ★★ THE EVIDENCE STEPS RUN BESIDE THE BOARDS, NOT BEFORE THEM (owner,
  #    2026-10-07: "make it faster"). 08a_holdout, 08a_sport_holdout,
  #    08b_ladder, 08d_diagnose and 08c_anchor_check publish nothing: they
  #    read the pack and the solve's state file and write reports. In run
  #    20261006_120609 they were 451 minutes between the go-live and the
  #    first board step (08b 351, 08a 48 + 51). Now they run in ONE
  #    background chain, in this order, while 09 onward builds the site, and
  #    are collected just before 17c_report, the one step that reads their
  #    logs (run_checklist opens none; 2026-11-07).
  #  ! ONE CHAIN, NOT FIVE bgsteps: each is a solve over the pack; side by
  #    side they would hold several packs in memory at once.
  #  ! AT nice 19 (XCP_EVIDENCE_NICE): the board steps win the CPU whenever
  #    both want it; with the boards done, the chain has the box.
  #  ! A FAILURE STILL COUNTS: the chain writes its failed steps to a file
  #    that the collection point adds to FAILED, as before.
  #  XCP_EVIDENCE_BG=0 runs them in line, where they used to be.
  _evidence() {
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
      ${XCP_NO_DIST_TABLE:+--no-dist-table} \
      ${XCP_TILT_SCALE:+--tilt-scale "$XCP_TILT_SCALE"} \
      ${XCP_FORM_TREND:+--form-trend "$XCP_FORM_TREND"} \
      ${XCP_RACE_KEY:+--race-key "$XCP_RACE_KEY"} \
      $([ "${XCP_SEASON_TIE:-0}" = "1" ] && echo --season-tie) || true
  # ★ THE CROSS-SPORT HOLDOUT (owner, 2026-10-04: "we holdout certain things
  #   to test the actual solve, but we also don't holdout things like the
  #   conversions or what you think someone will run xc vs track"). The
  #   track season of 10% of dual-sport athlete-seasons is held out whole
  #   and predicted from their cross country alone; the log's "cross-sport
  #   BIAS" table is the conversion's error by pool, event and ability, on
  #   races the model never saw. Same settings as 08a; skipped with 08a.
  #   XCP_SPORT_HOLDOUT=sport-xc asks the reverse (XC from track).
  step 08a_sport_holdout "$PY" -u engine/run_joint.py --holdout-only \
      --holdout-kind "${XCP_SPORT_HOLDOUT:-sport}" --sample-pct "${XCP_HOLDOUT_PCT:-25}" \
      --outer "${XCP_OUTER:-5}" --probes 0 --altitude \
      ${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"} \
      ${XCP_IMPORTANCE:+--importance "$XCP_IMPORTANCE"} \
      ${XCP_NO_IMPORTANCE:+--no-importance} \
      ${XCP_NO_INDOOR:+--no-indoor} \
      ${XCP_INDOOR_LEVEL:+--indoor-level "$XCP_INDOOR_LEVEL"} \
      ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} \
      ${XCP_ERA_DRIFT:+--era-drift "$XCP_ERA_DRIFT"} \
      ${XCP_NO_DIST_TABLE:+--no-dist-table} \
      ${XCP_TILT_SCALE:+--tilt-scale "$XCP_TILT_SCALE"} \
      ${XCP_FORM_TREND:+--form-trend "$XCP_FORM_TREND"} \
      ${XCP_RACE_KEY:+--race-key "$XCP_RACE_KEY"} \
      $([ "${XCP_SEASON_TIE:-0}" = "1" ] && echo --season-tie) || true
  # ★ THE DISTANCE HOLDOUT (owner, 2026-10-07: "how can we score the accuracy
  #   of the distance spline"): one whole distance of 10% of multi-distance
  #   athlete-seasons, predicted from their other distances; the log's
  #   "distance holdout BIAS" tables are the distance law's and the event
  #   offsets' error by distance and by the gap to the nearest distance raced.
  #   Every held-out run also prints the error by month (the season curve).
  #   In the background chain; XCP_DIST_HOLDOUT=0 skips it.
  if [ "${XCP_DIST_HOLDOUT:-1}" != "0" ]; then
    step 08a_distance_holdout "$PY" -u engine/run_joint.py --holdout-only \
        --holdout-kind distance --sample-pct "${XCP_HOLDOUT_PCT:-25}" \
        --outer "${XCP_OUTER:-5}" --probes 0 --altitude \
        ${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"} \
        ${XCP_IMPORTANCE:+--importance "$XCP_IMPORTANCE"} \
        ${XCP_NO_IMPORTANCE:+--no-importance} \
        ${XCP_NO_INDOOR:+--no-indoor} \
        ${XCP_INDOOR_LEVEL:+--indoor-level "$XCP_INDOOR_LEVEL"} \
        ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} \
        ${XCP_ERA_DRIFT:+--era-drift "$XCP_ERA_DRIFT"} \
        ${XCP_NO_DIST_TABLE:+--no-dist-table} \
        ${XCP_TILT_SCALE:+--tilt-scale "$XCP_TILT_SCALE"} \
      ${XCP_FORM_TREND:+--form-trend "$XCP_FORM_TREND"} \
        ${XCP_RACE_KEY:+--race-key "$XCP_RACE_KEY"} \
        $([ "${XCP_SEASON_TIE:-0}" = "1" ] && echo --season-tie) || true
  fi
  # ! A RUNG IS A SOLVE (2026-09-12: "08b takes over 6 hours ... gets
  #   stuck"). Each rung solves XCP_LADDER_PCT of the athletes, the era
  #   rungs on three times the cells, and until today nothing was printed
  #   while one ran. The ladder now runs its CORE set (seven rungs) by
  #   default, streams each rung to engine/data/ladder_logs/<rung>.log with
  #   a heartbeat in this log, and kills a rung past XCP_RUNG_TIMEOUT
  #   seconds (default 7200). XCP_LADDER_ALL=1 runs every rung;
  #   XCP_LADDER_ONLY=base,no-importance runs a named subset;
  #   XCP_LADDER_TUNE=1 runs the tuning grid (era length and drift, the
  #   distance walk, one step either side of what ships -- see TUNE in
  #   scripts/ablation_ladder.py).
  # ★★ ONLY WHEN THE MODEL CHANGED (2026-10-05: 18,193 s of run
  #    20261004_125010's 17h44m). The ladder answers "does switch X help?"
  #    and the answer moves when the engine's code or settings move, not
  #    when a week of races arrives. Its stamp is a hash of engine/*.py,
  #    deploy/solve_env.sh, the ladder itself and the XCP_ settings it is
  #    run with (the model's, not the database's or the box's); an
  #    unchanged stamp skips it, saying so. XCP_LADDER=1
  #    forces it, XCP_LADDER=0 skips it.
  LADDER_STAMP_FILE="$ROOT/engine/data/ladder_stamp"
  LADDER_STAMP=$( { cat "$ROOT"/engine/*.py "$ROOT/deploy/solve_env.sh" \
                        "$ROOT/scripts/ablation_ladder.py"
                    # the model's settings only, not the box's plumbing (XCP_ALERT*: the
                    # follow digest's switch and secret, 2026-10-10)
                    env | grep '^XCP_' | grep -vE '^XCP_(DB_|MAIL|ADMIN|NICE|THREADS|LIVE|LADDER|WEATHER_FIT|STREAMS|COURSE_SHARDS|ENV|PYTHON|INDEXNOW|LACCTIC|ALERT)' | sort; } \
                  | sha1sum | cut -c1-16)
  LADDER_RUN=1
  case "${XCP_LADDER:-auto}" in
    0) LADDER_RUN=0; LADDER_WHY="XCP_LADDER=0" ;;
    1) LADDER_WHY="XCP_LADDER=1" ;;
    *) if [ "$(cat "$LADDER_STAMP_FILE" 2>/dev/null)" = "$LADDER_STAMP" ]; then
         LADDER_RUN=0; LADDER_WHY="engine, settings and ladder unchanged since its last run"
       # ★ AT MOST WEEKLY (owner, 2026-10-12: "speed up other parts of the
       #   pipeline too"; 08b took 12,305 s on 20261011). Any edit to an
       #   engine file -- a grade rule -- changed the stamp and bought a
       #   three-hour report. It is a report the owner reads to choose a
       #   switch, not something a run uses; a week-old one answers the
       #   same question. XCP_LADDER_MIN_DAYS sets the gap, XCP_LADDER=1
       #   forces a run.
       elif [ -f "$LADDER_STAMP_FILE" ] && [ -n "$(find "$LADDER_STAMP_FILE" -mtime -"${XCP_LADDER_MIN_DAYS:-7}" 2>/dev/null)" ]; then
         LADDER_RUN=0; LADDER_WHY="last run under ${XCP_LADDER_MIN_DAYS:-7} days ago (weekly at most)"
       else
         LADDER_WHY="the model changed since its last run"
       fi ;;
  esac
  if [ "$LADDER_RUN" -eq 1 ]; then
    echo "  08b_ladder runs: $LADDER_WHY"
    step 08b_ladder       "$PY" -u scripts/ablation_ladder.py \
        --pct "${XCP_LADDER_PCT:-15}" --outer "${XCP_OUTER:-5}" \
        --rung-timeout "${XCP_RUNG_TIMEOUT:-7200}" \
        ${XCP_LADDER_ALL:+--all} \
        $([ "${XCP_LADDER_TUNE:-0}" = "1" ] && echo --tune) \
        ${XCP_LADDER_ONLY:+--only "$XCP_LADDER_ONLY"} || true
    case " $FAILED " in
      *" 08b_ladder "*) ;;
      *) [ "$DRY" -eq 1 ] || [ -n "${XCP_LADDER_ONLY:-}" ] || [ "${XCP_LADDER_TUNE:-0}" = "1" ] || echo "$LADDER_STAMP" > "$LADDER_STAMP_FILE" ;;
    esac
  else
    echo "  08b_ladder skipped ($LADDER_WHY; XCP_LADDER=1 forces it)" | tee -a "$SUMMARY"
  fi
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
  }
  EVID_PID=""
  EVID_FAILED_FILE="$LOGDIR/.evidence_failed"
  # ★ XCP_EVIDENCE=0 SKIPS THE CHAIN (owner, 2026-10-08: "speed it up" --
  #   two runs back to back, the first only to settle data). The three
  #   holdouts alone were 8,717 s of solves beside the boards in the last
  #   run, competing with 10_rankings for memory and disk; nothing they
  #   write is read by the site. The scorecard (17c) then reports no fair
  #   tests for this run.
  # ★ ONCE A WEEK (2026-11-12; owner: "speed it up more"). Three holdout
  #   solves of 34-38 GB, the ladder and the diagnostics ran on EVERY run,
  #   beside the boards the run waits for -- the same cores, the same memory
  #   bus. They publish nothing; the model they score changes by a commit,
  #   not by a night's data. A run within XCP_EVIDENCE_DAYS (7) of the last
  #   one whose holdout passed skips them, as XCP_EVIDENCE=0 does (17c then
  #   reports no fair tests for this run). XCP_EVIDENCE=force runs them.
  _evRecent=""
  if [ "${XCP_EVIDENCE:-1}" = "1" ]; then
    _evRecent=$(find "$ROOT/logs" -mindepth 2 -maxdepth 2 -name summary.log \
                  -mtime -"${XCP_EVIDENCE_DAYS:-7}" 2>/dev/null \
                | xargs -r grep -l "08a_holdout ok" 2>/dev/null | head -1)
  fi
  if [ "${XCP_EVIDENCE:-1}" = "0" ]; then
    echo "  08a..08d (holdouts, ladder, diagnostics) skipped (XCP_EVIDENCE=0)" | tee -a "$SUMMARY"
  elif [ -n "$_evRecent" ]; then
    echo "  08a..08d (holdouts, ladder, diagnostics) skipped -- last scored under" \
         "${XCP_EVIDENCE_DAYS:-7} days ago ($(dirname "$_evRecent")); XCP_EVIDENCE=force runs them" \
      | tee -a "$SUMMARY"
  elif [ "${XCP_EVIDENCE_BG:-1}" = "1" ] && [ "$DRY" -eq 0 ] && [ "${FROM:-0}" -le 8 ]; then
    echo ""
    echo "  08a..08d (holdouts, ladder, diagnostics) started IN THE BACKGROUND at nice" \
         "${XCP_EVIDENCE_NICE:-19}; their lines carry [evidence]"
    (
      FAILED=""
      # the summary says these ran beside the run (pipeline_profile reads it)
      STEP_NOTE="background, evidence chain"
      NICE="nice -n ${XCP_EVIDENCE_NICE:-19}"
      command -v ionice >/dev/null 2>&1 && NICE="$NICE ionice -c2 -n7"
      _evidence
      echo "$FAILED" > "$EVID_FAILED_FILE"
    ) 2>&1 | sed -u "s/^/  [evidence] /" &
    EVID_PID=$!
  else
    _evidence
  fi
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
# ★ FROM HERE NOTHING IN THE RUN REWRITES results / results_tf (05's
#   backfill and 08's merge are the rebuild-and-swap writers; what follows
#   updates rows in place or reads them). A nightly waiting on the lock may
#   scrape now -- its new rows can no longer be lost in a swap -- and prices
#   them once the run lets go (deploy/nightly_update.sh, 2026-11-10).
[ "$DRY" -eq 0 ] && lock_note "results_stable=1"

# ★★ THE SITE FOLLOWS THE MODE ITS RATINGS WERE BUILT IN (owner, 2026-11-08:
#    "it should obv not publish new ratings until they are right"). The site
#    reads distance_rated_<SPORT>.json (normalize_distance._readerRecords),
#    which only the go-live writes, and reads it once, at start. So when this
#    go-live changed it -- pool to ability, or back -- the site is restarted
#    here, the moment the ratings it converts are the new ones; until now it
#    went on converting the old ratings by the old mode, as it should.
if [ "$DRY" -eq 0 ] && [ -n "${_RATED_AT_START:-}" ]; then
  _RATED_NOW=$(_ratedKey)
  if [ "$_RATED_NOW" != "$_RATED_AT_START" ]; then
    echo "  the published ratings' distance mode changed ($_RATED_AT_START -> $_RATED_NOW):" \
         "reloading the site so it converts them by it" | tee -a "$SUMMARY"
    # ! RELOAD, NOT RESTART (CLAUDE.md, 2026-11-09): the new master reads the
    #   file at its preload while the old workers keep serving; a restart is
    #   only the fallback where the reload handoff is not installed
    #   (deploy/site_reload.sh)
    { systemctl reload xc-predictor || systemctl restart xc-predictor; } 2>&1 | tee -a "$SUMMARY" \
      || echo "  ⚠ reload failed: systemctl reload xc-predictor" | tee -a "$SUMMARY"
  fi
fi

# ★ THE FILL BESIDE THE SCHOOL NAMES (2026-10-25): 09b updates speed_rating
#   rows, which 09c does not read; 09c reads school, person and team columns
#   and writes only school_name_alias, which 09b does not read. 09d follows both.
# ★ CROSS COUNTRY AT ITS OWN COURSE (owner, 2026-11-02: BCL Meet #3 2023,
#   listed 3218 m and corrected to 5000 m, read 130.2 beside 124.6 at the
#   same park eleven days later). A corrected or novote division votes on
#   no course, so the solve leaves it unrated and this fill prices it --
#   and K_pool / nt is the pool's MEDIAN course, not the +2.9% park it ran
#   at, which then also read as a fake "Day fast". --venue prices it at its
#   course and corrected distance, as the nightly always has. Track as
#   before (its no-venue rows have no course to look up).
# ★ AND THE TWO SPORTS' FILLS SIDE BY SIDE (2026-11-07, owner-approved). The
#   XC fill reads and writes results only and the TF fill results_tf only
#   (their board queries, pool constants, venue maps and scrubs are per
#   table); the go-live lock that kept them apart is per table now
#   (speed_ratings_db._takeGoLiveLock). The XC fill keeps the old name,
#   09b_fill, so the watchdog's run-to-run step list sees no step vanish.
# ★ --after-golive: the fill's no-normalized-time scrub is a no-op right after
#   a go-live this run made from a pack this run built (fill_ratings.
#   _SCRUB_NOOP), and is two full-table UPDATE scans; only then is it skipped.
_FILL_FLAG=""
if [ "${XCP_JOINT_LIVE:-1}" = "1" ] && [ "$FROM" -le 7 ] \
   && ! skipped 07_pack && ! failed 07_pack \
   && ! skipped 08_golive && ! failed 08_golive; then
  _FILL_FLAG=" --after-golive"
fi
stepsN 09b_fill "$PY -u engine/fill_ratings.py --sport XC --venue$_FILL_FLAG" \
       09b_fill_tf "$PY -u engine/fill_ratings.py --sport TF$_FILL_FLAG" \
       09c_school_names "$PY -u scripts/merge_school_names.py --write"


# ---- boards and pages ----------------------------------------------- #
# the two sports stream side by side (each is a Python row walk of ~30M
# rows); prepare makes the shadow once, finish indexes and swaps
# ! BEFORE THE BOARDS, BECAUSE THE BOARDS APPLY IT. school_name_alias folds
#   two spellings of one team onto one name -- "La Jolla (CA)" and
#   "La Jolla-CA" put the same athlete twice on one board. The name only
#   proposes; shared athletes decide and an anet team id vetoes, so the four
#   schools called Oregon stay four schools. Additive: read the dry run
#   (scripts/merge_school_names.py --show 80) before trusting a change to
#   the bars, and drop the table to undo it on the next run.
# ★ THE OUTLIER RULE (owner, 2026-09-18: "more than 5-15 sigma away from
#   their season ... do not rate or rank"; wired 2026-09-26). After the last
#   rating is written (09b) and before the boards are built from them, which
#   leave the flagged rows out (build_ranking_results._outlierClause).
#   Rank-only: no rating changes.
# ★★ NEIGHBOURS, NOT THE SEASON, AND BOTH SIDES (owner, 2026-09-29, Jack
#    Moretta's "easy LR"). Each race is judged against the athlete's K
#    nearest rated races in both sports, across season lines; K, the spread
#    floor and both cuts are MEASURED on the corpus each run and printed in
#    this step's log. A slow row stays on the athlete page, greyed, and
#    counts toward no season number (the boards and athlete_season anti-join
#    it). Overrides, all optional: XCP_OUTLIER_SIGMA (fast cut),
#    XCP_OUTLIER_SLOW_SIGMA (slow cut), XCP_OUTLIER_FLOOR (spread floor in
#    points), XCP_OUTLIER_K. The old fixed 4-point floor and 8-sigma bar
#    are gone: the measured floor is the corpus's own race-to-race spread.
# ★ 09c2 THE FIELD GUARD (owner, 2026-11-05: "find all things where this can
#   happen ... and nuke it rn"): a division whose whole field rated 5%+ above
#   its runners' own seasons is pulled down to +2%, whatever made it so -- a
#   short course, a course number a mislabelled race taught, a course-day's
#   credit, two pool scales (engine/field_guard.py). Every season, on the
#   ratings just written, before the outlier rule and the boards read them.
#   Never blocks; XCP_FIELD_GUARD=0 turns it off.
if [ "${XCP_FIELD_GUARD:-0}" != 0 ]; then
  step 09c2_field_guard "$PY" -u engine/field_guard.py --write --since 2000-01-01 || true
fi
step 09d_outliers     "$PY" -u engine/rating_outliers.py --write --show 10 \
                      --streams "$XCP_STREAMS" \
                      ${XCP_OUTLIER_SIGMA:+--fast-sigma "$XCP_OUTLIER_SIGMA"} \
                      ${XCP_OUTLIER_SLOW_SIGMA:+--slow-sigma "$XCP_OUTLIER_SLOW_SIGMA"} \
                      ${XCP_OUTLIER_FLOOR:+--floor "$XCP_OUTLIER_FLOOR"} \
                      ${XCP_OUTLIER_K:+--k "$XCP_OUTLIER_K"}
# ★ THE RESULTS VACUUM BESIDE THE BOARDS, NOT AFTER EVERYTHING (2026-11-10;
#   run 20261108_155220: 18c_vacuum_results was 3,792 s IN LINE at the very
#   end, 15% of the run's in-line time). Its job is the visibility map the
#   athlete and race pages' index-only scans need after the go-live and the
#   fill rewrote results (see 18c below for the history). 09b/09c/09d are
#   the last bulk writers of results* -- nothing rewrites them until 16 (the
#   rowguard note below) -- so it starts here, at the same trickle
#   (gentle_vacuum's cost delay), and runs beside the hours of board steps.
#   bgwait collects it at the end. Nothing waits on it: a lazy VACUUM is
#   ignored by CREATE INDEX CONCURRENTLY's wait for old transactions, and
#   11b builds an index on results only when one is missing (then it waits
#   for the vacuum, and says so). 16's few changed pages wait for autovacuum.
#   XCP_VACUUM_RESULTS=0 skips.
if [ "${XCP_VACUUM_RESULTS:-1}" != "0" ]; then
  bgstep 18c_vacuum_results "$PY" -u scripts/gentle_vacuum.py --table results --table results_tf
fi
# ★★ THE ROWGUARD BESIDE THE BOARDS (2026-11-07, owner-approved). The two
#    diagnostics read results*.normalized_time / speed_rating (final once
#    09b, and 09c2 when on, are done), meets and engine/corrections.py, and
#    write only files under scripts/ (suspects_*, result_drop_*); the two
#    triage steps read those files and results*, and write per-sport files
#    under scripts/. Nothing between here and 16_rowguard_apply writes what
#    they read (no step rewrites results* or corrections.py until 16) or reads
#    what they write (apply_triage is their one reader). So: diags as a pair,
#    then the triages as a pair (they share nothing: per-sport files, TEMP
#    tables), in one background chain from here.
#  ! COLLECTED BEFORE 11b_indexes, not at 16: 11b's CREATE INDEX
#    CONCURRENTLY waits for every older open transaction in the database, and
#    a diagnostic's long read is one. It has the whole board build to finish in.
_rowguard() {
  steps2 15_rowguard_diag_xc "$PY -u scripts/diag_suspects.py --sport XC" \
         15_rowguard_diag_tf "$PY -u scripts/diag_suspects.py --sport TF"
  steps2 15_rowguard_triage_xc "$PY -u scripts/triage_suspects.py --sport XC" \
         15_rowguard_triage_tf "$PY -u scripts/triage_suspects.py --sport TF"
}
bgchain 15_rowguard _rowguard
step 10_rankings_prepare "$PY" -u racecast/build_ranking_results.py --stage prepare
# each sport in date ranges, every stream into one shadow: the row walk is
# Python per row and was 52 minutes for two (2026-09-06), 45 for four.
# ★ FOUR RANGES PER SPORT, CUT WHERE THE ROWS ARE (2026-11-07, owner-
#   approved). `--stage seams` reads the planner's statistics on results.date
#   / results_tf.date (no table scan) and prints XCP_RANK_PARTS-1 dates that
#   split each sport's rows into near-equal shares; the streams are
#   [1990-01-01, s1), [s1, s2), ..., [sN, 2100-01-01) -- the same rows as one
#   stream, whatever the seams (a stream reads date >= --since AND date <
#   --until, and prepares each row on its own). All XCP_RANK_STREAMS (8) run
#   at once: one connection each, against max_connections 200 with the site's
#   72. No statistics, or a dry run: the old single seam, 2018-01-01;
#   XCP_RANK_SEAM="d1 d2 ..." states the seams (one date, as before, is two
#   streams per sport).
#   XCP_RANK_PARTS=2 XCP_RANK_STREAMS=4 is the old shape.
# ★ THE STREAMS SHARED BY ROWS, NOT BY SPORT (2026-11-10; run 20261109_033652:
#   four streams each, and the four track streams ran 2,063-2,388 s while the
#   cross country ones were done in under 810 -- track has ~168M rows to XC's
#   ~43M). Still eight streams at once (the same connections and load), now
#   XC 2 and TF 6 (XCP_RANK_PARTS_XC / _TF): ~21M and ~28M rows a stream.
#   XCP_RANK_PARTS, when set, still gives both sports that many.
_RANK=()
for _sp in XC TF; do
  _lc=$(echo "$_sp" | tr 'A-Z' 'a-z')
  if [ "$_sp" = XC ]; then _parts="${XCP_RANK_PARTS:-${XCP_RANK_PARTS_XC:-2}}"
  else _parts="${XCP_RANK_PARTS:-${XCP_RANK_PARTS_TF:-6}}"; fi
  _seams="${XCP_RANK_SEAM:-}"
  if [ -z "$_seams" ] && [ "$DRY" -eq 0 ] && ! beforeFrom 10_rankings_prepare \
     && [ "$_parts" -ge 2 ]; then
    _seams=$("$PY" racecast/build_ranking_results.py --stage seams --sport "$_sp" \
               --parts "$_parts" 2>>"$LOGDIR/10_rankings_seams.log" | tail -n 1)
    _seams=$(rankSeamsOk "$_seams")
  fi
  [ -n "$_seams" ] || _seams="2018-01-01"
  echo "  10_rankings $_sp date seams: $_seams"
  _prev=""; _i=0
  for _s in $_seams ""; do
    _RANK+=("10_rankings_${_lc}_$(echo abcdefghijklmnop | cut -c$((_i + 1)))"
            "$PY -u racecast/build_ranking_results.py --stage stream --sport $_sp${_prev:+ --since $_prev}${_s:+ --until $_s}")
    _prev="$_s"; _i=$((_i + 1))
  done
done
XCP_STREAMS="${XCP_RANK_STREAMS:-8}" stepsN "${_RANK[@]}"
step 10_rankings_finish "$PY" -u racecast/build_ranking_results.py --stage finish
# ★ THE EDGE FORGETS THE OLD BOARDS (2026-11-06, scripts/edge_purge.py):
#   the swap just put new ratings live; without a purge Cloudflare keeps
#   serving last night's pages for PAGE_S_MAXAGE. A no-op until
#   XCP_CF_ZONE_ID / XCP_CF_API_TOKEN are set; never fails the run.
# static pages (racecast/build_static_pages.py): the new boards, before the edge refills
if [ "${XCP_STATIC_PAGES:-0}" = "1" ]; then
  step 10_static_pages env XCP_DB_APP=site XCP_DB_MIN_CONN=1 XCP_DB_MAX_CONN=2 "$PY" -u racecast/build_static_pages.py || true
fi
step 10_edge_purge "$PY" -u scripts/edge_purge.py || true
# ★ THE EVENTS PRESETS, PRECOMPUTED (owner, 2026-11-06; racecast/
#   build_season_events.py): the ability board's 800/1500/3000/5000-and-up
#   windows read athlete_season_ev instead of recomputing every season per
#   request (26-55 s cold). It reads the ranking_results just swapped in and
#   stamps that table's oid; until it finishes the board runs live, so it
#   holds nothing up -- in the background, collected by bgwait.
#   XCP_SEASON_EV=0 skips (the board then always runs live).
if [ "${XCP_SEASON_EV:-1}" != "0" ]; then
  bgstep 10f4_season_events "$PY" -u racecast/build_season_events.py
fi
# ★ WHERE EVERY DEFAULT BOARD'S PAGES START (2026-11-08; racecast/
#   build_board_seek.py): every 1,000th row's key of the performance and
#   ability boards (each pool, sport, season, distance), so a deep page is an
#   index range read instead of ranking every row above it (25.4 s at offset
#   150,850). Stamped with the swapped-in tables' oids; until it finishes the
#   boards page live, as before -- in the background, collected by bgwait.
#   XCP_BOARD_SEEK=0 skips (the boards then always page live).
if [ "${XCP_BOARD_SEEK:-1}" != "0" ]; then
  bgstep 10f8_board_seek "$PY" -u racecast/build_board_seek.py
fi
# ★ THE RECRUIT PROJECTION STARTS AS SOON AS athlete_season EXISTS (2026-10-18;
#   it was the run's last 71 idle minutes: 2h41m started after 11b). It
#   reads only athlete_season, only the recruiting and comps PAGES read its
#   table, and it commits per slice -- so 11b's CREATE INDEX CONCURRENTLY no
#   longer waits on it (the 626-minute stall of 2026-09-26 was one long
#   transaction). Collected by bgwait at the very end.
bgstep 10f2_projection "$PY" -u racecast/build_recruit_projection.py
# ★ THE BOARDS, CHECKED BEFORE ANYONE READS THEM (owner, 2026-09-14: college
#   boards headed at 170 by rows ranked against another pool's mean, clubs
#   with professionals among colleges, a 5:12 "mile"). Top rows per pool:
#   anchor gate, record pace, board pool == rating pool, club teams, margin
#   over the pool's own top seasons, the XC/TF gap. A hard finding FAILS
#   the step (no "|| true"): a wrong board is not a board.
# ★ THE BOARD STEPS, FOUR AT A TIME (2026-10-25, the step times: about
#   2,100 s in a row). Their writes are disjoint (nothing, school_team_link,
#   school_identity, school_unit, meet_unit, season_rank, ...) and the boards
#   they share as readers are finished (docs/PIPELINE-DEPENDENCIES.md, group
#   1). The orders that matter stay: 10b after 10b0 (it reads
#   school_team_link), 10g2 after 10g, 10f after 10b and 10d, 10f3 after
#   10f, 11 after 10b. Each wave is XCP_STREAMS wide at most.
# ! 10a STILL FAILS ITS STEP on a hard finding (a wrong board is not a board);
#   running beside the others changes nothing about that.
# ! 10b0 BEFORE 10b, BECAUSE 10b READS IT. school_team_link says which anet
#   college TEAM each tfrrs school string is, learned from athletes who
#   appear in both feeds -- the join that needs no spelling. It is what
#   separates the University of Oregon from Oregon (IL). Additive and
#   idempotent; exits 0 with nothing done before anet_teams has run.
# 10c is measured for telemetry only under XCP_JOINT_LIVE=1 (the default);
# 10e marks which meets are championships (issue 34); 10g precomputes every
# season's rank (311) and 10g2 keeps this week's state ranks (/movers).
if [ "${XCP_JOINT_LIVE:-1}" = "1" ]; then _GAP_FLAGS=""; else _GAP_FLAGS="--emit"; fi
# (10b0_tfrrs_link ran in the background beside the pack since 2026-11-07)
stepsN 10a_board_sanity "$PY -u scripts/board_sanity.py --top ${XCP_SANITY_TOP:-60}" \
       10d_school_units "$PY -u racecast/build_school_units.py"
# ★ THE BOARDS TAKE TONIGHT'S UNITS, NOT LAST RUN'S (owner, 2026-11-06:
#   "still see pac-12 things" -- Oregon and Washington as a PAC-12 of two
#   after school_unit had them in the Big Ten). Step 10 stamps every season
#   row's conference / league from school_unit, and 10d rebuilds school_unit
#   AFTER it (it reads the boards), so a units fix reached the boards one run
#   late. The restamp re-reads the new school_unit onto athlete_season
#   before anything that reads those columns: 10g's conference ranks, 11's
#   team boards (the conferences board), 10f's recruiting.
step 10d2_restamp_units "$PY" -u racecast/build_ranking_results.py --stage restamp
# ★ THE BOARDS' VACUUM STARTS HERE, NOT AT THE END (2026-11-10; run
#   20261108_155220: 18_vacuum 1,150 s in line after the reports). Its two
#   tables are written by build_ranking_results alone, and 10d2's restamp
#   is its last stage in the run -- from here they are only read. Same
#   trickle as before, beside the board steps and 18c; joined just before
#   18b_integrity_boards, which must read the pages after the vacuum's last
#   write. 11b only builds an index on them when one is missing (the finish
#   stage carries the old table's over); that build then queues behind the
#   vacuum -- no longer than the old order took -- and blocks no reader.
bgstep 18_vacuum "$PY" -u scripts/gentle_vacuum.py
# ★ KAILO'S UPCOMING MEETS, RECORDED BESIDE THE BOARDS (2026-11-10; run
#   20261109_033652: 16c_kailo_record ran 2,295 s inside the report group,
#   which waited on it -- 1,266 s longer than the next report). It writes
#   only engine/data/kailo/<race>.json, which no step of the run reads
#   (run_report, scoreboard and the watchdog do not), from the ratings and
#   boards, final from here. XCP_KAILO=0 skips it; the 6-hourly
#   xcp-kailo-record timer records them too.
if [ "${XCP_KAILO:-1}" != "0" ]; then
  bgstep 16c_kailo_record "$PY" -u scripts/kailo_compare.py --record
fi
# (10e_meet_units ran in the background beside the pack since 2026-11-07)
stepsN 10b_school_ids "$PY -u racecast/build_school_identity.py" \
       10c_gap "$PY -u scripts/measure_sport_gap.py $_GAP_FLAGS" \
       10g_season_ranks "$PY -u racecast/build_season_ranks.py"
# recruiting (282) reads school_identity (10b) and school_unit (10d); the
# teams read school_identity. "Runners like you" (10f3) reads college_recruit
# (10f) and holds a transaction, so it stays in line before 11b. 10g2 reads
# 10g's ranks.
# ★ 11c_ncaa JOINS THIS GROUP (2026-11-12, ~374 s off the line): it writes
#   ncaa_projection alone and reads athlete_season, results, meets_tfrrs,
#   dist_override, college_directory and corrections.py -- none written by
#   the other three. Its first reader is 13h/13h2. Still before 11b.
_G10F=(10f_recruits "$PY -u racecast/build_recruiting.py"
       11_teams "$PY -u racecast/build_team_season.py"
       10g2_rank_snapshot "$PY -u racecast/build_rank_snapshot.py")
if [ "${XCP_NCAA:-0}" = "1" ]; then
  _G10F+=(11c_ncaa "$PY -u racecast/build_ncaa_projection.py")
fi
stepsN "${_G10F[@]}"
# ★ THE NCAA CHAMPIONSHIPS PROJECTION (2026-10-10): the regionals, the
#   at-large selection and nationals, simulated for D1/D2/D3 men and women,
#   one stored row per board per week (build_ncaa_projection.py). Reads
#   athlete_season and the season's tfrrs results; /ncaa reads its table.
#   OFF until XCP_NCAA=1: run it by hand once first.
# (11c_ncaa now runs in the 10f group above)
# ! THE PAGE INDEXES COME BEFORE THE COURSE PAGES. They ran AFTER them
#   (step 14), so every course page of a first run was built without the
#   indexes the course queries need; 12b took 41,047 s on 2026-09-02.
# the rowguard chain (15_*, started after 09d): before 11b, whose concurrent
# index builds wait for every older open transaction
bgchainjoin 15_rowguard
step 11b_indexes      "$PY" -u scripts/add_page_indexes.py
# ★ 10f3_COMPS IN THE BACKGROUND FROM HERE (2026-11-12, ~113 s off the line):
#   it writes season_comps / season_comps_meta (a swap) that only comps.py
#   and 19b's prewarm read; its inputs are not written after 10f. After 11b,
#   never beside it (11b's concurrent index builds wait on open
#   transactions). Collected by bgwait.
bgstep 10f3_comps "$PY" -u racecast/build_comps.py
# ★ THE SEARCH INDEX FROM HERE (2026-11-12; run 20261112_173033: started
#   after 13b, it held the run 340 s at its join). Its inputs are final by
#   now (13c0 joined before the go-live; 10b/10d done) and nothing from 12
#   to 13b feeds it (it imports panels.isTeamName, courses, unit_board as
#   functions). After 11b, never beside it: its long transactions would
#   stall 11b's concurrent index builds. It builds search_index_new AND the
#   meet_agg_xc/tf tables the sitemap reads, so it is joined before the
#   sitemap group -- which, from 2026-11-10 until now, could read the last
#   run's meet_agg while 13c rebuilt them.
bgstep 13c_search_index "$PY" -u racecast/search_index.py
# ! IN THE BACKGROUND: only the recruiting page reads recruit_projection, so
#   nothing below waits on it. Collected by bgwait before 17.
# ! AND STARTED AFTER 11b, NOT BEFORE (2026-09-26). CREATE INDEX CONCURRENTLY
#   waits for every older open transaction in the database; with this
#   running beside it, 11b_indexes took 626 minutes. The script now commits
#   per slice too, so 13c's concurrent index build does not wait on it either.
# ★ THE COURSE AND VENUE PAGES AS ONE BACKGROUND CHAIN (2026-11-12, ~850 s
#   off the line). 12_courses, 12b and 12c write only course_rank,
#   course_boards and venue_tf_boards (each _new and swapped) and read
#   course_difficulties, meets*, results*, ranking_results and
#   course_canonical -- none of which the panels, 13f, 13f2 or 13g write.
#   In order inside the chain, so 12c (6.6 GB) starts about as the panels
#   (~16 GB together) end. Joined before 13b_pool_consts, so 12b/12c read
#   last run's pool constants file exactly as before; 13j starts after it.
_courseChain() {
step 12_courses       "$PY" -u racecast/build_course_rank.py
# ★ COURSE PAGES IN THREE SHARDS (issue 140). One process building 1,200
#   courses in a row was mostly waiting on the database; three workers on
#   every third course cut the wall time to about a third. --prepare and
#   --finish bracket them so the swap happens once, after all three.
# ! PREPARE, SHARDS AND FINISH ARE ONE UNIT (2026-11-10). --fast skips
#   12b_course_pages; 12b_prepare still emptied course_boards_new and
#   12b_finish then refused to swap an empty table -- FAILED in run
#   20261108_155220 with no shard log at all. The three now run or skip
#   together, on the shard step's own --from / --skip.
if willRun 12b_course_pages; then
  step 12b_prepare      "$PY" -u racecast/build_course_boards.py --prepare
  XCP_DB_MIN_CONN=1 shards 12b_course_pages "$XCP_COURSE_SHARDS" "$PY" -u racecast/build_course_boards.py --limit 1200
  failed 12b_course_pages || step 12b_finish "$PY" -u racecast/build_course_boards.py --finish
else
  echo "  12b_prepare, 12b_course_pages, 12b_finish skipped (the course pages keep the last build; app._boardFresh re-reads their ratings)"
fi
# ★ TRACK VENUE PAGES, THE SAME WAY (racecast/build_venue_tf_boards.py)
if willRun 12c_venue_pages; then
  step 12c_prepare      "$PY" -u racecast/build_venue_tf_boards.py --prepare
  XCP_DB_MIN_CONN=1 shards 12c_venue_pages "$XCP_COURSE_SHARDS" "$PY" -u racecast/build_venue_tf_boards.py --limit 2000
  failed 12c_venue_pages || step 12c_finish "$PY" -u racecast/build_venue_tf_boards.py --finish
else
  echo "  12c_prepare, 12c_venue_pages, 12c_finish skipped (the venue pages keep the last build)"
fi
}
if [ "${XCP_COURSE_BG:-1}" = "1" ]; then bgchain courses _courseChain; else _courseChain; fi
# ★ THE RECORD BOOKS (2026-10-10): state all-time lists, school record books,
#   course records by era, "on this day" (racecast/build_record_books.py).
#   IN THE BACKGROUND, off the main chain: only the record pages read
#   record_books, so nothing below waits on it; bgwait collects it before 17.
#   Incremental -- a part whose inputs did not change is skipped, and school
#   books rebuild only where a school's results moved (XCP_RECORD_SCHOOLS a
#   run, biggest first). ! AFTER 11b and 12b on purpose: CREATE INDEX
#   CONCURRENTLY waits on every older open transaction (see 10f2 above).
# (13j_record_books starts after the course chain is collected, below)
# ★ THE PANELS' HEAD FIRST, THEN 13f..13g IN LINE BESIDE THE PANELS
#   (2026-11-13). The steps below read only homepage_recent (breakouts.
#   latestDates) and season_year_<sport> (predict._currentSeason); `panels.py
#   --head` writes exactly those (and span_<sport>), with the full build's own
#   functions, in about a minute. The two full builds then run as a
#   background chain with --after-head, which takes the head's season and
#   span and never rewrites them or homepage_recent -- so every step below
#   reads the values the page ends with. Joined before 13_panels_facts.
#   XCP_PANELS_HEAD=0, a failed head, or a --skip of the head or either
#   panels step runs them all in line, as before.
_panelsPair() {
# the two sports' full passes side by side (14 minutes for both in one
# process on run12); each writes only its own sport's panels
steps2 13_panels_xc "$PY -u racecast/panels.py --sport XC$_PANELS_AFTER_HEAD" \
       13_panels_tf "$PY -u racecast/panels.py --sport TF$_PANELS_AFTER_HEAD"
}
_PANELS_AFTER_HEAD=""
if [ "${XCP_PANELS_HEAD:-1}" = "1" ] && willRun 13_panels_head \
   && willRun 13_panels_xc && willRun 13_panels_tf; then
  step 13_panels_head "$PY" -u racecast/panels.py --head
  failed 13_panels_head || _PANELS_AFTER_HEAD=" --after-head"
fi
if [ -n "$_PANELS_AFTER_HEAD" ]; then bgchain panels _panelsPair; else _panelsPair; fi
# Breakouts, precomputed: reads ranking_results and the panels' newest dates
step 13f_breakouts    "$PY" -u racecast/build_breakouts.py
# ★ "WHAT IT TAKES", PRECOMPUTED (2026-11-08; racecast/build_wit_marks.py):
#   every state's marks into wit_marks, which cuts.marksFor reads instead of
#   computing them in the web workers. After everything cuts reads (the
#   boards, 10d's units, 10e's meet_unit, 13_panels' season year). A failed
#   build costs nothing else: the site computes a state live when its row is
#   missing or older than cuts.MAX_AGE_S.
step 13f2_wit_marks   "$PY" -u racecast/build_wit_marks.py
# ★ THE BIG SCHOOLS' PAGES, PRECOMPUTED (2026-11-12; racecast/build_school_pages.py):
#   the history half of /school/:name (header, year bar, current season,
#   best, top athletes) for schools with XCP_SCHOOL_PAGE_MIN_SEASONS+ seasons,
#   by the page's own functions, served only for tonight's boards and code
#   (school_pages.py). A failure costs nothing: the page computes live.
#   XCP_SCHOOL_PAGES=0 skips it.
if [ "${XCP_SCHOOL_PAGES:-1}" != "0" ]; then
  step 13f3_school_pages "$PY" -u racecast/build_school_pages.py || true
fi
# ★ EVERY MEET'S NEWEST DATE AND RESULT COUNT (2026-11-12;
#   racecast/build_meet_counts.py): /meets reads them by key instead of
#   counting each candidate meet's results, while results is unchanged since
#   (its oid and change stamp). A failure costs nothing: /meets counts live.
#   XCP_MEET_COUNTS=0 skips it.
if [ "${XCP_MEET_COUNTS:-1}" != "0" ]; then
  step 13f4_meet_counts "$PY" -u racecast/build_meet_counts.py || true
fi
# ★ THE "ADD ANYONE" PICKER'S TABLE (2026-11-12; racecast/build_athlete_pick.py):
#   one row per rated runner, sport and gender with the newest season, used
#   by /api/predict/athletes while athletes and athlete_season are unchanged
#   since. A failure costs nothing: the search runs as before.
#   XCP_ATHLETE_PICK=0 skips it.
if [ "${XCP_ATHLETE_PICK:-1}" != "0" ]; then
  step 13f5_athlete_pick "$PY" -u racecast/build_athlete_pick.py || true
fi
# ★ THE WEEKLY INSTAGRAM POSTS (owner, 2026-10-13): images and captions
#   from the week's boards (10g2 snapshots, the breakouts above), picked up
#   at /account/admin/social. A report step: it never fails the run. Off with
#   XCP_SOCIAL=0; photos from XCP_SOCIAL_PHOTOS; see racecast/build_social.py.
#   (Before 13f6 now: it reads nothing the vacuum touches.)
if [ "${XCP_SOCIAL:-1}" != "0" ]; then step 13g_social "$PY" -u racecast/build_social.py; fi
bgchainjoin panels                        # 13_panels_xc/tf, before the facts read their spans
# the home page's hero numbers from BOTH sports' spans (each process above
# wrote its own; panels.combineFacts, 2026-11-12) -- a second, cheap read
step 13_panels_facts "$PY" -u racecast/panels.py --combine-facts
# ★ THE SMALL REBUILT TABLES' VISIBILITY MAPS, AFTER THE BUILDS (2026-11-12:
#   season_comps and athletes at 0% all-visible on the server). A VACUUM can
#   only mark a page all-visible once no older transaction is open, and a
#   build's own vacuum runs beside the rest of the run -- so season_comps'
#   index-only band read (comps.py) went to the table for every row. These
#   are small (a few GB in all) and are done at the trickle's pace.
# ! AFTER THE PANELS' JOIN, NOT BESIDE THEM (2026-11-13): their long scans
#   hold the snapshot horizon this vacuum needs to pass.
bgjoin 10f3_comps                        # its season_comps swap, before the vacuum reads it
step 13f6_vacuum_small "$PY" -u scripts/gentle_vacuum.py --table season_comps --table athletes \
     --table athlete_pick --table school_page --table meet_counts || true
# ★ STATE ODDS (owner, 2026-10-10): each state's season simulated into
#   state_odds_* (racecast/build_state_odds.py). After the boards and units
#   it reads; its own line, not chained, so a failure costs nothing else. OFF until
#   XCP_STATE_ODDS=1: time a first build by hand (it simulates every state).
# ★ IN THE BACKGROUND SINCE 2026-11-10 (run 20261109_033652: 810 s in line).
#   Only the pages read state_odds_* -- 13g_social above reads the LAST
#   run's tables, as it did when this ran after it in line -- so bgwait
#   collects it at the end.
if [ "${XCP_STATE_ODDS:-0}" = "1" ]; then bgstep 13i_state_odds "$PY" -u racecast/build_state_odds.py; fi
bgchainjoin courses                       # 12/12b/12c, before 13b rewrites the constants
if [ "${XCP_RECORD_BOOKS:-0}" = "1" ]; then bgstep 13j_record_books "$PY" -u racecast/build_record_books.py --max-schools "${XCP_RECORD_SCHOOLS:-4000}"; fi
step 13b_pool_consts  "$PY" -u scripts/warm_pool_constants.py
# ★ THE SEARCH INDEX, REBUILT EVERY RUN (issue 140). It was never in the
#   pipeline, so new athletes and meets stayed unsearchable until someone
#   rebuilt it by hand. Built into a shadow table and swapped, so search
#   never goes dark.
# (13c0_person_redirects and 13c0_search_helpers, its three source tables,
#  ran in the background beside the pack since 2026-11-07)
# (13c_search_index now starts after 11b and is joined before the sitemap;
#  see the note there)
# ★ THE WEEK'S FROZEN COPY (owner, 2026-11-06: "is this done in the pipeline?"):
#   both sports' season ratings and the course difficulties as a dated
#   snapshot on /data, the newest 8 weeks kept (data_snapshots.py). A full
#   run only -- a --fast iteration is not the week's numbers.
if [ "$FAST" -eq 0 ]; then
  step 13c2_snapshot "$PY" -u scripts/take_snapshot.py --weekly --keep 8 || true
fi
# ★ SIDE BY SIDE (2026-11-12, ~5 min off the line): the sitemap, the follow
#   digest, the posted-meet forecasts and the qualifier fields write disjoint
#   things (sitemap files; alert_* / follow_snapshot; meet_forecast and the
#   day's inputs file; the qualified-fields output) and none reads another's.
#   13h_raw stays after the group: it can share 13h's daily inputs file.
#   13e (IndexNow) reads the sitemap, so it runs after. XCP_LATE_PARALLEL=0
#   runs them one after another, as before.
bgjoin 13c_search_index                  # the sitemap reads its meet_agg tables
# the sitemap for Google: every ranked athlete, school, course and meet
_G13=(13d_sitemap "$PY -u racecast/build_sitemap.py")
# ★ THE FOLLOW DIGEST (owner, 2026-10-10), OPTIONAL: off unless XCP_ALERTS=1.
#   After 13f (the breakouts it reads); see racecast/follow_alerts.py.
if [ "${XCP_ALERTS:-0}" = "1" ]; then
  _G13+=(13g_follow_alerts "$PY -u racecast/follow_alerts.py --send --prewarm")
fi
# ★ "WHAT WE SAID" BEFORE EACH MEET, SCORED AFTER (owner, 2026-10-10): the
#   week's posted XC forecasts stored, the run meets scored, for /meet/recap
#   and /recaps. Incremental -- a forecast already frozen is never redone --
#   so after the nightly update it is usually a no-op here. Off the chain:
#   nothing after it reads the table. OFF until XCP_FORECASTS=1 (run it by hand once first).
#   See racecast/build_meet_forecasts.py.
if [ "${XCP_FORECASTS:-0}" = "1" ]; then
  # ★ AND EVERY INPUT AS THE PAGE HAD IT, BEFORE THE RACE (owner, 2026-11-06:
  #   "there shouldnt be any hindsight"). The race model trains on today's
  #   ratings and course numbers, which have seen the races it learns from;
  #   these files (race_model.keepInputs, one .jsonl.gz a day) are the table
  #   nothing after the forecast touched. XCP_RACE_INPUTS_DIR= (empty) stops it.
  _G13+=(13h_meet_forecasts "env XCP_RACE_INPUTS_DIR='${XCP_RACE_INPUTS_DIR-${XCP_PRED_DIR:-/srv/pred}/race_inputs}' $PY -u racecast/build_meet_forecasts.py")
  # ★ THE NO-RATINGS RACE MODEL, FROZEN BESIDE IT (owner, 2026-11-06, PLAN-MODEL
  #   §9): the same posted meets, its own table, so score_frozen_forecasts can
  #   hold the two side by side before the site serves either. Only when its
  #   file exists (a missing one would record the ratings under its name).
  #   XCP_FORECAST_RAW=0 skips it.
  RAW_MODEL="${XCP_RACE_MODEL_RAW:-$(dirname "${XCP_RACE_MODEL:-$ROOT/model/data/race_trees.pkl}")/race_trees_raw.pkl}"
  _RAW13=""
  if [ "${XCP_FORECAST_RAW:-1}" = "1" ] && [ -f "$RAW_MODEL" ]; then _RAW13=1; fi
fi
# ★ AND THE QUALIFIER FIELDS OF THE UPCOMING CHAMPIONSHIPS, as the nightly
#   (racecast/build_qualified_fields.py); XCP_QUALIFIED_BUILD=0 skips it
if [ "${XCP_QUALIFIED_BUILD:-1}" = "1" ]; then
  _G13+=(13h2_qualified_fields "$PY -u racecast/build_qualified_fields.py --budget-minutes 60")
fi
if [ "${XCP_LATE_PARALLEL:-1}" = "1" ]; then
  stepsN "${_G13[@]}"
else
  XCP_STREAMS=1 stepsN "${_G13[@]}"
fi
if [ -n "${_RAW13:-}" ]; then
  step 13h_raw_forecasts env XCP_PREDICT_BASIS=race_raw XCP_FORECAST_TABLE=pred_raw_meet_forecast \
    "$PY" -u racecast/build_meet_forecasts.py
fi
# IndexNow: the URLs that changed, to Bing and friends (needs XCP_INDEXNOW_KEY)
step 13e_indexnow     "$PY" -u scripts/indexnow_submit.py

# ---- rowguard ------------------------------------------------------- #
# 2000-row rail; anything past it diverts to .OVER-CAP for a human.
# (the diagnostics and the triage ran in the background from after 09d: the
#  _rowguard chain, collected before 11b_indexes)
step 16_rowguard_apply     "$PY" -u scripts/apply_triage.py
# ★ THE DATA WATCHDOG, IN THE BACKGROUND (2026-10-18: 53 min in line before
#   the vacuum). Every step that writes what readers see is done by here;
#   report only. FAILED is the run's failures so far (17* are reports).
# the search index (13c, in the background): the watchdog below counts it
if [ "${XCP_WATCHDOG:-1}" != "0" ]; then
  bgstep 17d_watchdog "$PY" -u racecast/watchdog.py --log-dir "$LOGDIR" --kind full \
                      --step-name 17d_watchdog --failed "$FAILED" --send --ensure-indexes
fi

# ---- the owner's go/no-go ------------------------------------------- #
# ! bgwait MOVED TO THE END (2026-10-18). Nothing in 17-18 reads the
#   background steps' tables (recruit_projection, record_books), so the
#   reports and the vacuum no longer idle behind them.
# ★★ THE REPORT STEPS SIDE BY SIDE, THE EVIDENCE CHAIN WAITED FOR LAST
#    (2026-11-07, owner-approved). Of everything below, only 17c_report reads
#    the evidence chain's logs (08a_holdout, 08a_sport_holdout); run_checklist
#    opens no log, so the wait moves from before 17_checklist to just before
#    17c. The steps of each group below write nothing another one reads:
#    17_checklist reads the database and scripts/canaries.json; 17b_status
#    writes site_status_snapshot; 16b, 16c and 16d write their own caches,
#    engine/data/kailo/ files and logs; 17c0 writes engine/data/run_changes/,
#    17e engine/data/scoreboard.jsonl and SCOREBOARD.txt. 17c reads 16b's and
#    16d's logs, so it stays after them.
#  ! 17a_page_timing STAYS ALONE, where it was (after 17-16d, before 17c0):
#    it times pages, and anything beside it skews the numbers; it may render
#    the status page (17b) and What's new (17c0), so its inputs are as before.
#  ! Each step is still its own line: --skip, --from, a failure and the
#    summary are per name (stepsN), and a failed report never stops the run.
_REPORTS=(17_checklist "$PY -u scripts/run_checklist.py"
          17b_status "$PY -u scripts/print_status.py")
# ★ THE RUN'S SCORECARD (owner, 2026-10-04: "so many moving parts how can
#   we capture it all?"): the fair tests, the health lines and the owner's
#   sentinel cases, this run against the last; appended to
#   engine/data/run_history.jsonl and written to $LOGDIR/REPORT.txt. 16b
#   feeds it the LACCTiC fair tests (college; public API, about 150
#   requests a second apart; XCP_LACCTIC=0 skips, XCP_LACCTIC_RACES picks).
# ★ ONCE A WEEK (owner, 2026-11-10: "make it once weekly"): a run whose
#   last LACCTiC comparison is under XCP_LACCTIC_DAYS (7) days old skips
#   it -- about 153 requests to their API each time, and the numbers hardly
#   move between runs. XCP_LACCTIC=force runs it anyway. The report reads
#   the last run that has it.
_lacRecent=""
if [ "${XCP_LACCTIC:-1}" != "force" ]; then
  _lacRecent=$(find "$ROOT/logs" -mindepth 2 -maxdepth 2 -name 16b_lacctic.log -size +1k \
                 -mtime -"${XCP_LACCTIC_DAYS:-7}" 2>/dev/null | head -1)
fi
if [ "${XCP_LACCTIC:-1}" = "0" ]; then
  :
elif [ -n "$_lacRecent" ]; then
  echo "  16b_lacctic: skipped -- last run under ${XCP_LACCTIC_DAYS:-7} days ago ($_lacRecent)" | tee -a "$SUMMARY"
else
  _REPORTS+=(16b_lacctic "$PY -u scripts/lacctic_compare.py --race ${XCP_LACCTIC_RACES:-12194,12158,11632} --runners 150")
fi
# ★ OUR PREDICTIONS FOR KAILO'S UPCOMING MEETS, SAVED BEFORE THE RACE (owner,
#   2026-10-21: "compare our accuracy to Kailo's"). scripts/kailo_compare.py
#   --record writes engine/data/kailo/<race>.json for each race Kailo has
#   published and not yet run, once; the comparison scores those as
#   "recorded" -- neither system saw the result. A few meets, a few dozen
#   requests. XCP_KAILO=0 skips.
# (16c_kailo_record runs in the background from after 10d2 since 2026-11-10;
#  see there)
# ★ HOW GOOD THE PREDICTIONS WERE, EVERY RUN (owner, 2026-10-22: "how come no
#   accuracy numbers show up"): the last two weeks' finished XC races
#   re-predicted with each runner's history cut the day before, on the new
#   ratings; run_report reads the blend line into the run's history and
#   What's new says it. 40 races, a few minutes each. XCP_BACKTEST=0 skips.
if [ "${XCP_BACKTEST:-1}" != "0" ]; then
  _REPORTS+=(16d_backtest "$PY -u scripts/backtest_predictions.py --from $(date -d '14 days ago' +%F) --to $(date -d 'yesterday' +%F) --weeks 0 --divs ${XCP_BACKTEST_DIVS:-40} --no-breakdown")
fi
XCP_STREAMS="${XCP_REPORT_STREAMS:-5}" stepsN "${_REPORTS[@]}"
# ★ PAGE TIMES WITH NOTHING CACHED (owner, 2026-10-13: "it should be fast
#   w or w/o a cache"): a fresh process renders random pages of every kind
#   and times each page and every SQL statement in it; the slow pages and
#   their slowest statements are the work list (page_timing table, log).
#   Report only. XCP_PAGE_TIMING=0 skips; XCP_PAGE_TIMING_N pages per kind.
if [ "${XCP_PAGE_TIMING:-1}" != "0" ]; then
  step 17a_page_timing  "$PY" -u scripts/page_timing.py --write --n "${XCP_PAGE_TIMING_N:-6}" || true
fi
# ★ THE RATINGS' STANDING TESTS (owner, 2026-10-18): course and day credit
#   inside a runner's season, track vs XC that fortnight, altitude credit,
#   and the owner's flagged races -- each beside the last run's
#   (engine/data/scoreboard.jsonl, $LOGDIR/SCOREBOARD.txt). Report only, a
#   few minutes. XCP_SCOREBOARD=0 skips. (Beside 17c0 since 2026-11-07; it
#   ran after 17c, which reads neither.)
_REPORTS=(17c0_run_changes "$PY -u scripts/run_changes.py --report --log-dir $LOGDIR")
if [ "${XCP_SCOREBOARD:-1}" != "0" ]; then
  _REPORTS+=(17e_scoreboard "$PY -u scripts/scoreboard.py --log-dir $LOGDIR")
fi
XCP_STREAMS="${XCP_REPORT_STREAMS:-5}" stepsN "${_REPORTS[@]}"
# the evidence chain (08a..08d), started after the go-live: 17c reads its logs
if [ -n "${EVID_PID:-}" ]; then
  echo "  waiting for the evidence chain (08a..08d)..."
  _tw=$(date +%s)
  wait "$EVID_PID"
  echo "  evidence_chain held the run $(( $(date +%s) - _tw ))s" | tee -a "$SUMMARY"
  _ef=$(tr -s ' \n' ' ' < "$EVID_FAILED_FILE" 2>/dev/null | sed 's/^ *//; s/ *$//')
  [ -n "$_ef" ] && FAILED="$FAILED $_ef"
fi
# the last solve of the run is done: engine replays may start again
[ "$DRY" -eq 0 ] && "$PY" "$ROOT/scripts/jobrun.py" --unwant $$ 2>/dev/null
step 17c_report       "$PY" -u scripts/run_report.py --log-dir "$LOGDIR" || true
# ★ THE DATA WATCHDOG (owner, 2026-10-10): after the boards swap and every
#   step that writes what readers see, before the vacuum. Report only; its
#   board churn and row counts are judged against FULL runs' own history
#   (--kind full), since a full run re-rates every row. It also names
#   10a_board_sanity's cause class when 10a failed (racecast/watchdog.py
#   classify10a). Mails only on new items and only when nothing FAILED --
#   otherwise summarise()'s notify_owner mail carries WATCHDOG.txt.
# (17d_watchdog runs in the background since 2026-10-18 -- started after 16)

# ---- the gentle vacuum ------------------------------------------------ #
# the boards tables are built with autovacuum off (createShadow); this is
# the one pass they need, at a trickle (scripts/gentle_vacuum.py). Started
# in the background after 10d2 since 2026-11-10; collected here, before the
# integrity read below.
bgjoin 18_vacuum
# ★ AND THE BOARDS TABLES READ AGAIN, AFTER IT (2026-10-20). Two builds in a
#   row put a ranking_results live that read clean at build time and had a
#   bad page hours later -- the server log said "page verification failed,
#   calculated checksum 58904 but expected 61754": the page changed AFTER
#   Postgres wrote it, on a drive with no errors. The vacuum is the last
#   writer of those pages; this reads every one of them once more (~20 s),
#   so a bad page FAILS tonight's run and mails, instead of turning up days
#   later in a backup or a script. The repair is in its log.
step 18b_integrity_boards "$PY" -u scripts/integrity_check.py --tables ranking_results,athlete_season
# ★ AND THE RESULT TABLES THE PAGES COUNT PLACES IN (2026-11-06: /athlete
#   55 s, /race/xc 63 s). The athlete page's places and every race page's
#   feed list read idx_results_meet_src_cover / idx_results_tf_meet_ev_place
#   INDEX-ONLY -- but only on heap pages the visibility map calls all-visible,
#   and the go-live rewrites results (merge_column) or updates its ratings,
#   which clears the map; nothing in the run vacuumed either table, so every
#   competitor of every race was a heap read again until autovacuum came by.
#   VACUUM skips pages already all-visible, so a night that changed little
#   costs little. Same trickle as 18. XCP_VACUUM_RESULTS=0 skips.
# (18c_vacuum_results starts in the background after 09d since 2026-11-10;
#  collected by bgwait below)

# the background steps (10f2, 13j, 17d): collected last
bgwait
# ★ AND ONCE MORE AT THE END, after every step and background step that
#   writes what pages read -- then again, detached, once the workers' own
#   15-minute board copies (app._BOARD_API_TTL) have run out, so nothing
#   refilled from an old worker copy outlives the run (edge_purge.py).
# ★ AND THE NEW TABLES READ INTO MEMORY BEFORE THE VISITORS DO (owner,
#   2026-11-06: "only cold timings"; scripts/prewarm.py): every run swaps in
#   tables no cache has seen, so the first page after a go-live paid for the
#   disk. pg_prewarm, capped (XCP_PREWARM_GB, default 16); XCP_PREWARM=0 skips.
if [ "${XCP_PREWARM:-1}" != "0" ]; then
  step 19b_prewarm "$PY" -u scripts/prewarm.py || true
fi
# static pages (racecast/build_static_pages.py): the new boards, before the edge refills
if [ "${XCP_STATIC_PAGES:-0}" = "1" ]; then
  step 19_static_pages env XCP_DB_APP=site XCP_DB_MIN_CONN=1 XCP_DB_MAX_CONN=2 "$PY" -u racecast/build_static_pages.py || true
fi
step 19_edge_purge "$PY" -u scripts/edge_purge.py || true
if [ "$DRY" -ne 1 ]; then
  # ! 9>&- : it outlives the run by 16 minutes and must not hold the lock
  #   that long (2026-11-09: "still locked" with the run long finished)
  nohup "$PY" -u scripts/edge_purge.py --delay 960 >> "$LOGDIR/19_edge_purge_again.log" 2>&1 9>&- &
fi

# ---- summary -------------------------------------------------------- #
summarise
