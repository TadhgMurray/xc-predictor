# Project: xc-predictor / scripts/lib
# File:    step.sh
# Purpose: the shared step machinery for the overnight chains -- what a step
#          IS, whether it is still alive, and a cap so the chain always ends.
#          Sourced, not executed.
#
# ★ ONE COPY, BECAUSE TWO CHAINS RUN STEPS. The same reason
#   wait_for_team_scrape.sh exists: progress reporting duplicated in two files
#   is progress reporting that gets improved in one.
#
# ⚠⚠ WHAT THIS REPLACES, AND WHY (owner, 2026-09-20). The old progress log was
#    two lines per step and neither said anything:
#
#        [02:06:51] START solve
#        <nothing, for ten and a half hours>
#
#    A chain that prints a label and then goes silent for the length of a
#    working day is indistinguishable from a chain that has hung, and the
#    owner could not tell which he was looking at. Three things fix that and
#    all three are here:
#
#      1. EVERY STEP SAYS WHAT IT IS DOING. `step` takes a description and
#         prints it. A reader who does not know that `curve` writes
#         distance_spline.pkl, or that `solve` is really eight pipeline
#         stages, now does.
#      2. A STEP REPORTS WHILE IT RUNS. Every HEARTBEAT seconds a step prints
#         how long it has been going and THE LAST LINE OF ITS OWN LOG -- so
#         the solve's pipeline stages surface in the progress log as they
#         happen, instead of being buried in solve.log where nobody tails
#         them. That is the difference between "still alive, now on 08_golive"
#         and silence.
#      3. A STEP CANNOT RUN FOREVER. Every step gets a wall-clock cap, so the
#         chain reaches its summary even when something wedges. See below.
#
# ★ THE CAP IS GENEROUS ON PURPOSE, AND IT IS NOT A DEADLINE. It exists so an
#   unattended chain always TERMINATES and always says why -- not to hurry a
#   step along. A killed step is reported as TIMEOUT, loudly, and is never
#   confused with a clean failure: those are different bugs and the log says
#   which one happened.
#
#   Defaults, overridable from the environment:
#       STEP_TIMEOUT=7200     the default cap, 2h
#       TIMEOUT_<step>=N      per step, e.g. TIMEOUT_solve=43200
#       HEARTBEAT=300         seconds between "still running" lines
#
# ! SIGTERM FIRST, THEN SIGKILL. A python step that traps TERM gets to close
#   its database connection and roll back; --kill-after is the backstop for one
#   that does not. Killing a solve mid-write is worse than letting it run, so
#   the caps are set well above any observed runtime.

: "${HEARTBEAT:=300}"
: "${STEP_TIMEOUT:=7200}"

# The per-step outcome table, printed by chain_summary at the end.
_STEP_ROWS=""

# hms <seconds> -> "4h12m", "9m03s", "41s"
hms() {
    local s="${1:-0}"
    if   [ "$s" -ge 3600 ]; then printf '%dh%02dm' $((s / 3600)) $((s % 3600 / 60))
    elif [ "$s" -ge 60   ]; then printf '%dm%02ds' $((s / 60)) $((s % 60))
    else                         printf '%ds' "$s"
    fi
}

# _step_cap <name> -- TIMEOUT_<name> if set, else STEP_TIMEOUT.
_step_cap() {
    local v
    eval "v=\${TIMEOUT_$1:-}"
    [ -n "$v" ] && { echo "$v"; return; }
    echo "$STEP_TIMEOUT"
}

# _heartbeat <pid> <name> <log> <start-epoch> <cap>
# ★ IT PRINTS THE STEP'S OWN LAST LINE, which is the whole value: the chain
#   does not have to know what a step's progress looks like, it just relays
#   it. run_pipeline.sh already announces each stage; this is what carries
#   those announcements up into 00-progress.log.
# ! SLEEPS IN SHORT TICKS so it notices the step finishing promptly instead of
#   sitting out the rest of a five-minute interval.
_heartbeat() {
    local pid="$1" name="$2" log="$3" t0="$4" cap="$5" stream="${6:-1}"
    local waited=0 tick
    while kill -0 "$pid" 2>/dev/null; do
        sleep 5
        waited=$((waited + 5))
        [ "$waited" -lt "$HEARTBEAT" ] && continue
        waited=0
        kill -0 "$pid" 2>/dev/null || break
        # ! WHEN THE STEP IS STREAMING, ITS LAST LINE IS ALREADY ON SCREEN --
        #   repeating it would double every log. The elapsed/cap line still
        #   earns its place: it is what says a SILENT step is alive.
        if [ "$stream" = "0" ]; then
            tick=$(grep -av '^[[:space:]]*$' "$log" 2>/dev/null | tail -1 | tr -d '\r' | cut -c1-110)
        else
            tick=""
        fi
        say "  ...  $name  $(hms $(( $(date +%s) - t0 )))/$(hms "$cap")${tick:+  | $tick}"
    done
}

