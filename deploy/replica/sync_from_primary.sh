#!/usr/bin/env bash
# deploy/replica/sync_from_primary.sh -- keep the standby box's copy of the
# files the database does not hold in step with the primary's. Runs every 10
# minutes on BOTH boxes (install_services.sh); does nothing on the primary.
#
#   sudo bash deploy/replica/sync_from_primary.sh
#
# What moves, primary -> standby:
#   - the checkout (a deploy is `git pull && systemctl restart xc-predictor`
#     on the primary; this follows it here and restarts this site when the
#     commit changed), engine/corrections.py and engine/data/* (the pipeline
#     writes them; the site reads them: restart when they changed),
#   - /srv/venv, /srv/models, /var/lib/racecast, /var/lib/xc-predictor.
# And standby -> primary, first, never deleting: account photos a visitor
# uploaded while this box served them (racecast/static/photos).
#
# ! A PRIMARY THAT IS DOWN IS NOT AN ERROR here (that is when this box
#   matters most): the sync waits for the next run.
set -euo pipefail
. "$(dirname "$0")/common.sh"
need_root
load_env
PEER="root@${RC_PEER_WG}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
RS=(rsync -aHAX -e "${SSH[*]}")
APP=/srv/xc-predictor

local_up && local_in_recovery || exit 0           # the primary, or no database: nothing to do
"${SSH[@]}" "$PEER" true 2>/dev/null || { say "the primary does not answer ssh; next run"; exit 0; }

exec 9>/run/racecast-sync.lock
flock -n 9 || { say "a sync is already running"; exit 0; }

# photos uploaded here go to the primary first, so the --delete below keeps them
"${RS[@]}" --ignore-existing "$APP/racecast/static/photos/" "$PEER:$APP/racecast/static/photos/" 2>/dev/null || true

before="$(git -C "$APP" rev-parse HEAD 2>/dev/null || echo none)"
changes="$("${RS[@]}" --delete --itemize-changes \
    --exclude logs/ --exclude '*.log' --exclude .pipeline.lock --exclude __pycache__/ \
    --exclude racecast/static/share/ \
    "$PEER:$APP/" "$APP/" | grep -vE '^\.d|^cd' || true)"
after="$(git -C "$APP" rev-parse HEAD 2>/dev/null || echo none)"
chown -R xcp:xcp "$APP"
venv="$("${RS[@]}" --delete --itemize-changes "$PEER:/srv/venv/" /srv/venv/ | grep -vE '^\.d|^cd' || true)"
for d in /srv/models /var/lib/racecast /var/lib/xc-predictor; do
  "${SSH[@]}" "$PEER" "[ -d $d ]" && { mkdir -p "$d"; "${RS[@]}" "$PEER:$d/" "$d/"; }
done

restart=0
[ "$before" != "$after" ] && { say "code moved: ${before:0:9} -> ${after:0:9}"; restart=1; }
printf '%s\n' "$changes" | grep -qE ' engine/(data/|corrections\.py)' && { say "engine data changed"; restart=1; }
[ -n "$venv" ] && { say "python environment changed"; restart=1; }
if [ "$restart" = 1 ] && systemctl is-active -q xc-predictor; then
  systemctl restart xc-predictor
  say "site restarted"
fi
