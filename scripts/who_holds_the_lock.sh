#!/bin/bash
# Project: xc-predictor / scripts
# File:    who_holds_the_lock.sh
# Purpose: which PROCESS holds .pipeline.lock, using nothing that has to be
#          installed.
#
# ⚠ fuser AND lsof ARE NOT ON THIS SERVER (owner, 2026-09-20). The chain's
#   error message named fuser, which is useless advice on the machine it is
#   printed on. /proc is always there.
#
# ! flock IS A KERNEL LOCK. A held lock means a process IS ALIVE and holding
#   it; deleting the file does not release anything and lets two pipelines run
#   at once, which is the exact accident the lock exists to prevent (run9:
#   both solves took 5.5h instead of 40min and every board after was a
#   mixture).
set -u
cd "$(dirname "$0")/.." || exit 1
LOCK="$(pwd)/.pipeline.lock"

if [ ! -e "$LOCK" ]; then
    echo "no lock file at $LOCK — nothing holds it"
    exit 0
fi

ino=$(stat -c %i "$LOCK" 2>/dev/null)
echo "lock file : $LOCK  (inode $ino)"
echo ""

found=0
for d in /proc/[0-9]*; do
    pid=${d#/proc/}
    for fd in "$d"/fd/*; do
        [ -e "$fd" ] || continue
        tgt=$(readlink "$fd" 2>/dev/null) || continue
        case "$tgt" in
            *.pipeline.lock)
                cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null | cut -c1-100)
                started=$(stat -c %y "$d" 2>/dev/null | cut -c1-19)
                echo "  HELD BY pid $pid  (since $started)"
                echo "      $cmd"
                found=1
                ;;
        esac
    done
done

if [ "$found" -eq 0 ]; then
    echo "  nothing has it open — the lock is FREE."
    echo "  (a leftover file is harmless; flock holds nothing once the"
    echo "   process is gone)"
else
    echo ""
    echo "  Stop it cleanly:  kill <pid>      then re-check with this script."
    echo "  Do NOT rm the lock file: that releases nothing and allows a"
    echo "  second pipeline to start on top of the first."
fi