# step <name> <description> <command...>
# Returns 0 on success, 1 on failure, 1 on timeout. The CALLER decides whether
# that stops the chain -- see step_fatal, and the 2026-09-19 note in
# overnight_fit_pool_solve.sh about a message that contradicted its own log.
step() {
    local name="$1" desc="$2"; shift 2
    local log="$LOGDIR/$name.log"
    local cap; cap=$(_step_cap "$name")
    local t0; t0=$(date +%s)

    say "START $name — $desc"
    say "      log: $log   cap: $(hms "$cap")"

    # ! `timeout` IS COREUTILS AND IS ALWAYS HERE, but a chain that dies
    #   because a helper is missing is a chain that did not run, so it falls
    #   back to running uncapped and SAYS so rather than failing.
    # ★★ THE STEP'S OWN OUTPUT IS STREAMED, NOT HIDDEN (owner, 2026-09-20:
    #    "I can't see what step it actually is on"). It used to go only to
    #    $log, so `tail -f 00-progress.log` showed START and then silence for
    #    hours while run_pipeline.sh printed every stage into a file nobody was
    #    watching. Now each line lands in BOTH: raw in the step's log, prefixed
    #    in the progress log, so one tail follows the whole chain.
    #
    # ! pipefail IN A SUBSHELL, so the pipe reports the COMMAND's status and
    #   not tee's, and the option does not leak into the rest of the chain.
    #
    # ! stdbuf -oL ON sed, or the prefixer buffers 4KB at a time and the
    #   streaming it exists to provide arrives in lumps, hours late.
    #
    #   STREAM_STEPS=0 restores the quiet behaviour.
    local stream="${STREAM_STEPS:-1}"
    # ! NO '|' IN THE PREFIX -- it is sed's delimiter below, and "  |name|"
    #   made every prefixed line die with "unknown option to `s'". Brackets
    #   read the same and cannot collide.
    local pfx="  [$name]"
    if [ "$stream" = "0" ]; then
        if command -v timeout >/dev/null 2>&1; then
            timeout --signal=TERM --kill-after=120 "$cap" "$@" > "$log" 2>&1 &
        else
            say "      ⚠ no timeout(1) — this step runs UNCAPPED"
            "$@" > "$log" 2>&1 &
        fi
    else
        (
            set -o pipefail
            if command -v timeout >/dev/null 2>&1; then
                timeout --signal=TERM --kill-after=120 "$cap" "$@" 2>&1
            else
                "$@" 2>&1
            fi | tee "$log" \
               | (stdbuf -oL sed "s|^|$pfx |" 2>/dev/null || sed "s|^|$pfx |") \
               | tee -a "$PROG"
        ) &
    fi
    local pid=$!
    _heartbeat "$pid" "$name" "$log" "$t0" "$cap" "$stream" &
    local hb=$!
    wait "$pid"; local rc=$?
    kill "$hb" 2>/dev/null
    wait "$hb" 2>/dev/null

    local el=$(( $(date +%s) - t0 ))
    if [ "$rc" -eq 0 ]; then
        say "  ok   $name  ($(hms $el))"
        _STEP_ROWS="$_STEP_ROWS$name|ok|$(hms $el)
"
        return 0
    fi
    # ★ 124 IS timeout(1)'s OWN CODE FOR "I KILLED IT", and it must never read
    #   as an ordinary failure: one means the step is broken, the other means
    #   the cap was wrong or the step wedged. Different bugs, different fixes.
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        say "  TIMEOUT $name after $(hms $el) — killed at its cap of $(hms "$cap")"
        say "          raise it with TIMEOUT_$name=<seconds> once you know why"
        say "          (tail -50 $log)"
        _STEP_ROWS="$_STEP_ROWS$name|TIMEOUT|$(hms $el)
"
        return 1
    fi
    say "  FAIL $name  (rc=$rc after $(hms $el); see $log)"
    _STEP_ROWS="$_STEP_ROWS$name|FAIL rc=$rc|$(hms $el)
"
    return 1
}

# step_fatal <name> <description> <command...>
# ⚠ `step` USED TO CLAIM "CHAIN STOPPED" ITSELF AND IT WAS A LIE (2026-09-19).
#   Whether a failure stops anything is the CALLER's decision, so the wording
#   and the exit live together here and nowhere else.
step_fatal() {
    if step "$@"; then
        return 0
    fi
    say "  CHAIN STOPPED: $1 feeds everything after it"
    chain_summary
    exit 1
}

# step_skipped <name> <why> -- a step that did not run, on the table anyway.
# ! AN ABSENT ROW IS NOT AN ANSWER. A reader comparing two runs needs to see
#   that a step was skipped rather than have to notice that it is missing.
step_skipped() {
    _STEP_ROWS="$_STEP_ROWS$1|skipped|$2
"
}

# chain_summary -- every step, its outcome and its duration, in one block.
# ★ THE LAST THING A CHAIN PRINTS, ON EVERY EXIT PATH INCLUDING A STOP. "Did
#   it finish, and what did it cost" should be answerable from the tail of the
#   log without reading the whole thing.
chain_summary() {
    say ""
    say "── steps ───────────────────────────────────────────────"
    printf '%s' "$_STEP_ROWS" | while IFS='|' read -r n s d; do
        [ -n "$n" ] && say "$(printf '  %-18s %-14s %s' "$n" "$s" "$d")"
    done
    say "── total: $(hms $(( $(date +%s) - CHAIN_T0 ))) ─────────────────────────────"
}
