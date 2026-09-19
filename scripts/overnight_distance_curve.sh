#!/bin/bash
# Project: xc-predictor / scripts
# File:    overnight_distance_curve.sh
# Purpose: the whole distance-curve comparison, unattended, in one command.
#
# ★ IT NEVER TOUCHES THE LIVE ARTIFACT. Every fit goes to its own file under
#   engine/data/variants/ via --out, so the engine keeps reading exactly the
#   curve it read before this script ran. Nothing here needs a decision at
#   3am; the decision is made in the morning from the logs.
#
# ★ THE PAIR STREAM RUNS ONCE. It is the expensive part (a full table scan
#   per sport); the four fits after it read the pair cache and take minutes.
#   So --fresh appears on the first fit and nowhere else.
#
# ! ORDER MATTERS AND IS NOT ARBITRARY. The baseline goes first so that if
#   the box dies at 2am there is still a like-for-like reference for
#   whatever did finish.
#
#   Usage:  nohup bash scripts/overnight_distance_curve.sh > /dev/null 2>&1 &
#           tail -f engine/data/variants/00-progress.log
set -u

cd "$(dirname "$0")/.." || exit 1
OUT="engine/data/variants"
mkdir -p "$OUT"
PROG="$OUT/00-progress.log"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$PROG"; }

: > "$PROG"
say "start. live artifact is engine/data/distance_spline.pkl and stays untouched."

# name : extra flags : whether to re-stream the DB
run() {
  local name="$1"; shift
  local fresh="$1"; shift
  local art="$OUT/$name.pkl"
  local log="$OUT/$name.log"
  say "FIT $name  ($* ${fresh:+--fresh})"
  if python engine/fit_distance_exponent.py $fresh "$@" \
        --out "$art" > "$log" 2>&1; then
    if [ -f "$art" ]; then
      say "  ok -> $art"
      say "  reading it back"
      python scripts/distance_curve_support.py --artifact "$art" \
        > "$OUT/$name.support.log" 2>&1 \
        || say "  ! support diagnostic failed, see $OUT/$name.support.log"
    else
      # savePotentials refuses to write an unphysical global curve. That is
      # a RESULT, not a crash: record it and carry on to the next variant.
      say "  SAVE REFUSED (unphysical global curve) — see $log"
    fi
  else
    say "  FIT FAILED — see $log"
  fi
}

# 1. baseline: today's methodology, re-streamed. Also the run whose
#    SHAPE TEST block answers step 0 -- does one shape fit both sports.
run baseline --fresh

# 2. the degree rule: is the TF slope a ladder artifact
run degree-locations "" --degree-rule locations

# 3. the slope prior: does floored_segments go to zero
run slope-prior "" --slope-prior 1

# 4. merged sports: do the six extrapolating TF pools stop extrapolating
run merged "" --merge-sports

# 5. and the combination, because these are not independent: a merged pool
#    has a wider span, which changes what the degree rule grants and where
#    the prior has any weight at all.
run all-three "" --degree-rule locations --slope-prior 1 --merge-sports

say "done. read in this order:"
say "  1. grep -A30 'SHAPE TEST' $OUT/baseline.log        <- ms_* and elem_* only"
say "  2. grep -c 'target [0-9,]* m\$' $OUT/*.support.log  <- the six in?=NO pools"
say "  3. grep 'floor' $OUT/slope-prior.log               <- want zero"
say "  4. grep 'by the other rule' $OUT/degree-locations.log"
say "nothing has been promoted. to adopt one:"
say "  cp $OUT/<name>.pkl engine/data/distance_spline.pkl && systemctl restart racecast"
