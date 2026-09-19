#!/bin/bash
# Project: xc-predictor / scripts
# File:    overnight_logos.sh
# Purpose: the SCRAPE chain, unattended: wait for the team-id scrape, then the
#          crests, then the 143 failed meets.
#
# ★ WHY THE CRESTS WAIT (owner, 2026-09-19: "only once the team id thing is
#   finished"). scrape_school_logos.py takes anet crests from
#   anet_team.mascot_url, so a run that starts early covers only the teams
#   scraped so far -- and worse than being incomplete, its coverage number is
#   a confident lie about the teams that do not exist in the table yet.
#
# ★ WHY THIS IS SEPARATE FROM THE COMPUTE CHAIN. These are crawls: rate-limited,
#   hours long, and bounded by anet rather than by this box. Putting them in
#   front of the solve would mean a slow crawl decides when the ratings get
#   rebuilt. Nothing here shares a table with the compute chain either --
#   school_logo and meet_queue against distance_spline.pkl, school_levels.pkl
#   and results -- so the two scripts can and should run at the same time.
#
# ⚠ THE RETRIED MEETS LAND IN THE *NEXT* SOLVE, NOT TONIGHT'S. New rows arrive
#   after the backfill has already run, so they carry no normalized_time from
#   this cycle. That is the cost of not blocking the solve on a crawl, and it
#   is the right trade: 143 meets against the whole corpus.
#
# ! A FAILING STEP DOES NOT STOP THE CHAIN. Unlike the compute chain, these
#   stages do not feed each other -- crests and meet retries are independent,
#   so a failure in one is no reason to skip the other.
#
#   Usage:
#       nohup bash scripts/overnight_logos.sh > /dev/null 2>&1 &
#       tail -f engine/data/overnight/scrape/00-progress.log
#
#   Options (environment):
#       PY=...          python to use
#       SKIP_WAIT=1     start now, do not wait for the team scrape
#       SKIP_MEETS=1    crests only, leave the failed meets alone
set -u

cd "$(dirname "$0")/.." || exit 1
. scripts/lib/wait_for_team_scrape.sh

if [ -f /etc/xc-predictor.env ]; then set -a; . /etc/xc-predictor.env; set +a; fi

PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python || echo python3 )}"
LOGDIR="engine/data/overnight/scrape"
mkdir -p "$LOGDIR"
PROG="$LOGDIR/00-progress.log"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$PROG"; }

step() {
    local name="$1"; shift
    say "START $name"
    if "$@" > "$LOGDIR/$name.log" 2>&1; then
        say "  ok   $name"
        return 0
    fi
    say "  FAIL $name  (see $LOGDIR/$name.log) — continuing"
    return 1
}

: > "$PROG"
say "python: $PY"

wait_for_team_scrape say

# ------------------------------------------------------------- 1. crests
step logos "$PY" scripts/scrape_school_logos.py || true

# -------------------------------------------------------- 2. failed meets
# 143 real meets: 121 anet stranded in state 3, 22 tfrrs. --retry-failed
# claims states (2,3) directly and skips the startup reset, so it cannot take
# the whole queue with it.
if [ "${SKIP_MEETS:-0}" != "1" ]; then
    step retry_meets "$PY" scripts/launcher.py --retry-failed || true
else
    say "SKIP_MEETS=1 — the 143 failed meets left alone"
fi

say "done. read:"
say "  $LOGDIR/logos.log        — crest coverage, now that every team id exists"
say "  $LOGDIR/retry_meets.log  — of the 143, how many are still failed"
say ""
say "! the retried meets have no normalized_time from tonight's backfill."
say "  They join the corpus at the next run of overnight_fit_pool_solve.sh."
