# Project: xc-predictor / deploy
# File:    solve_env_v2.sh
# Purpose: the SIMPLER MODEL's settings, for a comparison run against
#          deploy/solve_env.sh (owner, 2026-10-18: "it just feels all
#          patchwork" -- the go-ahead to build the new engine).
#
#   . deploy/solve_env_v2.sh && bash deploy/run_pipeline.sh --from 4
#   (it exports; the pipeline then sources solve_env.sh, whose := defaults
#   leave these values alone)
#
# ★ WHAT CHANGES, AND WHAT EACH REPLACES
#   XCP_EFFORT=1          runs that weren't all-out are a one-sided shortfall
#                         (joint_solve.effortFit), not a slow day: replaces
#                         the field check and the opener rule
#   XCP_RACE_KEY=division one race-day number per division, not one per
#                         venue-day: replaces the division guard and lean
#   XCP_SEASON_TIE=1      a runner's seasons are tied by a fitted walk: the
#                         island check's job, inside the fit
#   XCP_DIFFICULTY=joint  the joint solve publishes its own courses: one
#                         engine (the bracket engine's courses and the day
#                         refit against them stop being what ratings read)
#   XCP_FIELD_CHECK=0, XCP_ISLAND_CHECK=0   the patches above, off
#   XCP_ALTITUDE_FIT=1    (already the default) altitude fitted
#
# ⚠ DECIDE ON THE SCORECARD FIRST, PUBLISH SECOND. Before a full run with
#   these settings, score them forward against production on held-out races:
#     python scripts/scorecard.py --base production \
#         --try "production --effort --race-key division --season-tie"
#   and after a full run, scripts/scoreboard.py's tests beside the last run's.

export XCP_EFFORT=1
export XCP_RACE_KEY=division
export XCP_SEASON_TIE=1
export XCP_DIFFICULTY=joint
export XCP_FIELD_CHECK=0
export XCP_ISLAND_CHECK=0
export XCP_ALTITUDE_FIT=1
echo "  solve_env_v2: effort on, race day per division, season tie, one engine (joint), field and island checks off"
