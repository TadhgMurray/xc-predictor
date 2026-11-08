# Table layout experiments, 2026-11-08

Can `ranking_results` and `athlete_season` be laid out differently on disk,
so that the boards read fewer pages and the pipeline's rebuild writes less,
with every query returning exactly what it returns now?

Short answer: yes, and in three tiers.

1. **Lean indexes and columns.** Btree fillfactor 100, partial rating
   indexes, one redundant index retired, and the columns reordered. This is a
   build-only change. Result: indexes 20% smaller, heap 6% smaller, index
   build CPU 13-23% lower, no query changes, no board regressions.
2. **A board-ordered heap.** The `SET LOGGED` rewrite the build already does
   becomes an ordered `INSERT ... ORDER BY pool, sport, year, speed_rating
   DESC`. Boards read 2.2x fewer buffers and run 3.2x faster; filtered counts
   read 2.1x fewer buffers and run 1.8x faster. The extra sort costs about 24
   s in the lab, and sorted input makes the index builds 25-35% cheaper.
3. **LIST partitioning by pool** (on top of 2). Counts read 3.8x fewer
   buffers, and an unchanged pool partition can be kept instead of rebuilt.
   The cost: about 0.3 ms more planning on every point lookup, 7x the index
   objects, and real changes to the swap, verify and index-naming code.

Year partitioning, person-ordered heaps, BRIN and narrower column types are
not worth it here (sections a-c say why).

Every number below comes from `scripts/layout_lab.py`, run on the scratch
Postgres 16 with synthetic data. No production rows were used. The SQL is
what `rankings.py` sends, captured from the real functions. All 44
statements returned **the same rows in the same order** on every layout
(md5 over every result row).

## Method

`scripts/layout_lab.py` (re-runnable; its docstring has the commands):

* **`gen`** builds the synthetic corpus: 490k people, 6,013,536 result rows,
  1,340,933 athlete-seasons (4.5 rows per season; production is 61.6M / 12.9M
  = 4.8).
  * Columns and types are those of build_ranking_results._COLUMNS, in the
    live physical order.
  * Pools: hs 57%, ms 17%, college 17%, pro/elem 9% of rows.
  * Sports: XC 60%, TF 40%.
  * Years 2005-2026, growing toward the present (2025+2026 = 15% of rows).
  * Ratings normal per pool.
  * Distances clustered at the standard events, with surveyed odd values
    (4999.9, 8046.72).
  * States population-skewed, plus 1% foreign codes.
  * College rows split between the two feeds; the negative ids carry no
    state.
  * Sprints have a time and no rating (18.9% of rows unrated); field events
    have a mark and no time.
  * Unit columns are stamped per school.
  * **The heap order is the build's**: one sport, then the other, rows of a
    meet together, meets in scrape (random) order.
  * `athlete_season` is built by the build's own `_ATHLETE_SEASON_SQL`
    (imported, not copied).
  * The indexes are the deduplicated `_CANONICAL_INDEXES`: 16 on
    ranking_results, 10 on athlete_season.
* **`queries`** calls `rankings.getPerformanceRankings` /
  `getAbilityRankings` / `getPrRankings` / `countRows` for 21 boards, through
  a recording cursor. It adds 7 page lookups:
  * athlete rows;
  * record-badge cover;
  * race-page `stampRowsHs` by `result_id = ANY`;
  * school page;
  * roster count;
  * athlete seasons;
  * school roster.

  That is 44 statements in total.
* **`variant <layout>`** builds the layout in its own schema, timing the
  load, every index, ANALYZE and VACUUM.
* **`bench base <layout>`** runs each statement with `search_path =
  lab_<layout>, public` (byte-identical SQL) and records three things:
  * EXPLAIN (ANALYZE, BUFFERS): the median of 3 after a warm-up, with the
    top node's shared hit + read as "buffers";
  * planning time;
  * md5 of the full result set.
* **`build <layout> --jobs 3`** times the pipeline's finish step for
  ranking_results as wall time, with 3 index builds at a time like
  `_INDEX_JOBS`.
* **`partial`** rebuilds one partition and swaps it in.

**The box is shared and noisy.** 4 cores, 15 GB RAM, `shared_buffers`
128 MB, and other agents' work on the same disk. **Buffers are the stable
metric.** Times are always compared with the base run of the same
invocation.

**Scaling to production** (61.6M rows, about 10.2x): index sizes and build
work scale with rows.

