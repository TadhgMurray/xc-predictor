#!/bin/bash
# Project: xc-predictor / scripts
# File:    overnight_fit_pool_solve.sh
# Purpose: the COMPUTE chain, unattended: wait for the team-id scrape, then
#          fit the distance curve -> rebuild the pooling -> re-normalise ->
#          solve.
#
# ★ THE ORDER IS A HARD DEPENDENCY, NOT A PREFERENCE (owner, 2026-09-19: "the
#   curve needs to run before the solve"). Correct, and the reason is one link
#   further down than it looks: backfill_normalize.py rewrites
#   results.normalized_time, and normalized_time is WHERE THE DISTANCE CURVE
#   IS APPLIED. The solve then reads normalized_time. So a curve fitted after
#   the backfill changes nothing this solve can see, and a solve run without a
#   backfill in between is rating rows normalised by the OLD curve.
#
#       curve  ->  distance_spline.pkl
#       pools  ->  school_levels.pkl
#       backfill (reads BOTH)  ->  results.normalized_time
#       solve  (reads normalized_time)  ->  results.speed_rating
#
# ⚠ AND THE POOLING HAS TO BE REBUILT HERE TOO, for exactly the same reason.
#   build_school_levels.py's own header says "RUN (once, and again whenever
#   anet is re-scraped)" -- it learns each school's level from that school's
#   anet grades, so a finished team scrape is precisely when it is stale.
#   Running the solve on yesterday's school_levels.pkl after a rescrape pools
#   new schools as "tfrrs -> college".
#
# ⚠⚠ WHAT THE SOLVE DOES *NOT* USE, AND THE OWNER ASKED DIRECTLY: team_pool
#    and team_identity. Nothing reads either table -- grep for them and the
#    only hits are their own builders and one test. The solve pools through
#    normalize_distance.poolFor(grade, gender, source, school), which is keyed
#    on a school NAME STRING plus school_levels.pkl. So the team-id pooling is
#    BUILT by this script and CONSUMED BY NOTHING. It is the fourth thing in
#    this codebase written and wired to nothing, after
#    backfillMeetsTFVenueNames, school_team_link and the distance exponent
#    floor.
#
#    It is built anyway, deliberately: the artifacts have to exist and be
#    inspectable before anything can be pointed at them, and the census in
#    their logs is what says whether they are right. Wiring them is a change
#    to the pool SSOT (engine/pool_resolve.resolvePool) that moves every
#    rating and every board, and it is not something to do unmeasured
#    overnight.
#
# ! NO SCRAPING IN HERE. Scrapes live in overnight_logos.sh so that a slow
#   crawl cannot sit in front of the solve. The 143-meet retry is there too,
#   which means its meets land in the NEXT solve, not this one.
#
#   Usage:
#       nohup bash scripts/overnight_fit_pool_solve.sh > /dev/null 2>&1 &
#       tail -f engine/data/overnight/compute/00-progress.log
#
#   Options (environment):
#       PY=...            python to use
#       SKIP_WAIT=1       start now, do not wait for the scrape
#       SPORT=both|XC|TF  which sport to solve (default both)
#       SKIP_SOLVE=1      do the curve and the pools, stop before touching
#                         results (useful the first time)
set -u

cd "$(dirname "$0")/.." || exit 1
. scripts/lib/wait_for_team_scrape.sh

if [ -f /etc/xc-predictor.env ]; then set -a; . /etc/xc-predictor.env; set +a; fi

PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python || echo python3 )}"
SPORT="${SPORT:-both}"
LOGDIR="engine/data/overnight/compute"
mkdir -p "$LOGDIR"
PROG="$LOGDIR/00-progress.log"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$PROG"; }

# step <name> <command...>
# ★ A FAILING STEP STOPS THE CHAIN HERE, unlike the scrape script. These
#   stages FEED each other: solving on a half-built school_levels.pkl or a
#   curve that refused to save produces ratings that look fine and are wrong.
#   Better to wake up to "stopped at pools" than to a published bad solve.
step() {
    local name="$1"; shift
    say "START $name"
    if "$@" > "$LOGDIR/$name.log" 2>&1; then
        say "  ok   $name"
        return 0
    fi
    say "  FAIL $name  — CHAIN STOPPED (see $LOGDIR/$name.log)"
    return 1
}

: > "$PROG"
say "python: $PY   sport: $SPORT"

wait_for_team_scrape say

# ⚠ THE LAUNCHER MUST BE OFF FOR backfill AND engine. Both swap the table out
#   from under writers; a row written mid-rebuild lands in <table>_old and is
#   lost (scripts/pipeline.py's own header). Warn loudly rather than kill
#   something the owner may be running on purpose.
if [ -n "$(pgrep -f 'launcher\.py' || true)" ]; then
    say "⚠⚠ launcher.py IS RUNNING. backfill and the solve swap results/"
    say "   results_tf out from under it and rows written mid-rebuild are"
    say "   LOST. Stop it before this reaches the backfill stage, or re-run"
    say "   with SKIP_SOLVE=1 to do only the curve and the pools."
fi

# ---------------------------------------------------------------- 1. curve
# The live artifact, on purpose: this chain exists to produce a solve, and a
# solve has to read one curve. overnight_distance_curve.sh is the separate
# script that fits VARIANTS to their own files and touches nothing.
step curve "$PY" engine/fit_distance_exponent.py --fresh || exit 1

# ---------------------------------------------------------------- 2. pools
# school_levels.pkl FIRST: it is the one the solve actually reads.
step school_levels "$PY" scripts/build_school_levels.py || exit 1

# Then the team-id pooling. Built and inspectable; consumed by nothing yet
# (see the header). Not fatal to the chain for that exact reason -- a solve
# does not depend on them, so a failure here must not block one.
say "the three below are BUILT but not yet READ by the solve — see the header"
step team_identity "$PY" racecast/build_team_identity.py || true
step team_pool     "$PY" engine/build_team_pool.py       || true
step link_tfrrs    "$PY" scripts/link_tfrrs_to_anet.py   || true

if [ "${SKIP_SOLVE:-0}" = "1" ]; then
    say "SKIP_SOLVE=1 — stopping before anything writes to results"
    say "done. read $LOGDIR/curve.log and $LOGDIR/school_levels.log"
    exit 0
fi

# ------------------------------------------------- 3. re-normalise + solve
# pipeline.py does backfill -> engine -> suspects and the verify/drop
# bookkeeping between them. --from backfill skips its clean/weather stages,
# which is right here: nothing above touched the weather artifacts.
step solve "$PY" scripts/pipeline.py --sport "$SPORT" --from backfill || exit 1

say "done. read in this order:"
say "  $LOGDIR/curve.log          — SHAPE TEST (ms_*/elem_* only), and whether"
say "                               floored_segments reached zero"
say "  $LOGDIR/school_levels.log  — schools added, and collisions still dropped"
say "  $LOGDIR/team_pool.log      — how many team ids fell to 'pro' under the"
say "                               15-athletes-all-time rule"
say "  $LOGDIR/solve.log          — the backfill and the ratings"
say ""
say "then score the curve and the athlete prior against the old ones:"
say "  $PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2"
say "  $PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 \\"
say "      --prior-athlete fit"
say "  and compare the 'by training rows behind the ATHLETE' tables."
