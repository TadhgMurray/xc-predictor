#!/usr/bin/env bash
# deploy/replica/wg_setup.sh -- a private WireGuard tunnel between the two
# servers. Postgres and the standby site's writes go through it; no database
# port is ever open on a public address.
#
#   sudo bash deploy/replica/wg_setup.sh          # on BOTH boxes, twice:
#     1st run: makes this box's key and prints its public key. Put that key
#              in the OTHER box's /etc/racecast-replica.env as RC_PEER_WG_PUBKEY.
#     2nd run: (both keys in place) writes /etc/wireguard/wg0.conf, opens
#              the WireGuard port to the other box's address only, brings
#              the tunnel up, and pings across it.
#   sudo bash deploy/replica/wg_setup.sh --check  # is the tunnel up?
#
# Idempotent: re-running changes only what differs and never makes a new key.
#
# ! THE FIREWALL. ufw is on (server_setup.sh) with everything closed but ssh
#   and 80/443. This adds: UDP ${RC_WG_PORT:-51820} from the other box's
#   public address; Postgres (5432) and ssh on the tunnel interface only.
#   5432 stays closed on the public interface -- and Postgres only listens on
#   localhost and the tunnel address (primary_prepare.sh).
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env

if [ "${1:-}" = "--check" ]; then
  wg show "$RC_WG_IF" 2>/dev/null || die "$RC_WG_IF is not up"
  ping -c 2 -W 2 "$RC_PEER_WG" >/dev/null && say "the other box answers on $RC_PEER_WG" \
    || die "no answer from $RC_PEER_WG"
  exit 0
fi

command -v wg >/dev/null || { say "installing wireguard-tools"; apt-get install -yq wireguard-tools >/dev/null; }

KEY="/etc/wireguard/${RC_WG_IF}.key"
umask 077
mkdir -p /etc/wireguard
if [ ! -s "$KEY" ]; then
  wg genkey > "$KEY"
  say "made a new key for this box ($KEY)"
fi
umask 022
PUB="$(wg pubkey < "$KEY")"
say "this box ($RC_SELF_NAME) public key: $PUB"

if [ -z "${RC_PEER_WG_PUBKEY:-}" ]; then
  say "next: put the line below in $RC_ENV on the OTHER box ($RC_PEER_NAME),"
  say "      put that box's key in RC_PEER_WG_PUBKEY here, and run this again on both:"
  echo "RC_PEER_WG_PUBKEY=$PUB"
  exit 0
fi
: "${RC_PEER_PUBLIC_IP:?set RC_PEER_PUBLIC_IP in $RC_ENV}"

CONF="/etc/wireguard/${RC_WG_IF}.conf"
BODY="$(cat <<EOF
# deploy/replica/wg_setup.sh -- this box ($RC_SELF_NAME) <-> $RC_PEER_NAME
[Interface]
Address = ${RC_SELF_WG}/24
ListenPort = ${RC_WG_PORT}
PrivateKey = $(cat "$KEY")

[Peer]
PublicKey = ${RC_PEER_WG_PUBKEY}
AllowedIPs = ${RC_PEER_WG}/32
Endpoint = ${RC_PEER_PUBLIC_IP}:${RC_WG_PORT}
PersistentKeepalive = 25
EOF
)"
CHANGED=0
write_if_changed "$CONF" "$BODY" && CHANGED=1
chmod 600 "$CONF"

say "firewall"
if ufw status | grep -q "Status: active"; then
  ufw allow from "$RC_PEER_PUBLIC_IP" to any port "$RC_WG_PORT" proto udp comment 'racecast wireguard' >/dev/null
  ufw allow in on "$RC_WG_IF" to any port "$RC_PG_PORT" proto tcp comment 'racecast postgres, tunnel only' >/dev/null
  ufw allow in on "$RC_WG_IF" to any port 22 proto tcp comment 'racecast ssh over the tunnel' >/dev/null
  if ufw status | grep -E "^${RC_PG_PORT}(/tcp)? +ALLOW +Anywhere" >/dev/null; then
    warn "ufw allows ${RC_PG_PORT} from Anywhere -- remove that rule: ufw delete allow ${RC_PG_PORT}"
  fi
else
  warn "ufw is not active. Postgres listens on localhost and the tunnel only, but turn the"
  warn "firewall on (server_setup.sh does: ufw allow OpenSSH; ufw allow 'Nginx Full'; ufw --force enable)"
fi

# ★ POSTGRES MAY START BEFORE THE TUNNEL IS UP (a reboot). It would then fail
#   to listen on the tunnel address and replication would stay down until the
#   next restart. Two guards: Postgres waits for the tunnel, and the kernel
#   lets it bind an address that is not up yet.
SYSCTL=/etc/sysctl.d/90-racecast-wg.conf
write_if_changed "$SYSCTL" "net.ipv4.ip_nonlocal_bind = 1" && sysctl -q -p "$SYSCTL"
DROPIN="/etc/systemd/system/postgresql@.service.d/racecast-wg.conf"
mkdir -p "$(dirname "$DROPIN")"
if write_if_changed "$DROPIN" "$(printf '[Unit]\nWants=wg-quick@%s.service\nAfter=wg-quick@%s.service' "$RC_WG_IF" "$RC_WG_IF")"; then
  systemctl daemon-reload
fi

systemctl enable "wg-quick@${RC_WG_IF}" >/dev/null 2>&1
if systemctl is-active -q "wg-quick@${RC_WG_IF}"; then
  if [ "$CHANGED" = 1 ]; then
    wg syncconf "$RC_WG_IF" <(wg-quick strip "$RC_WG_IF")
    say "tunnel config reloaded"
  fi
else
  systemctl start "wg-quick@${RC_WG_IF}"
  say "tunnel up"
fi

if ping -c 3 -W 2 "$RC_PEER_WG" >/dev/null 2>&1; then
  say "the other box answers on $RC_PEER_WG"
else
  warn "no answer from $RC_PEER_WG yet -- normal until the other box has run this too"
fi