* A board's top-N stage does not scale with rows: it reads `cand =
  (limit+offset)*40` rows whatever the table size.
* Counts and the scans that misplan (R3, R4, R6, P4 below) scale with rows.
* So production buffers for those are about 10x the lab's, and the savings
  scale with them.

## Baseline (the live layout)

ranking_results: heap 1005 MB, 16 indexes 2702 MB. athlete_season: heap 185
MB, 10 indexes 530 MB.

Build, serial: RR indexes 120 s, season aggregate 148 s, AS indexes 26 s.
With 3 jobs (`build`): load 3.4 s, SET LOGGED 9.6-9.9 s, index wall 64-68 s
(CPU 184-199 s), total 78-82 s.

Which indexes the workload touched (`bench --index-use`, base):

| index | MB | scans | note |
|---|---:|---:|---|
| rr_pr_sport_time_idx | 195 | 145 | PR per-distance walk |
| rr_perf_dist_rating_idx | 202 | 90 | |
| rr_pool_rating_idx | 183 | 70 | |
| rr_result_idx | 129 | 55 | race pages |
| rr_board_rating_idx | 204 | 40 | |
| rr_pool_sport_rating_idx | 191 | 35 | |
| idx_rr_person_cover | 337 | 25 | athlete pages, badges |
| rr_board_mark_idx | 11 | 10 | |
| rr_pool_year_dist_idx | 192 | 5 | events window |
| rr_school_season_idx | 233 | 5 | |
| rr_school_sport_idx | 43 | 5 | the school page used this, then sorted |
| **rr_school_idx** | **305** | **0** | |
| **rr_board_time_idx** | **197** | **0** | |
| **rr_pr_time_idx** | **190** | **0** | (pool=all & sport=both PR boards only) |
| rr_person_idx | 49 | 0 | the cover index answers it |
| idx_rr_school | 43 | 0 | build_school_identity reads it |
| as_pool_best_idx | 54 | 0 | |

The workload is only boards plus seven page shapes. **The decision to
retire an index belongs to production's `pg_stat_user_indexes`**, which
`scripts/layout_probe.py` prints. The three bold trees are 692 MB in the
lab, about 7 GB in production: a week at zero scans there retires them.

## (a) Partitioning

Layouts:

* **part**: LIST (pool), with the six board pools plus a DEFAULT (elem/pro).
* **partyear**: LIST (pool), each sub-partitioned by RANGE (year) into old
  (< 2025) and current.
* **partclus**: part, with each leaf in board order (see b).

Buffers summed over the workload; ms is the variant against the same run's
base. All results were identical to base.

| layout | boards: buffers | boards: ms | counts: buffers | counts: ms | page lookups: buffers | page lookups: ms |
|---|---:|---:|---:|---:|---:|---:|
| base | 354,236 | - | 485,512 | - | 524 | 0.7 |
| part | 236,498 (-33%) | 1872 vs 4680 | 172,190 (-65%) | 1726 vs 5159 | 898 | 4.0 |
| partyear | 230,232 (-35%) | 1639 vs 4106 | 166,237 (-66%) | 1599 vs 4477 | 1154 | 9.0 |
| partclus | 154,271 (-56%) | 687 vs 2000 | 126,186 (-74%) | 822 vs 2220 | 1062 | 4.0 |

Per query (buffers, base -> part -> partclus):

* P1c count hs_m XC: 128,670 -> 37,766 -> 37,771 (one leaf's heap instead
  of the table)
* P4 perf hs_m TF 1600: 34,283 -> 12,894 -> 9,735
* R3 pr all TF 800 m: 178,097 -> 149,768 -> 105,449 (1.7 s -> 0.5 s ->
  0.08 s)
* R4 pr college_m shot_put: 22,502 -> 2,563 -> 1,207
* R6 pr college_f XC 6000 offset 100: 45,162 -> 1,318 -> 1,283 (650 ms ->
  17 ms in the part run; 342 ms -> 6 ms in the partclus run)
* A3c / A7c counts: 19,600 -> 2,430 -> 1,650
* X3 race page (`result_id = ANY`): 34 -> 233 -> 242 (0.06 ms -> 0.5 ms)

**Why the boards gain.** Pruning is only part of it. The rest is
statistics. A pool's leaf has its own MCVs and histograms, so
`distance = 6000` on college_f is estimated as the common value it is
there. On the single table the planner picked a bitmap heap scan of all
186k college_f XC 6000 rows plus a sort (R6), where the leaf's index walk
reads 1,300 buffers. The `xstats` layout below separates the two effects.

**Why page lookups lose.** `person_id = X` or `result_id = ANY(...)` cannot
prune: it probes every leaf's index (7 leaves; 13 for partyear). The
buffers are small, but planning goes from 0.03-0.06 ms to 0.27-0.49 ms per
statement (part), and to 0.4-1.7 ms for partyear. psycopg2 sends no
prepared statements, so every page pays it. Absolute cost: about 0.3 ms per
lookup, against pages served from Cloudflare's cache on a hit.

**Build** (ranking_results only, 3 jobs, two runs):

| layout | load | logged step | index wall | index CPU | total wall | index objects |
|---|---:|---:|---:|---:|---:|---:|
| base | 3.4 | 9.6-9.9 | 64-68 | 184-199 | 78-82 | 16 |
| part | 8.2-8.5 | 8.4-8.6 (leaves in parallel) | 52 | 156 | 72 | 128 |
| partclus | 8.2-8.3 | 11.7-13.8 (ordered, leaves in parallel) | 50 | 148-150 | 75-77 | 112 |

Smaller per-leaf sorts cut index CPU 18-22%. Leaves can be logged,
rewritten and indexed side by side. Attaching the parent's indexes over
already-indexed leaves is a catalogue step (0.0 s).

**Rebuild only what changed** (`partial`, on partyear):

* Rebuilding the current-season leaf of each of the six pools: 829,783 rows
  (13.8%). Load 7.0 s, SET LOGGED 2.3 s, CHECK 0.3 s, 16 indexes each
  24.8 s, ANALYZE 2.1 s: **36.5 s**, against about 210 s for the whole
  table (serial).
* Swap, per leaf: `DETACH` old + `ATTACH` new in one transaction, holding
  the lock **8-34 ms**. The new leaf carries a CHECK matching its bound, so
  ATTACH skips its validating scan. It already has every parent index, so
  ATTACH only matches them. Invalid indexes afterwards: 0.
* ⚠ **The swapped-in leaf must be VACUUMed before it is attached.** In the
  lab it was not, and the race-page lookup (an index-only read) went from
  0.06 ms to 10.9 ms on heap fetches.

⚠ **But "current season only" is not safe as it stands.**

* A full run re-solves every rating (`08_golive`).
* The nightly (`deploy/nightly_update.sh`) re-prices the last 150 days.
* It also moves person ids (04a linkers), pins distances (04g1b, any year),
  re-normalises stragglers (05b; Sagebrush 2021) and folds school names.
* Then it streams and rebuilds the whole table.

Rows of old seasons therefore change on ordinary nights. The safe form:

1. Stream everything as now.
2. Fingerprint each new leaf against the live one: `count(*),
   sum(hashtext(t::text)::bigint)`, one sequential read, no sort. That took
   9.4 s for all 13 leaves in the lab, against about 25 s of index builds
   for the current leaves alone.
3. Re-index and swap only the leaves that differ.

How many leaves change on a real night is unknown. The first deployment
should log the fingerprints for a week before acting on them. If old leaves
change most nights (likely after a full solve), only the nightly gains.

**Verdict:** partition by pool and nothing else. Year sub-partitions add
planning cost to every lookup (13 leaves) and do not match what a night
changes. Do it only together with (b) and after tier 1, because it is the
one change here that touches the swap machinery (see the plan below).

## (b) Physical order

Layouts:

* **clus**: heap written in board order, `ORDER BY pool, sport, year,
  speed_rating DESC NULLS LAST`; athlete_season by (pool, sport, year,
  mean_rating DESC).
* **pclus**: person order (athlete pages).
* **leanclus**: clus plus tier 1.

| layout | boards: buffers | boards: ms | counts: buffers | counts: ms | page lookups: buffers |
|---|---:|---:|---:|---:|---:|
| base | 354,236 | - | 485,512 | - | 524 |
| clus | 193,049 (-46%) | 1388 vs 6741 | 257,126 (-47%) | 2484 vs 5460 | 723 |
| pclus | 347,436 (-2%) | 3788 vs 9170 | 448,678 (-8%) | 6636 vs 7043 | **216** |
| leanclus | 158,644 (-55%) | **695 vs 2217** | 230,616 (-52%) | **1191 vs 2159** | 736 |

* A year- or state-filtered board reads its candidates from a few pages
  instead of one page per row:
  * P2 hs_f XC 2025: 2,727 -> 758;
  * P5 hs_m XC CA 2024: 13,492 -> 1,065;
  * P8: 2,719 -> 760.
* The plan misfires disappear too. Board order gives the planner a high
  correlation on pool/sport/year: R6 45,162 -> 1,286 and R3 178,097 ->
  105,331 (2.4 s -> 0.09 s).
* Person order helps only the athlete pages (19 -> 4 buffers), which are
  already sub-ms. Board order costs them nothing they can feel (524 -> 723
  buffers across the seven page lookups, all still about 0.1 ms).

**Build.** The stream cannot write in board order: four processes COPY as
they read `results`. But the finish step already rewrites the whole heap
once, in `SET LOGGED`. Make that rewrite an ordered one: `INSERT INTO <new,
logged> SELECT * FROM <shadow> ORDER BY ...` (lab `build`, plain table).

* relog_ordered 30-34 s vs SET LOGGED 10 s: a sort of the table added.
* Index CPU 121-125 s vs 174 s (lean) or 184-199 s (base). The keys arrive
  nearly sorted.
* Total wall: leanclus 86-88 s vs base 78-82 s; partclus 75-77 s, where
  the rewrite runs per leaf in parallel.

Net about +5% wall on the plain table, for boards 3x faster.

BRIN: not applicable. In stream order no column correlates with the heap
(|corr| < 0.05 for year, race_date, person_id, result_id). And every
canonical index serves either an ordered top-N scan or a point lookup;
BRIN can do neither. In board order a BRIN on year would be selective, but
nothing queries year without the composite btree that already leads with
it.

## (c) Narrower rows

| layout | RR heap | change | AS heap |
|---|---:|---:|---:|
| base (live column order) | 1005 MB | | 185 MB |
| narrow (8-byte, then 4-byte, then text) | 941 MB | **-6.4%** | 184 MB |

* Board buffers -2%, counts -2.5%, all results identical.
* The live order loses about 21 bytes per row to alignment: a short `sport`
  text before the bigint `result_id`, `grade` before `meet_id`, and so on.
* `scripts/layout_probe.py` estimates this from production's pg_stats
  without a scan.
* Both `SELECT *` readers name their columns afterwards
  (build_record_books.courseSql, pages3._UNIT_ATHLETES). No positional
  INSERT/COPY exists: seedOtherSports and the stream COPY name
  `_COLUMNS`.

The rest of (c):

* **Heap fillfactor**: already 100 (the heap default). Nothing to gain.
* **Smaller types**: no gain worth the risk.
  * year int to smallint and div_id bigint to int save 2-4 bytes, and only
    if the alignment walk allows it.
  * event_id needs bigint (the lab ids reach 3.2e9; production's are
    feed ids).
  * real is already used for the floats.
* **Vertical split**: the 11 unit columns average 30 bytes per row (18% of
  the row). Moving them to a (school, state) side table would cut the heap
  about 18%, but every unit filter (`rankings._whereClauses`) would then
  need a join. They were denormalised on purpose to avoid that semi-join.
  Not recommended.

## (d) Index trimming

| layout | RR indexes | AS indexes | serial RR index build | 3-job index CPU |
|---|---:|---:|---:|---:|
| base | 2702 MB | 530 MB | 120 s | 184-199 s |
| ixff (btree fillfactor 100) | 2450 MB (-9.3%) | 479 MB (-9.6%) | 135 s* | |
| trim (drop 3 + 4 partial) | 2405 MB (-11%) | 530 MB | 96 s | |
| **lean** (narrow + trim + ixff) | **2173 MB (-19.6%)** | **479 MB (-9.6%)** | 95 s | 142-174 s |

\* Noise. Fillfactor does not slow a build.

All results were identical. Board, count and page buffers were unchanged
or slightly lower (lean: 346,613 / 468,061 / 522).

* **Fillfactor 100.** The indexes are built once and never updated, so the
  10% a btree leaves free per page is waste. One caveat: athlete_season
  takes in-place UPDATEs from `--stage restamp`, which will split pages.
  That is harmless, and the next build packs them again.
* **Partial `WHERE speed_rating IS NOT NULL`** on rr_pool_rating_idx,
  rr_pool_sport_rating_idx, rr_board_rating_idx and
  rr_perf_dist_rating_idx. Every reader says `speed_rating IS NOT NULL`
  (the performance candidates, `_countLive`, `_rankInResults`). Each tree
  shrinks by the unrated share (19% in the lab; production's is
  `null_frac` of speed_rating in the probe).
* ⚠ **Not the time indexes.** The first lab run made rr_pr_sport_time_idx
  and its two siblings partial on `time_seconds IS NOT NULL`. The PR
  board's per-distance walk (`SELECT min(distance) ... WHERE pool AND sport
  AND distance BETWEEN`) carries no time predicate, so it lost its index.
  On the 245k-row first run, R1 went from 1,128 to 12,205 buffers and R3
  from 8,199 to 17,704.
* **Dropped:** rr_person_idx, since idx_rr_person_cover leads with the same
  column (athlete page 19 -> 18 buffers). The lab also dropped idx_rr_school
  and rr_school_sport_idx, but they are small (43 MB each, thanks to btree
  deduplication) and have readers (build_school_identity's `school =
  ANY(...)`; the school page used rr_school_sport_idx). **Keep those two.**
  The real candidates are the large zero-scan trees in the baseline table,
  decided on production usage.
* **Covering instead of separate:** idx_rr_person_cover already does it for
  the person lookups. No other pair shares a prefix in a way an INCLUDE
  would merge without changing an ordered scan.

## (e) Other ideas measured

* **Extended statistics instead of layout** (`xstats`: `CREATE STATISTICS
  (mcv, ndistinct, dependencies)` on (pool, sport, distance), (pool, sport,
  year), (pool, sport, state), (pool, event_kind)). It fixes the misplans
  with no layout change: R6 45,162 -> 1,320, P4 34,283 -> 12,886, R3 to
  149,800. But it is mixed: P6c count got worse, 74,689 -> 116,966 (it
  picked a bitmap plan). Boards -24% buffers, counts +7%. A cheap
  stop-gap, superseded by (b).
* **LOGGED shadow instead of UNLOGGED + SET LOGGED.** The same WAL either
  way (1157 MB for the 6M-row heap). But SET LOGGED rewrites the heap a
  second time: unlogged insert 2.7 s + SET LOGGED 9.9 s = 12.6 s, against
  6.2 s loading a logged table directly.
  * `setLogged`'s note dates the UNLOGGED choice to when SET LOGGED ran
    after the index builds (and rebuilt them). Now that it runs on the
    bare heap, a logged shadow would save one full heap write (about 23 GB
    in production).
  * Caveat: four COPY streams writing WAL at once is untested here. With
    (b) adopted, the ordered rewrite takes SET LOGGED's place anyway, so
    this matters only if (b) is not done.
* **A hot current-season table**: subsumed by partyear's pruning, which
  showed no gain over part for the boards (230k vs 236k buffers). The
  default boards have no year filter.
* **Materialising the scaled sort key**: only `pool=all & scale=hs` boards
  order by the CASE. Rare; not measured.

## The plan

Each step ships behind its own flag, default off. Each is rolled back by
turning the flag off: the next run builds the shadow the old way and swaps
it in. Nothing here changes a query.

### Step 1: lean indexes and columns (build only)

`XCP_RR_LEAN=1`, in build_ranking_results.

1. **Fillfactor.** buildIndexes/rename appends ` WITH (fillfactor = 100)`
   to every CREATE INDEX of both tables.
   * ⚠ `_indexSig` takes everything after the first "(". pg_indexes writes
     the option back as `... (person_id) WITH (fillfactor='100')`, so the
     signature would no longer match `_CANONICAL_INDEXES`, and
     verifyIndexes would call every index missing and refuse the swap (the
     2026-09-22 failure mode).
   * Strip a trailing `WITH (...)` in `_indexSig` first, and add a test
     beside the existing partial-predicate one.
2. **Partial rating indexes.** In `_CANONICAL_INDEXES`, append ` WHERE
   speed_rating IS NOT NULL` to rr_pool_rating_idx, rr_pool_sport_rating_idx,
   rr_board_rating_idx and rr_perf_dist_rating_idx. indexDefs' "redefined in
   the canonical list" path already rebuilds a canonical name whose columns
   changed. **Leave the time indexes full** (see d).
3. **Retire rr_person_idx** through `_RETIRED_INDEXES`. Check that
   `scripts/add_page_indexes.py` and ensure_ranking_indexes do not recreate
   it.
4. **Column order.** createShadow uses `LIKE live`, so the order only
   changes if the shadow is created from an explicit DDL list. Give
   `_COLUMNS` a typed, narrow-ordered DDL and create the shadow from it
   under the flag. COPY names its columns, so nothing else changes.
   * Rollback: flag off, and the next `LIKE live` copies whichever order is
     live. Both orders work.
5. **Before trimming further**, run `scripts/layout_probe.py` on production
   a week after a swap. Retire the trees still at 0 scans (lab candidates:
   rr_school_idx 305 MB, rr_board_time_idx 197 MB, rr_pr_time_idx 190 MB,
   as_pool_best_idx 54 MB) the same way, one release at a time.

Expected in production, scaled by rows (10.2x):

* RR indexes about 27.5 GB -> about 22 GB, so about 5.4 GB less written,
  WAL-logged, verified, vacuumed and cached per run;
* AS about -0.5 GB;
* heap about -6% (the probe gives production's own padding figure);
* index build CPU 13-23% lower;
* about 7 GB more if the zero-scan trees go.

Risk: low. The one trap is the `_indexSig` signature (item 1).

### Step 2: board-ordered heap

`XCP_RR_ORDERED=1`. In `--stage finish`, replace `setLogged(conn,
_LOAD_TABLE)` with:

```sql
SET LOCAL work_mem = '4GB';   -- the athlete_season ladder's numbers
CREATE TABLE ranking_results_sorted (<narrow DDL or LIKE shadow>)
       WITH (autovacuum_enabled = false);          -- LOGGED
