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
# ! NOT THE PIPELINE LOCK (owner, 2026-11-08: "change the replay thing to not
#   wait for pipeline lock"): a replay writes nothing the pipeline reads, and
#   holding the lock made the nightly skip. What it waits for is MEMORY
#   (owner, 2026-11-07: run beside a race_trees fit, the box ran out): no
#   other engine solve, race_rows, race_trees or race_blend running (busy),
#   XCP_REPLAY_MIN_GB available (default 70), and not in XCP_REPLAY_QUIET
#   (default 03:30-07:00 UTC, the Postgres restart). A pipeline that is only
#   running its other steps does not hold it up.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${XCP_ENV_FILE:-/etc/xc-predictor.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
OUT="${XCP_PRED_DIR:-/srv/pred}/asof"
LOGDIR="$ROOT/logs/replay"
QUIET="${XCP_REPLAY_QUIET:-03:30-07:00}"
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
busy() {
  pgrep -f "engine/run_joint.py|model/race_rows.py|model/race_trees.py|model/race_blend.py" >/dev/null
}
availGb() { awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo; }
ready() { ! inQuiet && ! busy && [ "$(availGb)" -ge "${XCP_REPLAY_MIN_GB:-70}" ]; }
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
  waited=0
  until ready; do
    [ "$waited" -eq 0 ] && say "$d: waiting (quiet hours, another solve or model fit, or under ${XCP_REPLAY_MIN_GB:-70} GB free: $(availGb) GB)"
    waited=1
    sleep 300
  done
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
done
say "all cutoffs tried: $(ls "$OUT"/asof_*.npz 2>/dev/null | wc -l) snapshots in $OUT"
