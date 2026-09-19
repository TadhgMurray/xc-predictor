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
# ★ team_pool IS NOW WIRED, and the earlier claim that it was not was too
#   broad. Team-id pooling was always live: speed_ratings calls
#   teamLevelOf(team_id, slug, loadAnetLevels()) per row and resolvePool
#   consumes team_level / team_has_pros / no_team, so team_id -> level,
#   team_id == 0 -> professional, and club-with-a-pro -> professional all
#   reached the pool already. What reached nothing was team_pool's OWN
#   verdict, and with it the rule the owner was emphatic about: "no not 15 per
#   year 15 over all time". speed_ratings now reads that verdict
#   (loadProTeams) and passes resolvePool a `team_pro` fact, ungated like
#   no_team, and counts it as census["team_pool_pro"].
#
# ⚠ WHICH IS WHY team_pool MUST BE BUILT BEFORE THE SOLVE IN THIS CHAIN, and
#   it is -- the step order below is load-bearing now, not decorative. An
#   absent team_pool table is not an error: loadProTeams returns an empty set
#   and the solve pools exactly as it did before, so a first run cannot be
#   half-applied.
#
# ! GREP team_pool_pro IN solve.log. It is the price of the 15-athlete rule in
#   rows, and it repools them off every school board.
#
# ! team_identity IS STILL READ BY NOTHING. It is built because the artifact
#   has to exist and be inspectable before anything can point at it, and its
#   log is what says whether the (team_id -> school, state) resolution is
#   right -- Georgetown should come out DC.
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

# ----------------------------------------------- 1b. the ability question
# ★ READ ONLY, AND THE MEASUREMENT THAT IS STILL MISSING (owner, 2026-09-19:
#   "did we ever get the distance spline by ability thing?"). No -- the FIT
#   does not exist; fit_distance_exponent has no ability dimension at all.
#   diag_exponent_by_ability.py MEASURES it and has for a while, and it was
#   only ever run for college: the deciles showed college XC spreading +0.055
#   while college TF was flat, and hs/ms were never run even after the owner
#   said "same for hs prolly smae problem".
#
#   It reads fit_distance_exponent's own pair cache -- which the curve step
#   above has just rebuilt -- so it costs almost nothing here and judges
#   exactly the pairs the spline was fitted on. No --pool means every pool.
#
# ! NOT FATAL. It changes nothing; a failure here must not stop a solve.
step ability_deciles "$PY" engine/diag_exponent_by_ability.py || true

# ---------------------------------------------------------------- 2. pools
# school_levels.pkl FIRST: it is the one the solve actually reads.
step school_levels "$PY" scripts/build_school_levels.py || exit 1

# Then the team-id pooling. Built and inspectable; consumed by nothing yet
# (see the header). Not fatal to the chain for that exact reason -- a solve
# does not depend on them, so a failure here must not block one.
say "team_pool IS read by the solve below (the 15-athlete rule);"
say "team_identity and the tfrrs link are built and inspectable only"
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
say "  $LOGDIR/ability_deciles.log — does the exponent move with ability, in"
say "                               EVERY pool this time, not just college"
say "  $LOGDIR/solve.log          — the backfill and the ratings; grep for"
say "                               team_pool_pro to price the 15-athlete rule"
say ""
say "then score the curve and the athlete prior against the old ones:"
say "  $PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2"
say "  $PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 \\"
say "      --prior-athlete fit"
say "  and compare the 'by training rows behind the ATHLETE' tables."