INSERT INTO ranking_results_sorted
SELECT * FROM ranking_results_new
ORDER BY pool, sport, year, speed_rating DESC NULLS LAST;
DROP TABLE ranking_results_new;
ALTER TABLE ranking_results_sorted RENAME TO ranking_results_new;
```

Then the indexes, as now. The same goes for athlete_season: its aggregate
inserts into an unlogged shadow, and its setLogged becomes `ORDER BY pool,
sport, year, mean_rating DESC NULLS LAST`.

* The rewrite is the write SET LOGGED already does, plus one sort: about
  +24 s per 6M rows in the lab, partly paid back by cheaper index builds.
* Production estimate: +4-8 min of sort, -15-25% index CPU.
* Boards: -55% buffers, about 3x faster. Filtered counts: -52%, about 1.8x
  faster.
* The PR and performance boards that misplan today (the R3/R6 class: 0.5-2
  s, 10x that in production) become index walks.
* The amcheck verify (verifyShadow) reads the rewritten table as before.

Risk: low-medium. A big sort in the finish step needs temp space: about 1x
the heap if it spills, so check free disk. The order is cosmetic to every
query: identical results, measured.

### Step 3 (optional): partition by pool

`XCP_RR_PARTITIONED=1`. Only after steps 1 and 2 have run clean for a few
weeks.

* The shadow is `PARTITION BY LIST (pool)` with leaves for the six board
  pools plus DEFAULT. COPY goes through the parent unchanged.
* The finish step works per leaf on `_INDEX_JOBS` connections: ordered
  rewrite (logged) and indexes. Then `CREATE INDEX ... ON parent` attaches
  the leaves' trees in a catalogue step.
* athlete_season gets the same.

What must change. These are the hazards found:

* **Leaf names collide across builds.** The live leaves keep their names
  after the parent's rename. Name leaves `<table>_<pool>_g<generation>`, or
  rename them inside the swap transaction. `_canonicaliseIndexNames`,
  `_freeIndexName` and `verifyIndexes` must walk the leaves:
  `pg_indexes.tablename` is the leaf, and a partitioned parent's index is
  valid only once every leaf has one.
* **`verifyShadow` would silently check nothing.** `_verifyPages` sizes the
  table with `pg_relation_size(parent)`, which is 0 for a partitioned
  table. It must iterate the leaves (`pg_inherits`), as
  `scripts/integrity_check.py` does by relkind.
* **`swapIn` / `dbfast.swapTable`.** `DROP TABLE parent` drops the leaves
  and `RENAME` works on the parent. Fine, but lock every leaf in the same
  `LOCK TABLE` statement as the parent, or the rename deadlocks against a
  reader that holds a leaf (the "both tables up front" lesson).
* **`reltuples` readers** (watchdog.py, drop_old.py, search_index-style
  helpers) see -1 on the parent. Sum the leaves.
* **pg_stats**: the parent's rows are `inherited = true`. `_rowHasUnit`
  does not filter on `inherited`, so it still works. ANALYZE the parent so
  they exist.
* **Planning**: +0.25-0.45 ms on every point lookup (athlete, race, school
  pages); 7 leaves × 16 indexes are 112 index objects instead of 16.
* **Changed-leaf swap**, later and under its own flag: log the leaf
  fingerprints for a week, then skip the re-index and swap of leaves whose
  fingerprint matches the live one. Per leaf: CHECK, VACUUM, then DETACH +
  ATTACH (8-34 ms under lock).

Expected on top of step 2:

* counts -45% more buffers (126k vs 231k, lab);
* boards about equal (154k vs 159k);
* index CPU -18-22% (smaller sorts);
* finish wall about -10%;
* and the option of keeping unchanged leaves on the nightly.

Risk: medium. It touches the swap, verify and index-naming code, each of
which has failed silently before. If the counts are not the pain,
**stop at step 2**: it gets most of the board gain with none of the
machinery.

### Rollout and checks for every step

1. Run the lab first:
   `python scripts/layout_lab.py gen && variant lean leanclus partclus &&
   queries && bench base <layout>`. Any `X` in the bench (a result that
   differs) blocks the step.
2. Run `pytest tests/` with the flag on. The test suite builds small
   ranking tables.
3. Turn the flag on for one nightly. Check three things:
   * `10_rankings_finish`'s phase report (index and logged-step seconds
     against the night before);
   * `verifyIndexes` all present;
   * `scripts/explain_board.py --plans` on the default boards: the plans
     should be index walks.
4. Run the all-time board audit (CLAUDE.md) as usual. A layout cannot
   change a row, but the audit is the standing check after any pipeline
   change.
5. Rollback: flag off. The next run builds and swaps the old layout. No
   migration of the live table is ever needed, because every run builds a
   fresh shadow.

## Steps 1 and 2, shipped behind switches (2026-11-08)

Both switches default **off**. They are read when `build_ranking_results`
runs, so to turn one on, add it to `/etc/xc-predictor.env` (the pipeline
sources that file) and the next run's step 10 builds the new layout:

```
XCP_RR_LEAN=1       # step 1
XCP_RR_ORDERED=1    # step 2
```

To roll back, remove the line (or set it to 0). The next run builds the old
layout and swaps it in. A lean run's partial and fillfactor trees are
rebuilt full when the switch is off: indexDefs' "redefined in the canonical
list" path, plus `rr_layout.withStorage`, which removes the fillfactor
clause.

Code: `racecast/rr_layout.py` holds the switches, the partial list, the
board order and the shared index signature. It is light enough for
`add_page_indexes` and `db_health` to import.

**Step 1, `XCP_RR_LEAN=1`:**

* Every index of both tables is built `WITH (fillfactor = 100)`.
* The four rating trees are partial `WHERE speed_rating IS NOT NULL`
  (`build_ranking_results.canonicalIndexes`). The `_CANONICAL_INDEXES`
  literal is unchanged.
* The ranking_results shadow is created in alignment order
  (`_narrowShadowBody`). The column list comes from the live table's
  catalogue: each column's type, collation, NOT NULL and default, and every
  CHECK constraint. athlete_season keeps its order.
* Nothing is dropped.
* The signature trap is closed. `_indexSig` (now `rr_layout.indexSig`)
  drops any `WITH (...)` clause before comparing.
* **A second trap the plan did not name.** Step 11b (`add_page_indexes`)
  compares the live trees against its own specs. If it had not been
  changed, it would have read every lean tree as missing: the fillfactor
  clause failed its text compare, and the partial `rr_perf_dist_rating_idx`
  counted as "PART". It would then have built a full `_f` copy of each,
  CONCURRENTLY, on every run. It now uses the same lean spec and signature,
  and accepts a full tree where the lean spec asks for a partial one.
  `ensure_ranking_indexes` had the same issue and gets the same fix.

**Step 2, `XCP_RR_ORDERED=1`:**

* `relog()` replaces `setLogged()` for both shadows.
* `orderedRewrite` copies the shadow into a LOGGED table in
  `rr_layout.BOARD_ORDER`, in one transaction, then drops and renames.
  * ranking_results: `pool, sport, year, speed_rating DESC NULLS LAST,
    result_id, person_id`.
  * athlete_season: `pool, sport, year, mean_rating DESC NULLS LAST,
    person_id`.
* It stands down to SET LOGGED, and says so, in two cases:
  * the data disk has less than 2.2x the shadow free;
  * the sort runs out of room (DiskFull, temp_file_limit or memory).
    The transaction then rolls back whole.
* A resumed finish finds the shadow already logged and does not sort it
  twice.
* The stream, the seams and the four COPY processes are unchanged. So are
  verifyShadow, the swap and the post-swap VACUUM.
* Physical order is board order except where the free-space map back-fills
  a page's leftover bytes with a later short row: 0.06% of rows at 6M.

**Exactness:**

* **With both switches off, the build sends exactly what it sent before.**
  The statements from createShadow, relog and buildIndexes, verify
  included, were recorded on Postgres for this code and for the previous
  commit's module: 182 statements each, identical.
* **With each switch on,** `layout_lab.py variant flag_<x>` ran the real
  createShadow, relog and buildIndexes on the 6M-row corpus. `bench`
  compared every statement against base:
  * all 44 statements returned identical results (md5 of every row, in
    order) for flag_off, flag_lean, flag_ordered and flag_both;
  * flag_off's buffers equal base's.
* **The athlete_season aggregate** was run over the stream-ordered heap and
  over the board-ordered heap. The 1,340,933 seasons were identical in
  every column, decayed_rating included.
* **tests/test_rr_layout_flags.py** runs the rankings functions themselves
  (21 boards, their counts, and the page lookups) and build_board_seek's
  source rows on four builds, one per switch setting, with identical
  results. It also checks count, checksum and md5 per table for the
  ordered build.

**Which indexes the plans used** (`bench`, flagged `i`):

* Performance, ability and page queries walk the same trees under every
  switch.
* Under LEAN, the PR boards no longer read `rr_perf_dist_rating_idx` for
  their `(pool, sport, distance)` probes, which carry no rating predicate.
  They read the full `rr_pr_sport_time_idx`, and R3 reads `rr_pr_time_idx`.
  The buffers were the same or lower: R3 178,145 -> 173,841, R4 22,542 ->
  3,958.
* ⚠ **So under LEAN, `rr_pr_time_idx` and `rr_board_time_idx` are not dead
  weight.** They are the full siblings that keep every prefix of the
  partial trees indexed. They were zero-scan in the base workload, but do
  not retire them while LEAN is on without re-running this bench.

**Lab numbers** (6,013,536 rows; buffers summed, ms against the same run's
base):

| switches | RR heap | RR indexes | AS indexes | boards: buffers | counts: buffers | page lookups |
|---|---:|---:|---:|---:|---:|---:|
| off | 1005 MB | 2701 MB | 530 MB | 354,201 (0%) | 485,882 (0%) | 517 |
| LEAN | 941 MB | 2304 MB (-15%) | 479 MB (-10%) | 328,504 (-7%) | 450,621 (-7%) | 509 |
| ORDERED | 1005 MB | 2701 MB | 530 MB | 204,473 (-42%), 2268 -> 1141 ms | 268,391 (-45%), 2107 -> 1318 ms | 487 |
| both | 942 MB | 2304 MB | 479 MB | 203,142 (-43%), 2412 -> 1074 ms | 241,849 (-50%), 2376 -> 1175 ms | 502 |

* The misplanned boards became walks:
  * R6: 45,191 -> 1,280 buffers (425 -> 9 ms);
  * R3: 178,145 -> 105,193 (779 -> 106 ms);
  * P3c: 79,251 -> 10,037.
* Two queries read more under ORDERED, both still faster:
  * P4 (hs_m TF 1600): 34k -> 43k buffers, 164 -> 91 ms;
  * A4c: 6.8k -> 14k buffers, 10 -> 7 ms.

**Build** (the finish step's work on both tables, 3 index jobs):

| switches | RR relog | RR indexes | AS relog | AS indexes | total |
|---|---:|---:|---:|---:|---:|
| off | 20.8 s (SET LOGGED) | 81.3 s | 1.9 s | 13.8 s | 117.8 s |
| LEAN | 13.4 s | 77.0 s | 1.6 s | 11.9 s | 103.9 s (-12%) |
| ORDERED | 44.0 s (sort + copy) | 68.6 s | 8.0 s | 10.0 s | 130.6 s (+11%) |
| both | 48.2 s | 60.5 s | 7.2 s | 10.2 s | 126.1 s (+7%) |

At production scale (10x), expect:

* about +4-5 min of sort for ORDERED, partly paid back by 2-3 min of
  cheaper index builds;
* the sort's temp files, up to about 1x the heap (about 23 GB), beside the
  new heap. The 2.2x free-space check is there for that.

## Files

* `scripts/layout_lab.py`: synthetic data, layouts, benchmark, build cost,
  partition swap. Writes its JSON to `$LAYOUT_LAB_OUT`
  (default `scripts/layout_lab_out/`).
* `scripts/layout_probe.py`: read-only production probe. It reports, per
  index, its scans and fillfactor; per column, null shares (for partial
  indexes) and correlation (for BRIN); and the alignment-padding estimate.
