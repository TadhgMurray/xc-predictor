#!/usr/bin/env bash
# deploy/replica/standby_site.sh -- the site on the standby box: a second
# gunicorn + nginx that reads the local replica and writes to the primary.
# Run on the standby, after standby_build.sh.
#
#   sudo bash deploy/replica/standby_site.sh
#
# It copies from the primary, over the tunnel (root ssh from this box to the
# other must work: docs/MIGRATE-2026-10.md step 2 shows how to add the key):
#   /srv/xc-predictor (the checkout, engine/corrections.py, engine/data, photos),
#   /srv/venv (and /srv/wxvenv, /srv/models when present),
#   /var/lib/racecast and /var/lib/xc-predictor (crests, social images),
#   /etc/xc-predictor.env, the site's systemd unit, /etc/nginx, /etc/letsencrypt
#   and /etc/ssl/racecast (an origin certificate, if you use one)
# then sets the site's role (site_role.sh standby) and starts it. It does NOT
# install any timer: a standby runs no pipeline, backup or scraper.
#
# Re-run any time; sync_from_primary.sh (every 10 minutes, install_services.sh)
# keeps the code and files current afterwards.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
PEER="root@${RC_PEER_WG}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

local_up && local_in_recovery || die "this box is not a running standby -- run standby_build.sh first"
"${SSH[@]}" "$PEER" true 2>/dev/null || die "cannot ssh to $PEER without a password.
           On this box:  ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519; cat ~/.ssh/id_ed25519.pub
           On the other: append that line to /root/.ssh/authorized_keys"

say "packages"
DEBIAN_FRONTEND=noninteractive apt-get install -yq nginx rsync python3-venv poppler-utils curl >/dev/null
id -u xcp >/dev/null 2>&1 || useradd -m -s /bin/bash xcp

RS=(rsync -aHAX --info=stats1 -e "${SSH[*]}")
say "the checkout, environments and data files"
"${RS[@]}" --delete --exclude logs/ --exclude '*.log' --exclude .pipeline.lock \
    --exclude __pycache__/ --exclude racecast/static/share/ \
    "$PEER:/srv/xc-predictor/" /srv/xc-predictor/
"${RS[@]}" --delete "$PEER:/srv/venv/" /srv/venv/
for d in /srv/wxvenv /srv/models /var/lib/racecast /var/lib/xc-predictor; do
  if "${SSH[@]}" "$PEER" "[ -d $d ]"; then
    mkdir -p "$d"
    "${RS[@]}" "$PEER:$d/" "$d/"
  fi
done
chown -R xcp:xcp /srv/xc-predictor

say "the env file, the unit, nginx and certificates"
"${RS[@]}" "$PEER:/etc/xc-predictor.env" /etc/xc-predictor.env
chmod 600 /etc/xc-predictor.env
"${RS[@]}" "$PEER:/etc/systemd/system/xc-predictor.service" /etc/systemd/system/xc-predictor.service
"${RS[@]}" "$PEER:/etc/nginx/" /etc/nginx/
for d in /etc/letsencrypt /etc/ssl/racecast; do
  if "${SSH[@]}" "$PEER" "[ -d $d ]"; then
    mkdir -p "$d"
    "${RS[@]}" "$PEER:$d/" "$d/"
  fi
done

bash "$(dirname "$0")/site_role.sh" standby --no-restart
systemctl enable xc-predictor >/dev/null 2>&1
systemctl restart xc-predictor
nginx -t && systemctl reload nginx

say "local checks"
sleep 5
for path in /robots.txt /; do
  code="$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H 'Host: racecast.co' "http://127.0.0.1:8000$path" || true)"
  say "  gunicorn $path -> HTTP $code"
done
say "  journalctl -u xc-predictor -n 30   if either is not 200"
say "next: sudo bash deploy/replica/install_services.sh; then on this box, once:"
say "      sudo bash deploy/origin_firewall.sh --apply   (web traffic only via Cloudflare)"
say "and add this box to Cloudflare (docs/REPLICA-PLAN.md, 'Cloudflare')."
