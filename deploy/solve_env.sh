# Project: xc-predictor / deploy
# File:    solve_env.sh
# Purpose: the solve's CONSTANTS, in one reviewable place. Sourced by
#          scripts/overnight_fit_pool_solve.sh; harmless to source by hand
#          before an interactive deploy/run_pipeline.sh.
#
# ⚠ WHY THIS FILE EXISTS. Every documented run of this pipeline carries a
#   line of XCP_ variables, and they are not defaults -- they are the run's
#   settings, and a run without them is a DIFFERENT MODEL. The unattended
#   chain previously called scripts/pipeline.py with none of them, which
#   would have produced a solve unlike any before it and published it.
#
# ★ THE VALUES BELOW ARE THE LAST DOCUMENTED SET (runs 22/24,
#   docs/HANDOFF-2026-09-13.md §2). They are not invented here. Change them
#   deliberately, and say so in the run log -- the chain prints every one
#   before it starts.
#
# ! XCP_SPORT_LEVEL SUPERSEDED XCP_WINTER_GAIN. The older runs (08-24, and
#   ISSUES-RUNNING) pin the winter gain directly with XCP_WINTER_GAIN=0.02
#   plus XCP_WINTER_GAIN_BANDS; the newer ones (RESEARCH-ENGINE-2026-09-11,
#   runs 22 and 24) state XCP_SPORT_LEVEL=0.0583 instead. Both are ways of
#   pinning the same cross-sport scalar. The newer spelling is used here
#   because it is what the most recent real runs carried; set
#   XCP_WINTER_GAIN yourself if you want the old behaviour back, and expect
#   to justify it.
#
# ★ XCP_DIFFICULTY=bracket IS WHAT MAKES THE BRACKET ENGINE THE PUBLISHED
#   ANSWER. Without it, 08_golive still solves everything but publishes the
#   joint model's course numbers instead (run_joint.bracketDifficulties).
#   The bracket engine is the engine this project uses, so it is set.

# --- the model's settings -------------------------------------------------
: "${XCP_DIFFICULTY:=bracket}"      # publish the BRACKET engine's courses
: "${XCP_SPORT_LEVEL:=0.0583}"      # the cross-sport scalar (ex-winter-gain)
: "${XCP_ERA_YEARS:=2}"             # course eras, in years
: "${XCP_ALTITUDE:=1}"              # altitude correction on
: "${XCP_INDOOR_LEVEL:=0.003}"      # indoor's level against outdoor
# ★ MEASURED, NOT CHOSEN. run_joint's default is 21 days. Swept on the SAME
#   held-out rows -- the only fair comparison, since a wider window also
#   changes coverage -- against a 21-day baseline, 571,290 rows covered by all:
#
#       window 21   0.041938   (baseline)
#       window 30   0.041401   -1.28%   <-- best
#       window 45   0.041434   -1.20%
#       window 60   0.041603   -0.80%
#       window 90   0.041658   -0.67%
#
#   Every one beats 21, and the curve turns over: an INTERIOR optimum around
#   30-45 (those two differ by 0.08%, a tie), declining after. That shape is
#   the finding. It says the window is a real fitness horizon of about a month
#   either side, NOT "the athlete term just wants more rows" -- which would
#   have kept improving out to 90.
#
# ⚠ AND IT REFUTES THE STRATUM STORY IT WAS RUN TO TEST. The idea was that a
#   December championship's voters only see other championship races, so a
#   wider window would let their October form in and fix Foot Locker. But
#   early December plus 30 days reaches back only to about 5 November -- still
#   postseason. The window that WOULD bridge to the September-October season
#   is 90, and 90 is the worst of the four. Bridging costs more in fitness
#   drift than it buys in evidence. So this is a general improvement and NOT
#   the Foot Locker / NXN fix.
#
#   Coverage also rises (63.1% -> 69.3% at 45), which is tens of thousands
#   more rows getting a rating at all -- a second, independent gain that the
#   headline sds hide, since they are then computed on different populations.
#
# ⚠ LEFT UNSET UNTIL THE RESTORATION RUN IS DONE (2026-09-19). It was briefly
#   set to 30 here, which would have made the next solve differ from the last
#   known-good one by TWO things -- the curve revert AND the window -- and the
#   whole point of the restoration is that it changes nothing but undoing a bad
#   run. Set it on the run AFTER the restoration verifies, one change at a
#   time:  XCP_BRACKET_WINDOW=30 bash deploy/run_pipeline.sh --from 8 ...
# : "${XCP_BRACKET_WINDOW:=30}"
# ⚠ LEAVE THIS AT none. I argued for `field` here on 2026-09-19 and the owner
#   refuted it on the spot, with the right instrument: if an untapered
#   championship field were the cause, NXN AND FOOT LOCKER WOULD BOTH SHOW IT,
#   and they do not -- and among athletes who raced both, Foot Locker's
#   normalised times are slightly SLOWER, so its difficulty should sit at or
#   above NXN's rather than below. A taper term cannot produce a difference
#   between two races that share a field and a week. Do not add it to chase
#   this; it would paper over whatever is actually moving the cells.
: "${XCP_IMPORTANCE:=none}"         # `field` is run_joint's own default

# --- housekeeping the pipeline expects -----------------------------------
: "${XCP_DB_QUIET:=1}"

export XCP_DIFFICULTY XCP_SPORT_LEVEL XCP_ERA_YEARS XCP_ALTITUDE \
       XCP_INDOOR_LEVEL XCP_IMPORTANCE XCP_DB_QUIET

# Anything else already in the environment is left alone, so a one-off
#   XCP_PROBES=16 bash scripts/overnight_fit_pool_solve.sh
# still works.
SOLVE_ENV_VARS="XCP_DIFFICULTY XCP_SPORT_LEVEL XCP_ERA_YEARS XCP_ALTITUDE \
XCP_INDOOR_LEVEL XCP_IMPORTANCE XCP_WINTER_GAIN XCP_WINTER_GAIN_BANDS \
XCP_SPORT_LEVEL_POOLS XCP_BRACKET_PRIOR XCP_BRACKET_PLACE_RADIUS \
XCP_BRACKET_PLACE_PRIOR XCP_BRACKET_WINDOW XCP_COURSE_SCALE \
XCP_FROM_STATE XCP_PROBES"
