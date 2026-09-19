#!/bin/bash
# Project: xc-predictor / scripts
# File:    after_team_scrape.sh
# Purpose: wait for the anet team-id scrape to finish, then run everything
#          that depends on it, unattended, in dependency order.
#
# ★ IT WAITS ON THE PROCESS, NOT ON A ROW COUNT. A queue count that stops
#   moving means "stalled" just as often as it means "finished", and the
#   scraper legitimately sits still for minutes on a VPN rotation. The one
#   unambiguous signal that a scrape is over is that its process is gone.
#   So: find the scraper by its command line, wait for that pid, then go.
#   If no scraper is running when this starts, that IS the finished state and
#   it proceeds immediately -- which is also what makes the script safe to
#   re-run after a crash.
#
# ⚠ THE LOGO SCRAPE IS IN HERE ON PURPOSE (owner, 2026-09-19: "This will also
#   kick off the logo scrape separately, only once the team id thing is
#   finished"). It reads anet_team.mascot_url, so a logo run that starts
#   early covers only the teams scraped so far and its coverage number is a
#   lie about the rest. It goes after identity is built, and it runs in
#   PARALLEL with the pool/link work below because it shares no table with
#   them -- it writes school_logo, they write team_identity, team_pool and
#   school_team_link.
#
# ! NOTHING HERE TOUCHES THE RATINGS. The last step is the 143-meet retry;
#   the refit and the solve are deliberately not chained on, because a solve
#   whose inputs changed under it cannot be attributed. Run
#   scripts/overnight_distance_curve.sh separately for the curve.
#
#   Usage:
#       nohup bash scripts/after_team_scrape.sh > /dev/null 2>&1 &
#       tail -f engine/data/after_scrape/00-progress.log
#
#   Options (environment):
#       PY=...          python to use (default /srv/venv/bin/python if it
#                       exists, else python3)
#       SKIP_WAIT=1     do not wait, start now
#       SKIP_LOGOS=1    leave the crests alone
set -u

cd "$(dirname "$0")/.." || exit 1

# the server keeps the password in /etc/xc-predictor.env; harmless if absent
if [ -f /etc/xc-predictor.env ]; then
    set -a; . /etc/xc-predictor.env; set +a
fi

PY="${PY:-$( [ -x /srv/venv/bin/python ] && echo /srv/venv/bin/python \
            || echo python3 )}"
LOGDIR="engine/data/after_scrape"
mkdir -p "$LOGDIR"
PROG="$LOGDIR/00-progress.log"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$PROG"; }

# step <name> <command...> -- run it, log it, never abort the chain on a
# failure. A step that fails is recorded and the next one still runs: the
# alternative is waking up to one broken step and nothing else attempted.
step() {
    local name="$1"; shift
    say "START $name"
    if "$@" > "$LOGDIR/$name.log" 2>&1; then
        say "  ok   $name"
        return 0
    fi
    say "  FAIL $name  (see $LOGDIR/$name.log)"
    return 1
}

# scraper_pids
# Purpose:   The pids actually RUNNING the team scraper, and nothing else.
# ⚠ `pgrep -f anet_teams.py` IS NOT GOOD ENOUGH, and this was caught in
#   testing rather than in production. -f matches the whole command line, so
#   it also matches any SHELL whose command line merely mentions the name --
#   including the very likely
#
#       nohup python scripts/anet_teams.py ... & nohup bash scripts/after_team_scrape.sh &
#
#   where the parent shell's command line contains both. The wait would then
#   never end, because the thing it was waiting for was the shell that
#   started it.
#
# ★ SO IT CHECKS argv[0]: a real scrape is a PYTHON process whose arguments
#   include the script. A bash wrapper that mentions the name has bash as
#   argv[0] and is correctly ignored. Read straight from /proc, which is the
#   only place argv is separable from the rendered command line.
scraper_pids() {
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [ "$pid" = "$$" ] && continue
        [ -r "$d/cmdline" ] || continue
        # cmdline is NUL-separated; argv[0] is the first field
        argv0=$(tr '\0' '\n' < "$d/cmdline" 2>/dev/null | head -1)
        case "${argv0##*/}" in
            python|python2|python3|python3.*|pypy*) ;;
            *) continue ;;
        esac
        if tr '\0' '\n' < "$d/cmdline" 2>/dev/null \
             | grep -q "anet_teams\.py"; then
            echo "$pid"
        fi
    done
}

: > "$PROG"
say "python: $PY"

# ---------------------------------------------------------------- wait
if [ "${SKIP_WAIT:-0}" != "1" ]; then
    pids=$(scraper_pids)
    if [ -z "$pids" ]; then
        say "no anet_teams.py process running — treating the scrape as "\
"finished and starting now"
    else
        say "waiting for anet_teams.py (pid(s): $(echo $pids | tr '\n' ' '))"
        # ★ POLL rather than `wait`: the scraper is not this shell's child,
        #   so `wait` cannot see it. 60s is well under any step's runtime.
        while [ -n "$(scraper_pids)" ]; do
            sleep 60
        done
        say "anet_teams.py has exited; continuing"
    fi
else
    say "SKIP_WAIT=1 — not waiting"
fi

# ------------------------------------------------- the dependent chain
# Order is a real dependency order, not a preference:
#   team_identity  -- resolves (team_id -> school, state) from anet_team
#   team_pool      -- classifies each team id, and reads team_identity
#   link_tfrrs     -- decides which anet team answers for each tfrrs school
step team_identity "$PY" racecast/build_team_identity.py
step team_pool     "$PY" engine/build_team_pool.py
step link_tfrrs    "$PY" scripts/link_tfrrs_to_anet.py

# ---------------------------------------------------- logos, in parallel
# Shares no table with the retry below, so there is no reason to serialise.
LOGO_PID=""
if [ "${SKIP_LOGOS:-0}" != "1" ]; then
    say "START logos (in parallel — writes school_logo, nothing else does)"
    ( "$PY" scripts/scrape_school_logos.py > "$LOGDIR/logos.log" 2>&1 \
        && echo ok > "$LOGDIR/logos.status" \
        || echo fail > "$LOGDIR/logos.status" ) &
    LOGO_PID=$!
else
    say "SKIP_LOGOS=1 — crests untouched"
fi

# --------------------------------------------------- the failed meets
# 143 real meets: 121 anet stranded in state 3, 22 tfrrs. --retry-failed
# claims states (2,3) directly and skips the startup reset, so it cannot
# take the whole queue with it.
step retry_meets "$PY" scripts/launcher.py --retry-failed

# ------------------------------------------------------------- join up
if [ -n "$LOGO_PID" ]; then
    say "waiting for the logo scrape to finish"
    wait "$LOGO_PID" || true
    say "  logos: $(cat "$LOGDIR/logos.status" 2>/dev/null || echo unknown)"
fi

say "done. what to read:"
say "  $LOGDIR/team_identity.log   — states_seen, and Georgetown should be DC"
say "  $LOGDIR/team_pool.log       — how many teams fell to 'pro' under the"
say "                                15-athletes-all-time rule"
say "  $LOGDIR/link_tfrrs.log      — accepted links and the margin that"
say "                                decided each"
say "  $LOGDIR/retry_meets.log     — of the 143, how many are still failed"
say "  $LOGDIR/logos.log           — crest coverage, now that every team id"
say "                                exists"
say ""
say "NOT run, on purpose: the distance refit and the solve. Chaining a solve"
say "onto inputs that moved under it makes the result unattributable."
say "  curve:  nohup bash scripts/overnight_distance_curve.sh &"
