# Margin of error of an incremental 10_rankings (2026-11-20)

The owner asked: what is the margin of error if the rankings rebuild only
what changed? Today `racecast/build_ranking_results.py` rebuilds
`ranking_results` (~158M rows) and `athlete_season` (~26M) from scratch on
every run: prepare, 10 streams, then finish, about 40-50 min.

This is an analysis. Nothing in production changes. The measuring tool is
`scripts/measure_rank_churn.py`, which is read-only (see section 6).

## 1. Short answer

1. **The solve does not run every night.** The joint solve runs only in a
   full pipeline run (`deploy/run_pipeline.sh`, including `--fast` and
   `--test`). The nightly (`deploy/nightly_update.sh`) runs no solve. It
   prices new rows at their course against the last full run's numbers
   (`fill_ratings --venue`). It also re-prices a few known sets: rows priced
   off scale in the last 150 days (`--recheck-since`), races since the last
   solve (`provisional_day`), two copies of one race (`copy_align`), the
   field guard, stragglers, and divisions whose distance pin moved
   (`wrong_distance --refresh`). Both kinds of run then rebuild 10_rankings
   from scratch.
2. **After a full run, expect nearly every rated row's stored rating to
   change.** Ratings are stored rounded to 0.01 (`np.round(chosen, 2)` in
   `joint_golive.buildLive`). The solve starts cold every run, and every
   cell's shrinkage depends on the whole graph: its robust weights, its
   posterior variances (Hutchinson probes over a vector whose length is the
   number of cells), and the free pool levels `mu`. One new race moves the
   levels, and the levels move everyone. Incremental at threshold 0 buys
   nothing here. At threshold θ it buys something only if the measured
   share of rows that moved at least θ is small (section 5). **This has not
   been measured on production.** The script measures it.
3. **After a nightly, few old rows change**: new rows, the re-priced sets
   in point 1, and rows whose non-rating inputs changed (links, merges,
   twins, flags, pins, overrides). An exact incremental build (θ = 0) fits
   here. It has **no rating error by construction**. Its risk is a missed
   input (section 4d), not a margin.
4. **With a threshold θ** (rows that moved less than θ are left stale), the
   error against a full rebuild is bounded:
   - a stale row's rating is off by less than θ;
   - a season's mean, decayed and best ratings are off by less than θ;
   - a rank can be wrong among entries whose values sit within θ of each
     other.

   Three things break these bounds: the 20-point season outlier rule, the
   pool ceiling rail, and a uniform per-pool shift (section 5).

## 2. What each column is computed from

The classes are:

- **(a) per-row:** the result's own row plus fixed lookups.
- **(b) per-athlete:** an aggregate over the athlete's own rows (season or
  career), or a per-person flag table.
- **(c) global or relative:** depends on other athletes or on corpus-wide
  derived tables.
- **(d) the solve:** written by the go-live.

A column can depend on several classes. In the "Class" column below, the
first class is the main one.

### Which rows exist at all (the WHERE in `_SQL` and the drops in `prepareRow`)

