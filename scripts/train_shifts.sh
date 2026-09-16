#!/usr/bin/env bash
# =====================================================================
# train_shifts.sh -- train the whole corpus on a pod that cannot hold it.
#
#   bash scripts/train_shifts.sh            # run it
#   bash scripts/train_shifts.sh --dry-run  # print the plan and stop
#   bash scripts/train_shifts.sh --from 2   # skip shifts already trained
#
# ★ THE PROBLEM. Extraction wrote 125 GB of chunks; the pod has a 100 GB
#   container disk. --chunk-range lets one model see all of it in shifts:
#   train a window, swap the files underneath, resume from the checkpoint.
#   This is the loop that does the swapping, so it does not have to be done
#   by hand at three in the morning.
#
# ★ THE NEXT WINDOW COPIES WHILE THE CURRENT ONE TRAINS. Two windows are
#   ~75 GB, which fits beside the half-gigabyte of lengths/val_mask and the
#   checkpoint -- so the GPU never waits on the network. Copying between
#   windows instead would idle a rented card for ten minutes a shift.
#
# ⚠ THE LEARNING RATE IS SET PER WINDOW, NEVER LEFT TO THE SCHEDULE.
#   _buildScheduler computes its cosine over EPOCHS * steps_per_epoch, so
#   resuming with a larger --epochs enlarges the denominator while the
#   restored step count stays put: the LR JUMPS BACK UP toward peak, an
#   unintended warm restart. The first full run diverged at 1e-3, so a
#   surprise climb back toward it is the last thing this should do
#   unattended. Each window names its own rate, decaying.
#
# ⚠ AND THE MODEL COMES HOME AFTER EVERY WINDOW. The pod is ephemeral. A
#   run that finishes all three shifts and then loses the container has
#   produced nothing.
# =====================================================================
set -euo pipefail

POD=${POD:-root@149.36.1.123}
PORT=${PORT:-47787}
KEY=${KEY:-$HOME/.ssh/runpod}
SRC=${SRC:-/srv/xc-predictor/model/data}
DST=${DST:-/workspace/data}
REPO=${REPO:-/workspace}
STREAMS=${STREAMS:-4}          # a single SSH stream is CPU-bound at ~17MB/s;
                               # four measured 78MB/s on this pair of boxes
EPOCHS_PER=${EPOCHS_PER:-12}

# lo hi lr -- one line per shift. 9991 is every chunk extraction wrote.
WINDOWS=(
  "0    3000 3e-4"
  "3000 6000 2e-4"
  "6000 9991 1.5e-4"
)

RS="ssh -i $KEY -p $PORT -c aes128-gcm@openssh.com"
POD_SSH=(ssh -i "$KEY" -p "$PORT" "$POD")

DRY=""
FROM=1
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    # ! SO A FINISHED SHIFT IS NOT REDONE. Resuming shift 1 with its own
    #   --epochs is a no-op rather than an error (the checkpoint already
    #   holds that many), but it still copies 37 GB to say so.
    --from) FROM=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n=== %s\n' "$*"; }

# The chunk filenames of window [lo, hi), newest listing each time so a
# partially-copied corpus cannot silently shift the numbering.
windowList() {
  (cd "$SRC" && ls chunk_*.pt | sort | sed -n "$(($1 + 1)),$2p")
}

copyWindow() {                 # lo hi -- parallel rsync, blocks until done
  local lo=$1 hi=$2 tmp
  tmp=$(mktemp -d)
  windowList "$lo" "$hi" > "$tmp/all"
  split -n "r/$STREAMS" "$tmp/all" "$tmp/part_"
  for p in "$tmp"/part_*; do
    rsync -a --partial --files-from="$p" -e "$RS" "$SRC/" "$POD:$DST/" &
  done
  wait
  rm -rf "$tmp"
}

dropWindow() {                 # lo hi -- free the disk for the next one
  windowList "$1" "$2" | "${POD_SSH[@]}" "cd $DST && xargs -r rm -f"
}

