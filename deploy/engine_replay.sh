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
# ! THE CUTOFFS: the 1st and 15th, mid-August to December 1 (XCP_REPLAY_DAYS,
#   month-day), of every season in XCP_REPLAY_SEASONS (default 2025 2026 2024 2023: the validation
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
# ★ SEVERAL AT ONCE (owner, 2026-11-09: 65 min a cutoff, 31 h for all). Start
#   the script in two or three tmux sessions: each CLAIMS a cutoff before
#   solving it ($OUT/.claim_<date>, holding its pid; a dead holder's claim is
#   taken over), so no two solve the same one. Other REPLAYS do not count as
#   busy; the memory floor still guards every start, so a second solve only
#   starts when XCP_REPLAY_MIN_GB is free with the first one running.
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
    for m in ${XCP_REPLAY_DAYS:-08-15 09-01 09-15 10-01 10-15 11-01 11-15 12-01}; do
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
  # another solve that is not a replay (the go-live, a holdout), or a model fit
  pgrep -af "engine/run_joint.py" | grep -v -- "--asof" | grep -q . && return 0
  pgrep -f "model/race_rows.py|model/race_trees.py|model/race_blend.py" >/dev/null
}
# one cutoff, one session: mkdir is atomic; the claim holds the solver's pid
claim() {
  local c="$OUT/.claim_$1"
  # a solve of it already running (a session from before claims existed)
  pgrep -f -- "--asof $1 " >/dev/null && return 1
  if mkdir "$c" 2>/dev/null; then echo $$ > "$c/pid"; return 0; fi
  local pid; pid="$(cat "$c/pid" 2>/dev/null)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then return 1; fi
  echo $$ > "$c/pid"                      # its holder is gone: take it over
  return 0
}
unclaim() { rm -rf "$OUT/.claim_$1"; }
# ★ THE PACK MUST BE THERE AND SETTLED (owner, 2026-11-09: "no pack at
#   engine/data/packed_XC_TF.npz"). A pipeline run deletes it (06_clear_cache)
#   and writes it again (07_pack); a solve started in between failed at once,
#   one cutoff after another. Now that is a wait: the file exists and has
#   not changed for 10 minutes.
PACK="$ROOT/engine/data/packed_XC_TF.npz"
packReady() {
  [ -f "$PACK" ] && [ $(( $(date +%s) - $(stat -c %Y "$PACK") )) -ge 600 ]
}
availGb() { awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo; }
ready() { ! inQuiet && packReady && ! busy && [ "$(availGb)" -ge "${XCP_REPLAY_MIN_GB:-70}" ]; }
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

# ★ FAILED CUTOFFS ARE TRIED AGAIN (owner, 2026-11-09: a run of failures
#   "after 0 min" from 09:43 UTC, beside the nightly). A solve that dies in
#   its first minutes failed at start-up, not in the solve: the session
#   backs off XCP_REPLAY_BACKOFF_MIN (15) before the next, and the whole list
#   is gone through again, up to XCP_REPLAY_PASSES (3) times, for the
#   cutoffs still without a snapshot.
for pass in $(seq 1 "${XCP_REPLAY_PASSES:-3}"); do
todo=0
for d in $DATES; do
  snap="$OUT/asof_$d.npz"
  if [ -f "$snap" ]; then continue; fi
  waited=0
  until ready; do
    [ "$waited" -eq 0 ] && say "$d: waiting (quiet hours, the pack being rebuilt, another solve or model fit, or under ${XCP_REPLAY_MIN_GB:-70} GB free: $(availGb) GB)"
    waited=1
    sleep 300
  done
  # memory is free: claim it, unless another session has (or has finished it)
  if [ -f "$snap" ] || ! claim "$d"; then continue; fi
  say "$d: solving on the rows before it (session $$): tail -f $LOGDIR/asof_$d.log"
  t0=$(date +%s)
  if nice -n 10 "$PY" -u engine/run_joint.py --asof "$d" --snapshot "$snap" \
       "${JOINT_FLAGS[@]}" > "$LOGDIR/asof_$d.log" 2>&1; then
    say "$d: done in $(( ($(date +%s) - t0) / 60 )) min: $(grep -m1 -- '--snapshot' "$LOGDIR/asof_$d.log" | cut -c1-160)"
  else
    say "$d: FAILED after $(( ($(date +%s) - t0) / 60 )) min (pass $pass): $(tail -1 "$LOGDIR/asof_$d.log" | cut -c1-200)"
    cp -f "$LOGDIR/asof_$d.log" "$LOGDIR/asof_$d.failed$pass.log" 2>/dev/null
    todo=1
    if [ $(( $(date +%s) - t0 )) -lt 300 ]; then
      unclaim "$d"
      sleep $(( ${XCP_REPLAY_BACKOFF_MIN:-15} * 60 ))
      continue
    fi
  fi
  # the solve's own npz and state are large and read by nothing: the snapshot is the output
  rm -f "${snap%.npz}_joint.npz" "${snap%.npz}_joint_state.npz"
  unclaim "$d"
  sleep 120                               # let the next session's memory check see this one's
done
[ "$todo" -eq 0 ] && break
say "pass $pass over: trying the failed cutoffs again"
done
say "all cutoffs tried: $(ls "$OUT"/asof_*.npz 2>/dev/null | wc -l) snapshots in $OUT"
