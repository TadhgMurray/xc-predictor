# Moving racecast to the OVH RISE-3 (2026-10-22)

Old: ReliableSite 5950X (faulty memory: five damaged pages in four weeks).
New: OVH RISE-3, Ryzen 9 5900X, 128 GB ECC, 4x 960 GB NVMe, Ubuntu 24.04.

The site keeps serving from the OLD server until step 6. Nothing on the old
server is deleted; it stays as the fallback for a week.

In every command below, replace `NEW` with the new server's IP: `15.204.196.219`.

---

## 0. Check the new box (on NEW)

```
lsblk; cat /proc/mdstat; df -h /; free -g
sudo dmidecode -t memory | grep -m1 "Error Correction Type"
sudo dmesg | grep -i edac
```

- `/` should be about 1.8 TB (RAID 10). If it is ~880 GB, OVH sized the
  RAID partition at half of each disk (2026-10-22: md3 on 446 GB of each
  894 GB NVMe). Reinstall from the OVH panel (Server -> Reinstall -> Ubuntu
  24.04 -> customise partitions): RAID 10 for `/`, size "use remaining
  space". Re-add the ssh key; on the PC, `ssh-keygen -R NEW` if ssh then
  complains about a changed host key.
- The ECC line must not say `None`; the EDAC line should name a memory
  controller.

## 1. Freeze the old server's writers (on OLD)

```
for t in xcp-nightly xcp-backup xcp-ovh-watch; do systemctl disable --now $t.timer 2>/dev/null && echo "off: $t"; done
systemctl list-timers --all --no-pager | grep -iE 'xcp|racecast|pipeline'
crontab -l 2>/dev/null | grep -v '^#'
```

One unit missing must not stop the others (2026-10-22: OLD has no
`xcp-nightly.timer`, and a single `disable` of all three turned off none).
Anything the last two lines still print that runs the pipeline, a scrape or
a backup: disable it too.

No pipeline or scrape runs until the move is done, so the dump stays the
truth. The site keeps serving.

## 2. Let OLD reach NEW over ssh

OVH's Ubuntu takes no root password, so OLD's key goes in by hand.

On OLD:
```
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Copy the one line it prints. On NEW (as root, after `sudo -i`):
```
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'PASTE THE LINE HERE' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Back on OLD:
```
ssh -o StrictHostKeyChecking=accept-new root@NEW true && echo ok
```

## 3. Copy everything git does not hold (on OLD)

The checkout (with engine/corrections.py, engine/data, the trained model),
both Python environments, the env file with every secret, and the R2
backup credentials. Same OS and paths, so a straight copy works. (nginx and
TLS come in 4b: the setup script would overwrite them.)

```
ssh root@NEW "apt-get update -q && apt-get install -yq rsync git"
rsync -aHAX --info=progress2 --exclude logs/ --exclude racecast/static/share/ \
      /srv/xc-predictor/ root@NEW:/srv/xc-predictor/
rsync -aHAX --info=progress2 /srv/venv/ root@NEW:/srv/venv/
[ -d /srv/wxvenv ] && rsync -aHAX --info=progress2 /srv/wxvenv/ root@NEW:/srv/wxvenv/
rsync -a /etc/xc-predictor.env root@NEW:/etc/xc-predictor.env.old
rsync -aR /root/.config/rclone root@NEW:/ 2>/dev/null || true
[ -d /srv/models ] && rsync -aHAX --info=progress2 /srv/models/ root@NEW:/srv/models/
```

`/srv/models` holds the live prediction model (`RACECAST_MODEL` in the env
file points there); it is outside the checkout.

## 4. Postgres 18, the service, nginx (on NEW)

Use the OLD database password, so the copied env file works unchanged.

```
export XCP_DB_PASSWORD="$(grep -oP '^XCP_DB_PASSWORD=\K.*' /etc/xc-predictor.env.old | tr -d "'\"")"
cd /srv/xc-predictor
bash deploy/server_setup.sh            # packages, service user, unit, nginx
bash deploy/server_pg_major.sh 18      # Ubuntu ships 16; the data is 18
cp /etc/xc-predictor.env.old /etc/xc-predictor.env && chmod 600 /etc/xc-predictor.env
apt-get install -yq rclone smartmontools
```

`server_setup.sh` writes its own env file; the `cp` line puts the real one
back. The checkout already exists (step 3), which it accepts.

### 4b. nginx and TLS from OLD (on OLD)

```
rsync -aR /etc/nginx /etc/letsencrypt root@NEW:/ 2>/dev/null || true
ssh root@NEW "nginx -t && systemctl reload nginx"
```

### 4c. Postgres settings: anything OLD has that NEW does not (both)

```
sudo -u postgres psql -Atc "select name||'='||setting from pg_settings where source='configuration file' order by 1" > /tmp/pg_$(hostname).txt
```

Run on both, then `diff` the two files (copy one across). Anything OLD set
that NEW lacks (beyond the tuning block both scripts write) goes into
`/etc/postgresql/18/main/postgresql.conf` on NEW, then
`systemctl restart postgresql`.

## 5. The database (dump on OLD, load on NEW)

`ranking_results` is left out: it is damaged on OLD (block 1422598) and is
rebuilt from the results on NEW in 5c anyway.

### 5a. Dump (on OLD, ~10 min) and copy (~5 min)

