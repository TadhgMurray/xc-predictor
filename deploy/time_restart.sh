#!/bin/bash
# time_restart.sh -- restart the site and watch how long the first answers
# take: /api/me (what the topbar asks to swap "Sign in" for the account)
# every half second for 40 s, straight to gunicorn (127.0.0.1:8000, no
# Cloudflare, no cache). Then the journal's worker boots and model loads.
#
#   sudo bash deploy/time_restart.sh            # restart, then measure
#   sudo bash deploy/time_restart.sh --no-restart
#
# ★ WHY (owner, 2026-10-27): after a restart the topbar says "Sign in" for
#   about five seconds, and the first click on the picture is slow. The
#   numbers say whether it is the workers starting (no answer at all), the
#   model loading beside them (slow answers), or neither.
# ! AVOID 06:00-07:00 UTC: the apt / Postgres restart window.
set -u
T0=$(date +%s.%N)
if [ "${1:-}" != "--no-restart" ]; then
  echo "[time] restarting xc-predictor  $(date +%H:%M:%S)"
  systemctl restart xc-predictor
fi
SINCE=$(date -d "@${T0%.*}" '+%Y-%m-%d %H:%M:%S')
for i in $(seq 1 80); do
  NOW=$(date +%s.%N)
  OUT=$(curl -s -o /dev/null -m 10 -w "%{http_code} %{time_total}" http://127.0.0.1:8000/api/me)
  printf "  +%5.1fs  /api/me  %s\n" "$(echo "$NOW - $T0" | bc)" "$OUT"
  sleep 0.5
done
echo "[time] the journal since the restart:"
journalctl -u xc-predictor --since "$SINCE" --no-pager -o short-precise \
  | grep -E "Booting worker|Listening|warm in|Worker exiting|ERROR|Traceback" | head -40
