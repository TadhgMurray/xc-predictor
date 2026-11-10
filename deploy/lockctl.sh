# deploy/lockctl.sh -- sourced, not run. The ONE way a job takes
# .pipeline.lock (run_pipeline.sh, nightly_update.sh, backup_db.sh,
# race_model_weekly.sh).
#
#   . deploy/lockctl.sh
#   lock_take <job> <mode> <max-wait-s> "<what it does, how long, its log>"
#   ...
#   lock_release
#
# ★ WHY (owner, 2026-11-09: the pipeline "still locked" with nothing visibly
#   running). Three things made the lock opaque:
#   1. The backup and the race-model build are systemd timers. Fired during a
#      run they WAITED on the lock (6 h, 3 h), so the moment a run finished
#      one of them took it -- for the dump or the 1.5 h table build -- and a
#      run started by hand right after said "another pipeline holds
#      .pipeline.lock" with no pipeline anywhere: the holder was a timer's
#      shell, not in tmux, not on any screen.
#   2. who_holds_the_lock.sh listed every process with the FILE open --
#      a job waiting for the lock looked exactly like the holder.
#   3. The pipeline's own last line (`nohup edge_purge.py --delay 960 &`)
#      inherited the lock's descriptor and held it 16 minutes past the end.
#
# THE RULES (docs/PIPELINE-ORDER.md):
#   * A holder writes .pipeline.lock.holder: job, pid, since, what, log.
#     scripts/who_holds_the_lock.sh reads /proc/locks, which names the
#     holder and each waiter apart, and prints that file beside it.
#   * MODES
#       exclusive  run_pipeline / nightly: never two. If a pipeline or the
#                  nightly holds it: fail at once (nightly: skip the night).
#                  If a QUEUED job (backup, race model) holds it: post
#                  .pipeline.lock.want while waiting up to <max-wait-s> for
#                  it, so no other queued job slips in first.
#       queued     backup, race model, qualifier routes: take the lock only
#                  when it is free AND nobody has posted a want. Polls every
#                  30 s instead of sitting in flock's queue, so an
#                  interactive run always goes first.
#   * Children that must not hold the lock past the run (the delayed edge
#     purge, scrapers' Xvfb) are started with 9>&-.
LOCK_FILE="${XCP_LOCK_FILE:-${ROOT:-.}/.pipeline.lock}"
LOCK_HOLDER="$LOCK_FILE.holder"
LOCK_WANT="$LOCK_FILE.want"

_lock_write_holder() {
  {
    echo "job=$1"
    echo "pid=$$"
    echo "since=$(date '+%F %H:%M:%S %Z')"
    echo "what=$2"
  } > "$LOCK_HOLDER.tmp$$" 2>/dev/null && mv -f "$LOCK_HOLDER.tmp$$" "$LOCK_HOLDER" 2>/dev/null
}

# lock_holder_job -- the job named in the holder file, when its pid is alive
lock_holder_job() {
  local job pid
  [ -f "$LOCK_HOLDER" ] || return 0
  job=$(sed -n 's/^job=//p' "$LOCK_HOLDER" | head -1)
  pid=$(sed -n 's/^pid=//p' "$LOCK_HOLDER" | head -1)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "$job"; fi
}

_lock_who() {
  if [ -f "$LOCK_HOLDER" ]; then sed 's/^/      /' "$LOCK_HOLDER" >&2; fi
  echo "      (bash scripts/who_holds_the_lock.sh for the whole picture)" >&2
}

