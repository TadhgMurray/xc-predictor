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
# ⚠ AND IT IS OPT-IN: speed_ratings.loadProTeams reads team_pool only when
#   XCP_TEAM_POOL=1. Neither this script nor deploy/solve_env.sh sets it, so
#   the default is UNCHANGED POOLING. The banner below prints which state the
#   run is in, and XCP_TEAM_POOL is in SOLVE_ENV_VARS so it lands in the log.
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
# ⚠⚠ AND THE SOLVE IS deploy/run_pipeline.sh, NOT scripts/pipeline.py. The
#    first version of this script called the latter, which was wrong in two
#    ways that both mattered (owner, 2026-09-19: "did you use the bracketed
#    engine, and did you use the constants that we've used in previos
#    pipelines/solve like XCP WINTER GAIN?" -- no, and no):
#
#    1. THE BRACKET ENGINE WAS NEVER REACHED. It is published by 08_golive
#       (engine/run_joint.py --golive) and ONLY when XCP_DIFFICULTY=bracket;
#       without that the go-live publishes the joint model's course numbers
#       instead. scripts/pipeline.py has no go-live stage at all, so the
#       chain would have stopped after speed_ratings -- no bracket
#       difficulties, no tilt, no fill_ratings, no rankings rebuild. The site
#       would have been serving boards built on the previous solve's
#       difficulties against this solve's ratings.
#
#    2. NONE OF THE CONSTANTS WERE SET. Every documented run carries a line
#       of XCP_ variables -- XCP_SPORT_LEVEL (which superseded
#       XCP_WINTER_GAIN), XCP_ERA_YEARS, XCP_ALTITUDE, XCP_INDOOR_LEVEL,
#       XCP_IMPORTANCE. They are the run's settings, not defaults, and a run
#       without them is a different model. They now live in
#       deploy/solve_env.sh, are printed before anything starts, and are
#       overridable from the environment.
#
# ! AND 10b0_tfrrs_link IS ALREADY A PIPELINE STAGE
#   (scripts/link_tfrrs_to_anet.py --write). This script used to run it
#   separately AND without --write, so it printed a verdict and wrote
#   nothing, then the pipeline did the real thing later. Removed from here.
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
#       SKIP_SOLVE=1      do the curve and the pools, stop before touching
#                         results (useful the first time)
set -u

cd "$(dirname "$0")/.." || exit 1
. scripts/lib/wait_for_team_scrape.sh

if [ -f /etc/xc-predictor.env ]; then set -a; . /etc/xc-predictor.env; set +a; fi

PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python || echo python3 )}"
LOGDIR="engine/data/overnight/compute"
mkdir -p "$LOGDIR"
PROG="$LOGDIR/00-progress.log"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$PROG"; }

# ★ THE STEP MACHINERY IS SHARED (scripts/lib/step.sh): what each step is FOR,
#   a heartbeat carrying the step's own last log line up into this log, a
#   wall-clock cap per step, and the summary table at the end. Sourced after
#   `say` and LOGDIR exist, because it uses both.
CHAIN_T0=$(date +%s)
. scripts/lib/step.sh

# ★ THE CONSTANTS, SOURCED AND THEN PRINTED. A solve whose settings are not
#   in its own log cannot be reproduced or argued with later.
. deploy/solve_env.sh

# ★ THE TABLE LOCK. backfill and the solve SWAP results / results_tf, and the
#   meet scrapers WRITE to them -- a row written mid-rebuild lands in
#   <table>_old and is lost. overnight_logos.sh waits on this marker before it
#   retries any meet, so the two chains can run at the same time without one
#   of them quietly dropping rows. Removed on every exit path, including a
#   kill: an orphaned marker would block the other chain for ever.
LOCK="$LOGDIR/RESULTS-BUSY"
trap 'rm -f "$LOCK"' EXIT INT TERM

# ★ A FAILING STEP STOPS THE CHAIN HERE (step_fatal), unlike the scrape
#   script. These stages FEED each other: solving on a half-built
#   school_levels.pkl or a curve that refused to save produces ratings that
#   look fine and are wrong. Better to wake up to "stopped at pools" than to a
#   published bad solve. step/step_fatal/chain_summary live in
#   scripts/lib/step.sh, sourced above.

