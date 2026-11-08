#!/usr/bin/env bash
# deploy/joint_flags.sh -- the production solve's flags, in ONE place.
#
# ★ ONE DEFINITION (owner, 2026-11-07: "there shouldnt be any hindsight").
#   The go-live (run_pipeline 08_golive) and the replays as of a date
#   (deploy/engine_replay.sh, run_joint --asof) must solve the SAME model,
#   or a replay would train the race model on a different engine. Both
#   source this file and call jointFlags, which fills JOINT_FLAGS from the
#   XCP_* environment exactly as 08_golive always read it.
jointFlags() {
  JOINT_FLAGS=(--probes "${XCP_PROBES:-0}")
  JOINT_FLAGS+=(--outer "${XCP_OUTER:-5}")
  JOINT_FLAGS+=(${XCP_DIFFICULTY:+--difficulty "$XCP_DIFFICULTY"})
  JOINT_FLAGS+=(${XCP_BRACKET_PRIOR:+--bracket-prior "$XCP_BRACKET_PRIOR"})
  JOINT_FLAGS+=(${XCP_BRACKET_WINDOW:+--bracket-window "$XCP_BRACKET_WINDOW"})
  JOINT_FLAGS+=(${XCP_TRACK_LEVEL_BY_POOL:+--track-level-by-pool "$XCP_TRACK_LEVEL_BY_POOL"})
  JOINT_FLAGS+=(${XCP_BRACKET_PLACE_RADIUS:+--bracket-place-radius "$XCP_BRACKET_PLACE_RADIUS"})
  JOINT_FLAGS+=(${XCP_BRACKET_SIBLING_TOL:+--bracket-sibling-tol "$XCP_BRACKET_SIBLING_TOL"})
  JOINT_FLAGS+=(${XCP_GAUGE:+--gauge "$XCP_GAUGE"})
  JOINT_FLAGS+=(${XCP_GAUGE_SCOPE:+--gauge-scope "$XCP_GAUGE_SCOPE"})
  JOINT_FLAGS+=(${XCP_BRACKET_INDOOR_GATES:+--bracket-indoor-gates "$XCP_BRACKET_INDOOR_GATES"})
  JOINT_FLAGS+=(${XCP_DAY_NOISE:+--day-noise "$XCP_DAY_NOISE"})
  JOINT_FLAGS+=(${XCP_BRACKET_INDOOR_CENTRE:+--bracket-indoor-centre "$XCP_BRACKET_INDOOR_CENTRE"})
  JOINT_FLAGS+=(${XCP_BRACKET_INDOOR_MODE:+--bracket-indoor-mode "$XCP_BRACKET_INDOOR_MODE"})
  JOINT_FLAGS+=(${XCP_BRACKET_INDOOR_LEVELS:+--bracket-indoor-levels "$XCP_BRACKET_INDOOR_LEVELS"})
  JOINT_FLAGS+=(${XCP_BRACKET_XC_LEVEL:+--bracket-xc-level "$XCP_BRACKET_XC_LEVEL"})
  JOINT_FLAGS+=(${XCP_BRACKET_XC_LEVEL_MODE:+--bracket-xc-level-mode "$XCP_BRACKET_XC_LEVEL_MODE"})
  JOINT_FLAGS+=(${XCP_BRACKET_PLACE_PRIOR:+--bracket-place-prior "$XCP_BRACKET_PLACE_PRIOR"})
  JOINT_FLAGS+=(${XCP_BRACKET_PLACE_VENUE:+--bracket-place-venue "$XCP_BRACKET_PLACE_VENUE"})
  JOINT_FLAGS+=(${XCP_COURSE_SCALE:+--course-scale "$XCP_COURSE_SCALE"})
  JOINT_FLAGS+=(${XCP_RACE_EFFECT_SPORTS:+--race-effect-sports "$XCP_RACE_EFFECT_SPORTS"})
  JOINT_FLAGS+=(${XCP_RACE_EFFECT_OWN:+--race-effect-own "$XCP_RACE_EFFECT_OWN"})
  JOINT_FLAGS+=(${XCP_RACE_KEY:+--race-key "$XCP_RACE_KEY"})
  JOINT_FLAGS+=(${XCP_SPORT_LEVEL_POOLS:+--sport-level-pools "$XCP_SPORT_LEVEL_POOLS"})
  JOINT_FLAGS+=(${XCP_FROM_STATE:+--from-state "$XCP_FROM_STATE"})
  JOINT_FLAGS+=(${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"})
  JOINT_FLAGS+=(${XCP_IMPORTANCE:+--importance "$XCP_IMPORTANCE"})
  JOINT_FLAGS+=(${XCP_NO_IMPORTANCE:+--no-importance})
  JOINT_FLAGS+=(${XCP_NO_INDOOR:+--no-indoor})
  JOINT_FLAGS+=(${XCP_INDOOR_LEVEL:+--indoor-level "$XCP_INDOOR_LEVEL"})
  JOINT_FLAGS+=(${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"})
  JOINT_FLAGS+=(${XCP_ERA_DRIFT:+--era-drift "$XCP_ERA_DRIFT"})
  JOINT_FLAGS+=(${XCP_DIST_WALK:+--dist-walk "$XCP_DIST_WALK"})
  JOINT_FLAGS+=(${XCP_NO_DIST_TABLE:+--no-dist-table})
  JOINT_FLAGS+=(${XCP_MERGE_SPORTS:+--merge-sports})
  JOINT_FLAGS+=(${XCP_CENTRE_CURVE:+--centre-curve})
  JOINT_FLAGS+=(${XCP_TAU_MAX:+--tau-max "$XCP_TAU_MAX"})
  JOINT_FLAGS+=(${XCP_SPLIT_ABILITY:+--split-ability})
  JOINT_FLAGS+=(${XCP_WINTER_GAIN:+--winter-gain "$XCP_WINTER_GAIN"})
  JOINT_FLAGS+=(${XCP_WINTER_GAIN_BANDS:+--winter-gain-bands "$XCP_WINTER_GAIN_BANDS"})
  JOINT_FLAGS+=(${XCP_TILT_SCALE:+--tilt-scale "$XCP_TILT_SCALE"})
  JOINT_FLAGS+=(${XCP_FORM_TREND:+--form-trend "$XCP_FORM_TREND"})
  if [ "${XCP_SEASON_TIE:-0}" = "1" ]; then JOINT_FLAGS+=(--season-tie); fi
  if [ "${XCP_ALTITUDE:-1}" != "0" ]; then JOINT_FLAGS+=(--altitude); fi
  if [ "${XCP_ALTITUDE:-1}" != "0" ] && [ "${XCP_ALTITUDE_FIT:-0}" = "1" ]; then JOINT_FLAGS+=(--altitude-fit); fi
}
