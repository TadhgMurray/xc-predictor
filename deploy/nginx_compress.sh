#!/usr/bin/env bash
# deploy/nginx_compress.sh -- gzip at the origin for CSS, JS, JSON, SVG, XML.
#
#   sudo bash deploy/nginx_compress.sh            # install / update
#   sudo bash deploy/nginx_compress.sh --remove   # take it out again
#
# ★ WHY (SEO pass, 2026-10-10). Debian's nginx.conf ships `gzip on;` with
#   gzip_types commented out, so nginx compresses text/html ONLY: style.css
#   (284 KB, on every page) and the JS went out raw from the origin.
#   Cloudflare compresses at the edge for visitors, but anything reaching the
#   origin directly -- a cache miss path, a crawler that bypasses the edge, a
#   staging box -- paid full size. Page speed is a ranking input; this is the
#   free half of it.
#
# ! ONE FILE IN conf.d, NOTHING ELSE TOUCHED (as nginx_limits.sh). Directives
#   the main nginx.conf already sets are NOT repeated -- nginx refuses a
#   duplicate `gzip` or `gzip_types` at the same level -- so this asks
#   `nginx -T` what is there first, writes only what is missing, and removes
#   its own file again if `nginx -t` fails.
set -euo pipefail
OUT=/etc/nginx/conf.d/xc-compress.conf

if [ "${1:-}" = "--remove" ]; then
  rm -f "$OUT"; nginx -t && systemctl reload nginx
  echo "removed $OUT; nginx reloaded"; exit 0
fi

rm -f "$OUT"
CURRENT="$(nginx -T 2>/dev/null | grep -vE '^\s*#' || true)"
has() { printf '%s\n' "$CURRENT" | grep -qE "^\s*$1\s"; }

{
  echo "# written by deploy/nginx_compress.sh -- origin gzip for text assets"
  has gzip            || echo "gzip on;"
  has gzip_vary       || echo "gzip_vary on;"
  has gzip_proxied    || echo "gzip_proxied any;"
  has gzip_comp_level || echo "gzip_comp_level 5;"
  has gzip_min_length || echo "gzip_min_length 1024;"
  has gzip_types      || echo "gzip_types text/css text/plain text/xml application/javascript application/json application/xml application/manifest+json image/svg+xml;"
} > "$OUT"

if ! nginx -t; then
  echo "nginx -t refused the new file; removing it" >&2
  rm -f "$OUT"; exit 1
fi
systemctl reload nginx
echo "wrote $OUT:"; cat "$OUT"
echo "check: curl -sI -H 'Accept-Encoding: gzip' http://127.0.0.1/static/style.css | grep -i content-encoding"
