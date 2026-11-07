#!/usr/bin/env bash
# deploy/engine_replay.sh -- the engine solved AS OF each cutoff date, for a
# race model trained without hindsight (docs/PLAN-MODEL-2026-10.md §9).
#
#   tmux new -d -s replay "bash deploy/engine_replay.sh"          # every cutoff
#   bash deploy/engine_replay.sh 2025-10-01                        # one, as a test
#   bash deploy/engine_replay.sh --list                            # what it would do
#   ls -la /srv/pred/asof/; tail -3 logs/replay/summary.log         # progress
#
# ★ WHY (owner, 2026-11-06: "there shouldnt be any hindsight"). The race
#   model's training races read today's ratings and course numbers, from a
#   solve that has seen them and every race after. Each cutoff here is the
#   go-live's own solve (deploy/joint_flags.sh) on the rows dated before
#   it; its result ratings and course cells go to
#   $XCP_PRED_DIR/asof/asof_<date>.npz (run_joint --asof --snapshot) and
#   NEVER to the database. race_rows --asof-dir then builds every training
#   race from the snapshot in force the day it was predicted.
#
# ! THE CUTOFFS: the 1st and 15th, mid-August to December 1, of every season
#   in XCP_REPLAY_SEASONS (default 2025 2026 2024 2023: the validation
#   season first, so an honest score comes soonest). A done cutoff (its file
#   exists) is skipped, so the script can be stopped and started again.
# ! NEVER BESIDE THE PIPELINE: each solve takes .pipeline.lock (the nightly
#   SKIPS a night it finds held, so no cutoff STARTS in XCP_REPLAY_QUIET,
#   default 03:30-09:30 UTC: Postgres restarts 04:30-07:00 and the nightly
#   starts 09:17 UTC). A solve holds the whole pack in memory: do not run
#   race_trees beside it.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${XCP_ENV_FILE:-/etc/xc-predictor.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
OUT="${XCP_PRED_DIR:-/srv/pred}/asof"
LOGDIR="$ROOT/logs/replay"
QUIET="${XCP_REPLAY_QUIET:-03:30-09:30}"
. "$ROOT/deploy/joint_flags.sh"
jointFlags

cutoffs() {
  local today s m
  today="$(date -u +%F)"
  for s in ${XCP_REPLAY_SEASONS:-2025 2026 2024 2023}; do
    for m in 08-15 09-01 09-15 10-01 10-15 11-01 11-15 12-01; do
      [[ "$s-$m" < "$today" ]] && echo "$s-$m"
    done
  done
}

minutes() { echo $(( 10#${1%%:*} * 60 + 10#${1##*:} )); }
inQuiet() {
  local now lo hi
  now=$(minutes "$(date -u +%H:%M)"); lo=$(minutes "${QUIET%%-*}"); hi=$(minutes "${QUIET##*-}")
  [ "$now" -ge "$lo" ] && [ "$now" -lt "$hi" ]
}
say() { echo "[replay] $(date -u '+%F %H:%M') UTC  $*" | tee -a "$LOGDIR/summary.log"; }

if [ "${1:-}" = "--list" ]; then
  for d in $(cutoffs); do
    [ -f "$OUT/asof_$d.npz" ] && echo "$d  done" || echo "$d  to do"
  done
  echo "flags: ${JOINT_FLAGS[*]}"
  exit 0
fi
DATES="${*:-$(cutoffs)}"
mkdir -p "$OUT" "$LOGDIR"

for d in $DATES; do
  snap="$OUT/asof_$d.npz"
  if [ -f "$snap" ]; then say "$d already done"; continue; fi
  while inQuiet; do sleep 600; done
  exec 9>"$ROOT/.pipeline.lock"
  until flock -n 9; do sleep 300; while inQuiet; do sleep 600; done; done
  say "$d: solving on the rows before it: tail -f $LOGDIR/asof_$d.log"
  t0=$(date +%s)
  if nice -n 10 "$PY" -u engine/run_joint.py --asof "$d" --snapshot "$snap" \
       "${JOINT_FLAGS[@]}" > "$LOGDIR/asof_$d.log" 2>&1; then
    say "$d: done in $(( ($(date +%s) - t0) / 60 )) min: $(grep -m1 -- '--snapshot' "$LOGDIR/asof_$d.log" | cut -c1-160)"
  else
    say "$d: FAILED after $(( ($(date +%s) - t0) / 60 )) min: $LOGDIR/asof_$d.log"
  fi
  # the solve's own npz and state are large and read by nothing: the snapshot is the output
  rm -f "${snap%.npz}_joint.npz" "${snap%.npz}_joint_state.npz"
  flock -u 9
  exec 9>&-
done
say "all cutoffs tried: $(ls "$OUT"/asof_*.npz 2>/dev/null | wc -l) snapshots in $OUT"
