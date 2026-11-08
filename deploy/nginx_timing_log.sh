#!/usr/bin/env bash
# deploy/nginx_timing_log.sh -- a second nginx access log with each request's
# time, so p50/p90/p99 come from real traffic (scripts/latency_report.py).
#
#   sudo bash deploy/nginx_timing_log.sh            # install (idempotent)
#   sudo bash deploy/nginx_timing_log.sh --remove   # take it out
#
# ★ HTTP-LEVEL, ITS OWN FILE: /etc/nginx/conf.d/racecast-timing.conf. The
#   site file is not touched; every server block without its own access_log
#   inherits it beside the default access.log. $request_time is the whole
#   request at nginx (client upload + upstream + send); $upstream_response_time
#   is gunicorn's part ("-" for a file nginx served itself).
set -euo pipefail
CONF=/etc/nginx/conf.d/racecast-timing.conf
LOG=/var/log/nginx/racecast_timing.log
if [ "${1:-}" = "--remove" ]; then
  rm -f "$CONF"
  nginx -t && systemctl reload nginx
  echo "removed $CONF (the log file $LOG is left)"
  exit 0
fi
cat > "$CONF" <<'NGX'
# deploy/nginx_timing_log.sh -- request timings for scripts/latency_report.py
log_format racecast_timing '$time_iso8601 $status $request_time $upstream_response_time $body_bytes_sent "$request_method $request_uri"';
access_log /var/log/nginx/racecast_timing.log racecast_timing;
NGX
if ! nginx -t; then
  echo "nginx -t refused the timing log; removing it" >&2
  rm -f "$CONF"; exit 1
fi
systemctl reload nginx
SITE="${XCP_NGINX_SITE:-/etc/nginx/sites-available/xc-predictor}"
if [ -f "$SITE" ] && grep -q "^\s*access_log" "$SITE"; then
  echo "NOTE: $SITE sets its own access_log, so its server blocks do not inherit this one."
  echo "      Add to each server block:  access_log $LOG racecast_timing;"
fi
echo "installed $CONF -> $LOG ; after some traffic: python3 scripts/latency_report.py"
