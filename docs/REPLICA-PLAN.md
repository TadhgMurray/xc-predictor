# A second server: keep racecast up when the main box fails (2026-11-08)

The whole site runs on one OVH RISE-3 (Ryzen 9 5900X, 128 GB ECC, Postgres 18,
~600 GB database). Its CPU has a confirmed fault: uncorrected machine-check
exceptions, and it reboots under sustained full load -- 4+ times in one day,
the site down 2 to 60 minutes each time. The nightly pipeline runs on the same
box and competes with the site for the database.

This plan adds a second server that holds a live copy of the database and
serves the site from it. Scripts are in `deploy/replica/`; the code change is
`scripts/read_routing.py` plus a small dispatch in `scripts/database.py`.

---

## The short version

- **Order one more OVH Rise box** with the same specs the stock watcher
  already uses (`scripts/ovh_stock_watch.py`: 128 GB ECC, 1.92 TB+ mirrored
  NVMe, US datacenter, at most $250 a month). It should be in the same
  datacenter as today's box if one is in stock. Expect about **$110-200 a
  month** (assumption below), plus one month's setup fee unless you take a
  12-month term.
- The new box (**B**) is a **hot standby**: a streaming copy of the
  database, a few hundred milliseconds behind. It runs **its own gunicorn**,
  which reads its local copy and sends the few writes the site makes (sign-in,
  follows, claims, reports, usage counts) to the primary (**A**) through a
  private WireGuard tunnel.
- **Cloudflare Load Balancing** ($5 a month) sends visitors to B and falls
  back to A. When A reboots, B keeps serving every page. Writes fail with the
  site's 503 page until A is back, and nothing needs doing.
- Once B is in sync, **switch roles**. B becomes the primary and runs the
  pipeline on healthy hardware; A becomes the standby. Then ask OVH to
  replace A's CPU. That repair costs you nothing, and with the standby in
  place the downtime no longer matters.
- Total: **~$115-205 a month more**, about a day of elapsed time (most of it
  waiting for OVH and a 1-2 hour copy), and about 2 hours of your hands-on
  time.

---

## 1. Options compared

