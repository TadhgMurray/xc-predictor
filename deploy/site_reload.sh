#!/usr/bin/env bash
# deploy/site_reload.sh -- deploy the site with no cold window.
#
#   sudo bash deploy/site_reload.sh            # install (one last restart)
#   sudo bash deploy/site_reload.sh --remove   # back to plain restarts
#
# Then deploy with:   cd /srv/xc-predictor && git pull && systemctl reload xc-predictor
#
# ★ WHY (2026-11-09). A restart stops every worker, and the new master's
#   preload (model, corrections, templates) runs while requests queue on the
#   socket: two of the day's five busiest minutes were restarts, 16-20 of 24
#   workers "busy" at 13 req/s. `reload` sends USR2 instead: gunicorn starts
#   a new master with the new code on the same socket while the old workers
#   keep serving; deploy/gunicorn.conf.py's handoff then tells systemd the
#   new master is the main process and stops the old one gracefully.
#
# ! Type=notify + NotifyAccess=all is what lets systemd follow the main
#   process from the old master to the new one (MAINPID=). A restart still
#   works as before; only `reload` hands off.
set -euo pipefail
DROP=/etc/systemd/system/xc-predictor.service.d/reload.conf
if [ "${1:-}" = "--remove" ]; then
  rm -f "$DROP"
  systemctl daemon-reload
  systemctl restart xc-predictor
  echo "removed; deploy with systemctl restart xc-predictor"
  exit 0
fi
mkdir -p "$(dirname "$DROP")"
cat > "$DROP" <<'UNIT'
[Service]
Type=notify
NotifyAccess=all
ExecReload=/bin/kill -USR2 $MAINPID
TimeoutStartSec=300
UNIT
systemctl daemon-reload
systemctl restart xc-predictor
sleep 2
systemctl is-active --quiet xc-predictor || { echo "not active -- systemctl status xc-predictor" >&2; exit 1; }
old=$(systemctl show -p MainPID --value xc-predictor)
echo "installed; trying a reload now (main pid $old)"
systemctl reload xc-predictor
for _ in $(seq 1 120); do
  sleep 1
  new=$(systemctl show -p MainPID --value xc-predictor)
  if [ "$new" != "$old" ] && [ "$new" != "0" ] && ! kill -0 "$old" 2>/dev/null; then
    systemctl is-active --quiet xc-predictor && {
      echo "reload handed off: main pid $old -> $new, the old master is gone. Deploy with:"
      echo "  cd /srv/xc-predictor && git pull && systemctl reload xc-predictor"
      exit 0; }
  fi
done
echo "the handoff did not finish in 120 s -- journalctl -u xc-predictor -n 50; undo with --remove" >&2
exit 1
