#!/usr/bin/env bash
# deploy/nginx_microcache.sh -- nginx keeps each public answer 60 s in front
# of gunicorn: repeats never reach Python, a burst of identical misses is one
# request, and a restart or a failing app is answered from the last copy.
#
#   sudo bash deploy/nginx_microcache.sh            # install (idempotent)
#   sudo bash deploy/nginx_microcache.sh --remove   # take it out
#   sudo bash deploy/nginx_microcache.sh --clear    # empty the cache now
#
# ★ WHAT IT KEEPS IS THE APP'S CALL, PER RESPONSE: racecast/edge_cache.py
#   sends X-Accel-Expires (60 for a public 200 with no cookie, 0 otherwise).
#   nginx acts on it and strips it; visitors never see it. A signed-in
#   visitor (xcp_session cookie) is never answered from the cache and never
#   fills it. Only GET/HEAD. One file at the http level, so the site file is
#   not touched.
set -euo pipefail
CONF=/etc/nginx/conf.d/racecast-microcache.conf
DIR=/var/cache/nginx/racecast-micro
if [ "${1:-}" = "--remove" ]; then
  rm -f "$CONF"; nginx -t && systemctl reload nginx
  rm -rf "$DIR"; echo "removed $CONF and $DIR"; exit 0
fi
if [ "${1:-}" = "--clear" ]; then
  find "$DIR" -type f -delete 2>/dev/null || true; echo "cleared $DIR"; exit 0
fi
# the cache must belong to the user nginx's workers run as, or EVERY page is
# a 500 (open() ... Permission denied): read it from nginx's own config
NUSER="${XCP_NGINX_USER:-$(nginx -T 2>/dev/null | awk '$1=="user"{gsub(";","",$2); print $2; exit}')}"
NUSER="${NUSER:-www-data}"
install -d -m 700 -o "$NUSER" "$DIR"
chown -R "$NUSER" "$DIR"
cat > "$CONF" <<'NGX'
# deploy/nginx_microcache.sh -- 60 s of the app's public answers (see the script)
proxy_cache_path /var/cache/nginx/racecast-micro levels=1:2 keys_zone=rc_micro:32m
                 max_size=1g inactive=20m use_temp_path=off;
proxy_cache rc_micro;
proxy_cache_key "$scheme$host$request_uri";
proxy_cache_methods GET HEAD;
proxy_cache_bypass $cookie_xcp_session $http_authorization;
proxy_no_cache $cookie_xcp_session $http_authorization;
proxy_cache_lock on;
proxy_cache_lock_timeout 5s;
proxy_cache_lock_age 10s;
proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
proxy_cache_background_update on;
NGX
if ! nginx -t; then
  echo "nginx -t refused the microcache config; removing it" >&2
  rm -f "$CONF"; exit 1
fi
systemctl reload nginx
sleep 1
# ! SELF-CHECK: one page through nginx. A 5xx means nginx cannot use the
#   cache (owner, path) -- take the config out again at once.
code=$(curl -sk -o /dev/null -m 20 -w '%{http_code}' --resolve racecast.co:443:127.0.0.1 \
       https://racecast.co/robots.txt || true)
if [ "${code:0:1}" = "5" ]; then
  echo "self-check: robots.txt through nginx gave $code -- removing the microcache" >&2
  rm -f "$CONF"; nginx -t && systemctl reload nginx; exit 1
fi
[ "$code" = "200" ] || echo "self-check: could not reach https://127.0.0.1 as racecast.co (got '$code'); check a page by hand"
echo "installed $CONF (cache in $DIR, owned by $NUSER; the app decides per response via X-Accel-Expires)"
