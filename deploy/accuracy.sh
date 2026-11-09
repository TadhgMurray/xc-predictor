#!/usr/bin/env bash
# deploy/accuracy.sh -- the accuracy check a --fast pipeline run skips.
#
#   bash deploy/accuracy.sh          # about an hour; run it under tmux
#
# ★ WHY (owner, 2026-11-09: "I didn't see any accuracy metric, where did
#   they go?"). A --fast run leaves out the evidence steps (08a_holdout and
#   the rest), so it publishes ratings without the number that says whether
#   they predict better. This is step 08a_holdout on its own, exactly as the
#   full pipeline runs it: a quarter of the races held out, the model fitted
#   without them, and how far off it is on them (MdAE, bias) per level.
#   It reads; it writes no rating, board or difficulty. The output goes to
#   the screen and to logs/accuracy_<time>.log.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
cd "$ROOT" || exit 1
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
mkdir -p logs
LOG="logs/accuracy_$(date +%Y%m%d_%H%M%S).log"
echo "[accuracy] the race holdout (pipeline step 08a), about an hour; log: $LOG"
nice -n 10 "$PY" -u engine/run_joint.py --holdout-only \
    --holdout-kind race --sample-pct "${XCP_HOLDOUT_PCT:-25}" \
    --outer "${XCP_OUTER:-5}" --probes 0 --altitude \
    ${XCP_SPORT_LEVEL:+--sport-level "$XCP_SPORT_LEVEL"} \
    ${XCP_ERA_YEARS:+--era-years "$XCP_ERA_YEARS"} \
    ${XCP_TILT_SCALE:+--tilt-scale "$XCP_TILT_SCALE"} \
    ${XCP_FORM_TREND:+--form-trend "$XCP_FORM_TREND"} \
    ${XCP_RACE_KEY:+--race-key "$XCP_RACE_KEY"} \
    $([ "${XCP_SEASON_TIE:-0}" = "1" ] && echo --season-tie) 2>&1 | tee "$LOG"
