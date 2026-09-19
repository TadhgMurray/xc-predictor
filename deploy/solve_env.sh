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
# ★ UNSET ON PURPOSE, WITH A MEASUREMENT BEHIND IT. run_joint's default is 21
#   days. On the SAME held-out rows -- the only fair comparison, because a
#   wider window also changes coverage -- 45 beats 21:
#
#       SAME ROWS, BOTH WINDOWS: 571,290 rows covered by both
#         window 45: 0.041434     window 21: 0.041938      (-1.2% relative)
#
#   and coverage rises 63.1% -> 69.3%, which is 28,000 more XC rows getting a
#   rating at all. The headline sds say the opposite (45 reads 0.042507) only
#   because 45 scores the harder rows 21 could not reach.
#
#   It is left unset rather than set to 45 because 45 was the only alternative
#   tried. Sweep it before adopting one:
#     for w in 30 45 60 90; do
#       scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 #           --window "$w" --compare /tmp/w21.npz
#     done
#   (dump the 21 baseline once with --window 21 --dump /tmp/w21.npz.)
# : "${XCP_BRACKET_WINDOW:=45}"
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
