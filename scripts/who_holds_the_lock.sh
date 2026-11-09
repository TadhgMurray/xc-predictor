#!/bin/bash
# Project: xc-predictor / scripts
# File:    who_holds_the_lock.sh
# Purpose: who HOLDS .pipeline.lock, who is WAITING for it, and what each is
#          doing -- using nothing that has to be installed.
#
#   bash scripts/who_holds_the_lock.sh
#
# ⚠ fuser AND lsof ARE NOT ON THIS SERVER (owner, 2026-09-20). /proc is.
# ★ HOLDER AND WAITERS APART (2026-11-09: the owner kept finding the lock
#   "still held" with nothing visibly running). The old version listed every
#   process with the FILE open, so a backup or race-model timer WAITING for
#   the lock looked exactly like a holder. /proc/locks says which is which:
#   the holder's line, then one "->" line per waiter. The job that took it
#   wrote .pipeline.lock.holder (deploy/lockctl.sh): its name, pid, since
#   when, and what it is doing.
# ★ AND THE systemd UNIT. The backup (xcp-backup), the race model
#   (xcp-race-model) and the nightly (xcp-nightly) run under timers, not in
#   tmux -- so "nothing is running" on screen while a unit holds the lock.
#
# ! flock IS A KERNEL LOCK. Deleting the file releases nothing and lets two
#   pipelines run at once (run9: both solves 5.5 h instead of 40 min).
set -u
cd "$(dirname "$0")/.." || exit 1
LOCK="$(pwd)/.pipeline.lock"

if [ ! -e "$LOCK" ]; then
    echo "no lock file at $LOCK -- nothing holds it"
    exit 0
fi

ino=$(stat -c %i "$LOCK" 2>/dev/null)
dev=$(stat -c %D "$LOCK" 2>/dev/null)   # hex major:minor packed
echo "lock file : $LOCK  (inode $ino)"

_unit() {
    # the systemd unit a pid runs in, from its cgroup
    sed -n 's|.*/\([^/]*\.\(service\|scope\)\)$|\1|p' "/proc/$1/cgroup" 2>/dev/null | head -1
}
_cmd() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | cut -c1-110; }
_since() { stat -c %y "/proc/$1" 2>/dev/null | cut -c1-19; }

# /proc/locks: "N: FLOCK  ADVISORY  WRITE PID MAJ:MIN:INODE 0 EOF" for the
# holder, "N: -> FLOCK  ADVISORY  WRITE PID MAJ:MIN:INODE 0 EOF" per waiter
holders=$(awk -v ino="$ino" '$2=="FLOCK" { split($6,a,":"); if (a[3]==ino) print $5 }' /proc/locks)
waiters=$(awk -v ino="$ino" '$2=="->" && $3=="FLOCK" { split($7,a,":"); if (a[3]==ino) print $6 }' /proc/locks)

# ! THE PID IN /proc/locks IS flock(1)'s, which took the lock on the job's
#   descriptor and exited -- so it is only "held or free". The job is the
#   pid in .pipeline.lock.holder; under it, everything with the file open
#   that is not waiting.
wparents=""
for w in $waiters; do wparents="$wparents $(awk '{print $4}' "/proc/$w/stat" 2>/dev/null)"; done
hpid=$(sed -n 's/^pid=//p' "$LOCK.holder" 2>/dev/null | head -1)
if [ -z "$holders" ]; then
    echo ""
    echo "  FREE -- nothing holds the lock."
else
    echo ""
    if [ -n "$hpid" ] && [ -d "/proc/$hpid" ]; then
        echo "  HELD BY $(sed -n 's/^job=//p' "$LOCK.holder" | head -1): pid $hpid" \
             "(since $(sed -n 's/^since=//p' "$LOCK.holder" | head -1))  unit: $(_unit "$hpid")"
        echo "      what: $(sed -n 's/^what=//p' "$LOCK.holder" | head -1)"
        echo "      $(_cmd "$hpid")"
    else
        echo "  HELD -- and the job that took it (${hpid:-unknown pid}) is GONE: what is"
        echo "  left of it still has the lock open:"
    fi
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [ "$p" = "$hpid" ] && continue
        case " $waiters $wparents " in *" $p "*) continue ;; esac
        for fd in "$d"/fd/*; do
            tgt=$(readlink "$fd" 2>/dev/null) || continue
            if [ "$tgt" = "$LOCK" ]; then
                echo "      under it: pid $p  unit: $(_unit "$p")  $(_cmd "$p")"
                break
            fi
        done
    done
fi

if [ -n "$waiters" ]; then
    echo ""
    echo "  WAITING for it (in flock's queue):"
    for pid in $waiters; do
        pp=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
        echo "      pid ${pp:-$pid}  unit: $(_unit "${pp:-$pid}")  $(_cmd "${pp:-$pid}")"
    done
fi
# queued jobs poll instead of sitting in flock's queue (deploy/lockctl.sh)
pol=$(pgrep -af 'backup_db.sh|race_model_weekly.sh|nightly_update.sh|run_pipeline.sh' 2>/dev/null \
      | grep -v -e "^$$ " -e who_holds | grep -v -e "^${hpid:-x} " || true)
if [ -n "$pol" ]; then
    echo ""
    echo "  also running (holding, waiting, or done with the lock):"
    echo "$pol" | cut -c1-120 | sed 's/^/      /'
fi
if [ -s "$LOCK.want" ] && ! ( exec 7>>"$LOCK.want"; flock -n 7 ) 2>/dev/null; then
    echo ""
    echo "  A RUN IS WAITING FOR IT: $(cat "$LOCK.want") -- no queued job starts first."
fi
if [ -n "$holders" ]; then
    echo ""
    echo "  Stop it cleanly: kill <pid> (a timer's job: systemctl stop <unit>)."
    echo "  Do NOT rm the lock file: that releases nothing and lets a second"
    echo "  pipeline start on top of the first."
fi
echo ""
echo "  memory-heavy jobs (scripts/jobrun.py --status):"
_py="${XCP_PYTHON:-/srv/venv/bin/python}"; [ -x "$_py" ] || _py=python3
"$_py" scripts/jobrun.py --status 2>&1 | sed 's/^/      /'
# ★ ENGINE REPLAYS STOPPED FOR THE PIPELINE OR FOR MEMORY (review, 2026-11-09)
if [ -f logs/replay/summary.log ]; then
    n=$(grep -c ': STOPPED' logs/replay/summary.log)
    if [ "$n" -gt 0 ]; then
        echo ""
        echo "  engine replays stopped so far: $n (logs/replay/summary.log); the last:"
        grep ': STOPPED' logs/replay/summary.log | tail -3 | cut -c1-160 | sed 's/^/      /'
    fi
fi