Assumptions. Prices come from OVH's and Cloudflare's published pricing as I
understand it; I did not query either service, so check them before you
order.
- **OVH US Rise, 128 GB ECC + 2x1.92 TB NVMe**: about $90-120 a month for
  RISE-1 or RISE-2 (6-8 core Xeon E), $130-170 for RISE-3 (5900X), and
  $150-230 for RISE-4, 5 or 7 (EPYC). Public bandwidth is 1 Gbit/s and
  unmetered. The setup fee is one month, waived on a 12-month term (as the
  watcher's mail says). `ovh_stock_watch.py --show` prints today's exact
  in-stock prices under your ceiling.
- **Cloudflare Load Balancing**: $5 a month for 2 endpoints, with a health
  check every 60 s from one region. More endpoints, regions or a faster check
  cost extra.
- **Cloudflare Workers Paid**: $5 a month for 10M requests, then $0.30 per
  million. The free tier allows 100k requests a day.
- **Database**: ~600 GB. The pipeline's go-live builds 40-60 GB shadow
  tables, so the peak is about 700-800 GB.

| | (a) Hot standby + 2nd site + CF failover | (b) = (a) + the standby is the site's READ database | (c) Logical replication | (d) Warm spare / static mirror | (e) Pipeline on a separate compute box |
|---|---|---|---|---|---|
| **Monthly** | server $110-200 + LB $5 | same as (a): no more hardware | same as (a) | d1: $0 (edge cache + maintenance page)  d2: $100-200 (needs the same disk) | $100-200 |
| **Site up when A dies** | yes, reads in ~1-2 min (LB check); writes 503 until A is back or B is promoted (~5 min, by hand) | yes, and with no switch at all when B is already the origin | in theory, but see below | d1: cached pages only  d2: after a manual restore, data up to 24 h old | **no** |
| **Takes read load off A** | only after a failover | **yes, all site reads** | yes | no | no (the site still reads A) |
| **Data loss on failover** | < 1 s of commits (async) | same | seconds to minutes | up to 24 h | n/a |
| **Fits the pipeline's CTAS+rename swaps** | yes, byte for byte | yes | **no** (below) | yes | yes |
| **Complexity** | medium: tunnel, standby, LB, runbooks | (a) + read routing (written, tested) | high | low | high: the pipeline assumes a local database and local files |
| **Setup time, 600 GB** | ~1 day elapsed, ~2 h hands-on | + 0 (same scripts) | days | d2: 3-5 h per nightly restore | days |

**(a) Hot standby (physical streaming replication).** B gets a byte-for-byte
copy of A's data directory (`pg_basebackup -X stream`) and then replays A's
WAL as it is written. Everything A does arrives on B in the same order:
DDL, `CREATE TABLE AS`, `ALTER TABLE ... SET LOGGED`, the rename swaps, mass
`UPDATE`s and `VACUUM`. Nothing in the pipeline has to change. B is
read-only until it is promoted.

There are three ways to get Cloudflare to send traffic to the right box:
1. **Cloudflare Load Balancing, $5 a month (recommended).** A monitor polls
   each box's `:8099` health endpoint (`origin_health.py`) every 60 s; the
   pools fail over in order. Detection takes 1-2 minutes, and the edge's
   `stale-if-error` keeps cached pages up meanwhile.
2. **A Worker that tries origin A, then B.** It would need its own hostnames
   for each origin and runs on every request, including cache hits. The
   site's traffic overruns the free tier, so this means the $5 Workers plan
   plus per-request fees past 10M. It also means code at the edge you have to
   maintain. It fails over faster, per request, but it is not worth that.
3. **Free: flip the DNS record.** A script on a third machine calls the DNS
   API when A's health check fails. It costs $0, but it is a new moving part
   that must run somewhere other than A or B. Use it only if $5 a month
   matters.

**(b) The standby is the site's READ database.** The site reads far more than
it writes. Section 3 lists every write path. With (b), the site's gunicorn on
B reads its local replica and only its writes cross the tunnel to A. A then
does almost nothing for the site: it runs the pipeline and takes the writes.
That is the "take read traffic off the primary" goal, and it costs nothing
beyond (a). Replication lag is the cost. Section 4 explains what the site
shows while B is behind, and why that is safe.

**(c) Logical replication. Not recommended.** It replicates row changes per
table, not files. The pipeline's pattern of building a new table with
`CREATE TABLE AS` (or `UNLOGGED` and then `SET LOGGED`), building its
indexes, then `RENAME` old to `_old`, new to live, and `DROP` is the worst
case for it:
- DDL is not replicated. Every new shadow table has to be created on the
  subscriber by hand, added to the publication (or `FOR ALL TABLES`), and
  then `ALTER SUBSCRIPTION ... REFRESH PUBLICATION` run.
- A newly added table starts with a full initial `COPY`, so 40-60 GB per
  big table per night is copied again.
- The rename does not travel: the subscriber's `ranking_results` keeps the
  old rows until someone renames there too, in step.
- `UPDATE` and `DELETE` need a primary key or `REPLICA IDENTITY FULL` on
  every table (FULL makes a 61M-row update unworkable on the subscriber).
  Sequences are not replicated.
- Mass `UPDATE`s replay row by row.

It would also mean writing a second pipeline to drive the subscriber.
Physical replication has none of these problems. Logical replication's real
advantages are a writable subscriber, a subset of tables, and crossing major
versions. None of them is needed here.

**(d) Warm spare.**
- **(d1)** costs $0 and covers almost nothing new. The edge already keeps
  cached pages through an outage (`stale-if-error=86400`). A free-tier
  Worker or Cloudflare custom error page could add a branded "back in a few
  minutes" page for cache misses. Worth doing in any case.
- **(d2)** a second box restored nightly from `backup_db.sh`'s dump: a
  600 GB restore needs the same 1.92 TB disk, so it costs about what (a)
  costs. It takes 3-5 hours of restore every night, loses up to a day of
  data, and needs a manual cutover. (a) is better at the same price.
- **(d3)** a $5-20 VPS with a static maintenance mirror and Cloudflare
  failover: a branded outage page, no data. Only worth it if (a) is out of
  budget.

**(e) Move the pipeline to a separate compute box.** This moves the Python
work (the solve, torch) off A, but not the Postgres work: `CREATE TABLE AS`,
index builds and mass `UPDATE`s run inside the database server. It does
nothing for the site when A dies. It is also the most invasive option. The
pipeline assumes a local database (backend renice in `database._quietTune`)
and writes `engine/data/*` files the site reads. **The switchover in (a)
gets the same benefit for free**: once B is the primary, the heavy pipeline
runs on healthy hardware.

There is one more option that is free and should happen anyway. **(f) Open
an OVH support ticket for A's CPU fault.** Hardware is OVH's to replace.
Attach the machine-check log (`journalctl -k | grep -iE 'mce|machine check'`).
Do this after the standby is live, so the repair downtime is invisible to
visitors.

---

## 2. Recommendation

**(a) + (b), with Cloudflare Load Balancing, in three phases:**

1. **Phase 1 (this week): B becomes a hot standby and the site's main
   origin.**
   - A stays the primary and runs the pipeline.
   - B serves the site: it reads its local replica and writes to A through
     the tunnel.
   - Cloudflare uses pool order [B, A].
   - When A reboots, reads go on from B with no action and no detection
     delay, because B is already the origin; writes return 503 until A is
     back. When B fails, the load balancer falls back to A, which serves
     everything itself (today's setup).
2. **Phase 2 (once B has run through one pipeline cleanly): a planned
   switchover.**
   - B becomes the primary, runs the pipeline and serves the site.
   - A is rewound into a standby: minutes, not a 2-hour copy.
   - Open the OVH ticket for A's CPU.
   - The pool stays [B, A].
3. **Phase 3 (A repaired and stable for two weeks; optional): reads off the
   primary again.**
   - Pool order [A, B]. The site on A reads A's replica, and B does the
     pipeline and the writes.

**Why this one?**
- It is the only option that keeps the site serving through every kind of
  failure A has had: a reboot, Postgres killed by the OOM killer, a
  reboot loop.
- It changes nothing in the pipeline.
- It reuses everything already in place: the scripts, the timers, the edge
  cache, the 503 page, the stock watcher's hardware filter.
- It moves the heavy pipeline onto healthy hardware within days.
- It takes the site's read load off the box that crashes under load.

---

## 3. Every write the site makes

`grep -nE "INSERT INTO|UPDATE .* SET|DELETE FROM|CREATE (TEMP )?TABLE|TRUNCATE" racecast/*.py`
was run outside the pipeline's builders (`build_*.py`, `watchdog.py`, the
`panels`/`search_index`/`activity` builders). The site writes:

| Where | Tables | When | Routed to |
|---|---|---|---|
| `accounts.py` | account, login_token, account_session, account_claim, account_photo (+ a file on disk), auth_event | sign-in, sign-up, claims, settings: **POST**. `loadSession` also touches `account_session.last_seen_at` on a signed-in **GET** (at most hourly) | primary: POST requests and every request carrying `xcp_session` read and write the primary |
| `follows.py`, `shortlist.py` | account_follow, alert_pref, alert_item, account_shortlist | follow/unfollow, alert cadence, shortlist: **POST** | primary |
| `my_page.py` | next_meet_cache | signed-in **GET** (cache fill) | primary (signed-in) |
| `fixes.py` | fix_request, meet_queue, alive_meets | fix requests and admin decisions: **POST** (`SELECT ... FOR UPDATE`) | primary |
| `uploads.py` | result_upload(_row); on approval meets, results, meets_tf(_meta), results_tf | upload and admin approval: **POST** | primary |
| `app.py` | prediction_snapshot (GET cache), shared_prediction (POST create, GET `opened + 1`), issue_reports (POST `/api/report`), `CREATE TABLE IF NOT EXISTS` on first use | mixed | each write statement goes to the primary, and so does the rest of its block |
| `app.py` TF venue page | `CREATE TEMP TABLE venue_rows ON COMMIT DROP` | **GET** | primary: a standby cannot create a temp table. This one page always reads the primary; rewriting it as a CTE would move it to the replica |
| `forecast.py`, `meet_forecast.py` | weather_forecast (GET-time cache), `CREATE TABLE IF NOT EXISTS` | GET | primary for the write |
| `usage.py` | usage_daily upsert (`SET LOCAL statement_timeout`, then `executemany`) | background flush | primary; the `SET LOCAL` is replayed there |
| `app_errors.py` | app_error, app_error_notify (`FOR UPDATE SKIP LOCKED`) | when a page fails | primary |
| `site_status.py`, `data_snapshots.py` | issue_reports, site_status_snapshot, data_snapshot* | admin | primary |
| `follow_alerts.py` | alert_item, follow_snapshot, `pg_advisory_unlock` | the alert job (a timer on the primary box) | primary |

**How the routing works** (`scripts/read_routing.py`, 48 tests in
`tests/test_read_routing.py`):
- Each statement is classified.
  - Plain reads go to the replica: `SELECT`, `WITH`, `VALUES`, `SHOW` or
    `EXPLAIN`, with no write word anywhere outside comments and string
    literals.
  - Everything else goes to the primary: INSERT, UPDATE, DELETE, MERGE, any
    DDL including TEMP tables, COPY, `FOR UPDATE`/`FOR SHARE`, `nextval`,
    advisory locks, `SELECT ... INTO`.
- After the first write, **the rest of the block stays on the primary**.
  The site parks one connection per request (`db_timing.py`), so in practice
  that is the rest of the request. A page that writes and then reads reads
  its own write.
- A `SET` or `SET LOCAL` (and `SELECT set_config(...)`) that ran on the
  replica is replayed on the primary when the block moves there.
- **The classifier leans towards "write".** A wrong "write" costs one primary
  round trip. A wrong "read" is caught: the replica refuses it having done
  nothing (25006 read-only transaction, 0A000 "unlogged relations during
  recovery", 40001 "conflict with recovery"), and the statement re-runs on
  the primary.
- **Whole requests on the primary**: POST, PUT, PATCH and DELETE, and any
  request carrying the session cookie (`XCP_DB_PRIMARY_COOKIES`, default
  `xcp_session`). A visitor who has just signed in or followed someone reads
  what they wrote. Signed-in visitors are a small share of traffic; the
  anonymous majority reads the replica.
- **Either side down**:
  - A replica that refuses connections is skipped for 30 s
    (`XCP_DB_READ_RETRY_S`).
  - A replica more than `XCP_DB_READ_MAX_LAG_S` (300 s) behind gives way to
    the primary. The lag is re-read every 5 s on a connection being handed
    out.
  - A primary that cannot be reached is skipped for 15 s
    (`XCP_DB_PRIMARY_RETRY_S`). Reads that wanted it, such as a signed-in
    page, use the replica instead, and a write raises
    `database.PoolUnavailable`, which the site already answers with its
    branded 503 and `Retry-After`. **That is the read-only mode during A's
    reboots.** During that time a signed-in visitor shows as signed out,
    because the session touch fails.
- **Off unless asked for.** `database.getConn()` routes only when
  `XCP_DB_READ_DSN` is set, the process is the site (`XCP_DB_APP=site`,
  `XCP_DB_READ_APPS`), and it is not the pipeline (`XCP_DB_QUIET`). Unset, it
  takes exactly today's path, and `read_routing` is never imported.
- The standby box's role file `/etc/xc-predictor-role.env` sets these
  variables; the site's unit reads it through a drop-in, see
  `site_role.sh`. The pipeline and the timers read only
  `/etc/xc-predictor.env`, so a read DSN can never reach a builder.

**The change in `scripts/database.py`.** `getConn()`'s old body is now
`_primaryConn()`, unchanged. The new `getConn()` above it is 20 lines: if
`XCP_DB_READ_DSN` is set and `read_routing.active()`, it yields a routed
connection; otherwise it yields `_primaryConn()`. Nothing else in the file
changed. Connection options (`_PG_CONFIG_WITH_TIMEOUT`, `_opts`) are
untouched; the read pool copies them, then adds
`default_transaction_read_only=on` and a 3 s connect timeout.

---

## 4. A go-live on the replica: WAL, disk, lag, what the site shows

**What travels.** The shadow tables are built `UNLOGGED`, which writes no WAL.
The WAL comes from three steps:
- `ALTER TABLE ... SET LOGGED` writes the whole table into the WAL (40-60 GB
  for `ranking_results`).
- The index builds that follow are logged too (roughly their size).
- The mass `UPDATE`s log each row changed.

The rename swap itself is a few kilobytes of catalog changes. The `DROP` of
`_old` removes the files on B as soon as it is replayed.

`wal_compression = zstd` (in `90-racecast-replication.conf`) shrinks the
full-page images, which make up most of a `SET LOGGED` or index build, by
roughly 2-3x.

**Estimate**, to be measured on the first run with
`SELECT wal_bytes FROM pg_stat_wal` before and after:
- 20-40 GB on the wire per big go-live.
- 150-400 GB for a full pipeline run.

At ~100 MB/s through the tunnel that is 3-7 minutes per go-live, and about
an hour spread across a full run, well inside 1 Gbit/s. Replay on NVMe is
single-threaded but faster than the link for this kind of WAL.

The WAL is already written today (`wal_level` defaults to `replica`); the
replica only ships it.

**Disk on B.** The same as A's peak: the logged shadow copies exist on both
boxes until the `DROP`, so about 800 GB with the database at 600 GB.
`standby_build.sh` refuses to start without 1.3 x the database + 50 GB free.

**Disk on A.** The slot keeps WAL for B while B is away, capped at
`max_slot_wal_keep_size = 200GB`. Past the cap the slot is dropped and B
needs `standby_build.sh --rebuild` (1.5-4 h), rather than A's disk filling.
Check `df -h /` on A: if more than 800 GB is free, raise
`RC_MAX_SLOT_WAL_KEEP` to 400GB, so B can be down for a whole pipeline run
and still catch up.

**Lag**, and why reads still go to B while it lags:
- Normally well under a second.
- During a go-live it rises by the time the burst takes to cross (minutes),
  plus up to 60 s at the swap (next point).
- The site keeps reading B while it is behind, up to 300 s by default: an
  older view is still a whole view (see "What the site shows").
- Past 300 s the site's reads go to A until B catches up. That keeps reads
  fresh at the cost of load on A at its busiest moment. Raise
  `RC_READ_MAX_LAG_S` (the role file) to keep reads on B through longer
  bursts.

**Conflicts at the swap, and the two settings that handle them:**
- **`max_standby_streaming_delay = 60s`.** Replaying the rename and the
  `DROP` needs an exclusive lock on `ranking_results`. On B that conflicts
  with site queries still reading it. The standby waits up to 60 s before
  cancelling them. The site's own `statement_timeout` is 55 s, so in
  practice it never cancels a site query, and replay pauses for at most
  about a minute. `tests/test_replica_scripts.py` checks that the delay
  stays above the timeout. Any query that is still cancelled (40001)
  re-runs on the primary, and the site's `_db_blip` retries a GET once on
  top of that.
- **`hot_standby_feedback = on`.** Without it, `VACUUM` on A (the
  pipeline's mass `UPDATE`s create plenty of dead rows) removes rows a site
  query on B still sees, and B cancels that query. With it, B reports its
  oldest snapshot and A's vacuum waits for it. The site's queries are capped
  at 55 s, so vacuum on A is held back by at most 55 s.
  - The risk is a long query on B, such as a `psql` session left open or a
    `pg_dump` run on B. It would hold back vacuum on A for as long as it
    runs. `idle_in_transaction_session_timeout = 10min` covers the
    forgotten session. Run ad-hoc work on B with `SET statement_timeout`.

**What the site shows during lag.** Physical replication replays A's commits
in order, so B always shows a state A actually had: never a half-swapped
table, never new `ranking_results` beside old `athlete_season`. After a
go-live, B shows yesterday's boards complete until the swap replays, then
today's complete. That is the same thing the edge cache already does for up
to 15 minutes.

**The one real hazard: the edge purge.**
- `scripts/edge_purge.py` runs right after a go-live. If it purges before B
  has replayed the swap, Cloudflare fetches fresh pages from B's site, which
  still has the old boards, and caches them for another `s-maxage`.
- The fix is one line before each purge step, on the primary. In
  `deploy/run_pipeline.sh` before `step 10_edge_purge` and
  `step 19_edge_purge`, and in `deploy/nightly_update.sh` before
  `step 14_edge_purge`:

      "$PY" -u deploy/replica/origin_health.py --wait-caught-up --timeout 1800 || true

- It returns at once when no standby is connected, so it is safe to add
  now. I did not edit those files, because other work is in progress on
  them.
- The existing second purge 16 minutes later (`--delay 960`) is a further
  safety net.

---

## 5. Network: no database port on the internet

- **WireGuard** (`wg_setup.sh`) runs between the two public addresses on UDP
  51820, opened to the other box's address only. The tunnel uses 10.77.0.1
  (a) and 10.77.0.2 (b). If both boxes are in the same OVH datacenter and the
  plan includes vRack, the tunnel can run over the private network instead.
  It works either way.
- **Postgres** listens on `localhost` and its tunnel address only
  (`listen_addresses = 'localhost,10.77.0.x'`), never on the public one.
  - ufw allows 5432 only `in on wg0`. A test checks no script opens it any
    other way.
  - `pg_hba.conf` gets two lines, both from the other box's tunnel address
    only, with scram: `replication replicator` and `all postgres` (the
    standby site's writes, `pg_rewind`).
  - Postgres waits for the tunnel at boot (a systemd drop-in), and
    `net.ipv4.ip_nonlocal_bind = 1` lets it bind the address even if the
    tunnel comes up later.
- **Passwords never appear on a command line.** psql reads them with
  `\getenv`; the walreceiver and `pg_rewind` use `/var/lib/postgresql/.pgpass`
  (mode 600) through `passfile=`.
- **The health port** 8099 is opened to Cloudflare's published ranges only
  (`install_services.sh`). Ports 80/443 on B are closed to everything but
  Cloudflare by the existing `deploy/origin_firewall.sh --apply`.

---

## 6. Cloudflare

1. **Origin certificate first.** With two origins behind a load balancer,
   certbot's HTTP challenge for one box can land on the other, and renewals
   start failing. In the dashboard, go to SSL/TLS > Origin Server > Create
   Certificate (RSA, `racecast.co` and `*.racecast.co`, 15 years). Then:
   - Put the certificate and key at `/etc/ssl/racecast/cert.pem` and
     `key.pem` on A.
   - Change `ssl_certificate` and `ssl_certificate_key` in the nginx site,
     `nginx -t && systemctl reload nginx`.
   - `systemctl disable --now certbot.timer`.
   - `standby_site.sh` copies `/etc/ssl/racecast` and `/etc/nginx` to B.

   The SSL mode stays Full (strict), which accepts Origin CA certificates.
2. **Traffic > Load Balancing > enable** ($5 a month).
3. **Monitor**: HTTP, port **8099**, path `/`, expected code 200, interval
   60 s, timeout 5 s, retries 2. Add an email notification.
4. **Pools**:
   - `racecast-b`: B's public IP.
   - `racecast-a`: A's public IP.
   - Each pool has one endpoint and uses the monitor.
5. **Load balancer** on `racecast.co` (and one on `www.racecast.co`,
   which only redirects), proxied:
   - Steering: Off (failover).
   - Pool order: `racecast-b`, `racecast-a`.
   - Fallback pool: `racecast-a`.
   - No session affinity: every write goes to the primary database whichever
     origin serves it.

   Leave the existing A records in place. The load balancer takes
   precedence on the same hostname, and deleting it puts things back the
   way they were.
6. Cache rules and the purge token are unchanged: the cache is per
   hostname, not per origin.

---

## 7. Health checks and alerts

- **`origin_health.py --serve`** (the `rc-origin-health` service, both
  boxes) answers on :8099. It returns 200 when the site answers on
  127.0.0.1:8000 and this box's Postgres answers a query, 503 otherwise.
  The body includes the role and the lag. Lag alone never makes a box
  unhealthy: a standby that is behind still serves, and when the primary is
  down it is the best copy there is.
- **`origin_health.py --report`** is for people. On a standby it shows the
  receiver state, the lag and whether replay is paused. On the primary it
  shows each standby, each slot (how much WAL it keeps, how far from the
  cap) and free disk.
- **`rc-replica-alert.timer`** runs every 5 minutes and mails the admins
  (`accounts.sendMail`, the same channel as the stock watcher) when the set
  of problems changes, and again every 6 hours while it lasts. It checks for:
  - not streaming;
  - replay paused (usually a setting on A raised above B's);
  - more than 30 minutes behind;
  - a slot that is idle, `unreserved` or `lost`;
  - less than 10% disk free.
- **`rc-sync.timer`** (`sync_from_primary.sh`) runs every 10 minutes and
  only acts on the standby.
  - It pulls the checkout and restarts B's site when the commit changes, so
    a deploy on the primary reaches B within 10 minutes.
  - It also pulls `engine/data` and `engine/corrections.py` (restarting
    the site when they change), `/srv/venv`, `/srv/models`, the crest and
    social image directories, and account photos.
  - Before pulling, it pushes any photo uploaded on B back to A.

---

## 8. Runbooks

**A reboots (the common case).** Do nothing.
- B keeps serving: reads from its replica, writes 503 ("try again in a
  minute") until A is back.
- B's walreceiver reconnects by itself and catches up from the slot.
- Check afterwards with `origin_health.py --report` on B.

**Planned switchover (Phase 2; ~15 min, writes paused ~1-2 min).** No
pipeline running.
1. A: `sudo bash deploy/replica/switchover_stop_primary.sh`
   - It turns A's timers off, waits for B to replay everything, stops A's
     site and Postgres cleanly, and prints the final checkpoint LSN.
2. B: `sudo bash deploy/replica/promote.sh --planned --expect-lsn <that LSN>`
   - It checks B has that LSN (no write lost), promotes, and points B's
     site at its own database.
3. B: install the timers A had: `install_nightly_timer.sh`,
   `install_backup_timer.sh`, `install_kailo_record_timer.sh`, and
   `install_ovh_watch_timer.sh` if it was on.
4. A: `sudo bash deploy/replica/standby_build.sh --rewind` (minutes:
   `pg_rewind` copies only changed blocks), then
   `sudo bash deploy/replica/standby_site.sh`.
5. Cloudflare: pool order unchanged (`racecast-b` first).

**Failover (A is dead for hours: disk, board, OVH intervention).**
- **Never automatic.** Do not promote for a reboot.
1. Cloudflare: disable A's pool, or its endpoint.
2. Power A off in the OVH panel. If you can still reach it,
   `sudo bash deploy/replica/fence.sh` there instead: it stops and masks
   Postgres.
3. B: `sudo bash deploy/replica/promote.sh --failover --old-primary-is-fenced`.
   - It refuses if A's Postgres still answers.
   - It prints the last replayed commit (writes after it are lost, typically
     under a second's worth).
4. B: install the timers as in step 3 above.
5. When A is reachable again, **before anything else**:
   `sudo bash deploy/replica/fence.sh` on A. Then
   `standby_build.sh --rewind`, then `standby_site.sh`, then re-enable A's
   pool as the second one.

**Failback.** This is a planned switchover in the other direction. Nothing
special.

**The standby is broken or fell past the slot cap.**
`sudo bash deploy/replica/standby_build.sh --rebuild` on it (1.5-4 h; run it
in tmux). The site keeps going: the load balancer sends traffic to the
primary's box when the standby's health fails, and while the standby's site
is still up its reads go to the primary.

**A setting was raised on the primary** (`max_connections`,
`max_worker_processes`, `max_locks_per_transaction`...). The standby pauses
replay: the alert says "replay is paused". Fix it with
`sudo bash deploy/replica/standby_build.sh --settings` on the standby, which
copies the settings and restarts it if needed. Do this before raising them
on the primary next time.

**Read routing misbehaves.** `sudo bash deploy/replica/site_role.sh remote`
on the standby box. Its site then reads and writes the primary through the
tunnel with routing off. Or reorder the pools so the primary's box serves.

---

## 9. Your steps, in order

| # | Where | What | Time |
|---|---|---|---|
| 1 | PC | `ovh_stock_watch.py --show` (or wait for its mail). Order the cheapest in-stock line at **A's datacenter**, else the other one: 128 GB ECC, 1.92 TB+ mirrored NVMe, Ubuntu 24.04, RAID for `/` using all space (MIGRATE step 0). RISE-3 or RISE-2 preferred; the box should be able to become the primary. | 10 min + OVH delivery (1-2 h at "1H-high", up to 72 h) |
| 2 | B | MIGRATE step 0 checks (disk size, ECC, EDAC). Then `apt-get update && apt-get install -y git`, clone the repo to `/srv/xc-predictor`. | 15 min |
| 3 | both | `cp deploy/replica/replica.env.example /etc/racecast-replica.env; chmod 600`, then fill it in. The `SELF`/`PEER` lines are swapped between the two boxes. Set `RC_REPL_PASSWORD` to the same value on both (`openssl rand -base64 24`). | 10 min |
| 4 | both | `sudo bash deploy/replica/wg_setup.sh` (prints the key) → copy each key into the other box's file → run it again on both. Ends with "the other box answers". | 10 min |
| 5 | B → A | root ssh key from B into A's `authorized_keys` (MIGRATE step 2). | 5 min |
| 6 | A | `sudo bash deploy/replica/primary_prepare.sh`. Then, with no pipeline running, `... --restart` (~10-30 s of 503s while Postgres restarts to listen on the tunnel). | 10 min |
| 7 | B | `tmux new -s standby`, then `sudo bash deploy/replica/standby_build.sh`. It installs Postgres 18, copies 600 GB with a checksum check, and starts streaming. | **45 min-2.5 h** (1 Gbit/s; server-side zstd) |
| 8 | B | `sudo bash deploy/replica/standby_site.sh` (copies the checkout, venv, env, unit, nginx, certificates; starts the site with `role=standby`). | 20-40 min |
| 9 | PC | Look at it before anyone else does: `curl --resolve racecast.co:443:<B-ip> https://racecast.co/ -I`, or the hosts-file check (MIGRATE 5d). Open an athlete, a race, the rankings, then sign in and follow someone (a write crossing the tunnel). | 15 min |
| 10 | both | `sudo bash deploy/replica/install_services.sh`. On B also `sudo bash deploy/origin_firewall.sh --apply` (after step 9). | 10 min |
| 11 | Cloudflare | Origin certificate on both (section 6.1), then the load balancer (6.2-6.5). Watch the monitor turn both endpoints green. | 30 min |
| 12 | A | Add the `--wait-caught-up` line before the three edge-purge steps (section 4). | 5 min |
| 13 | - | Let one full pipeline run happen. Watch `origin_health.py --report` on both and note the WAL volume (`pg_stat_wal`) and the peak lag. | one run |
| 14 | A, B | Planned switchover (runbook). | 15 min hands-on, ~10 min rewind |
| 15 | OVH | Ticket for A's CPU with the MCE log. Accept any intervention window. Afterwards `standby_build.sh --rewind` on A, or `--rebuild` if they replaced the disks. | 10 min + OVH |

**Rough total:** about 2-3 hours of your time, spread over 1-2 days, mostly
waiting for OVH, the copy and a pipeline run.

**Cost:** +$110-200 a month for the server, +$5 a month for the load
balancer, and one month's setup fee unless you take a 12-month term.

---

## 10. Risks

1. **Asynchronous replication can lose the last moment of writes on a
   failover.** Normally that is less than a second; during a go-live burst
   it is the lag, but those writes are the pipeline's and can be re-run.
   Synchronous replication would remove this risk, but it would make every
   pipeline commit wait on B, and A would stall whenever B is down. Not
   worth it here.
2. **Two primaries** (split brain) is the one unrecoverable mistake.
   - Promotion is manual only; `promote.sh` demands
     `--old-primary-is-fenced` and refuses while A's Postgres answers.
   - `fence.sh` masks Postgres on a returning old primary.
   - The load balancer pool is disabled before promoting.
3. **The slot fills A's disk** if B is down for long. This is capped by
   `max_slot_wal_keep_size`; past the cap, B needs a full rebuild. The alert
   fires as soon as the slot goes idle.
4. **A bad CPU can corrupt pages, and physical replication copies them**
   (full-page images). B is not a backup.
   - Keep `backup_db.sh` and its off-server copy.
   - The base backup's checksum pass is a free integrity scan of A. If it
     names a damaged page, stop and look before going further.
5. **Settings drift.** Raising `max_connections` and similar settings on A
   pauses B until `standby_build.sh --settings` is run. Other work on
   Postgres settings is going on now, so run `--settings` after it lands.
6. **Connections on A.** B's site opens connections to A only for writes
   (`XCP_DB_MIN_CONN=0`). A's own site still holds its 24 idle connections,
   and the pipeline uses more during its runs. `primary_prepare.sh` prints
   the current count. If A runs near `max_connections` during a pipeline,
   raise it on both boxes (B first).
7. **Unlogged and temp tables are not readable on B.** Reads of them go to
   the primary automatically. `primary_prepare.sh` lists the unlogged ones;
   all are pipeline scratch tables today, and the live tables are
   `SET LOGGED` before go-live. The TF venue page's temp table keeps that
   one page on the primary.
8. **Files outside the database** are copied every 10 minutes, so a deploy
   or a pipeline's `engine/data` reaches B up to 10 minutes later. Photos
   uploaded on B are pushed back to A.
9. **Writes from B cross the tunnel.** In the same datacenter that adds
   under 1 ms per statement. Across datacenters (Virginia to Oregon) it adds
   ~70 ms per statement, so a sign-in (~10 statements) takes ~0.7 s. Prefer
   the same datacenter.
10. **The tunnel goes down.** B stops receiving WAL and its writes fail,
    while its reads go on from a frozen copy. Past 300 s of lag its reads go
    to A, if A is reachable. The alert fires after 5 minutes.
11. **New code on the hot path.** `read_routing` only runs where
    `XCP_DB_READ_DSN` is set, which is on the standby's site and nowhere
    else. `site_role.sh remote`, or putting A's pool first, turns it off in
    seconds.
12. **B's hardware is new and unproven.** Check `dmesg | grep -iE 'edac|mce'`
    after the 600 GB copy; it is a good load test.
13. **Postgres minor upgrades**: upgrade the standby first, then the
    primary. A major upgrade means `pg_upgrade` on the primary and
    `standby_build.sh --rebuild` on the standby.

---

## Files

| File | What |
|---|---|
| `scripts/read_routing.py` | the routed connection: classifier, read pool, lag and down tracking |
| `scripts/database.py` | `getConn()` dispatches; the old body is `_primaryConn()` (additive, ~20 lines) |
| `tests/test_read_routing.py` | reads to the read pool, writes and the rest of a write's block to the primary, `SET LOCAL` replay, refusals re-run, POST and signed-in on the primary, either side down, lag, `db_timing`'s per-request connection, the classifier, default unchanged |
| `tests/test_replica_scripts.py` | scripts parse; Postgres only on the tunnel; replay delay > statement timeout; no password on a command line; the role file wins over the env file |
| `deploy/replica/replica.env.example` | `/etc/racecast-replica.env` on both boxes |
| `deploy/replica/common.sh` | shared helpers; the replication settings (one file, both roles) |
| `deploy/replica/wg_setup.sh` | the tunnel and firewall |
| `deploy/replica/primary_prepare.sh` | role, pg_hba, conf.d, reload or restart, report |
| `deploy/replica/standby_build.sh` | base backup, `--rewind`, `--rebuild`, `--settings` |
| `deploy/replica/standby_site.sh` | the second site |
| `deploy/replica/site_role.sh` | `standby` / `primary` / `remote` database settings for this box's site |
| `deploy/replica/sync_from_primary.sh` | code and files every 10 minutes |
| `deploy/replica/origin_health.py` | `--serve` (Cloudflare monitor), `--report`, `--alert`, `--wait-caught-up` |
| `deploy/replica/install_services.sh` | the health service and the alert and sync timers |
| `deploy/replica/switchover_stop_primary.sh`, `promote.sh`, `fence.sh` | switchover and failover |
