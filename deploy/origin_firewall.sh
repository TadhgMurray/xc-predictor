#!/usr/bin/env bash
# deploy/origin_firewall.sh -- web traffic reaches this server ONLY through
# Cloudflare.
#
#   sudo bash deploy/origin_firewall.sh            # check: who is connected right now
#   sudo bash deploy/origin_firewall.sh --apply    # allow 80/443 from Cloudflare only
#   sudo bash deploy/origin_firewall.sh --remove   # take the rule out again
#
# ★ WHY (owner, 2026-09-26: "it can hit my server instead? How is that
#   allowed"). Cloudflare only sees traffic that looks up racecast.co in DNS.
#   The server's own address is public, and anything that knows it can open
#   port 443 here directly and never meet a Cloudflare rule. This closes
#   ports 80 and 443 to everyone except Cloudflare's published ranges.
#
# ! ONLY PORTS 80 AND 443. SSH and everything else are not touched, so this
#   cannot lock you out of the box. It lives in its own chains (XCP_ORIGIN),
#   so --remove takes out exactly what --apply put in.
# ! CERTBOT KEEPS WORKING: Let's Encrypt checks racecast.co through DNS,
#   which is Cloudflare, so its request arrives from a Cloudflare address.
# ! NOT PERSISTENT BY ITSELF. After a reboot the rule is gone until this runs
#   again; --apply prints the two commands that make it stick.
set -euo pipefail
CHAIN=XCP_ORIGIN
V4=$(curl -fsS https://www.cloudflare.com/ips-v4)
V6=$(curl -fsS https://www.cloudflare.com/ips-v6)
[ -n "$V4" ] && [ -n "$V6" ] || { echo "could not fetch Cloudflare's ranges; nothing changed" >&2; exit 1; }

inCf() {  # inCf <address> -> exit 0 when it is in a Cloudflare range
  python3 - "$1" <<PY
import ipaddress, sys
a = ipaddress.ip_address(sys.argv[1])
nets = """$V4
$V6""".split()
sys.exit(0 if any(a in ipaddress.ip_network(n) for n in nets) else 1)
PY
}

remove() {
  for t in iptables ip6tables; do
    while $t -D INPUT -p tcp -m multiport --dports 80,443 -j $CHAIN 2>/dev/null; do :; done
    $t -F $CHAIN 2>/dev/null || true
    $t -X $CHAIN 2>/dev/null || true
  done
}

case "${1:-}" in
  --remove)
    remove
    echo "removed: ports 80/443 are open to everyone again"
    ;;
  --apply)
    remove
    for t in iptables ip6tables; do
      $t -N $CHAIN
      $t -A $CHAIN -i lo -j RETURN
      if [ $t = iptables ]; then R="$V4"; else R="$V6"; fi
      for n in $R; do $t -A $CHAIN -s "$n" -j RETURN; done
      $t -A $CHAIN -p tcp -j DROP
      $t -I INPUT -p tcp -m multiport --dports 80,443 -j $CHAIN
    done
    echo "applied: ports 80/443 accept Cloudflare only (SSH untouched)."
    echo "Check the site still loads in a browser. To keep it after a reboot:"
    echo "  apt install -y iptables-persistent && netfilter-persistent save"
    echo "Undo:  sudo bash deploy/origin_firewall.sh --remove"
    ;;
  "")
    echo "connections to ports 80/443 right now, by whether they come from Cloudflare:"
    cf=0; direct=0; declare -A seen=()
    while read -r peer; do
      ip=${peer%:*}; ip=${ip#[}; ip=${ip%]}; ip=${ip#::ffff:}
      if inCf "$ip"; then cf=$((cf + 1)); else direct=$((direct + 1)); seen[$ip]=1; fi
    done < <(ss -Htn state established '( sport = :443 or sport = :80 )' | awk '{print $4}')
    echo "  through Cloudflare: $cf"
    echo "  DIRECT to this server: $direct"
    for ip in "${!seen[@]}"; do echo "    $ip"; done | head -20
    [ "$direct" -gt 0 ] && echo "Direct connections bypass every Cloudflare rule. --apply closes that door."
    true
    ;;
  *)
    echo "usage: $0 [--apply | --remove]" >&2; exit 2 ;;
esac
