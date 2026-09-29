#!/usr/bin/env bash
# owner_checks.sh -- every pending read-only check, in one run, one log.
#
#     cd /srv/xc-predictor && git pull && bash scripts/owner_checks.sh
#     bash scripts/owner_checks.sh --list          # what it would run
#     bash scripts/owner_checks.sh --only board    # the checks whose name matches
#
# ★ WHY (owner, 2026-09-29: "there are so many scripts please try to make
#   sure i run them all"). Each fix came with its own diagnostic, handed over
#   one message at a time, and some ran before the pull that fixed them.
#   This pulls nothing and writes nothing: it runs the list below, each under
#   a header, keeps going past a failure, and ends with a table of what ran,
#   how long it took and whether it failed. The whole output also lands in
#   logs/checks/<timestamp>.log -- paste that file back.
#
# ! READ-ONLY BY CONSTRUCTION. Every command here is a report or a dry run.
#   The steps that WRITE are decisions, so they are only PRINTED at the end
#   (PENDING_WRITES), never run.
#
# ! KEEP IT CURRENT. When a check has been read and acted on, delete its
#   line; when a new one is handed over, add it here rather than in a chat.
set -u
cd "$(dirname "$0")/.." || exit 1
if [ -f /etc/xc-predictor.env ]; then set -a; . /etc/xc-predictor.env; set +a; fi
PY="${PY:-/srv/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

# name | command -- the name is what --only matches
CHECKS=(
  "collision-mosley|$PY scripts/person_collision.py --show 20000 | grep -i -E 'mosley|^\[collision\]' || true"
  "why-unrated-sahlman|$PY scripts/why_unrated.py 29347137 --sport TF"
  "why-unrated-kitchen|$PY scripts/why_unrated.py 29332123 --sport TF"
  "brooks-salcido|$PY scripts/why_unrated.py 19068584 --sport TF --replay"
  "brooks-edwards|$PY scripts/why_unrated.py 21572633 --sport TF --replay"
  "ms-zone-lutkenhaus|$PY scripts/why_unrated.py 30245014 --sport XC --replay"
  "ms-zone-vijaykumar|$PY scripts/why_unrated.py 17385087 --sport XC --replay"
  "ncs-weather|$PY scripts/diag_row_weather.py --xc 59845242 52770927 46185539"
  "ncs-explain|$PY scripts/explain_joint_row.py --xc 59845242 52770927 46185539"
  "outliers-dry|$PY -u engine/rating_outliers.py --dry-run --show 20"
  "outliers-moretta|$PY -u engine/rating_outliers.py --dry-run --person 29603084"
  "board-plan|$PY scripts/explain_board.py 'board=ability&pool=hs_m&sport=both&scope=usa&scale=hs&limit=50&offset=0&sort=rating' --plans"
  "sport-gap|$PY scripts/diag_sport_gap_ability.py"
  "amnesty-dry|$PY scripts/amnesty_division_drops.py"
  "ability-curve-dry|$PY engine/fit_distance_ability.py --dry-run"
  "one-scale|$PY scripts/diag_one_scale.py --all"
  "group-means|$PY scripts/build_group_means.py"
  # ! writes engine/data/indoor_geometry_levels.json only (no database):
  #   the measured level per indoor track type, which the holdout below reads
  "indoor-levels|$PY scripts/indoor_outdoor_check.py --by geometry --write-levels"
)

# printed, never run: hours each, and each is a comparison to read before a
# switch is turned on (see the commit messages of df34f33 and the B+C merge)
LONG_RUNS=(
  "$PY -u scripts/ablation_ladder.py --only base,season-tie,tilt-hs --pct 15 --outer 5   # then switch_scorecard.py --holdout base vs each"
  "$PY engine/fit_distance_ability.py                         # writes distance_ability.pkl (30-60 min)"
  "$PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 --window 30 --gauge flat400 --dump /tmp/holdout_pin.npz   # indoor: pin (today)"
  "$PY scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 --window 30 --gauge flat400 --indoor-mode geometry --compare /tmp/holdout_pin.npz   # indoor: by track type"
  "XCP_DISTANCE_BY=ability bash deploy/run_pipeline.sh --from 5 --skip 08a_holdout,08b_ladder   # adopt C; revert with XCP_DISTANCE_BY=pool"
)

# printed, never run: each needs the owner's yes after reading the checks
PENDING_WRITES=(
  "$PY engine/meet_date_fix.py --write                      # XC 25 meets + TF 50, read 2026-09-29: grades agree"
  "$PY scripts/person_collision.py --write                  # if collision-mosley shows no Mosley"
  "$PY scripts/amnesty_division_drops.py --write && $PY engine/dump_overrides.py"
  "$PY -u engine/rating_outliers.py --write --show 10       # after reading outliers-dry"
  "runuser -u postgres -- psql -d xc_predictor -c 'CREATE INDEX CONCURRENTLY IF NOT EXISTS as_pool_mean_nl_idx ON athlete_season (pool, mean_rating DESC NULLS LAST, person_id)'   # optional: fast boards now, before the pipeline rebuilds them"
  "XCP_WEATHER_FIT=1 bash deploy/run_pipeline.sh --from 3   # last: rebuilds everything above"
)

list=0; only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --list) list=1 ;;
    --only) only="$2"; shift ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
  shift
done

if [ "$list" = 1 ]; then
  for c in "${CHECKS[@]}"; do echo "  ${c%%|*}: ${c#*|}"; done
  exit 0
fi

mkdir -p logs/checks
LOG="logs/checks/$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1
echo "[checks] $(git log --oneline -1)   log: $LOG"

summary=()
for c in "${CHECKS[@]}"; do
  name="${c%%|*}"; cmd="${c#*|}"
  if [ -n "$only" ] && [[ "$name" != *"$only"* ]]; then continue; fi
  echo; echo "================ $name ================"; echo "\$ $cmd"
  t0=$(date +%s)
  bash -c "$cmd"
  rc=$?
  dt=$(( $(date +%s) - t0 ))
  summary+=("$(printf '%-22s %6ss  %s' "$name" "$dt" "$([ $rc = 0 ] && echo ok || echo "FAILED ($rc)")")")
done

echo; echo "================ summary ================"
for s in "${summary[@]}"; do echo "  $s"; done
echo; echo "================ writes waiting on you (NOT run) ================"
for w in "${PENDING_WRITES[@]}"; do echo "  $w"; done
echo; echo "================ long runs (NOT run; hours each) ================"
for w in "${LONG_RUNS[@]}"; do echo "  $w"; done
echo; echo "[checks] full output: $LOG"
