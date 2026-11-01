#!/usr/bin/env bash
# deploy/scrape_local.sh -- every non-athletic.net, non-TFRRS source in one go:
#
#   1. AthleticLIVE  (athletic.net's live-results platform, read from its public
#      JSON; track meets): discover new meet ids, land the settled ones
#   2. every enabled timing company's own site (sources/registry.py --enabled):
#      read its results index for meet pages, fetch + parse the new ones,
#      land -> dedup (athletic.net/TFRRS already have it?) -> promote
#   3. link the promoted timer rows to people (the nightly 04a does this too)
#
#   bash deploy/scrape_local.sh                 # everything, written
#   bash deploy/scrape_local.sh --dry           # parse only, nothing written
#   bash deploy/scrape_local.sh --only yentiming-services --per-source 20
#   ALIVE=0 bash deploy/scrape_local.sh         # timers only
#   TIMERS=0 bash deploy/scrape_local.sh        # AthleticLIVE only
#
# Then rate what landed: bash deploy/nightly_update.sh --no-scrape --no-publish
#
# ★ WHY (owner, 2026-10-31: "make all the local timers scrapers actually work
#   with an easy command"). Every fetch goes through sources.fetch: one request
#   at a time per host, 10 s apart, robots.txt honoured, an honest user agent,
#   bodies kept under /srv/raw so a re-run costs the timer nothing.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${XCP_PYTHON:-/srv/venv/bin/python}"
ENV_FILE="${XCP_ENV:-/etc/xc-predictor.env}"
cd "$ROOT" || exit 1
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
DRY=0
for a in "$@"; do [ "$a" = "--dry" ] && DRY=1; done
WRITE="--write"; [ "$DRY" -eq 1 ] && WRITE=""

rc=0
if [ "${ALIVE:-1}" != "0" ]; then
  echo "[local] $(date -u +%H:%M:%S) AthleticLIVE: discover"
  "$PY" -u -m sources.athleticlive init >/dev/null && \
  "$PY" -u -m sources.athleticlive discover || rc=1
  echo "[local] $(date -u +%H:%M:%S) AthleticLIVE: land (limit ${ALIVE_LIMIT:-200})"
  "$PY" -u -m sources.athleticlive land --limit "${ALIVE_LIMIT:-200}" $WRITE || rc=1
  if [ "$DRY" -eq 0 ]; then
    # athletic.net then never asks for a meet AthleticLIVE already gave us
    "$PY" -u -m sources.athleticlive cover || rc=1
  fi
fi
if [ "${TIMERS:-1}" != "0" ]; then
  echo "[local] $(date -u +%H:%M:%S) timers: discover -> fetch -> land -> dedup -> promote"
  "$PY" -u -m sources.timers all "$@" || rc=1
  if [ "$DRY" -eq 0 ]; then
    echo "[local] $(date -u +%H:%M:%S) timers: link promoted rows to people"
    "$PY" -u scripts/link_uploads.py --source timer --apply || rc=1
    echo "[local] $(date -u +%H:%M:%S) timers: close athletic.net meets a timer already gave us"
    "$PY" -u -m sources.anet_twins || rc=1
  fi
fi
echo "[local] $(date -u +%H:%M:%S) done (rc $rc). Rate them: bash deploy/nightly_update.sh --no-scrape --no-publish"
exit $rc
