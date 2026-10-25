# Runbook: timing-company results and the HS TFRRS sites (2026-10-25)

Background is in `docs/PLAN-MULTISOURCE.md` ("Built 2026-10-25"). Every step is a dry run until you add `--write` or `--apply`. Run all of them in tmux (`tmux new -s timers`).

Every block starts with the same line:

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
```

## A. The HS TFRRS sites (fl.tfrrs.org, indiana.tfrrs.org)

**A1. Read both sites' meet lists.** This is network only and writes no database rows. It is about 380 pages, one request every 10 s, so about an hour.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
$P -u -m sources.tfrrs_hs index --out logs/hs_index.csv 2>&1 | tee logs/hs_index.out
```

**A2. What we hold (read only).** Per site, sport and year it shows meets, meets with rows, empty meets, missing meets, rows, linked rows, minted rows and rows on an athletic.net career.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
$P -m sources.tfrrs_hs check --index logs/hs_index.csv | tee logs/hs_check_before.txt
```

**A3. Queue the meets with no rows** (dry run first). `--apply` also writes the list to `tfrrs_hs_meets`.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
$P -m sources.tfrrs_hs queue --index logs/hs_index.csv
$P -m sources.tfrrs_hs queue --index logs/hs_index.csv --apply
```

**A4. Scrape just those** with the existing TFRRS driver at the nightly pace. That pace is about 15 meets a minute, so 1,000 meets take about 70 minutes. ⚠ Finish before 06:00 UTC (apt and Postgres restarts) and before the nightly run at 01:17 Pacific. The `timeout` below stops it at 3 hours. Once the queue is empty the driver walks a short way past the newest id (`TFRRS_SEED_AHEAD=50`) and stops.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
TFRRS_NO_SEED=1 TFRRS_SEED_AHEAD=50 TFRRS_SESSIONS=2 TFRRS_PER_MEET_DELAY=5,10 NO_VPN=1 timeout 180m xvfb-run -a $P -u tfrrs/driver/launch_tfrrs.py 2>&1 | tee logs/hs_scrape.out
```

**A5. Link the HS rows to athletic.net careers** (dry run, then apply). This is pass 2b of 04a. With no `--days` it judges every HS identity.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
$P -u scripts/link_tfrrs_hs.py --show 40 | tee logs/hs_link_dry.txt
$P -u scripts/link_tfrrs_hs.py --apply | tee logs/hs_link.txt
$P -u scripts/link_tfrrs_rows.py --apply | tee logs/hs_link_rows.txt     # fan-out + mint for the rest
$P -m sources.tfrrs_hs check --index logs/hs_index.csv | tee logs/hs_check_after.txt
```

`$P scripts/link_tfrrs_hs.py --undo` takes back every `tfrrs_hs` move.

From now on the nightly run does this by itself. Step `01_tfrrs_hs` reads the 3 newest index pages per site before the TFRRS scrape, and 04a pass 2b links whatever raced in the last 14 days. Set `XCP_NIGHTLY_TFRRS_HS=0` or `XCP_LINK_TFRRS_HS=0` to turn them off.

## B. Timing-company meets: land, dedup, promote, link

**B1. Fetch and parse into a file** (no database). This example uses one YenTiming meet directory and one B&B MeetPro meet.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
$P -m sources.timers fetch --source yentiming-services --url "https://results.yentiming.com/2026/cross/09-12-26-Wayne-Invit/" --out logs/landed_yen.jsonl
$P -m sources.timers fetch --source b-b-race-kc --url "http://results.tfmeetpro.com/B__B_Race_KC/2A_Region_20_Winchester_Jefferson_County_North/" --out logs/landed_mp.jsonl
```

To fetch many meets of one timer, put the urls in a file and pass `--urls FILE`.

**B2. Land, judge, promote, link.** Each command is shown dry first, then for real.

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
for f in logs/landed_yen.jsonl logs/landed_mp.jsonl; do $P -m sources.timers land --from $f --write; done
$P -m sources.timers dedup && $P -m sources.timers dedup --write
$P -m sources.timers promote && $P -m sources.timers promote --apply
$P scripts/link_uploads.py --source timer && $P scripts/link_uploads.py --source timer --apply
$P -m sources.timers status
```

`status` shows meets per source and status, promoted rows and how many have a person, and twin races where the timer's distance and the feed's differ by more than 3%. `$P scripts/link_uploads.py --source timer --undo all` takes the links back.
