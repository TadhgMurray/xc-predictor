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
# ⚠⚠ AND THE MEET RETRIES WAIT FOR THE COMPUTE CHAIN, WHICH THE FIRST VERSION
#    OF THIS SCRIPT GOT WRONG. The claim "nothing here shares a table with the
#    compute chain" was false: the meet scrapers WRITE results / results_tf and
#    backfill + the solve SWAP those exact tables, so a row scraped mid-rebuild
#    lands in <table>_old and is lost. The crests really are independent
#    (school_logo), so they still start immediately; only the retries wait, on
#    the RESULTS-BUSY marker the compute chain holds.
#
# ⚠ THE RETRIED MEETS THEREFORE LAND IN THE *NEXT* SOLVE. They arrive after the
#   backfill, so they carry no normalized_time from this cycle. That is the
#   cost of not letting a crawl gate the ratings, and the right trade at 143
#   meets against the whole corpus.
#
# ⚠⚠ AND THE RETRY IS AN ENVIRONMENT VARIABLE, NOT A FLAG. launcher.py has NO
#    argparse at all -- `launcher.py --retry-failed` is silently ignored and it
#    starts an ordinary FORWARD scrape of the whole queue. Unattended, at
#    night, that is much worse than an error, and the first version of this
#    script did exactly that. The switches are ANET_RETRY_FAILED=1 and, for
#    the 22 tfrrs meets of the 143, TFRRS_RETRY_FAILED=1 on its own driver --
#    two separate programs, and the anet launcher does not cover tfrrs.
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
    if env "$@" > "$LOGDIR/$name.log" 2>&1; then
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
# The crests are genuinely independent -- school_logo, which nothing else
# writes -- so they do not wait for anything.
step logos "$PY" scripts/scrape_school_logos.py || true

# -------------------------------------------------------- 2. failed meets
# 143 real meets: 121 anet stranded in state 3, 22 tfrrs. The retry mode claims
# states (2,3) directly and skips the startup reset, so it cannot take the
# whole queue with it.
if [ "${SKIP_MEETS:-0}" != "1" ]; then
    LOCK="engine/data/overnight/compute/RESULTS-BUSY"
    if [ -e "$LOCK" ]; then
        say "waiting: the compute chain holds results/results_tf ($LOCK)."
        say "  Scraping into a table mid-rebuild loses the rows, so the"
        say "  retries wait. The crests above already ran."
        # ! BOUNDED. If the compute chain dies without clearing its marker the
        #   trap should have removed it, but a hard kill -9 cannot run a trap.
        #   Six hours is longer than any solve here and short enough that this
        #   script still finishes overnight.
        waited=0
        while [ -e "$LOCK" ] && [ "$waited" -lt 21600 ]; do
            sleep 60
            waited=$((waited + 60))
        done
        if [ -e "$LOCK" ]; then
            say "  STILL held after 6h — skipping the meet retries rather than"
            say "  risk scraping into a table being swapped. Re-run this script"
            say "  once the compute chain is done."
            SKIP_MEETS=1
        else
            say "  released after $((waited / 60))m; continuing"
        fi
    fi
fi
if [ "${SKIP_MEETS:-0}" != "1" ]; then
    step retry_anet  ANET_RETRY_FAILED=1  "$PY" scripts/launcher.py || true
    step retry_tfrrs TFRRS_RETRY_FAILED=1 "$PY" tfrrs/driver/launch_tfrrs.py || true
else
    say "SKIP_MEETS=1 — the 143 failed meets left alone"
fi

say "done. read:"
say "  $LOGDIR/logos.log        — crest coverage, now that every team id exists"
say "  $LOGDIR/retry_anet.log   — of the 121 anet meets, how many are still failed"
say "  $LOGDIR/retry_tfrrs.log  — and the 22 tfrrs ones"
say ""
say "! the retried meets have no normalized_time from tonight's backfill."
say "  They join the corpus at the next run of overnight_fit_pool_solve.sh."
