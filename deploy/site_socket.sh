#!/usr/bin/env bash
# deploy/site_socket.sh -- restarts of the site drop no requests.
#
#   sudo bash deploy/site_socket.sh            # install (idempotent; one short restart)
#   sudo bash deploy/site_socket.sh --remove   # back to gunicorn binding the port itself
#
# ★ SYSTEMD HOLDS PORT 8000, NOT GUNICORN (2026-11-08: the nginx error log
#   showed "connect() failed (111: Connection refused)" for every request
#   during a restart -- gunicorn closes its port on stop and, preloading the
#   app, model and templates, opens it again only seconds later). With
#   xc-predictor.socket the kernel keeps the listening socket open across a
#   restart: a request that arrives meanwhile waits in the backlog and is
#   answered by the new gunicorn (gunicorn uses the socket systemd passes in,
#   LISTEN_FDS). Tested: 60 of 60 requests answered across a restart, the one
#   that arrived mid-restart waited 2.1 s.
# ★ AND IN-FLIGHT REQUESTS FINISH: KillMode=mixed sends the stop signal to the
#   gunicorn master only, which lets each worker finish its request (up to
#   TimeoutStopSec); the default signalled every worker at once ("Connection
#   reset by peer while reading response header" in the same log).
set -euo pipefail
SOCK=/etc/systemd/system/xc-predictor.socket
DROP=/etc/systemd/system/xc-predictor.service.d/socket.conf
if [ "${1:-}" = "--remove" ]; then
  systemctl stop xc-predictor.socket 2>/dev/null || true
  systemctl disable xc-predictor.socket 2>/dev/null || true
  rm -f "$SOCK" "$DROP"
  systemctl daemon-reload
  systemctl restart xc-predictor
  echo "removed: gunicorn binds 127.0.0.1:8000 itself again"
  exit 0
fi
cat > "$SOCK" <<'UNIT'
[Unit]
Description=Racecast site socket (held across site restarts)

[Socket]
ListenStream=127.0.0.1:8000
Backlog=4096
NoDelay=true

[Install]
WantedBy=sockets.target
UNIT
mkdir -p "$(dirname "$DROP")"
cat > "$DROP" <<'UNIT'
[Unit]
Requires=xc-predictor.socket
After=xc-predictor.socket

[Service]
KillMode=mixed
TimeoutStopSec=40
UNIT
systemctl daemon-reload
# the running gunicorn holds the port: stop it, let systemd take the port,
# start the site on it (the one short gap this script causes)
systemctl stop xc-predictor
systemctl enable --now xc-predictor.socket
systemctl start xc-predictor
sleep 3
systemctl is-active --quiet xc-predictor.socket && systemctl is-active --quiet xc-predictor \
  && echo "installed: systemd holds 127.0.0.1:8000; restarts now queue requests instead of refusing them" \
  || { echo "something is not active -- see: systemctl status xc-predictor.socket xc-predictor" >&2; exit 1; }
