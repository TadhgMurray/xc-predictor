# Runbook — what to run, in what order, and what can run together

The owner's plan for tonight: **school scrape → rescrape anet/tfrrs → relaunch
engine (with the new ideas) → extraction → train.**

This is the ordering with the dependencies made explicit, because several steps
look independent and are not, and two pairs really can run at once.

## What actually blocks what

    anet_teams --unfetched  (RUNNING)
        |
        +-- build_team_identity ----+
        |                           +-- team-keyed crests (Phase 3, NOT BUILT)
        +-- build_team_pool --------+
        |
        +-- link_tfrrs_to_anet ---------- pooling rule 3, name+state splits

    meet rescrape (anet + tfrrs)  -- INDEPENDENT of all of the above
        |
        +-- engine relaunch
                |
                +-- extraction -> train

**Safe to run at the same time:** the meet rescrape and the team work touch
different tables (`meet_queue`/`results*` vs `anet_team`/`school_logo`) and
different hosts are irrelevant — but **both hit athletic.net**, so running them
together doubles your request rate against the one site that can block you.
That is the real constraint, not CPU.

**Never at the same time:** two things that write `results*`. The engine
relaunch rebuilds ratings over 34M + 27M rows; a rescrape writing into the same
tables while it runs will give you a rating built on half-written data.

## Tonight, in order

### 0. RUNNING — the school/team scrape (~11h)

    scripts/anet_teams.py --unfetched --no-logos --no-core --sports xc,tf \
        --rate 1.0 --write

20,344 teams with no metadata. Leave it. Everything in section 1 waits on it.

⚠⚠ **A READ-ONLY DIAGNOSTIC CAN STILL TAKE THE BOX DOWN, and one did**
   (2026-09-18): `diag_indoor_level` filled the server's temp space mid-scrape
   with `DiskFull: could not write to file base/pgsql_tmp/...`. Postgres frees
   the temp files when the query aborts, so it was transient — but the scraper
   could as easily have hit the wall first and died.

   Every heavy diagnostic now opens with `scripts/pg_guard.guard(cur)`, which
   sets `temp_file_limit`, `statement_timeout` and `work_mem` **on that
   connection only**. A runaway query now aborts itself instead of the disk.
   Override with `XCP_DIAG_TEMP_LIMIT`, `XCP_DIAG_TIMEOUT`, `XCP_DIAG_WORK_MEM`.

   Before starting any of them while a scrape runs: `df -h`.

**While it runs, these are safe (read-only, no anet):**

    scripts/diag_indoor_level.py                       # the indoor number
                                                       # (defaults to 2021+;
                                                       #  2015+ is what filled
                                                       #  the disk)
    engine/diag_difficulty_calibration.py --since 2020 --sport XC
    engine/rating_outliers.py --dry-run --since 2020 --sigma 5,8,10,15
    scripts/queue_status.py --sample 10

One at a time — they are big scans and compete with the scraper for disk.

### 1. After the scrape — the team rekey

    racecast/build_team_identity.py --dry-run     then --write
    engine/build_team_pool.py --dry-run --show 40 then --write
    scripts/link_tfrrs_to_anet.py                then --write

`build_team_identity`'s report says how many teams still lack metadata. If that
is not ~0, section 0 did not finish.

⚠ **`build_team_pool` and `link_tfrrs_to_anet` can run concurrently** — one
reads `anet_team` + `results*`, the other reads `results*` and writes
`school_team_link`. Both are long scans, so expect each to take longer than
alone; do it only if wall-clock matters more than throughput.

Then **check the sections/divisions**, which the owner flagged as "generally
right but some are fucked up" — `scripts/anet_units.py --report` is what the
anet_teams run keeps pointing at, and it now has 20,344 more teams' worth of
`anet_division` rows to learn from than it did.

### 2. The meet rescrape

    # 143 known-broken meets first -- minutes
    NO_VPN=1 ANET_RETRY_FAILED=1 xvfb-run -a scripts/launcher.py
    NO_VPN=1 TFRRS_RETRY_FAILED=1 xvfb-run -a tfrrs/driver/launch_tfrrs.py

    # then the 5,420 genuinely-due ids
    NO_VPN=1 PER_MEET_DELAY=3,6 xvfb-run -a scripts/launcher.py
    NO_VPN=1 xvfb-run -a tfrrs/driver/launch_tfrrs.py

**anet and tfrrs CAN run together** — separate hosts, separate queue rows,
separate id spaces. That pair is the one genuinely free parallelism here, and
it was designed for it.

The owner's two acceptance checks for this step:

- **TF venue names must land**, so a venue is never labelled with its id.
  `database.backfillMeetsTFVenueNames` is the backfill; run it after and
  confirm the count of `meets_tf` rows with a `venue_name` went up.
- **tfrrs must parse without column shifts.** `tfrrs/parser/column_map.py` is
  where a shift would come from. Worth a spot check of a few parsed meets
  against their pages before trusting 33M rows of it.

⚠ Do NOT start the engine relaunch until both feeds are finished writing.

### 3. Engine relaunch

Order chosen so the measurements come before the changes they would justify:

1. `engine/diag_difficulty_calibration.py` — is difficulty calibrated or
   overfed? Flat columns = calibrated. **Everything else here depends on this.**
2. `scripts/diag_indoor_level.py` — the size of the indoor bias, within
   athlete-years.
3. Then the changes: the indoor gauge (pin `(sport, surface, era)` instead of
   `(sport, era)`), the 5k anchor, the same-day/same-time XC-into-TF dedup.
4. `engine/rating_outliers.py --write` and the pool tables, then the corrections
   cull (`scripts/wipe_overrides.py`) LAST, after the rules that supersede the
   hand-patches are in — otherwise the rebuild regenerates the same overrides
   from the same unfixed inputs.
5. `scripts/bracket_holdout.py` before and after every change. A change that
   makes the boards look better and the held-out error worse has learnt the
   story, not the sport.

### 4. Extraction → train

The owner's own diagnosis is "the model just sucks dick rn", with three
concrete symptoms worth treating as tests rather than opinions:

- a 9:00 3200m runner becoming a 31:00 8k runner on their first collegiate race;
- a 24-minute race winner predicted at 27;
- college 5k "way off".

All three are **distance-transfer** failures, and all three point at the same
place: whatever maps a performance across distances is wrong in the tails, not
the model's capacity. That is measurable before retraining — take athletes with
races at two distances in one season and plot predicted-vs-actual by distance
ratio. If the error grows with the ratio, the transfer curve is the bug and
training a bigger model on it will fit the same error.

Also on the owner's list and unaddressed: **school labelling for extraction
should be `(name, state, id)`**, which is exactly what `team_identity` now
provides — so section 1 should land before extraction, not after.

## Parallelism, summarised

| together | why |
|---|---|
| anet meet scrape + tfrrs meet scrape | different hosts, different queue rows, designed for it |
| any one read-only diagnostic + a scrape | no write contention; one at a time |
| `build_team_pool` + `link_tfrrs_to_anet` | different writes; both slower, but safe |

| NEVER together | why |
|---|---|
| team scrape + meet scrape | both hammer athletic.net; that is what gets you blocked |
| engine relaunch + any rescrape | ratings built on half-written `results*` |
| two things writing `results*` | obvious, and the most expensive to discover late |