```
mkdir -p /srv/migrate && chown postgres /srv/migrate && rm -rf /srv/migrate/db
sudo -u postgres pg_dump -Fd -j 8 -Z 5 --exclude-table-data=ranking_results \
     -f /srv/migrate/db xc_predictor
ssh root@NEW "mkdir -p /srv/migrate"
rsync -a --info=progress2 /srv/migrate/db root@NEW:/srv/migrate/
```

If pg_dump stops on another `invalid page`, send the message: that table
gets the same treatment.

### 5b. Load (on NEW, a few hours)

```
sudo -u postgres pg_restore -j 12 --no-owner -d xc_predictor /srv/migrate/db
sudo -u postgres vacuumdb --analyze-only -j 12 xc_predictor
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
/srv/venv/bin/python scripts/add_page_indexes.py
/srv/venv/bin/python scripts/integrity_check.py 2>&1 | grep -E "✗|DAMAGED|✓ every"
```

### 5c. Rebuild the rankings and everything after them (on NEW, ~3-4 h)

```
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
/srv/venv/bin/python scripts/backfill_tfrrs_location.py --apply --out logs
/srv/venv/bin/python scripts/build_course_canonical.py --incremental --apply
bash deploy/run_pipeline.sh --from 10
```

The first two give the college (tfrrs) courses their gps and course ids
(2026-10-22: none had any), so the solve in step 10 fits their difficulty.

### 5c+. Right after the pipeline finishes (on NEW)

The code that landed during the run (What's new, the overnight schedule,
blend) and a check of the college course numbers:

```
cd /srv/xc-predictor && git pull -q && systemctl restart xc-predictor
grep -E '^XCP_(ALERTS|NIGHTLY_ALERTS|PREDICT_BASIS|PREDICT_VENUE_DIFFICULTY)=' /etc/xc-predictor.env
/srv/venv/bin/python scripts/diag_tfrrs_course_cells.py 2>&1 | sed -n 3,10p
```

Expect `XCP_PREDICT_BASIS=blend` and `XCP_PREDICT_VENUE_DIFFICULTY=xc`, and
"cell" as the big row of the diag (college races with their own course
number). This run started before 00b_run_snapshot existed, so What's new
says only "Every rating recomputed" for it; the next Sunday's run says what
changed.

### 5d. Look at it before anyone else does (from your PC)

Add `NEW racecast.co` to `C:\Windows\System32\drivers\etc\hosts`, open
https://racecast.co (an athlete page, a race page, rankings, a school page),
then remove the line again. Look at What's new and About -> "When new
results show up" too.

## 6. Cut over (about 10 minutes of downtime)

The site's own tables (accounts, claims, follows, fix requests, uploads,
shared predictions, error log, usage) kept changing on OLD after 5a. Move
their latest copy, then switch.

On OLD:
```
systemctl stop xc-predictor
sudo -u postgres pg_dump -Fc -f /srv/migrate/live.dump xc_predictor \
  -t 'account*' -t 'alert_*' -t 'app_error*' -t auth_event -t fix_request \
  -t follow_snapshot -t issue_reports -t login_token -t person_optout \
  -t prediction_snapshot -t 'result_upload*' -t shared_prediction -t usage_daily
rsync -a /srv/migrate/live.dump root@NEW:/srv/migrate/
```

On NEW:
```
sudo -u postgres pg_restore --clean --if-exists --no-owner -d xc_predictor /srv/migrate/live.dump
systemctl restart xc-predictor
```

Then in Cloudflare -> DNS: change the `racecast.co` and `www` A records to
NEW (keep them proxied, orange cloud). Pages come from NEW within a minute.

## 7. After the switch (on NEW)

```
cd /srv/xc-predictor
sudo bash deploy/install_nightly_timer.sh       # Mon-Sat 01:17 Pacific, not Saturday night
sudo bash deploy/install_backup_timer.sh
sudo bash deploy/install_kailo_record_timer.sh   # (and disable it on OLD)
sudo bash deploy/origin_firewall.sh        # web traffic only via Cloudflare
sudo systemctl start xcp-backup && journalctl -u xcp-backup -n 5 --no-pager
systemctl list-timers --all | grep xcp     # xcp-nightly next on a weekday morning, backup, kailo
```

And on OLD: `systemctl disable --now xcp-kailo-record.timer` (the recorder
moves to NEW with the line above).

Keep OLD powered for a week with the site stopped
(`systemctl disable --now xc-predictor` on OLD), then cancel it.

## The probe check (any time, read-only)

The overnight update looks 150 ids past the newest athletic.net meet per
sport (two empty blocks to stop) and 300 past the newest TFRRS meet
(deploy/nightly_update.sh). That is enough only if real meets are never
further apart than that. The widest gaps between consecutive real meets
among the newest 20,000 ids of each queue:

```
sudo -u postgres psql -d xc_predictor -c "
WITH m AS (
  SELECT source, sport, meet_id FROM meet_queue q
  WHERE scraped = 1 AND meet_id > (SELECT max(meet_id) - 20000 FROM meet_queue
                                   WHERE source = q.source AND sport = q.sport AND scraped = 1)),
g AS (SELECT source, sport, meet_id - lag(meet_id) OVER (PARTITION BY source, sport ORDER BY meet_id) AS gap FROM m)
SELECT source, sport, count(*) AS meets, max(gap) AS widest,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY gap) AS p99
FROM g WHERE gap IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2;"
```

anet: `widest` should stay under 300 (two blocks of 150); tfrrs: under 300.
If not, raise XCP_NIGHTLY_SEED_AHEAD / XCP_NIGHTLY_TFRRS_AHEAD in the env file.