| Gate | Source | Class |
|---|---|---|
| `speed_rating IS NOT NULL` (TF: or a sprint or field row) | results.speed_rating | d |
| not a twin | `result_twin` (cross-feed duplicate detection) | c |
| not an impossible race | `impossible_result`: one impossible row condemns its whole race | c |
| not a rating outlier | `rating_outlier`: each person's K nearest races, with a corpus-wide floor, K and cut | b, c |
| not a chair athlete or chair race | `wheelchair_person`, `wheelchair_result` | b |
| non-school, DoDEA, foreign | panels / pool_resolve name lists | a |
| pool resolvable and rankable | `rating_pool` (go-live), else `resolvePool` over grade_fix, athlete_season_level, pro_athlete_season, college_first_season, person_gender | d, b |
| `grade_trust = 'low'` | grade_fix | b |
| normalized_time inside the pool band | `poolBandFor` (constants) | a |
| corrected division, HS XC over 5250 m, no distance | dist_override (built from a division's field), the event name | a, c |
| anchor mismatch, impossible pace | `anchor_check`, `record_pace` (constants) | a |
| rating inside 20..200 and at or below `raceCeiling(pool)` (ceiling + RACE_MARGIN 10) | the rating against pool constants | d |

### ranking_results columns

| Column | Computed from | Class |
|---|---|---|
| sport, result_id | key | a |
| person_id | results.person_id after links, merges and splits (04a*) | a, but a merge re-keys two people's rows |
| pool | `rating_pool` from the go-live, else the resolver above | d, b |
| speed_rating | results.speed_rating | d |
| race_date | results.date | a |
| year | `seasonYearForPerson`: the date, plus pro_athlete_season's pro summer | a, b |
| state | `_homeStateOf`: the meet's state, unless school_identity (rebuilt at 10b from every row's votes) or the college directory says otherwise | a, c |
| school | `canonicalSchool(pinnedSchool(...) or school)`: school_pin is per person; school_name_alias is rebuilt by 09c from name clusters | a, b, c |
| grade | results.grade | a |
| meet_id, div_id, canon_meet_id, time_seconds, event_id, mark, event_kind | the row, the event name, `marks` | a |
| distance | dist_override (per division, set from the division's field by wrong_distance and dump_overrides), meets, the tfrrs blob, the event name | a, c |
| division ... section (11 unit columns) | school_unit by (school, home state) | a |

### athlete_season columns (`_ATHLETE_SEASON_SQL`, then `_stampSeasonUnits` and `_homeStates`)

| Column | Computed from | Class |
|---|---|---|
| person_id, pool, sport, year | the group key | b |
| mean_rating | 80th percentile (type 8) of the season's rated rows, leaving out rows more than 20 points under the season median | b, discontinuous |
| decayed_rating | weighted mean, weight 0.996^(2100-01-01 − date), same row set | b, discontinuous |
| best_rating, n_races, first_race, last_race | max, count, min and max over the same set | b |
| state | the career's most-raced state over all school-age rows (the `home` CTE), else the mode; then `_homeStates` against school_identity | b over the career, c |
| school, grade | filtered modes over the season | b |
| unit columns | school_unit by the season's school and state | a after b |

### Built from these tables (relative, class c)

These are recomputed from the two tables. None of them is a column of the
two tables.

- Ranks and percentiles, computed at query time.
- `10g_season_ranks`.
- `board_size`: the length of every default board.
- `board_seek`: every 1000th row of every default board, so every position
  after a changed row shifts.
- The Events presets and the search.

The HS-equivalent factor (`pool_view.toHs`) comes from the go-live and is
applied when a page is read. It is class d, but it is not stored in either
table, so an incremental build does not change it.

## 3. How much changes, and what feeds each change

| Event | speed_rating | Other inputs |
|---|---|---|
| Full run (solve + go-live) | ~all rated rows at the 0.01 storage step (expected, not measured) | pools re-decided, flag tables rebuilt, school_identity and aliases rebuilt |
| Nightly | new rows; re-priced sets (recheck 150 d, provisional_day, copy_align, field_guard, stragglers, wrong-distance refresh) | links, merges, twins, flags of new rows, pins, dist_override |

A row-level change detector would have to watch every table in section 2.
Some of those tables are rebuilt wholesale every run (rating_outlier,
impossible_result, result_twin, school_identity, school_name_alias,
athlete_season_level, grade_fix). Each needs a diff against its previous
copy, or a change log written by its own builder.

## 4. The scheme and its margin of error

The scheme has four steps:

1. Rebuild a row of ranking_results when any of these hold:
   - it is new;
   - its non-rating inputs changed;
   - its pool changed;
   - its rating moved at least θ.
2. Rebuild every row of a person who has a new, removed or re-keyed row.
3. Recompute every season that holds a rebuilt row.
4. Recompute the relative outputs.

Against a full rebuild, these outputs differ:

a. **ranking_results.speed_rating of stale rows:** |error| < θ, exactly.
   The printed rating (`rv`, "%.1f") differs for about E[|Δ|]/0.1 of the
   stale rows. Small moves still flip a printed tenth. The script prints
   this count as "stale tenth".

b. **Which rows exist:** a row whose new rating crossed `raceCeiling(pool)`
   or the 20/200 rail by less than θ is wrongly in or out of the boards.
   The script bounds this with the count of stale rows within θ of the
   ceiling ("near rail").

c. **athlete_season mean, decayed and best:** |error| < θ while the season
   keeps the same row set. The type-8 quantile is a convex combination of
   two order statistics, the decayed rating is a weighted mean, and the best
   is a max, so none of them moves more than its rows do. Where a row sits
   within θ of (season median − 20), the kept set changes. n_races then
   changes by one and the mean can jump by points. The script prints this
   as "n_races". "mean tenth" counts seasons whose printed mean differs.

d. **Non-rating columns:** zero error, if the change detector sees every
   input in section 2. A missed input gives an unbounded error: a wrong
   school or pool, or a twin that should have left the boards. This, not
   θ, is the real risk of an incremental build.

e. **Ranks and board positions:** two entries whose full-rebuild values
   are within θ (2θ when both are stale) can trade places. The error in
   positions is about θ times the local density of the board. That is near
   zero at the top of a board and can be thousands deep in hs_m, where a
   tenth of a point holds thousands of seasons. The script prints
   - an estimated rank shift over the sample;
   - the exact wrong positions and membership changes in the top 25 and
     top 100 of every default board, from the candidates' own rows.

f. **A uniform shift:** if a full run moves a pool's whole scale by c with
   |c| < θ (the pool level `mu` is a free parameter), every stale row lags
   by c while rebuilt rows do not. New races then sit c above or below old
   ones on the same board. The script prints this per pool as the "med d"
   column (the median signed move). Any |med d| that is not small rules out
   θ > 0 after a full run.

g. **board_size, board_seek, season ranks, percentiles:** recomputed, so
   they inherit b, c and e.

Sections b, c (the n_races jumps) and f are where the bounds stop being
tight. With θ = 0 every one of a-c and e-g is zero, and only d remains.

## 5. Speed-up estimate

Phase costs come from the code's own notes (runs 20261116_204106 and
20261117_142219):

