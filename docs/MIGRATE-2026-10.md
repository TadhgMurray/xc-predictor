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

- `/` should be about 1.8 TB (RAID 10). If it is ~900 GB it was installed as
  a 4-way mirror: reinstall with RAID 10 before going on.
- The ECC line must not say `None`; the EDAC line should name a memory
  controller.

## 1. Freeze the old server's writers (on OLD)

```
systemctl disable --now xcp-nightly.timer xcp-backup.timer xcp-ovh-watch.timer
```

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
```

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
cd /srv/xc-predictor && bash deploy/run_pipeline.sh --from 10
```

### 5d. Look at it before anyone else does (from your PC)

Add `NEW racecast.co` to `C:\Windows\System32\drivers\etc\hosts`, open
https://racecast.co (an athlete page, a race page, rankings, a school page),
then remove the line again.

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
sudo bash deploy/install_nightly_timer.sh
sudo bash deploy/install_backup_timer.sh
sudo bash deploy/origin_firewall.sh        # web traffic only via Cloudflare
sudo systemctl start xcp-backup && journalctl -u xcp-backup -n 5 --no-pager
```

Keep OLD powered for a week with the site stopped
(`systemctl disable --now xc-predictor` on OLD), then cancel it.