# ★★ THE CAPS, AND WHY THESE NUMBERS. Every step is capped so the chain always
#    reaches its summary; the caps are well above anything observed, because
#    the cap is there to end a HANG, not to hurry a solve.
#
#    The solve is deploy/run_pipeline.sh --from 5 -- backfill, pack, go-live,
#    a ~40 minute holdout, tilt, rankings and the unit builders. The 2026-09-19
#    run took about four and a half hours; 14h is triple that and still ends
#    before a second night. Raise it here if the corpus grows, and say so.
: "${TIMEOUT_curve:=10800}"            # 3h
: "${TIMEOUT_ability_deciles:=3600}"   # 1h, read-only
: "${TIMEOUT_school_levels:=7200}"     # 2h
: "${TIMEOUT_team_identity:=7200}"     # 2h
: "${TIMEOUT_team_pool:=7200}"         # 2h
: "${TIMEOUT_solve:=50400}"            # 14h -- the whole pipeline
: "${TIMEOUT_joint_vs_bracket:=3600}"  # 1h, read-only

: > "$PROG"
say "python: $PY"
say "solve constants (deploy/solve_env.sh; override from the environment):"
for _v in $SOLVE_ENV_VARS; do
    eval "_val=\${$_v-}"
    [ -n "$_val" ] && say "    $_v=$_val"
done

wait_for_team_scrape say

# ⚠ THE LAUNCHER MUST BE OFF FOR backfill AND engine. Both swap the table out
#   from under writers; a row written mid-rebuild lands in <table>_old and is
#   lost (scripts/pipeline.py's own header). Warn loudly rather than kill
#   something the owner may be running on purpose.
# ! THE MEET SCRAPERS, NOT THE TEAM SCRAPER. anet_teams.py writes anet_team
#   and cannot collide with anything here; the meet drivers write results /
#   results_tf, which the backfill and the solve SWAP, and a row written
#   mid-rebuild lands in <table>_old and is lost. So this warns about those
#   two and says which it means -- an earlier version said "the launcher",
#   which meant nothing to the one person who had to act on it.
for drv in "launcher.py" "launch_tfrrs.py"; do
    if [ -n "$(pgrep -f "$drv" || true)" ]; then
        say "⚠⚠ $drv IS RUNNING and it writes results/results_tf, which the"
        say "   backfill and the solve swap. Rows it writes mid-rebuild are"
        say "   LOST. Stop it before this reaches the backfill, or re-run with"
        say "   SKIP_SOLVE=1 for the curve and pools only."
    fi
done

# The lock covers the whole chain, not just the solve step: the curve and the
# pools are what the backfill will read, so a scrape landing rows between them
# and the backfill would be normalised by artifacts it never saw.
: > "$LOCK"
say "results/results_tf marked BUSY ($LOCK) — the scrape chain will wait"

# ---------------------------------------------------------------- 1. curve
# The live artifact, on purpose: this chain exists to produce a solve, and a
# solve has to read one curve. overnight_distance_curve.sh is the separate
# script that fits VARIANTS to their own files and touches nothing.
step_fatal curve \
    "fit the time-vs-distance curve -> engine/data/distance_spline.pkl; the backfill re-applies it to every normalized_time" \
    "$PY" engine/fit_distance_exponent.py --fresh

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
step ability_deciles \
    "read-only: does the distance exponent move with ability? every pool this time, not just college" \
    "$PY" engine/diag_exponent_by_ability.py || true

# ---------------------------------------------------------------- 2. pools
# school_levels.pkl FIRST: it is the one the solve actually reads.
step_fatal school_levels \
    "re-learn each school's level from the freshly scraped anet grades -> school_levels.pkl; the solve reads it" \
    "$PY" scripts/build_school_levels.py

# Then the team-id pooling. Built and inspectable; consumed by nothing yet
# (see the header). Not fatal to the chain for that exact reason -- a solve
# does not depend on them, so a failure here must not block one.
# ⚠⚠ THE BANNER USED TO ASSERT SOMETHING NOBODY HAD SET (2026-09-20). It read
#    "team_pool IS read by the solve below (the 15-athlete rule)" -- but
#    speed_ratings.loadProTeams is OPT-IN behind XCP_TEAM_POOL, deliberately
#    (see its own comment: the column bug meant the rule had never fired, and
#    switching it on in the same run as a bad solve would make the two
#    inseparable). Nothing in this chain or in deploy/solve_env.sh sets it, and
#    it was not in SOLVE_ENV_VARS either, so the run's own log could not say
#    which state it was in -- which is the exact failure solve_env.sh exists to
#    prevent. It now reports the truth instead of asserting a default.
if [ "${XCP_TEAM_POOL:-}" = "1" ] || [ "${XCP_TEAM_POOL:-}" = "true" ]; then
    say "team_pool IS read by the solve below — XCP_TEAM_POOL=${XCP_TEAM_POOL}"
    say "  the <15-athletes-all-time rule repools those rows OFF every school"
    say "  board. Grep team_pool_pro in solve.log for what it cost."