- streams: 10 × ~1,046 s side by side, ~17.5 min wall;
- finish: 31.2 min, of which:
  - the ranking_results indexes take 20.5 min;
  - the season shards take ~7 min;
  - the season units take ~6 min;
  - the ordered or logged rewrite takes ~3 min.

**The build's cost tracks the size of the table, not the number of changed
rows.** A shadow table plus a swap builds every index from scratch whatever
changed. An incremental build has to change the live tables in place:
DELETE plus INSERT of the changed rows, with ~25 btrees on ranking_results
kept up by row inserts. The repository has measured that trade once.
`speed_ratings_db` writes a 39M-row table by heap rebuild in 122 s against
75-100 min by UPDATE, so in place costs about 150 µs a row with indexes.
For a changed share f of ~158M rows:

| f | in-place row work (est.) | plus finding the change set | against today's ~45 min |
|---|---|---|---|
| 0.5% | ~2 min | ~3-6 min (one join over the table), ~0 with a change log | 5-8 min, **~6x** |
| 2% | ~8 min | same | 10-14 min, **~3-4x** |
| 5% | ~20 min | same | ~25 min, ~2x |
| ≥ 10% | ≥ 40 min | same | **no gain**, plus bloat, vacuum and lost board order |

These are estimates, not measurements. The table also costs things that do
not show in the timing:

- In-place writes undo `XCP_RR_ORDERED`'s board-order layout and the
  fillfactor-100 trees (page splits).
- They need autovacuum on tables that are built with it off.
- They take row locks on live tables the site reads, against the
  shadow-and-rename design the owner keeps for site latency.

