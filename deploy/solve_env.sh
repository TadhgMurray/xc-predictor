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
# ⚠⚠ THIS IS THE ONE TO ARGUE ABOUT, AND IT IS THE LEADING SUSPECT FOR
#    UNDER-DIFFICULTIED CHAMPIONSHIPS (Foot Locker, and Mt. SAC's 5k against
#    its 3 mile). run_joint's own default is `field` -- "the race's front
#    strength, the mean rating of its top five relative to the median race".
#    `none` is NO TERM AT ALL. Runs 22/24 carried none, which is why it is the
#    value here, but that means the last real solve had no taper/field term.
#
#    WHY THAT UNDER-DIFFICULTIES A CHAMPIONSHIP. A cell's reading is
#    (z - a)/h, z being log normalised time, smaller = faster. At a
#    championship everyone is peaked and tapered, so they run FASTER than
#    their windowed level a predicts; (z - a) goes more negative; D comes out
#    LOWER, i.e. the course reads EASIER. With no importance term there is
#    nothing else for that peak to land in.
#
#    AND IT EXPLAINS THE ASYMMETRY BETWEEN TWO CELLS AT ONE VENUE, which is
#    the part a shrinkage story could not. Mt. SAC's 3 mile is the
#    regular-season invitational distance -- thousands of ordinary runners on
#    ordinary days -- so its cell is dominated by untapered races and reads a
#    true +10%. Mt. SAC's 5k is the postseason distance, so its cell is mostly
#    tapered championship races and absorbs the full bias: +4.0%. Same hill,
#    same footing, different populations. Glendoveer likewise hosts ordinary
#    meets all season, so NXN's cell is mixed and partly anchored; a venue
#    used only for a national final has a cell that is 100% tapered.
#
#    SET XCP_IMPORTANCE=field AND SEE WHETHER THE 5k AND THE 3 MILE CONVERGE.
#    That is the test, and it is one line.
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
XCP_BRACKET_PLACE_PRIOR XCP_COURSE_SCALE XCP_FROM_STATE XCP_PROBES"