# ! LAUNCHED DETACHED AND POLLED, not run down the SSH pipe. A dropped
#   connection between these two machines must not kill a two-hour run.
trainWindow() {                # lo hi lr epochs tag
  local lo=$1 hi=$2 lr=$3 ep=$4 tag=$5
  "${POD_SSH[@]}" "cd $REPO && rm -f done.$tag && nohup sh -c '
      python model/train.py --data $DST --chunk-range $lo:$hi \
        --batch 1024 --lr $lr --epochs $ep --patience 4 --workers 12 --amp \
        --checkpoint $DST/ck.pt; echo \$? > done.$tag
    ' > train.$tag.log 2>&1 &"
  echo "  training $lo:$hi at lr $lr, epochs<=$ep -- $REPO/train.$tag.log"
  while ! "${POD_SSH[@]}" "test -f $REPO/done.$tag" 2>/dev/null; do
    sleep 60
  done
  local rc
  rc=$("${POD_SSH[@]}" "cat $REPO/done.$tag")
  "${POD_SSH[@]}" "tail -4 $REPO/train.$tag.log"
  [ "$rc" = "0" ] || { echo "  training exited $rc -- stopping"; exit 1; }
}

bringModelHome() {             # tag
  rsync -a -e "$RS" "$POD:$DST/model.pt" "$SRC/model.pt"
  rsync -a -e "$RS" "$POD:$DST/target_stats.pkl" "$SRC/target_stats.pkl"
  cp "$SRC/model.pt" "$SRC/model.$1.pt"      # keep each shift's winner
  echo "  saved $SRC/model.pt and $SRC/model.$1.pt"
}

# --------------------------------------------------------------- plan
say "plan"
n=0
for w in "${WINDOWS[@]}"; do
  read -r lo hi lr <<< "$w"
  n=$((n + 1))
  skip=""
  [ "$n" -lt "$FROM" ] && skip="   (skipped: --from $FROM)"
  echo "  shift $n: chunks $lo:$hi  lr $lr  epochs<=$((n * EPOCHS_PER))  " \
       "($(( (hi - lo) * 125 / 9991 )) GB)$skip"
done
[ -n "$DRY" ] && exit 0

# ★ THE POD GETS THE CURRENT CODE FIRST. Every shift runs whatever is in
#   /workspace/model, and a fix committed here that never reached the pod is
#   a fix that did not happen -- the NaN guard and the horizon bands both
#   arrived this way.
say "syncing model code to the pod"
rsync -a -e "$RS" "$(dirname "$SRC")"/*.py "$POD:$REPO/model/"

# ------------------------------------------------------------- the loop
prefetch=""
n=0
for w in "${WINDOWS[@]}"; do
  read -r lo hi lr <<< "$w"
  n=$((n + 1))
  tag="w$lo"
  if [ "$n" -lt "$FROM" ]; then
    echo "  shift $n ($lo:$hi) already done -- skipping"
    continue
  fi

  # whatever the previous iteration started copying must have landed
  if [ -n "$prefetch" ]; then
    say "waiting for the prefetch of $lo:$hi"
    wait "$prefetch" || true
  else
    say "copying $lo:$hi"
    copyWindow "$lo" "$hi"
  fi

  # ! THE NEXT WINDOW STARTS COPYING NOW, while this one trains.
  nxt=$((n))
  if [ "$nxt" -lt "${#WINDOWS[@]}" ]; then
    read -r nlo nhi _ <<< "${WINDOWS[$nxt]}"
    say "prefetching $nlo:$nhi in the background"
    copyWindow "$nlo" "$nhi" &
    prefetch=$!
  else
    prefetch=""
  fi

  say "shift $n: chunks $lo:$hi"
  trainWindow "$lo" "$hi" "$lr" "$((n * EPOCHS_PER))" "$tag"
  bringModelHome "$tag"

  say "dropping $lo:$hi from the pod"
  dropWindow "$lo" "$hi"
done

say "done -- the model saw all ${#WINDOWS[@]} windows"
