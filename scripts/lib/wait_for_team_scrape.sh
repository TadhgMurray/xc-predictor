# Project: xc-predictor / scripts/lib
# File:    wait_for_team_scrape.sh
# Purpose: the shared "wait until the anet team-id scrape is finished" helper.
#          Sourced, not executed.
#
# ★ ONE COPY, BECAUSE TWO SCRIPTS WAIT ON THE SAME THING. The compute chain
#   and the scrape chain both start when the team scrape ends, and a pid
#   check duplicated in two files is a pid check that gets fixed in one.
#
# ★ IT WAITS ON THE PROCESS, NOT A ROW COUNT. A queue count that stops moving
#   means "stalled" as often as "finished", and the scraper legitimately sits
#   still for minutes on a VPN rotation. The one unambiguous signal that a
#   scrape is over is that its process is gone. No scraper running when this
#   starts IS the finished state, which is also what makes both scripts safe
#   to re-run after a crash.

# scraper_pids
# Purpose:   The pids actually RUNNING the team scraper, and nothing else.
# ⚠ `pgrep -f anet_teams.py` IS NOT GOOD ENOUGH, and this was caught in
#   testing rather than in production. -f matches the whole command line, so
#   it also matches any SHELL whose command line merely mentions the name --
#   including the very likely
#
#       nohup python scripts/anet_teams.py ... &  nohup bash scripts/overnight_logos.sh &
#
#   where the parent shell's command line contains both. The wait would then
#   never end, because the thing it waited for was the shell that started it.
#   (The same trap bit an interactive `pkill -f` during development and killed
#   the wrong process, so this is not a theoretical hazard.)
#
# ★ SO IT CHECKS argv[0]: a real scrape is a PYTHON process whose arguments
#   include the script. A bash wrapper that mentions the name has bash as
#   argv[0] and is correctly ignored. Read straight from /proc, the only place
#   argv is separable from the rendered command line.
scraper_pids() {
    for d in /proc/[0-9]*; do
        pid=${d#/proc/}
        [ "$pid" = "$$" ] && continue
        [ -r "$d/cmdline" ] || continue
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

# wait_for_team_scrape <say-function>
# Purpose:   Block until no scraper is running. Polls, because the scraper is
#            not this shell's child and `wait` cannot see it.
wait_for_team_scrape() {
    local say_fn="${1:-echo}"
    if [ "${SKIP_WAIT:-0}" = "1" ]; then
        "$say_fn" "SKIP_WAIT=1 — not waiting for the team scrape"
        return 0
    fi
    local pids
    pids=$(scraper_pids)
    if [ -z "$pids" ]; then
        "$say_fn" "no anet_teams.py process running — treating the team scrape as finished"
        return 0
    fi
    "$say_fn" "waiting for the team scrape, pid(s): $(echo $pids | tr '\n' ' ')"
    while [ -n "$(scraper_pids)" ]; do
        sleep 60
    done
    "$say_fn" "the team scrape has exited; continuing"
}