else
    say "team_pool is BUILT but NOT read by the solve (XCP_TEAM_POOL unset)."
    say "  Pooling is unchanged; set XCP_TEAM_POOL=1 to apply the"
    say "  <15-athletes-all-time rule. Confirm either way in solve.log:"
    say "    grep '\[engine\] team_pool:' $LOGDIR/solve.log"
fi
say "team_identity and the tfrrs link are built and inspectable only"
step team_identity \
    "resolve every anet team_id to (school, state); inspectable only -- read by nothing, Georgetown should come out DC" \
    "$PY" racecast/build_team_identity.py --write || true
step team_pool \
    "one row per anet team saying which pool it is and why (the <15-athletes-all-time rule)" \
    "$PY" engine/build_team_pool.py --write || true
# ! NOT link_tfrrs_to_anet HERE. It is pipeline stage 10b0_tfrrs_link, with
#   --write, and running it early without --write printed a verdict and
#   changed nothing.

if [ "${SKIP_SOLVE:-0}" = "1" ]; then
    rm -f "$LOCK"
    step_skipped solve "SKIP_SOLVE=1"
    step_skipped joint_vs_bracket "needs the solve"
    say "SKIP_SOLVE=1 — stopping before anything writes to results; released"
    say "done. read $LOGDIR/curve.log and $LOGDIR/school_levels.log"
    chain_summary
    exit 0
fi

# ------------------------------------------------- 3. re-normalise + solve
# ★ --from 5 IS THE BACKFILL, and the backfill is the point: 05_backfill_xc /
#   05_backfill_tf run backfill_normalize.py, which re-applies the new
#   distance curve and the new pools to results.normalized_time. Everything
#   after it -- 07_pack, 08_golive (the bracket engine), 09_tilt, 09b_fill,
#   10_rankings and the unit builders -- then reads the rebuilt numbers.
#
# ⚠ THE LADDER IS SKIPPED; THE HOLDOUT IS NOT, AND THAT IS A CHANGE. The
#   ladder is the six-to-eight-hour step and it does not change the site, so it
#   stays out. 08a_holdout is ~40 minutes and it is the thing that writes the
#   joint model's per-row held-out predictions -- without a FRESH one, the
#   joint-against-bracket comparison silently falls back to a dump written
#   against another pack and prints "the joint file's held-out rows do not land
#   on this pack", which is exactly what happened on 2026-09-19. Option (a) --
#   publishing the joint solve's own difficulties -- cannot be measured without
#   it, and measuring it is the point of this run.
step_fatal solve \
    "the pipeline from stage 5: backfill -> pack -> 08_golive (the bracket solve) -> holdout -> tilt -> fill -> rankings -> unit builders" \
    bash deploy/run_pipeline.sh --from 5 --skip 08b_ladder

# ---------------------------------------------------- 4. the comparison (a)
# ! AFTER THE SOLVE, READ-ONLY, AND NOT FATAL. Both estimators re-gauged onto
#   the reference class, because comparing them on two different zeros measures
#   the gauge and not the model. The joint solve's shift onto that reference is
#   the "everything outside California went negative" complaint as a number.
step joint_vs_bracket \
    "read-only: the joint and bracket difficulty estimators re-gauged onto ONE reference class" \
    "$PY" engine/diag_joint_vs_bracket.py --show 25 || true

rm -f "$LOCK"
say "results/results_tf released — the scrape chain may retry meets now"

chain_summary

say ""
say "done. read in this order:"
say "  $LOGDIR/curve.log          — SHAPE TEST (ms_*/elem_* only), and whether"
say "                               floored_segments reached zero"
say "  $LOGDIR/school_levels.log  — schools added, and collisions still dropped"
say "  $LOGDIR/joint_vs_bracket.log — (a): the two estimators on ONE gauge, and"
say "                               how far the joint solve's zero sits from the"
say "                               flat-outdoor-400 reference"
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
