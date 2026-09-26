#!/usr/bin/env bash
# deploy/nginx_limits.sh -- per-visitor request limits on the costly routes.
#
#   sudo bash deploy/nginx_limits.sh            # install / update
#   sudo bash deploy/nginx_limits.sh --remove   # take them out again
#
# ★ WHY (sweep, 2026-09-26). Eight gunicorn workers serve everything, and a
#   handful of requests to the expensive routes -- prediction POSTs, share
#   card images, login links -- can hold every one of them. The app's own
#   per-IP limits cover reports and login mail only, and live per worker.
#
# ! ONE FILE IN conf.d, NOTHING ELSE TOUCHED. limit_req works at http level,
#   and a `map` gives an EMPTY key to every path not listed -- nginx does
#   not count a request whose key is empty -- so the site's own server
#   block (which certbot may have edited) is left alone.
# ! NEEDS THE REAL VISITOR ADDRESS. Behind Cloudflare, without
#   deploy/cloudflare_realip.sh installed, every visitor arriving through
#   one Cloudflare edge shares one address and would share one allowance.
#   So this refuses to install unless that file exists; pass
#   --no-cloudflare only if the site is NOT behind Cloudflare.
set -euo pipefail
OUT=/etc/nginx/conf.d/xc-limits.conf
REALIP=/etc/nginx/conf.d/cloudflare-realip.conf

if [ "${1:-}" = "--remove" ]; then
  rm -f "$OUT"; nginx -t && systemctl reload nginx
  echo "removed $OUT; nginx reloaded"; exit 0
fi
if [ ! -f "$REALIP" ] && [ "${1:-}" != "--no-cloudflare" ]; then
  echo "STOP: $REALIP is missing, so nginx sees Cloudflare's address, not" >&2
  echo "the visitor's. Run: sudo bash deploy/cloudflare_realip.sh  first." >&2
  exit 1
fi

cat > "$OUT" <<'CONF'
# written by deploy/nginx_limits.sh -- per-visitor limits on costly routes

# one key per zone: the visitor for the listed paths, empty (= not counted)
# for everything else
map $request_uri $xcp_lim_api {
    ~^/api/predict/(team|individual|lineup)  "";   # stricter zone below
    ~^/api/          $binary_remote_addr;
    default          "";
}
# only the three that run the model; squad, athlete and weather lookups
# are light and the page fires ~30 squad lookups at once
map $request_uri $xcp_lim_predict {
    ~^/api/predict/(team|individual|lineup)  $binary_remote_addr;
    default          "";
}
map $request_uri $xcp_lim_card {
    ~^/card/         $binary_remote_addr;
    default          "";
}
map $request_uri $xcp_lim_login {
    ~^/(login|auth/) $binary_remote_addr;
    ~^/api/report    $binary_remote_addr;
    default          "";
}

# rates: the board and typeahead fire several /api/ calls a second while
# typing, so that zone is generous; a prediction is seconds of CPU; a login
# link is an email.
limit_req_zone $xcp_lim_api     zone=xcp_api:10m     rate=10r/s;
limit_req_zone $xcp_lim_predict zone=xcp_predict:10m rate=30r/m;
limit_req_zone $xcp_lim_card    zone=xcp_card:10m    rate=2r/s;
limit_req_zone $xcp_lim_login   zone=xcp_login:10m   rate=10r/m;

limit_req zone=xcp_api     burst=40 nodelay;
limit_req zone=xcp_predict burst=10 nodelay;
limit_req zone=xcp_card    burst=10 nodelay;
limit_req zone=xcp_login   burst=5  nodelay;
limit_req_status 429;
CONF

if nginx -t; then
  systemctl reload nginx
  echo "installed $OUT; nginx reloaded"
else
  rm -f "$OUT"
  echo "nginx rejected the limits; $OUT removed, nothing changed" >&2
  exit 1
fi