lock_take() {
  local job="$1" mode="$2" maxw="${3:-0}" what="${4:-}" t0 holder
  exec 9>"$LOCK_FILE"
  if flock -n 9; then _lock_write_holder "$job" "$what"; return 0; fi
  holder=$(lock_holder_job)
  case "$mode" in
    exclusive)
      case "$holder" in
        backup|race_model|qualifier_routes)
          echo "  .pipeline.lock is held by the queued job '$holder':" >&2
          _lock_who
          echo "  waiting for it (up to ${maxw}s); no other queued job will start meanwhile." >&2
          exec 8>"$LOCK_WANT"
          flock -n 8 2>/dev/null || true
          echo "$job $$" >&8
          if flock -w "$maxw" 9; then
            flock -u 8 2>/dev/null; exec 8>&-; rm -f "$LOCK_WANT"
            _lock_write_holder "$job" "$what"
            echo "  lock taken after the $holder finished" >&2
            return 0
          fi
          flock -u 8 2>/dev/null; exec 8>&-; rm -f "$LOCK_WANT"
          echo "FATAL: '$holder' still holds .pipeline.lock after $((maxw / 60)) min" >&2
          return 1 ;;
        *)
          if [ "$job" != nightly ]; then
            echo "FATAL: another job holds $LOCK_FILE -- never run two at once:" >&2
            _lock_who
          fi
          return 1 ;;
      esac ;;
    queued)
      t0=$(date +%s)
      while :; do
        # a posted want (an interactive run waiting) goes first
        if ! { [ -f "$LOCK_WANT" ] && ! ( exec 7>>"$LOCK_WANT"; flock -n 7 ) 2>/dev/null; }; then
          if flock -n 9; then _lock_write_holder "$job" "$what"; return 0; fi
        fi
        [ $(( $(date +%s) - t0 )) -ge "$maxw" ] && return 1
        sleep 30
      done ;;
  esac
  return 1
}

# lock_note KEY=VALUE -- add a fact to our holder record (who_holds and the
# nightly read it): run_pipeline notes results_stable=1 once nothing in the
# run will rewrite results / results_tf again (after the go-live).
lock_note() {
  if [ -f "$LOCK_HOLDER" ] && grep -qx "pid=$$" "$LOCK_HOLDER" 2>/dev/null; then
    echo "$1" >> "$LOCK_HOLDER"
  fi
}

# lock_holder_says KEY=VALUE -- true when the live holder's record has it
lock_holder_says() {
  [ -n "$(lock_holder_job)" ] && grep -qx "$1" "$LOCK_HOLDER" 2>/dev/null
}

lock_release() {
  # only our own holder record goes
  if [ -f "$LOCK_HOLDER" ] && grep -qx "pid=$$" "$LOCK_HOLDER" 2>/dev/null; then
    rm -f "$LOCK_HOLDER"
  fi
  flock -u 9 2>/dev/null
  exec 9>&-
}

# lock_queue_behind_run <job> <max-wait-s> <log-file> <on-stable-function> "<what>"
#   For the nightly (2026-11-10): the lock is held by a run. Post a want (no
#   backup or race model goes first), wait for the lock up to <max-wait-s>,
#   and call <on-stable-function> once, the first time the holder's record
#   says results_stable=1. Every line goes to the screen and <log-file>.
#   0 = lock taken; 1 = gave up (the caller says what that cost).
lock_queue_behind_run() {
  local job="$1" maxw="$2" log="$3" onstable="$4" what="$5" t0 holder done=0
  holder=$(lock_holder_job)
  t0=$(date +%s)
  echo "  ⚠ ${job^^} QUEUED: '${holder:-another job}' holds .pipeline.lock ($(date +%H:%M))." \
       "Not skipping: waiting up to $((maxw / 60)) min." | tee -a "$log"
  exec 8>"$LOCK_WANT"; flock -n 8 2>/dev/null || true
  echo "$job $$ (queued since $(date '+%F %H:%M'))" >&8
  until flock -n 9; do
    if [ "$done" -eq 0 ] && [ -n "$onstable" ] && lock_holder_says "results_stable=1"; then
      done=1
      echo "  the run's results tables are stable ($(date +%H:%M)): $onstable now, the rest when it lets go" | tee -a "$log"
      "$onstable"
    fi
    if [ $(( $(date +%s) - t0 )) -ge "$maxw" ]; then
      flock -u 8 2>/dev/null; exec 8>&-; rm -f "$LOCK_WANT"
      return 1
    fi
    sleep "${LOCK_POLL_S:-60}"
  done
  flock -u 8 2>/dev/null; exec 8>&-; rm -f "$LOCK_WANT"
  _lock_write_holder "$job" "$what"
  echo "  lock taken after $(( ($(date +%s) - t0) / 60 )) min in the queue" | tee -a "$log"
  return 0
}
