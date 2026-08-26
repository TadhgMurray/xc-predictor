#!/usr/bin/env bash
# Project: xc-predictor / deploy
# File:    server_restore.sh
# Purpose: Load the dump, build stats, start the site. Run as root after
#          server_setup.sh, with the dump already scp'd to the server.
#
#     bash deploy/server_restore.sh /srv/xc_predictor.dump

set -euo pipefail
DUMP="${1:?usage: server_restore.sh /path/to/xc_predictor.dump}"

echo "== restore (data, then automatic index builds -- the long part) =="
sudo -u postgres pg_restore -j 4 --no-owner -d xc_predictor "$DUMP"

echo "== planner statistics (NOT optional -- fresh restores seq-scan) =="
sudo -u postgres vacuumdb --analyze-only -j 4 xc_predictor

echo "== belt-and-suspenders indexes (idempotent) =="
cd /srv/xc-predictor
set -a; . /etc/xc-predictor.env; set +a
/srv/venv/bin/python scripts/add_page_indexes.py || true

echo "== site up =="
systemctl restart xc-predictor
sleep 2
curl -s -o /dev/null -w "local health check: HTTP %{http_code}\n" \
    http://127.0.0.1:8000/ || true
echo "visit http://<server-ip>/ -- then point DNS and run certbot for TLS."