The break-even is near f ≈ 5-8%.

**Recommendation:**

- Keep the full rebuild after a full run.
- Consider an exact (θ = 0) incremental build after nightlies only, and
  only once the script has measured f there. Two numbers decide it:
  - f on a nightly, from `--save` and `--against` on two nightlies;
  - for a full run, the share of rows that moved at least 0.05 or 0.1, and
    the per-pool "med d".
- An incremental build should come with a periodic full rebuild: at least
  every full run, to restore the layout and catch any missed input.

## 6. The measurement: scripts/measure_rank_churn.py

To run it:

```
( set -a; . /etc/xc-predictor.env; set +a; /srv/venv/bin/python -u scripts/measure_rank_churn.py )
# night to night, exact for every column:
( set -a; . /etc/xc-predictor.env; set +a; /srv/venv/bin/python -u scripts/measure_rank_churn.py \
    --save /srv/xc-predictor/logs/churn_$(date +%F).tsv.gz )
#   ... the next night, after its run:
( set -a; . /etc/xc-predictor.env; set +a; /srv/venv/bin/python -u scripts/measure_rank_churn.py \
    --against /srv/xc-predictor/logs/churn_<previous>.tsv.gz --save /srv/xc-predictor/logs/churn_$(date +%F).tsv.gz )
```

**Baselines.** The script uses the first one it finds:

1. `--against FILE` (its own snapshot).
2. The go-live's `results(_tf)_speed_rating_backup`.
3. `run_prev_athlete` (this season's means at the start of the last full
   run, from `run_changes --snapshot`).

The go-live backup is **kept when present and not refreshed**
(`backupSpeedRatings(force=False)`), so it can be weeks old. The script
prints the newest result the backup holds and how many results came after
it. For a clean night-to-night number, use `--save` and `--against`.

**What it prints:**

- table sizes and the age of each baseline;
- speed_rating churn per sport and pool: new, same, moved by at least
  0.005, 0.05, 0.1, 0.5 and 2, printed-tenth flips, the median signed move,
  and the p50, p90 and p99 of |move|;
- with a snapshot, the other columns: person re-linked, pool, the
  school/state/grade/units hash, the date/race/time/distance hash, and rows
  gone;
- the simulated scheme for θ = 0.05, 0.1, 0.25 and 0.5: rows rebuilt,
  seasons touched, stale tenth flips, the season error p50, p99 and max,
  mean tenth flips, n_races flips, the largest error in best, rows near the
  rail, and the estimated rank shift;
- athlete_season churn per column, and a **check that the script's season
  rule reproduces the live means**, so the simulation is trustworthy;
- top-25 and top-100 churn of every default board (performance and
  ability, 6 pools × 2 sports × all-time and this season);
- the exact top-of-board error of the scheme for each θ;
- the build's input tables: estimated rows, rows then, and rows stamped
  since the baseline.

**Sampling.** The sample is every person with `person_id % 100 = 0` (all
of their rows), the same people every night. Counts are printed as sampled;
multiply them by the rate for the whole table. `--sample 500` makes the
script lighter.

**Locks and limits.**

- Each section is one read-only transaction (`default_transaction_read_only`),
  committed before the next section starts, and runs only SELECTs, which
  take AccessShare locks.
- `statement_timeout` is 10 min and `lock_timeout` is 5 s.
- The script creates nothing in the database. `--save` writes only the
  local file.

The heaviest statement is the sampled scan of ranking_results joined to the
backup, an estimated 1-4 min. **Run the script after a run has finished,
not while 10_rankings_finish is about to swap.** A swap's rename waits
behind an open statement and has 20 tries of 3 s each.

**Checked on a scratch database only** (Postgres 16, a fake schema of
54k rows; athlete_season built with the builder's own `seasonQuantileSql`).
Both modes run, and the season-rule check matched the builder's means 3,066
of 3,066. The script created no table. The scratch numbers are synthetic
and say nothing about production.
