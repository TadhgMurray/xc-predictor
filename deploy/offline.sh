#!/usr/bin/env bash
# deploy/offline.sh -- take racecast.co offline the way search engines read as
# "temporarily away", and bring it back (owner, 2026-11-13).
#
#   sudo bash deploy/offline.sh on       # every request: 503 + Retry-After + deploy/offline/index.html
#   sudo bash deploy/offline.sh off      # the site again
#   sudo bash deploy/offline.sh status
#
# ★ A 503 WITH Retry-After, NOT A DEAD ORIGIN. Stopping the app makes
#   Cloudflare answer 502/521 -- "broken server" to Google and Bing. A 503
#   with Retry-After is the standard "planned downtime, come back later":
#   pages stay indexed for a short break (a couple of weeks; past that they
#   drop whatever is sent, and come back slowly when the site does).
# ★ HOW: one include line at the top of each server { } in the site file that
#   proxies to the app (a backup is kept beside it, .bak-offline). The snippet
#   answers 503 for EVERY path -- robots.txt too, which tells crawlers to
#   pause -- while the flag file deploy/offline/ON exists. "off" removes the
#   flag; the include stays (a stat() per request, harmless).
# ! "on" ALSO stops what runs on its own: the scrapers' and builders' timers
#   and the app. The database backup timer keeps running. "off" starts the
#   app and those timers again. Cloudflare's cached copies are purged both
#   ways (scripts/edge_purge.py; a no-op without its credentials).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${XCP_NGINX_SITE:-/etc/nginx/sites-available/xc-predictor}"
SNIPPET=/etc/nginx/snippets/racecast-offline.conf
FLAG="$ROOT/deploy/offline/ON"
PAGE_DIR="$ROOT/deploy/offline"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
TIMERS="xcp-nightly.timer xcp-kailo-record.timer xcp-race-model.timer xcp-static.timer"
MARK="# racecast-offline (deploy/offline.sh)"

purge() {
  if [ -f /etc/xc-predictor.env ]; then
    ( set -a; . /etc/xc-predictor.env; set +a; "$PY" "$ROOT/scripts/edge_purge.py" ) || \
      echo "  (Cloudflare purge did not run -- purge everything in the Cloudflare dashboard: Caching > Purge Everything)"
  fi
}

check() {
  # this box's own nginx, straight (no proxy), TLS first, then plain
  local code
  code=$(curl -sk --noproxy '*' -o /dev/null -m 20 -w '%{http_code}' \
           --resolve racecast.co:443:127.0.0.1 https://racecast.co/ 2>/dev/null || true)
  if [ -z "$code" ] || [ "$code" = 000 ]; then
    code=$(curl -s --noproxy '*' -o /dev/null -m 20 -w '%{http_code}' \
             -H 'Host: racecast.co' http://127.0.0.1/ 2>/dev/null || true)
  fi
  echo "${code:-?}"
}

install() {
  mkdir -p "$(dirname "$SNIPPET")"
  cat > "$SNIPPET" <<CONF
$MARK
# while $FLAG exists, every request is a 503 with Retry-After and the offline page
error_page 503 @racecast_offline;
location @racecast_offline {
    root $PAGE_DIR;
    try_files /index.html =503;
    default_type text/html;
    add_header Retry-After 86400 always;
    add_header Cache-Control "no-store" always;
}
if (-f $FLAG) { return 503; }
CONF
  if ! grep -q "include $SNIPPET;" "$SITE"; then
    cp -p "$SITE" "$SITE.bak-offline"
    python3 - "$SITE" "$SNIPPET" "$MARK" <<'PYEOF'
import re, sys
path, snippet, mark = sys.argv[1:4]
s = open(path).read()
out, i, n = [], 0, 0
# every "server {" whose block proxies to the app gets the include as its first line
for m in re.finditer(r'(^|\n)([ \t]*)server[ \t]*\{', s):
    start = m.end()
    depth, j = 1, start
    while j < len(s) and depth:
        depth += {"{": 1, "}": -1}.get(s[j], 0)
        j += 1
    if "127.0.0.1:8000" in s[start:j]:
        out.append(s[i:start] + f"\n    include {snippet};  {mark}")
        i = start
        n += 1
out.append(s[i:])
if not n:
    sys.exit("no server block proxies to 127.0.0.1:8000 -- nothing patched")
open(path, "w").write("".join(out))
print(f"  patched {n} server block(s) in {path}")
PYEOF
  fi
  if ! nginx -t 2>/dev/null; then
    echo "  ! nginx -t failed -- restoring the site file"
    [ -f "$SITE.bak-offline" ] && cp -p "$SITE.bak-offline" "$SITE"
    nginx -t
    exit 1
  fi
}

case "${1:-status}" in
  on)
    install
    touch "$FLAG"
    systemctl reload nginx
    echo "  site: $(check) (503 means offline)"
    systemctl disable --now $TIMERS 2>/dev/null || true
    systemctl stop xcp-nightly.service xcp-kailo-record.service xcp-race-model.service 2>/dev/null || true
    systemctl disable --now xc-predictor.socket 2>/dev/null || true
    systemctl disable --now xc-predictor 2>/dev/null || true
    purge
    echo "OFFLINE. Back with: sudo bash deploy/offline.sh off"
    ;;
  off)
    systemctl enable --now xc-predictor.socket 2>/dev/null || true
    systemctl enable --now xc-predictor
    for i in $(seq 1 60); do
      code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8000/ 2>/dev/null || true)
      [ "$code" = 200 ] && break
      sleep 5
    done
    rm -f "$FLAG"
    systemctl reload nginx
    echo "  site: $(check) (200 means back)"
    systemctl enable --now $TIMERS 2>/dev/null || true
    purge
    echo "ONLINE. Then: rebuild + resubmit the sitemap, run scripts/indexnow_submit.py"
    ;;
  status)
    if [ -f "$FLAG" ]; then echo "offline (flag $FLAG)"; else echo "online"; fi
    echo "  site answers: $(check)"
    ;;
  *) echo "usage: $0 on|off|status"; exit 2 ;;
esac
