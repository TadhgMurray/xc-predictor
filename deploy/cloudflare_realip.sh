#!/usr/bin/env bash
# deploy/cloudflare_realip.sh -- make nginx log the visitor, not Cloudflare.
#
#   sudo bash deploy/cloudflare_realip.sh
#
# Once the DNS records are proxied (orange cloud), every request reaches
# nginx from a Cloudflare address and the visitor's own address rides in
# the CF-Connecting-IP header. This writes the list of Cloudflare ranges
# nginx should trust for that header, then reloads nginx. Re-run it now
# and then (a monthly cron is fine): the ranges change rarely, but they do.
#
# Idempotent. Safe to run before the records are proxied: a request that
# does not come from a Cloudflare range keeps its own address.
set -euo pipefail
OUT=/etc/nginx/conf.d/cloudflare-realip.conf
TMP=$(mktemp)
{
  echo "# Cloudflare edge ranges -- written by deploy/cloudflare_realip.sh $(date -u +%F)"
  echo "# Trust CF-Connecting-IP only from these; see https://www.cloudflare.com/ips/"
  for url in https://www.cloudflare.com/ips-v4 https://www.cloudflare.com/ips-v6; do
    # ! the lists end without a newline, which ran the last v4 range and
    #   the first v6 range onto one line; the echo ends each list
    { curl -fsS "$url"; echo; } | sed '/^$/d; s/^/set_real_ip_from /; s/$/;/'
  done
  echo "real_ip_header CF-Connecting-IP;"
} > "$TMP"
# a fetch that returned nothing would trust nobody: refuse to install it
if [ "$(grep -c set_real_ip_from "$TMP")" -lt 10 ]; then
  echo "cloudflare_realip: fewer than 10 ranges fetched; not installing" >&2
  rm -f "$TMP"; exit 1
fi
install -m 0644 "$TMP" "$OUT"; rm -f "$TMP"
nginx -t && systemctl reload nginx
echo "installed $OUT ($(grep -c set_real_ip_from "$OUT") ranges); nginx reloaded"
