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
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"

FROM=0; SKIP_BACKFILL=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
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

LOGDIR="$ROOT/logs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOGDIR"
SUMMARY="$LOGDIR/summary.log"
echo "  logs -> $LOGDIR"

FAILED=""
T_START=$(date +%s)

# step <name> <command...>
step() {
  name="$1"; shift
  num=$(echo "$name" | sed 's/^0*\([0-9]*\).*/\1/')
  if [ "${num:-0}" -lt "$FROM" ] && [ "$name" != "02_drop_old" ]; then
    echo "  $name skipped (--from $FROM)"
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

# ! THE BACKFILL RUNS AFTER grade_sanity, NOT BEFORE -- it resolves pools from
#   grade_fix, and a disagreement writes normalized_time on the wrong SCALE
#   (measured at a 64% rating error, frozen into the row).
if [ "$SKIP_BACKFILL" -eq 1 ]; then
  echo "  05_backfill skipped (--skip-backfill)"
else
  step 05_backfill    "$PY" -u backfill/backfill_normalize.py --sport both --apply
fi

# ---- pack and solve ------------------------------------------------- #
# ! BOTH caches must go: packed_XC_TF.npz is checked for existence only, and
#   pair_solve_cache.npz fingerprints on sum(y), which does not notice a pool
#   reassignment that leaves the sum intact.
if [ "$FROM" -le 6 ]; then
  if [ "$DRY" -eq 1 ]; then
    echo "  06_clear_cache : rm engine/data/{packed_XC_TF,pair_solve_cache}.npz"
  else
    rm -f engine/data/packed_XC_TF.npz engine/data/pair_solve_cache.npz
    echo "  06_clear_cache : caches cleared"
  fi
else
  echo "  06_clear_cache skipped (--from $FROM)"
fi

step 07_pack          "$PY" -u engine/speed_ratings.py --sport merged --cache --pack-only

# ★ TWO SOLVERS, ONE SWITCH.
#   XCP_JOINT_LIVE=1  the joint solve IS step 08: it writes course_difficulties,
#                     athlete_ratings, results.speed_rating and
#                     pair_difficulty.npz (issue 116). The tilt is inside its
#                     ratings, so 09_tilt is skipped -- running it would tilt
#                     twice -- and 10c_gap is skipped, since the sport level is
#                     a parameter and the bbar loop has nothing to steer.
#   XCP_JOINT=1       the sequential solve stays live; the joint solve runs
#                     beside it as a shadow and writes joint_difficulty.npz only.
if [ "${XCP_JOINT_LIVE:-0}" = "1" ]; then
  step 08_golive        "$PY" -u engine/run_joint.py --golive --holdout --probes 16
  echo "  08b_joint_shadow: the joint solve is live (XCP_JOINT_LIVE=1)"
  echo "  09_tilt skipped: the joint ratings carry the tilt"
else
  step 08_golive        "$PY" -u engine/linkage_check.py --golive --split
  if [ "${XCP_JOINT:-0}" = "1" ]; then
    # --holdout scores 10% of rows first (a second solve); --probes 16 keeps
    # the posterior-variance pass to minutes rather than hours on a first run.
    step 08b_joint_shadow "$PY" -u engine/run_joint.py --holdout --probes 16
  else
    echo "  08b_joint_shadow skipped (set XCP_JOINT=1 to run)"
  fi
  step 09_tilt          "$PY" -u engine/apply_tilt.py --refresh --write
fi
step 09b_fill         "$PY" -u engine/fill_ratings.py


# ---- boards and pages ----------------------------------------------- #
step 10_rankings      "$PY" -u racecast/build_ranking_results.py
step 10b_school_ids   "$PY" -u racecast/build_school_identity.py
if [ "${XCP_JOINT_LIVE:-0}" = "1" ]; then
  # measured for telemetry only: the joint level is not steered by the json
  step 10c_gap        "$PY" -u scripts/measure_sport_gap.py
else
  step 10c_gap        "$PY" -u scripts/measure_sport_gap.py --emit
fi

step 10d_school_units "$PY" -u racecast/build_school_units.py
step 11_teams         "$PY" -u racecast/build_team_season.py
step 12_courses       "$PY" -u racecast/build_course_rank.py
step 12b_course_pages "$PY" -u racecast/build_course_boards.py --limit 1200
step 13_panels        "$PY" -u racecast/panels.py
step 13b_pool_consts  "$PY" -u scripts/warm_pool_constants.py
step 14_indexes       "$PY" -u scripts/add_page_indexes.py

# ---- rowguard ------------------------------------------------------- #
# 2000-row rail; anything past it diverts to .OVER-CAP for a human.
step 15_rowguard_diag_xc   "$PY" -u scripts/diag_suspects.py --sport XC
step 15_rowguard_diag_tf   "$PY" -u scripts/diag_suspects.py --sport TF
step 15_rowguard_triage_xc "$PY" -u scripts/triage_suspects.py --sport XC
step 15_rowguard_triage_tf "$PY" -u scripts/triage_suspects.py --sport TF
step 16_rowguard_apply     "$PY" -u scripts/apply_triage.py

# ---- the owner's go/no-go ------------------------------------------- #
step 17_checklist     "$PY" -u scripts/run_checklist.py

# ---- summary -------------------------------------------------------- #
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
