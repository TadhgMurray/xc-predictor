# Running issue list — opened 2026-09-08

Every issue raised in this session, what it turned out to be, and where it
stands. Newest sections first within each status. `git log 1e220f2..` is the
same story in commits.

**Legend** — ✅ fixed and pushed · ⏳ fixed, needs a run to take effect ·
🔎 open, needs data · 💤 open, not started · ❌ diagnosed, deliberately not
fixed

---

## 0. WHAT IS BLOCKING RIGHT NOW

| # | Thing | Who does it |
|---|---|---|
| 1 | `git pull` on the box, restart the site | you |
| 2 | `python racecast/build_team_season.py` — **run it again**, so the ceiling change (§2.6) lands | you |
| 3 | ~~`scripts/add_page_indexes.py`~~ ✅ **done** — `rr_pool_year_dist_idx` built | — |
| 4 | ~~`engine/wheelchair_flag.py --write`~~ ✅ **done** — 69s, 6,056 people (the labels check out: `Wheelchair` 19,708 races, `Ambulatory` 5,997, `Adaptive` 1,543, real T/F class codes) | — |
| 5 | ~~`scripts/athlete_dump.py 26155532 23965611`~~ ✅ **done** — Kohen was §1.10; Lex Young is §5.1, still open | — |

Nothing in §1–§4 needs a pipeline run. §5 items do.

---

## 1. PIPELINE AND ENGINE

### ✅ 1.1 The joint go-live crashed three hours in (310, 171)
`writeLive` unpacked two arrays out of a three-array tuple —
`ValueError: too many values to unpack (expected 2)` — **after**
`course_difficulties` and `athlete_ratings` were written and before a single
result rating was. fbf4419 gave `buildLive` a third array (issue 171,
`rating_pool` on the row) and left the consumer at two.

It went unseen for two days because `XCP_JOINT_LIVE` was off and no run
reached the line. **Issue 310 hid this**: the flag being off did not only
publish the wrong engine, it stopped the right engine's crash from ever
being seen.

*Fixed by binding the tuple whole rather than naming its parts.* `12676e7`

### ✅ 1.2 A failed go-live published anyway
`step()` records a failure and carries on — right for a diagnostic, wrong for
the one step that produces what everything after it publishes. With 08 dead,
`results.speed_rating` still held the **previous** run's ratings and 09b_fill,
the boards, the search index, the sitemap and IndexNow all republished it.
Four hours dressing yesterday's ratings up as today's.

*A failed `08_golive` now aborts before `09b_fill` and exits 1.*
`XCP_IGNORE_GOLIVE_FAIL=1` overrides. `b7bc4b1`

### ✅ 1.3 Chair athletes: the list went stale (14)
`wheelchair_person` is built only by step `04b`, and the standard `--from 8`
recipe had it on `--skip`. So the list described an older corpus and every
chair athlete ingested since was priced and ranked.

*`04b_wheelchair` now runs on any `--from`; `--skip` on it prints a banner.*
`b7bc4b1`

### ✅ 1.4 …and the checklist could not catch it
Checks 4 and 4a ask "does anyone on the exclusion list carry a rating?" — and
the engine builds its refusals from that same list. They catch a
**propagation** failure and are blind to a **detection** failure.

*Check 4a-ii applies `wheelchair_flag.WHEELCHAIR_RX` to the corpus as it
stands and FAILs when the live rule finds a rated athlete the table does not
list.* `b7bc4b1`

### ✅ 1.5 …and the list never reached the ratings anyway
`_chairFilter()` is interpolated into both **pack** queries, so rebuilding the
list without rebuilding the pack leaves chair athletes in the solve. Worse,
`--from 7` did not rebuild the pack either: `06_clear_cache` was gated at
`FROM <= 6` while step 07 runs with `--cache`.

*The clear now fires whenever 07 will run.* `0ea462a`, `97b30e3`

### ⏳ 1.6 Chair athletes keep un-detecting themselves (14) — **a guard, NOT the cause**

> ⚠ **CORRECTION, 2026-09-09.** I was wrong that this is what you are seeing.
> The first sticky run reported `all 578 previously flagged people were found
> again by the fresh scan` — **nothing had been lost**. The list is not stale
> and the feedback loop below did not fire on this corpus. The fix stays as a
> guard (it costs nothing and the failure it prevents is real and silent), but
> the chair athlete on your board is a **different bug**, still open. The dump
> in §6 decides which: the rule not matching them at all, or the rule matching
> and something downstream rating them anyway.

The mechanism, kept because it is still a live hazard:

> *"it's not a detection gap bcs we've detected these ppl so many times b4,
> which is an issue that keeps popping up in them stop getting detected."*

Correct. The rebuild is `DROP TABLE` + `CREATE TABLE AS`, re-deriving the list
from whatever labels survive in `results` **today** — while the file's own rule
is *"ANY CONFIRMED CHAIR RACE CONDEMNS THE WHOLE CAREER"* and *"IT FLAGS, IT
NEVER DELETES"*. A snapshot cannot honour "once confirmed".

**And one way the evidence goes is a loop the pipeline drives itself:**

```
flagged → a run without the flag rates them → the chair time reads absurd on
the running scale → the rowguard drops that result → the next rebuild cannot
see it → rated again
```

plus a re-scrape renaming a division, a `div_id` changing, a person merge
renumbering `person_id`.

*The fresh scan is now UNIONed with what the table held; a person is only ever
added. Carried rows are marked `via 'carried:'` and the count is printed.
`--forget <person_id>` is the only way off.* `9231cc9`

⚠ The first run of this crashed: `ensureTable` did `cur.fetchone()[0]`, which
works on the plain cursor `build_ranking_results` uses and raises
`KeyError: 0` on the `RealDictCursor` this module runs on. My new call site
exposed a latent assumption. *Fixed — both shapes handled.*

**Run once (done, 2026-09-09): `all 578 previously flagged people were found
again` — carry count zero, so this was never the cause here.**

### ✅ 1.10 Chair athletes: the rule read one of track's TWO label columns — **the actual cause**
Kohen Grantom (26155532), first on the HS boys performance board at **150.8**
for a 1:42.68 800m. In the same meet he ran **16.54** for 100m. No pair of
legs produces both; a racing chair produces both easily.

His race page says it outright: **`800m · Boys · Wheelchair · Outdoor`**. The
word is in **`meets_tf.division`**, and `event_short` is a bare `"800m"`.

`wheelchair_flag`'s TF branch read `event_short` **alone**, on a stated
premise that was true of one feed and false of the other:

> *"TF keeps the distance and the class in the EVENT name, which is where
> 'Wheelchair 1500' lives. No division blob to read."*

tfrrs does that. **anet track puts the class in the division and leaves the
event bare.** So every anet chair track race was invisible.

**And the census read healthy the whole time** — `425 via TF event names,
0 via the tfrrs blob` — because a source nobody reads has no line to be zero
on. It now counts `tf.division` and the carried rows separately.

*Both columns are matched now.* Re-run `wheelchair_flag.py --write`: the
`found via TF divisions` line is this bug, counted.

⚠ **The first version of that fix was too slow to run and double-counted** —
see §1.11. Use the current one.

⚠ **I got this wrong twice before landing it** — first blaming a stale list
(the carry proved nothing was lost), then concluding the labels carried no
chair signal at all. They did; the dump wasn't showing the division, because
it read the same single column the rule did.

### ✅ 1.11 …and the fix for it took forever, and double-counted
> *"wheelchair flag is taking forever can you speed it up"* — you, 2026-09-09

I wrote the division read as one predicate over a join: `results_tf LEFT JOIN
meets_tf`, **61M rows against 14M**, with both regexes evaluated on the join
output. Nothing narrows either side first, so the planner has to build the
whole product. Correct, and unusable.

**It was also wrong.** `meets_tf` is `PRIMARY KEY (div_id, event_id)` — one
row per **event**, not per division. A chair division that ran four events is
four rows there, so the three-column join matched each of its results four
times and `n_chair` counted them four times. `n_chair / n_total` is exactly
what `--review` reads to decide who is doubtful, so it was quietly making
chair-heavy careers look chair-heavier. My own comment on that join said it
"cannot fan out". It could.

*The division regex now runs against `meets_tf` alone — 14M short strings, no
join — `DISTINCT ON (meet_id, div_id, source)` collapses it to one row per
division, and those few rows drive index lookups into `results_tf` on
`meet_id`. One scan of each table instead of a product of the two biggest
tables in the database.* The event-name branch is separate and unchanged, and
excludes what the division branch already took, so a race labelled in **both**
columns is still one row.

Verified against a real Postgres on a fixture: the old form emitted result 10
twice, the new one emits it once, and the clean row in the same meet under a
different `div_id` is still untouched.

**And it now says where it is.** The build was one multi-statement `execute`,
so nothing printed until it was over — a working run and a wedged one looked
identical for however long that took. It is five statements now, each counted
and timed:

    [chair] scanning results 12.4M · results_tf 61.2M · meets_tf 14.1M  (8 workers, work_mem 256MB)
    [chair]   1/5 chair divisions      meets_tf       2,904 rows    38.2s
    [chair]   2/5 XC, both feeds       results        1,102 rows   112.4s
    [chair]   3/5 track, event names   results_tf     2,571 rows   201.7s
    [chair]   4/5 track, divisions     results_tf       431 rows     9.1s
    [chair]   5/5 union, index, analyze                6,004 rows     0.4s
    [chair]   races built in 362s
    [chair]   previous list kept: 578 people
    [chair]   people rolled up               601 rows    44.3s

(row counts and times illustrative — the shape is what to read). Every print
is flushed, so a redirect to a log shows them as they happen.

**Two things also made it genuinely faster:**

- **Parallel scans.** Every expensive step is a regex over a whole table,
  which is exactly what a parallel seq scan is for, and Postgres' default
  `max_parallel_workers_per_gather` is **2** on a 32-core box. Now 8 —
  `--workers N` or `XCP_PG_WORKERS` to change it, and the server's own
  `max_parallel_workers` still caps it, so asking high is free. The scratch
  tables are `UNLOGGED`, **not** `TEMP`, for this reason alone: a parallel
  worker cannot read a temp table.
- **`n_total` stopped counting everybody.** It was a hash aggregate over
  `results` + `results_tf` — ~73M rows into millions of groups, big enough to
  spill to disk — to then `LEFT JOIN` the six hundred rows it wanted. It now
  semi-joins to the flagged set first. Same numbers (checked both ways on a
  fixture, including a flagged athlete with a long ordinary career); neither
  table has a `person_id` index so the scans remain, but the aggregate is
  gone.

### ⏳ 1.7 Pros get "crazy low" ratings
Not a solve bug. A rating is `100 × pool_mean / exp(a)` and 100 is the mean of
**your own pool** — the pro pool's mean is a professional, so an elite pro
reads ~100 next to college 146.

*The anchor moves, not the pool: pro rows are rated against the **college**
mean while the college mean is still computed over collegians alone.
Repooling would have folded pro abilities into the college mean and dragged
every college rating down. Pros keep `pro_m`/`pro_f`, so they stay off every
board — mechanically college, not ranked as college.* Fixture: 98.8/101.3 →
125.0/128.2, college rows unchanged. `7e79909`

### ❌ 1.8 The CG solve takes 300–400 iterations per outer
Measured, then **not** built. Deflating the level/sport direction looked right
— `CG_TOL_OUTER` blames exactly that direction — but on a synthetic operator
of this shape, a *cluster* of 20 weak directions gives: plain 448 iterations,
deflate-one **449**, deflate-twenty 59. A count in the hundreds is the cluster
signature, so the one-vector deflation would have bought nothing.

*`XCP_CG_TRACE=<n>` prints the residual shape, which tells the two apart
before any deflation is written.* Smooth geometric decay = cluster (wrong
tool); plateau breaking into drops = isolated directions. `1e4b288`

### ⏳ 1.9 The solve is slow (8h 14m)
`XCP_THREADS` defaulted to a flat 8 on a 32-core box. `rowPrediction` splits
~60M **rows** across threads and is 0.9s of every 1.2s iteration — it scales
with cores, unlike the reductions, which cannot use more than their ~9 jobs
and were never the reason for the cap.

*The default now follows the box: `min(cpu_count, 24)`, so the server gets 24
and nothing under 24 cores changes. Capped rather than left at `cpu_count`
because the gather is memory-bandwidth bound at the top end. `XCP_THREADS`
still overrides both ways.* Takes effect on the next solve. `d744086`

---

## 2. THE SITE — BOARDS

### ✅ 2.1 One squad ranked as several teams — **confirmed live**
> `restated  686,207 athlete-seasons moved to their school's own state`


BYU held ranks 5, 7 and 8 as WI, OK and FL with 5/6/6 athletes instead of one
BYU with 17. `ranking_results.state` is where the **race** was;
`athlete_season.state` is the mode of that; `team_rank.teamKey` keys a squad
`(school, state)`. A college races away most weekends.

This broke `build_team_season`'s own stated invariant — *"A team sits in
exactly one state … two rows per team, never more"* — and moved the ranking,
because split squads score as five- and six-man teams against full ones.

*`school_identity.teamState` resolves it; `schoolLabelFor` is now a wrapper
over the same function so the key and the label cannot disagree. Not just
`primaryState`: a context state that is genuinely one of the name's own
clusters is kept, so Kingston MO and Kingston WA stay two teams.* `6a6731e`

### ✅ 2.2 Div/section filters did nothing
`rankings.js` shows those combos by **pool**, not by board, so the Teams tab
always offered them and always sent them — and `teams.parseFilters` never read
them.

*A division now narrows the **field**, so `raceStored` races it and the ranks
come out 1, 2, 3 with nothing renumbered.* Deliberately not renumbering a
sliced board: `teams.py` argues that inventing a championship nobody ran
cannot see depth. `47ea349`

### ✅ 2.3 A DI school in a DIII filter
"Washington" is DI in WA and DIII in MO. My first cut matched school **names**,
which I shipped as a known caveat; it bit immediately.

*`team_season` now carries the unit columns `athlete_season` already stamps per
(school, state), so the team filter is the identical column comparison the
athlete boards make.* `fb2afb8`

### ✅ 2.4 The meets page 500'd on every course view
`results.date` is **TEXT**, so `max(r.date)` returns a string and `d.year`
raised `AttributeError`. Pre-existing, unrelated to this session's changes.
*Both shapes handled.* `cf74838`

### ✅ 2.6 The straight 150 cap on team rankings is gone
> *"There probably shouldn't be a straight 150 cap btw" … "just remove it for
> now flag if an issue later"* — you, 2026-09-09

`build_team_season` dropped any athlete-season above its pool's ceiling before
a team was scored, so a squad could be ranked on five runners while a
neighbouring squad kept six. A flat line through a distribution with a real
tail cannot tell a genuine 150.1 from a chair time on the running scale — and
the second one has a cause worth fixing at the source, which §1.10 now does.

*The rail counts and no longer drops.* The build still prints what it would
have removed:

    ceiling   N athlete-seasons are ABOVE their pool's ceiling and are
              RANKED ANYWAY -- hs_m N (>150), ...

If a squad turns up with an impossible scorer, that line is where to look.

**Removed from both places at once.** `teams.raceReturning` applied the same
ceiling to the returning board; leaving it there would have scored one board
on five runners and the other on six, and the two would disagree about a team
that had not changed. `tests/test_returning_teams.py` now runs the build's
rail on a stub where *everything* is implausible and asserts every row
survives — so the two can't drift apart again.

⚠ **A different rail is still up, and I have not touched it.**
`build_ranking_results.raceCeiling` = pool ceiling + 10, applied **per race**
to the athlete/performance boards. That one is there for a measured reason:
the whole top-25 of the 2025 `hs_m` XC board was once a single meet, times
21:32–21:55, every row rated 157–159 from a bad anchor. Say the word and it
goes too — but it is not the same rail and removing it puts that back.

### ✅ 2.5 The year combo drew every option twice
`renderOptions` falls back to `o[2] || o[0]` for the code chip and draws it
whenever it differs from the label — right for states ("California" + "CA"),
invisible for years while value and label matched, wrong the moment the label
became "2025-26". *Year options carry the label as their own code; panel
widened to 520px so "2025-26" is not ellipsised to "20…".* `9231cc9`

---

## 3. THE YEAR CHANGE — and what it cost

### ✅ 3.1 Boards show the academic year
Asked for as *"year should be academic year … just change for rankings"*.
Done across all four boards: display **and** filter together. `b7c3b16`

**I was wrong about the reason.** I told you the boards and the athlete page
disagreed. They did not — `rankings.py`, `teams.py`, `app.py`, `school.py` and
`cards.py` all applied `+1` for track *and the filters accepted the label*, so
it was self-consistent. Changing only the boards **creates** a divergence
rather than removing one. Flagged before the change; chosen anyway; recorded
in `tests/test_board_academic_year.py` so it reads as a decision.

### ✅ 3.2 …and it leaked past the boards, four times
Every one failed as an **empty board**, not an error:

| Surface | What it sent |
|---|---|
| athlete page rank line | year+1 — your Camas TF season stored 2025, looked up as 2026 |
| landing pages + share cards | `season_year_TF` from the meta, which `panels.py` publishes as the **label** |
| home page View-all | the same label |
| recruiting | converted stored→label *toward* a board — backwards |

`panels.py` documents this exact trap in the opposite direction, which is how
I found the rest. *All four go through one `rankings.boardYear()`.* `cf74838`

### ✅ 3.3 "Academic year", shown as 2025-26
Not cosmetic — I believe this was your empty best-times/performances boards.
With Year=2026 selected you were asking for the season that just **opened**.
It reads "2026-27" now. `4882c27`

### ❌ 3.4 `predict._currentSeason` is a year off for TF
Reads the label from the meta while `_currentSquads` treats it as academic
(`stale = season_year < academicYear(today)`). **Predates this session** — the
meta has always held the label. Its own surface, so flagged not touched.

---

## 4. NEW FEATURES

### ✅ 4.1 Events filter on the athletes board
800m+ / 1500m+ / 3000m+ (XC projection) / 5000m+. Reproduces the build's
estimator exactly — 80th percentile, same outlier guard — so a restricted
board is comparable to the unfiltered one; a plain average would have put a
different statistic beside it. `ac2679e`

### ✅ 4.2 …and it was slow enough to 500
With `sport=Both` there is no sport predicate, so
`rr_board_rating_idx (pool, sport, year, …)` can only use `pool` and never
reaches `year`. On college_m that is every row of the pool per page load —
enough to hit the 55s statement timeout and return the HTML page the console
read as `Unexpected token '<'`.

*Index added in two places: `build_ranking_results` creates it on the shadow
at step 10, and `scripts/add_page_indexes.py` builds it on the LIVE table with
`CREATE INDEX CONCURRENTLY` — no locks, safe with the site running, and it is
already step 11b so it stays maintained.* `4882c27`

### ✅ 4.3 Returning teams
"Leaving" control on the Teams tab. Re-scores from `athlete_season` because
the stored ratings are bare numbers with no grades attached; excludes by
**meaning**, so a senior spelled "Sr" goes too. Needs exactly one year, and
refuses otherwise. `5c97334`

---

## 5. ENGINE JUDGEMENTS — open, in your priority order

### 🔎 5.1 The 200–400 ratings — **found: normalised on one pool, rated on another**
> *"The entire season is insane. That means it has to be something with
> pool."* … *"If we catch him we catch all of them."*

The 2023 HS mile final settles it — eight seniors inside four seconds:

| | time | rating | rating × time |
|---|---|---|---|
| Birnbaum | 4:02.22 | 144.2 | 34,928 |
| **Leo Young** | 4:02.58 | **231.8** | **56,230** |
| Hansen | 4:03.63 | 143.4 | 34,937 |
| Burns | 4:04.24 | 143.0 | 34,926 |
| **Lex Young** | 4:04.60 | **229.9** | **56,234** |
| Cutting | 4:05.38 | 142.2 | 34,893 |
| Boler | 4:06.01 | 141.9 | 34,909 |
| Jones | 4:06.93 | 141.4 | 34,916 |

One race, one distance, one day. The six sit on one anchor (spread 0.12%),
the two Youngs on another (spread 0.01%), **ratio 1.6104**.

`rating = 100 × pool_mean / normalized_time`, and
`normalize_distance.targetFor` gives:

    elem_m 2414m · ms_m 3200m · hs_m 5000m · college_m 8000m · college_f 6000m

Recomputing every row of that race on the **hs_m** spline reproduces all
eight stored `normalized_time` values. Divide them out:

    the six      -> pool_mean 1,219   (hs_m; the module's own note says 1236.4)
    the Youngs   -> pool_mean 1,962   (hs_m x (8000/5000) -- college_m's)

**Their rows were normalised as `hs_m` and rated against `college_m`'s pool
mean.** That season the Youngs ran mostly non-HS races, so the season
resolved to `college_m` while these rows had already been normalised as
`hs_m`. The population is therefore *anyone who raced across levels in one
season*, and the athlete did nothing unusual except get invited.

This is the failure `engine/anchor_check.py` was written for — one level up
from the eighth grader in its header (ms 3200 against hs 5000, 1.64×).

⚠ **And the audit could not see them.** It joined `ranking_results` with an
INNER join to learn the pool, while `build_ranking_results` drops anything
over `raceCeiling(pool)` before writing a board row. A mismatch inflates a
rating by ~60%; an inflated rating is over the ceiling; an over-ceiling row
never reaches a board. **The worse the mismatch, the more certain the checker
was to miss it.**

*Fixed: the pool comes off the row (`rating_pool`, issue 171), the board join
is a LEFT JOIN, and the report has a `board` column plus a count of how many
findings never reached one. `was` also no longer names the wrong sport —
hs_m anchors at 5000m in both sports, so a track row tied and XC won.*
`tests/test_anchor_check.py`

    /srv/venv/bin/python engine/anchor_check.py --sport TF --person 23965611
    /srv/venv/bin/python engine/anchor_check.py --sport TF --scan 2000000

**Confirmed on the live corpus, 2026-09-09.** All eight of Lex Young's rows:
rated `college_m`, normalised `hs_m|TF`, −38.5%, **none on a board**. And
2M rows scanned: **1,419 mismatched (0.07%), 100% of them off the boards.**
Both directions exist — `college_m` rated on an `hs_m` scale inflates
(230), `elem_m` rated on a `college_m` scale deflates (41–47).

| pool | checked | mismatched | % |
|---|---|---|---|
| college_f | 387,063 | 613 | 0.16% |
| college_m | 426,105 | 376 | 0.09% |
| elem_f | 3,527 | 41 | 1.16% |
| elem_m | 3,949 | 44 | 1.11% |
| hs_f | 453,833 | 41 | 0.01% |
| hs_m | 628,145 | 164 | 0.03% |
| ms_f | 43,625 | 51 | 0.12% |
| ms_m | 53,737 | 73 | 0.14% |
| **pro_f** | 5 | 5 | **100%** |
| **pro_m** | 11 | 11 | **100%** |

⚠ That scan was `LIMIT` with no `ORDER BY` — the first pages on disk, not a
sample. `--pct 1` now uses `TABLESAMPLE` for a real corpus rate.

⚠ **pro_m/pro_f read 100%, and that is probably the audit, not the data.**
`normalize_distance` says pro and open have **no pool mean and no distance
spline**, so there is no pro scale for a row to be normalised on and the
check does not apply as written. 16 rows; worth a look, not a fire. It is
also downstream of the pro-scale change (§1.7), which redirects pro rows to
the college anchor.

✅ **Fixed by `engine/anchor_repair.py`** — option (a), your call.

    python engine/anchor_repair.py --sport TF            # dry run, counts
    python engine/anchor_repair.py --sport TF --apply
    python engine/anchor_repair.py --sport XC --apply

**The seam:** `backfill_normalize` resolves a season's level from
`season_level`, which only speaks when the verdict is **unanimous**. An
athlete who ran mostly non-HS races and a few HS ones has no unanimous
verdict, so the backfill falls back to `poolFor(grade=12)` → `hs_m` and
writes `normalized_time` at the 5000m anchor. The engine's resolver takes a
majority, calls the season `college_m`, and divides by a mean on the 8000m
anchor.

⚠ **It rescales, it does not recompute.** `anchor_check`'s `expected` is a
bare distance factor — no season, no weather, no track geometry, no course.
The stored value has all of those baked in, so writing `expected` back would
fix the anchor and silently strip every correction the pipeline computed.
Instead:

    new = stored × factor(d, rated_pool) / factor(d, pool_it_was_on)

Everything that is not the pool factor survives exactly. Verified on a
fixture carrying a 1.7% correction: the repaired value matches the correct
one to five significant figures, and the naive `expected` write does not.

It abstains rather than guesses: if no pool reproduces the stored value to
within `IDENTIFY_TOL` (3%, against the 10% that decides "wrong"), the row is
counted and left alone. It is idempotent, dry-run by default, and writes
`normalized_time` and nothing else.

⚠ **It takes a run to show up.** The rating is computed *from*
`normalized_time` during the solve, so this fixes the next engine run, not
the ratings in the table now. Order: go-live (writes `rating_pool`) →
`anchor_repair --apply` → re-run the engine.

Expected effect on the mile final: **Leo 231.8 → 142.9, Lex 229.9 → 141.7**,
beside Birnbaum's 144.2 and Hansen's 143.4.

**Not the cause, ruled out:** the seasonal anchor (academic 2022 is normal —
avg 106.0, p99 123.5); recency (max has been 280–345 most years since 2002);
the solve (`rating × normalized_time` is constant to 0.03% across 38 rows
1995–2026, so an impossible rating is an impossible `normalized_time`).

`scripts/rating_scale_probe.py` still measures the population corpus-wide;
`anchor_check.py` names the cause per row.

### ⏳ 5.2 TF overrated vs XC (the sport gap) — **measured, plumbed, not yet run**
> *"mainly the fact that most of the best seasons of all time are tf"*

`scripts/measure_sport_gap.py`, on **2.4M sandwiches** (an athlete's TF level
interpolated across an XC season and back), SE 0.00004:

    D = -0.02795 in log-rating, XC minus interpolated TF

Negative = XC rates **below** the athlete's own interpolated TF level, i.e.
**TF is over-rewarded by 2.8%.** Each sport carries half:

| | now | corrected |
|---|---|---|
| a 150 TF season | 150 | **147.9** |
| a 150 XC season | 150 | **152.1** |

A 4.2-point swing at 150 — which is exactly the size that decides an all-time
board.

**It is not the distance curve.** The tool's own test: `D/span` across the 8
pools has mean −0.0234 and sd 0.0165, a spread 70% of the mean. Not constant,
so the distance-exponent hypothesis is out. Against the other columns
(n=8, all confounded, so read as a hint not a finding):

    TF races per season   r = -0.886
    mean race distance    r = -0.87
    mean season rating    r = +0.728
    span                  r = -0.625

⚠ **A single global constant is right for the boards that matter and wrong
for the small pools.** Residual after applying D globally:

    hs_m       +0.4%   hs_f       -0.5%
    college_m  -1.3%   college_f  -1.1%     <- still TF-high
    ms_m       +2.9%   ms_f       +1.0%     <- now TF-low
    elem_m     +1.9%   elem_f     +2.0%

All-time boards are hs and college, so the global fix takes those from
2.4–4.0% wrong to under 1.3%. ms/elem get worse in relative terms. Per-pool
bbar is the obvious follow-up; not built.

*Plumbed as `run_joint --sport-gap-delta D`, off by default.*
`tests/test_sport_gap_delta.py`

    XCP_WINTER_GAIN=0.02 XCP_WINTER_GAIN_BANDS=0.045,0.04,0.035 XCP_ALTITUDE=1 \
      bash deploy/run_pipeline.sh --from 7 ... --sport-gap-delta -0.02795

★ **A delta, not an absolute — and the tool's own suggestion is wrong here.**
It prints `bbar -0.03924 -> -0.06719`, but −0.03924 is the **old pair
engine's** number, quoted out of `linkage_check`'s header. The joint solve
computes its own bbar from `beta` on every outer pass and never reads
`sport_gap_bbar.json`, so pinning the absolute would import an unrelated
engine's estimate. Adding D to whatever the pass computed moves the gap by
exactly D, whatever the base turns out to be.

⚠ **Only valid at the ridge it was measured at** — the same caveat
`linkage_check.recenterSport` already carries. bbar is a weighted mean of
`beta` and `--ridge` decides how much of the level lives in `beta` rather
than `mu`. Change the ridge, re-measure.

**Two ways to tell scale from fitness, both built (2026-09-09).**
> *"Could track fitness gain be messing up on indoor to outdoor switch?"*

The sandwich cancels a linear trend **exactly** and the curvature between its
two bread types — the internal check is clean, the two types differ by
`0.02940 − 0.02638 = 0.00302` and twice the measured curvature is
`2 × 0.00151 = 0.00302`. But a **phase-locked** season (sharper every spring
than every autumn) enters both arrangements with the same sign and is
**inside D**. And by `joint_golive`'s own rule — form is left in the rating,
"a November race SHOULD rate higher if it was better" — that part *should* be
there. **So D = −0.02795 is an upper bound on the scale error.**

    scripts/measure_sport_gap.py --split-indoor            # ~2-4 min
    scripts/measure_sport_gap.py --split-indoor --pool hs_m   # faster still
    scripts/curve_window_gap.py logs/run18.out --compare -0.02795

⚠ **The first cut of `--split-indoor` ate the server** — the `is_indoor`
lookup was a per-row LATERAL. `results_tf.result_id` is a PRIMARY KEY so it
*looked* harmless, but over millions of `ranking_results` rows an indexed
lookup is millions of **random seeks** into a 191M-row table plus another
into `meets_tf`. It is now two sequential passes with hash joins, samples
every 20th **person** (a sandwich needs all three of a person's seasons, so
row sampling would shred them), and runs with `work_mem 256MB` and 8 workers
instead of 1GB and 2.

**MEASURED 2026-09-09, AND IT DOES NOT SUPPORT A CORRECTION.**

    XC - TF-in     -0.03189   (n 7,767,  SE 0.00055)
    XC - TF-out    -0.02556   (n 80,301, SE 0.00016)
    TF-in - TF-out -0.00807   (n 5,867,  SE 0.00036)

★ **The triangle does not close.** Three arms with one level each make the
pairwise differences differences of three constants, so
`(XC−out) − (XC−in)` must equal `(in − out)`:

    (XC-out) - (XC-in)   +0.00633
    indoor - outdoor     -0.00807
    CLOSURE ERROR        +0.01440      <- 20-90 SE, not noise

**D is not a single sport constant.** The answer depends on which pair you
measure — i.e. on how far apart in the calendar the two seasons sit — which
is a phase effect by definition, and no single `--sport-gap-delta` can be
right. The opening (0.0144) is larger than the indoor/outdoor gap itself and
half the size of D.

⚠ **So do not apply −0.02795.** Not yet, and not as one number.

**(b) came back useless, and I should have seen it before shipping it.**
`logs/run18.out` gave `-0.02000` for eleven windows and `nan` for three.
`joint_solve` sets `gap_target = -winter_gain` with `CURVE_GAP_WEIGHT = 100`,
and the run carried `XCP_WINTER_GAIN=0.02` — **the curve was told to be
−0.02**. It agreeing with a sandwich D of −0.02795 is the pin, not evidence.
`curve_window_gap.py` now detects that and refuses to compare. To get a real
second opinion the solve has to be re-run with `--winter-gain 0`.

The `nan`s are a sentinel (a pool with no dual-sport balance), not a bug —
now counted and skipped rather than averaged into a `nan` mean I then drew a
conclusion from.

⚠ Also worth noting: that run's own bbar was **+0.00361**, nowhere near the
−0.03924 the tool quotes. Confirms the delta-not-absolute call.

**(a) `--split-indoor`.** Indoor and outdoor are one sport to the engine —
one indicator, one `mu`, one pool anchor — so no sport-level scale error can
sit between them. Whatever gap they show is phase.

    D(indoor − outdoor)  = pure phase (+ any indoor geometry error)
    D(XC − indoor), D(XC − outdoor)  would be EQUAL if the gap were pure scale

The report prints the ratio `(XC−out − XC−in) / (in−out)`: **+1.00 means the
two XC arms differ by exactly the indoor/outdoor phase**. Verified on a
fixture with three planted levels under a 0.05/yr improvement — every level
recovered to 1e-9, trend cancelled.

**(b) `curve_window_gap.py`.** The solve already fits a per-pool form curve
and prints `curve TF-XC window gap` every pass — the same quantity from a
completely different estimator (the shape of the season for *everybody*, not
just crossers). Curve ≈ D means the curve already absorbed it and correcting
the level double-counts; curve ≈ 0 means D is more likely real scale.

⚠ **Even after (a), the XC-arm average is still an upper bound** — indoor vs
outdoor bounds the phase *within track only*. Subtract at most the
indoor/outdoor figure before passing anything to `--sport-gap-delta`.

`tests/test_phase_split.py`

Still possibly the same coin as 5.3: a systematic XC-difficulty compression
shows up as "TF looks overrated". Worth re-measuring D after 5.3 lands.

### ✅ 5.3 XC course difficulty — **not compressed, and not a CA thing**
> *"look at if it's a CA thing as well"*

The symptom, from Lex Young's dump — his six California courses:

    Woodbridge 0.1%   Clovis 0.2%   CIF State 0.2%
    Marmonte 1.7%     CIF-SS 5.8%   NXN 5.9%

A flat September 5k and a hilly November one do not differ by a fifth of one
percent, and the three with the **largest fields** are the ones reading as
exactly average.

    scripts/difficulty_spread.py            # seconds
    scripts/difficulty_spread.py --top 40

**Two explanations, different fixes, and §2 of the report separates them:**

- **shrinkage** — thin courses pulled toward zero by a prior. Then the sd of
  difficulty **rises** across the `n_results` buckets and the big courses are
  already honest.
- **a flat scale** — every course near zero however much data it has. sd
  stays flat, and more data will not fix it.

**§3 and §4 ask the CA question both ways round**, because both answers are
plausible and need opposite fixes:

- CA **wider** → the estimator works where the data is dense and the rest of
  the country is being shrunk.
- CA **narrower** → the big CA courses are effectively defining zero, and the
  compression starts there.

§4 also weights by results, not by course: CA has many small courses and a
few enormous ones, and "what does a random *race* look like" is the question.
§5 lists the biggest courses — if those cluster at 0.00, the scale is
anchored on them.

Read-only, one pass over `meets`, no per-row lookups.
`tests/test_difficulty_spread.py`

---

**RESULT (2026-09-09): the compression hypothesis is wrong on all three
tests.**

**Not compressed.** 47,165 XC courses, sd **6.09**, p05 −4.14 to p95 11.25 —
a 15-point span. And the famous courses land where they should:

| fastest | | hardest | |
|---|---|---|---|
| Detweiller Park (IL) | −1.81 | Hereford HS (MD) | 10.51 |
| Great Park (CA) | −1.55 | Van Cortlandt (NY) | 6.76 |
| Woodbridge HS (CA) | −1.50 | Mt. SAC (CA) | 5.85 |

Detweiller and Woodbridge are the two fastest courses in the country and read
negative; Hereford, Van Cortlandt and Mt. SAC are the hardest and read high.
**The model is working on well-evidenced courses.**

**Not a CA thing.** California is *wider* than average, not narrower —
sd 6.96 vs 6.01, span 17.25 vs a 11–21 range across states. It also reads
slightly harder (weighted mean 2.23 vs 1.70), which is plausible on its face.

★ **The real problem is the opposite of shrinkage.** sd *falls* monotonically
with evidence:

| n_results | courses | sd |
|---|---|---|
| <50 | 13,610 | **8.33** |
| 50–199 | 14,768 | 5.87 |
| 200–999 | 12,592 | 4.28 |
| 1k–5k | 5,096 | 3.18 |
| 5k–20k | 913 | 2.48 |
| 20k+ | 186 | 2.43 |

Thin courses are **under-shrunk, not over-shrunk** — that 8.33 is noise, not
signal. **28,378 of 47,165 courses (60%) have under 200 results** and carry
sd 6–8, and every rating computed on them inherits it. The fix is *more*
shrinkage for thin courses, not less.

💤 **Two loose ends, not chased yet:**
- **Impossible outliers**: min −34.54%, max **173.06%**. Nothing is 173%
  harder than average; those want a cap or a look at what they are.
- **The mean is +2.57, not 0.** Whatever "0" is anchored on is faster than
  the average course, so the whole XC scale carries an offset.
- TF shows `no_state` for all 27,205 rows — the state map only reads `meets`
  (XC). Cosmetic; TF "courses" are tracks and their sd of 0.51 is expected.

Mt SAC and Crystal Springs +8% → +4%; Foot Locker at Morley Field +0.7% on a
hard course; Ultimook back to +8.7%. **Your own hypothesis is the one I would
chase**: *"might be something with distance solve with really good runners vs
avg."* Foot Locker and Mt SAC are elite-only fields and compressed; Ultimook is
a broad 1A–4A field and went the other way. That is a field-composition
pattern, not a venue one — and it is consistent with the 10k-too-slow /
800-too-fast conversion skew.

### 💤 5.4 Conversions
10k too slow, 800 possibly slightly fast. Everything else "looks rlly good".

### 💤 5.5 Women's 800m
Suspected larger rating issue than men's.

### ✅ 5.6 Race-day tilt — the PAGE was lying, the ratings were fine
The log settled it:

```
race-day term, XC: median 1.67 points at 130, p90 4.82, OUT OF the rating
race-day term, TF: median 1.18 points at 130, p90 3.01, OUT OF the rating
```

The term is in neither rating. But `app.RACE_DAY_SPORTS` defaulted to
**`"XC"`** — set when the engine's default *was* XC, and never moved when the
engine's default became none for both sports days later. So the hover told you
every XC rating from a slow day was *"raised by"* that amount, which was false
about the number underneath it. **You were right to doubt it; the doubt just
belonged to the sentence, not the rating.**

*Default is now empty, matching `run_joint`'s own, and
`tests/test_race_day_wording.py` pins the two together — they live in
different files and different languages and nothing connected them. The
not-carried wording also stopped hardcoding "Track", since that branch now
renders for XC races too.* Live on restart.

---

## 5-0. THE RESTRUCTURE — ability per sport-season, and one written-down scalar

> *"fitness should have a mean of 0 in season but fitness needs to apply to
> course difficulty"* … *"how do we measure the free scalar?"*

**You can't.** Sport is season, the two halves share no data, so the relative
level of XC against TF is a **definition**. Everything below is about making
it one definition in one place instead of four implicit ones.

### What's built

- **ability per `(athlete, year, sport)`** — `--split-ability`. It was keyed
  `(athlete, year)`, so one number had to serve an autumn 5k and a spring
  800; `beta` was bolted on to patch the difference. Split, the
  autumn-to-spring gain **is** the difference between two abilities, and it
  reaches the rating because the rating *is* the ability. Nothing is deleted
  at go-live.
- **`beta` off** — implied by the flag; there's no shared ability left to
  patch, and leaving it in would fit the sport level twice.
- **`XC_TRACK_GAP = 0.0583`** in `joint_solve` — the free scalar, named,
  once.

⚠ All three `buildDesign` call sites pass it, including the holdout's two — a
holdout scored on a differently-keyed ability is scoring a different model
from the one that goes live.

### The scalar: track is the reference

`ln(1.06) = 0.0583`. The scale then means **"what you'd run on a track."**
From coaching practice, not from this corpus — same distance on grass:

| firm / flat | average | hilly | muddy championship |
|---|---|---|---|
| ×1.03 | **×1.06** | ×1.08 | ×1.10 |

Physiology agrees on the size: grass costs roughly 5% more energy than a hard
surface at the same speed.

**Constant, not a function of ability — and that was checked, not assumed.**
The surface cost is a per-step energy loss and stays a roughly constant
*fraction* of running economy across speeds. The course-to-course variation
(the 3→10% ladder) already lives in `course_difficulties`; putting it in the
scalar too would count it twice.

**How to falsify it in one command.** Anchored at track, the XC difficulties
must land on that ladder — famous fast courses near +2%, average near +6%,
brutal ones +10–15%, and **nothing meaningfully negative** (nothing is faster
than a track). `scripts/difficulty_spread.py` prints exactly that
distribution.

### Still open — the curve

⚠ **Not built, deliberately.** With ability split by sport-season, a constant
inside one window is still confounded between the curve and the abilities in
it, and the curve is dropped at go-live — so it can still delete signal. The
fix is to move each window's curve mean *into* the abilities (a
reparameterisation, predictions unchanged), not to penalise it toward a
guess. That needs care with the amplitude weighting and the pinned reference
knot, and I've shipped one wrong version of this already tonight.

`tests/test_split_ability.py`

---

## 5a. ONE SCALE THAT MEANS FITNESS — `--merge-sports`

> *"so how do we get one scale that actually means fitness"*

**Sport is season.** XC is autumn, track is spring, and nobody races both
close enough together for fitness to be held constant. So *"track courses are
easier"* and *"athletes are fitter in spring"* are the **same sentence** in
this corpus. No estimator can split them — which is why the sport gap came
back as a closure error of +0.01440 instead of a number. **It was never
identified.**

★ **But it is exactly one scalar.** Relative difficulties inside autumn are
pinned by athletes racing several grass courses each fall; same inside
spring; same for the curve's shape inside each window. The only thing the
data cannot see is the mean offset between the two sets — and
`recentreLevels` is already where that number lives, banked in `mu`.

★ **`--merge-sports` asserts it is zero.** The per-sport means of course
difficulty **and** of the race effect are dropped rather than banked, so the
two sports' average course is equal by construction. Then autumn-to-spring
movement has nowhere to go but the form curve — which is fitness.

    XCP_MERGE_SPORTS=1 XCP_ALTITUDE=1 \
      bash deploy/run_pipeline.sh --from 7 2>&1 | tee logs/run19.out

**Drop `XCP_WINTER_GAIN` and `XCP_WINTER_GAIN_BANDS` from the line** — they
are the assumption this replaces. Leaving them set is harmless (run_joint
drops them and says so) but pointless.

It implies, and applies, **five** places the sport level is asserted today —
four in the solve and one after it:

| flag | what it was |
|---|---|
| `--no-sport-offset` | the per-athlete sport offset `beta` |
| `--winter-gain 0` | the curve pinned at `gap_target = -winter_gain` |
| `--curve-gap 0` | the `CURVE_GAP_WEIGHT = 100` penalty enforcing that pin |
| `--sport-gap-delta` refused | meaningless with no sport level to shift |
| `--winter-gain-bands` dropped | ⚠ **a post-solve shift of the track rows at go-live** (issue 194) — not in the solve at all, so `merge=True` would have been undone by hand right after the fit |

That fifth one is why this is a single flag and not a command line the
operator assembles.

! **`targetFor` already ignores the sport** — its own docstring: "the bare
key is authoritative", so hs_m normalises to 5000m in *both* sports. The
shared ruler this rests on is already there. **Nothing needs re-normalising
and no backfill is required.**

⚠ **This is an assumption, and it replaces four implicit ones.** Today the
same scalar is set by `XCP_WINTER_GAIN=0.02` pinning the curve at weight 100,
tangled with `beta`, the ridge and `mu` — four places, interacting, none
labelled. This is that choice made once, in the open, where it can be argued
with.

**No second run is needed to find the winter gain.** Under `--merge-sports`
there is no winter gain input: the curve is free and *reports* the
autumn-to-spring path instead of being told it. Read it afterwards with
`scripts/curve_window_gap.py logs/<run>.out` — unpinned, that number is now
meaningful.

**Constant, by grade, or by ability?** Neither constant nor a new knob: the
curve is already fitted **per pool** (elem/ms/hs/college × gender ≈ grade
band) with 14 knots, and its amplitude is already tilted by ability
(`amplitudeFromRating`, `AMP_TILT_PER_POINT`). So it is per-grade and
ability-scaled today. It has just never been allowed to move. Free it first,
look at the shape, then decide whether it needs more structure.

`tests/test_merge_sports.py`

---

## 5b. THE RESET — two ratings and one conversion

> *"there must just be some way to say that this is a 145 in xc, and it
> correlates to a 4:00 mile fitness wise at that moment."*

There is, and it is a **different question** from the one the solve asks.

    the solve   one ability per athlete plus a sport offset -- "how good are
                they, and how much do they specialise?"
    this        a MAPPING -- "an XC rating of R at this time of year goes
                with what track performance?"

★ **The first question has no answer in this data.** The closure error
(+0.01440, 20–90 SE) says the three arms do not share one set of levels.
Every hour spent hunting a single XC/TF offset was spent hunting something
that is not there.

★ **The mapping does exist, and the closure error IS its shape.** It is the
conversion varying with the calendar — which is exactly what an athlete
experiences. So stop removing time to recover a constant; keep it.

    scripts/xc_tf_bridge.py --pool hs_m

Three tables: TF-minus-XC by **month** of the track race; the same by
**level** (a constant ratio is an assumption, so it gets checked); and what a
TF rating is **in seconds** over the mile. Compose them:

    an XC rating R in month M  ->  TF rating R x (1 + pct/100)  ->  a mile time

On a fixture with a month-by-month gap planted, it recovered every month
exactly and turned a 145 into a **4:00.3 mile**.

⚠ This is a measurement. It writes nothing and changes no rating. It exists
to show the mapping is there and has a shape, so the engine can be pointed at
it — and it is the natural companion to splitting the sports (§5.2), not a
replacement for it: rate each sport on its own scale, then publish the
conversion between them. That says strictly more than one blended scale, and
it is honest about the part that moves.

---

## 6. WHAT I NEED FROM YOU

`racecast.co` is blocked from my sandbox by the environment's egress policy,
so I cannot open the athlete links, and you should not have to write SQL for
me. One command gives me everything:

```sh
/srv/venv/bin/python scripts/athlete_dump.py 26155532 23965611
```

It prints, per athlete: identity, `athlete_season` rows (the rating the boards
rank), every rated race with `rating_pool` / `normalized_time` / course
difficulty, the exclusion tables, **whether the live chair rule matches any of
their rows regardless of the list**, and how many rows lost their
`normalized_time`.

That last pair is the whole chair diagnosis in two lines: in the list → fine;
not in the list but the rule matches → the list went stale (§1.6, fixed); not
in the list and the rule matches nothing → the rule never saw them, which is a
different bug and needs a different fix.

Read-only — every statement is a SELECT, safe with the site running.

## 2026-09-15 — two engine issues, deferred behind the model run

Both reported by the owner while the TF conversion scale break was being
fixed. Neither is a page bug; both are the engine's own numbers. Logged
rather than fixed, by the owner's call: "I want to train and extract the
model first".

### A. Track difficulty is not believable

> "tf difficulty is just so insane. like we have indoor tracks that are 4%
> easy supposedly. Idk maybe we just say nixsay on track difficulty? It
> doesn't seem to work well."

A 4% easy indoor track is roughly 22 seconds on a 9:00 3200, which no
banking or surface explains. The suspicion to test first is that a track
"venue" has far less genuine course variation than a cross-country course
does, so the solver is fitting NOISE into a free per-venue parameter --
the same failure mode `feature_extraction`'s venue embedding already
guards against with a 50-race floor, and the same one that gave Mission
Concepcion a +0.7585 delta and 176 ratings.

Options, cheapest first:

1. **Shrink it.** A prior pulling every track venue toward 0.0, strength
   by race count. Keeps a real banked-track effect if one exists.
2. **Floor the race count** before a track venue gets its own parameter at
   all, as the embedding does.
3. **Drop it.** Every outdoor track is the display zero already
   (`venueEffect`: "a track with no venue is the zero"); indoor would need
   one asserted constant rather than a per-venue solve.

⚠ Whatever is chosen, `course_difficulty` is BOTH a model feature and part
of what `normalized_time` is solved against, so changing it invalidates an
extraction.

### B. The distance spline is off and wants redoing

> "the distance spline is just off. I think we need to entirely just redo
> it tbh."

Suspected to be behind the XC conversions still reading high after the TF
fix, since the curve is what relates a 5K to a 3-mile to a 2-mile.

`scripts/diag_conversion_gain.py` separates the two halves and will say
whether this is the curve or the page:

```
    /srv/venv/bin/python scripts/diag_conversion_gain.py --sport XC --pool hs_m
```

* half **A** (rating ↔ normalized_time) off → the page's pool mean or
  engine scale, a racecast bug.
* half **B** (time ↔ normalized_time) off with A clean → the distance
  curve or the difficulty, an engine bug. This is the one to expect here.

⚠ Same warning as A, more so: `normalized_time` is the model's TARGET. A
new spline means a new extraction and new weights.

### ⏳ C. Predicted times are on the target's clock now — but the corpus distances are still suspect

> "why do ppl predct so fast it's predicting an 8k adn knows it right? (is it
> bcs of most 8ks they run are mislableed 5ks?)" — owner, 2026-09-16
>
> "we need to fix the 8k issue as well now how can we do that?" — same day

**The display half is fixed.** `predictInterval` returns
`baselineSeconds * exp(mu)`, and `baselineSeconds` is the athlete's last
race's `normalized_time`, so the model's output is on whatever scale the
corpus stores. `predictions.js` printed it through `fmtTime` with no label,
whatever the target race was. It is now converted to the target's own
distance and course, and the page says so when it cannot convert.

⚠ **And inverting through the distance spline would have made it much
worse.** The engine defines `normalized = raw * (5000/d)**k`, so the curve
turns a 24:16 into **39:57** at 8000 m. But 24:16 is already a believable
8K, and its 5K equivalent (14:45) rates **139.7** — which is the rating that
athlete actually carries. **The number on the page was already behaving like
a raw 8K time.** That is only possible if the rows behind it are raw 8K times
recorded at 5000 m, with the forward factor `(5000/5000)**k = 1` and nothing
normalized. The owner's guess was right.

★ **So the conversion is measured, not modelled.** Every corpus row carries
both `time_seconds` and `normalized_time`, so their ratio is what the engine
*actually* did to that race, whatever its label claims. `predict._distanceRatio`
takes the median of that ratio over the athlete's own races within 6% of the
target distance. It is correct under either world:

| | ratio measured | model predicts | shown |
|---|---|---|---|
| labels correct | 1.646 (= the curve, exactly) | 885 s (5K-equiv) | 24:16 |
| labels wrong | 1.000 | 1456 s (raw 8K) | 24:16 |

It cannot be fooled by a mislabelled *target* either: a target recorded at
5000 that is really 8000 matches the athlete's 8K rows recorded at 5000, and
the ratio measured on them is the right one. Two rows minimum, median not
mean. `conversions.normalized_to_time` — the site's own inverse, used by the
conversions page — stays as the fallback for a distance the athlete has never
raced.

**What is still open is the data.** The arithmetic above is strong evidence
that college distances are mislabelled, but it is inference from one athlete,
not a count. Run:

```
    /srv/venv/bin/python scripts/diag_distance_labels.py \
        --skip-sample --person <person_id>
```

* `n/raw` ≈ 0.66 → those labels are right after all, and the ratio simply
  reproduces the curve. Nothing more to do.
* `n/raw` ≈ 1.00 on a 24-minute college race → confirmed. **That is a corpus
  bug, not a display one**, and it invalidates the college half of an
  extraction: the model trained on raw 8K times as though they were 5K
  equivalents, so every cross-distance comparison it learned is wrong. A
  distance repair over the college corpus is then needed, and it invalidates
  an extraction exactly as **A** and **B** do.

⚠ The ratio makes the PAGE right in both worlds. It does not make the MODEL
right in the second one.

### ✅ D. The weather features cost 5% and bought nothing — adjustment off by default

> "I don't think the weather is going right tbh, I don't think its getting it
> right." — owner, 2026-09-17

Right, and it was worth more than it looked. Measured with
`scripts/diag_model_quality.py` on a 152-athlete championship — same model,
same field, same race, only the weather basis changed:

| weather | overall bias | median error at a normal gap |
|---|---|---|
| normal | **−5.0%** | 5.0% |
| none | **+0.4%** | 1.9% |

Section E said why it is not weather modelling: the normal moved every
prediction **−5.44%**, with p05 −6.25% and p95 −4.28%. A near-constant offset
for the whole field. Real conditions help some athletes more than others; one
number for everybody is the signature of a **feature distribution the model
never trained on**.

★ The mechanism is in the extraction. `_orZero` writes a NULL as `0.0`, so
**pressure 0 hPa — physically impossible — is how "we do not know" is
spelled**, in the same slot where 1013 is a real reading. Training saw a great
deal of the first shape; inference feeds the second.

*The default is now `none`, in `app._target` and `predict._predictTimes`
both.* `?weather=normal` still asks for it, so this is reversible without a
deploy, and `/api/predict/weather` still shows the reader the conditions —
what is withdrawn is the model adjusting a TIME for weather it cannot use.

⚠ **And it fixes the times, not the ranking.** Spearman went 0.899 → 0.900
and inversions 13.4% → 13.4%. A uniform shift cannot reorder anybody. The
ordering problem is section E below, and it is a different fault.

**To actually use weather** the extraction has to stop conflating missing with
zero: a `has_weather` flag beside the values, or NULL imputed to the corpus
mean rather than to a number that means something. Either needs a
re-extraction, so it belongs with **A** and **B**.

### 🔎 E. The network's correction is noise on the pairs that matter

Same run, and this is the one the owner actually complained about —
"it has me losing to my teamate who I beat in every single race that season."

Overall ordering, against what the day did:

| | spearman | inversions |
|---|---|---|
| the model | 0.900 | 13.4% |
| its baseline alone | 0.792 | 19.9% |
| season mean rating | **0.904** | **12.8%** |

A 4.05M parameter transformer loses to a one-column `ORDER BY`. But the
within-team rows are worse than that:

| within a team (450 teammate pairs) | inversions |
|---|---|
| the model | **20.7%** |
| its baseline alone | 20.0% |
| season mean rating | 19.1% |

⚠ **On close pairs the model is worse than its own baseline.** Teammates are
nearer in ability than two random runners, so every row here is higher — but
the model's row being *above* the do-nothing row means the network's
correction is not information at that resolution, it is noise. It is actively
making the hardest pairs worse.

That is a **different fault from a bad anchor**, and the two need different
fixes:

1. **The anchor.** `predictInterval` returns `baselineSeconds * exp(mu)` and
   the baseline was one row. `transformer.BASELINE_EWMA` replaces it with a
   recency-weighted geometric mean — a **retrain, not a re-extraction**,
   because the chunks store raw targets in seconds. This should lift the
   baseline row toward the rating row and drag the model's with it.
2. **The correction.** If the network is adding noise, a better anchor does
   not fix it; it just gives it a better thing to spoil. The candidates, and
   none is cheap: it has no field-relative feature (section 2 below), it is
   always told `is_forecast=1` at inference, and `normalized_time` — the
   thing it is trained to predict — is under suspicion from **B** and **C**.

★ Do (1) first anyway: it is an hour of GPU and it re-measures cleanly with
`diag_model_quality.py --model`, whose "its baseline alone" row now reads the
rule off the checkpoint rather than assuming the last race.

### 🔎 F. What to take from LACCTiC, and what not (engine only)

Owner, 2026-09-17, on `lacctic.com` — Bijan Mazaheri's NCAA ratings site.
⚠ Read from its FAQ only: the site and the author's page are both blocked from
the dev environment, so the method below is a reconstruction, not a reading.

**How it probably works.** *"we can look at the median difference in times
between courses… comparing all possible runners and courses"* reads as
**pairwise differencing**: for each course pair, take runners who ran both,
median `ln(t_A) − ln(t_B)` over them, then reconcile the graph. Differencing
within a runner *eliminates* ability rather than estimating it. Their
"races are not included until there is enough data to score them" is that
graph's connectivity.

★ That structure is not new to us — `bracket_engine.py` is the same family
(Tully's method) and goes further, subtracting a form curve so a fitness
change between two races is not charged to the course. **The robustness
choice is what is new: they take a median where we take a mean.**

#### Taking

1. **⏳ Median over a race's voters.** `bracket_engine` had *no* medians while
   `joint_solve` has five and `conversions.default_difficulty` takes a
   weighted one. A mean has no breakdown point: five honest voters at +0.018
   plus one scraped 1-second row read as **−0.652** — "this course is 65%
   easy" — where the median moves 0.005. *Landed as a rung:*
   `fit(..., voter_agg="median")`, default `"mean"`, same shape as
   `topFractionWeights`. Not obvious that it wins — the field is already
   trimmed to the top fraction, and a median of three voters is noisier than
   their mean — so the held-out score decides.
2. **🔎 A track anchor — REOPENED, my count was broken twice over.**
   The first run said 356 of 4,045,851 (0.009%), which should have read as a
   broken query and instead nearly read as a finding. Two bugs:
   it took the distance from `meets_tf.distance_meters`, when TF distance is
   parsed from `results_tf.event_short` (64,079 distinct spellings, see
   `engine/event_parse`) into **`ranking_results.distance`**, already parsed
   and already rated; and it **presumed 5000 m**, when high schoolers race
   3200 m on a track and the corpus is ~72% high school. Asking a
   high-school corpus about a 5000 asks almost nobody.
   Section B now asks *what track distances XC-rated athletes actually race*,
   per level, most-covered first — the anchor candidate is whatever tops that
   list, and it will differ by level.
   ⚠ The representativeness check still applies and is the thing most likely
   to kill it: on the (broken) 5000 sample the athletes who had one rated
   **120.2 against 101.2**. B2 re-runs that against whatever the real
   candidate turns out to be.

   The reasoning, kept because the PROBLEM is real and still unsolved:
   I dismissed this twice as display and
   was wrong twice. `speed_ratings` re-centres to **result-weighted mean
   zero**, so "difficulty 0" means *the average course in our corpus right
   now* — a number that moves when the corpus changes. A physical reference
   does not. And with two sports it stops being a pure gauge: XC is anchored
   to its own average, TF to "average outdoor track", and the sport gap
   between them is a *fitted* parameter with no external check. One physical
   zero makes that gap testable instead. ⚠ Risk: the 5000 population is
   selective, and ISSUES A says track difficulty is the least believable cell
   in the engine — anchoring to the shakiest thing we fit. **Measure first:**
   `scripts/diag_engine_counts.py` section B counts how many rated XC
   athletes have an outdoor 5000 *and whether they rate like everyone else*.
3. **🔎 Quantile, not mean, for athlete ability.**
   `computeAthleteAbilities` takes a weighted mean. Performance is
   `ability − shortfall` with shortfall ≥ 0, so a mean estimates
   `ability − mean(shortfall)` — and **the bias differs per athlete**,
   because it depends how many junk races their schedule contained. Two
   identical runners get different ratings from different schedules, which is
   what inverts head-to-heads. The principled form is a fixed quantile, not
   LACCTiC's "best two" (which is your top 50% at four races and your top 10%
   at twenty) — and a top-25% quantile is **Slaney's filter pointed at the
   athlete axis**, already tested and accepted on the course axis. Costs:
   noisier for few-race athletes, wants shrinkage by race count, and
   invalidates every rating on the site. Planted worlds first.
4. **🔎 Race importance as a weight on ability.** `abilityWeights` exists
   because the back of a field is not a measurement of the *course*; it is
   not a measurement of the *runner* either. League/qualifier/final are
   already fitted and unused here.
5. **❌ Stop imputing `d = 0` silently — DEFERRED, my number was wrong.** An unfitted course asserted to be
   exactly average pushes its difficulty into the ability of everyone who
   raced it, and because the solve is joint that leaks into every other
   course they ran. `diag_engine_counts.py` section A counts the share.

   ⚠ **The first run said 26.7% of COLLEGE rows and that was my query's
   bug.** It joined only `meets` — the anet table — while tfrrs XC venues
   live in `meets_tfrrs`, keyed on `meet_id` alone. The engine's own
   `_xcQuery` COALESCEs the two; measuring an engine with a join the engine
   does not use measures nothing. Fixed and needs re-running. The `hs` 1.5%
   and `ms` 3.7% figures are anet-sourced so less affected, but are not
   quotable until the re-run either.

#### ⏳ G. The model could not see a quarter of college cross country — **CONFIRMED**

`diag_engine_counts.py` section C, 1% sample:

| source | level | rated | in `meets` | reaches training |
|---|---|---|---|---|
| anet | college | 19,389 | 19,389 | 100.0% |
| **tfrrs** | **college** | **6,505** | **0** | **0.0%** |
| tfrrs | hs | 2,564 | 0 | 0.0% |

**6,505 of 25,894 — 25.1% of all rated college XC rows** — rated by the
engine, shown on the site, invisible to the model. College is the level the
model performs worst on. hs and ms lose ~1% each.

★ **It took four separate filters to let them in**, and fixing any one alone
changes nothing while looking like a fix:

1. `JOIN meets` was an **INNER** join and `meets` is the anet table; tfrrs XC
   venues live in `meets_tfrrs`, one row per (meet_id, sport).
2. the `WHERE` required a distance from the same two-term COALESCE, so a
   tfrrs row would have been filtered straight back out.
3. `r.athlete_id IS NOT NULL` — and **tfrrs XC has `athlete_id` NULL on 100%
   of rows**. Written to drop profile-less AAU entries; it deleted a source.
4. gender came off a LATERAL keyed on `athlete_id`, so every tfrrs row would
   have had NULL gender — and gender feeds `resolvePool`, which picks the
   pool the target was normalized in. Now `COALESCE(pg.gender, a.gender)`
   from `person_gender`, stubbed like `weather` when the table is absent.

The engine already did all of this — `speed_ratings_db._xcQuery` LEFT JOINs
both meet tables and COALESCEs them, and its header lists this exact bug
among ones it fixed once: *"INNER JOIN meets — anet-only table; deleted tfrrs
again."* The extraction never got the same fix.

⚠ **Needs a re-extraction to take effect, and the SQL has not been executed.**
`corrections` is server-only and 51 MB, so the query cannot be built off the
server; `tests/test_extraction_sees_tfrrs.py` pins the structure of all four
parts but a smoke extraction over a few chunks is still required before a
full run.

★ **This reorders the rest of the list.** Any model change measured against
college fields before this lands is measured on a model that never saw a
quarter of them.

### ✅ H. The distance spline is NOT the problem

ISSUES B's premise was *"the distance spline is just off. I think we need to
entirely just redo it."* `diag_conversion_gain.py --sport XC --pool hs_m`
says otherwise:

* **half B (time ↔ normalized_time): mean |err| 0.081%.** Clean. That is the
  distance curve and the difficulty, and they reproduce times to within a
  tenth of a percent.
* **half A (page ↔ engine applied effect): mean |err| 2.312%**, median
  +0.081%, IQR −2.21…+1.76.

By the diagnostic's own rule — *"half A off → the page's pool mean or engine
scale, a racecast bug; half B off with A clean → the distance curve or the
difficulty, an engine bug"* — this is **a page bug, not a spline bug**. The
median is near zero so the two agree on average; the ±2% is per-row scatter
in `venueEffect`/`engineScale` against what the engine actually applied.

**Independent corroboration.** Our equivalents against VDOT for a 29:53 10K:

| | VDOT | ours | diff |
|---|---|---|---|
| 5000 | 14:21 | 14:13.12 | −0.9% |
| 3200 | 8:51 | 8:51.23 | ~0 |
| 3000 | 8:15 | 8:15.10 | ~0 |
| Mile | 4:10 | 4:07.96 | −0.8% |
| 1500 | 3:51 | 3:49.05 | −0.8% |

Within ~1% of Daniels everywhere, exact at 3000–3200. One mild systematic:
ours is faster at **both** ends, so the curve is slightly **over-curved**
about its 3000–3200 anchor — ~0.9%, worth a look, nothing like "redo it".

*So a re-extraction for the spline is not justified.* **G** justifies one on
its own.

### 🔎 I. The grass-cost anchor is an unweighted mean, and outliers drag it

From the same run: *"every difficulty is anchored on the **unweighted mean**
over outdoor track cells. Wild per-venue track estimates drag that mean, and
the shift lands on every XC course at once — which is a conversion error with
a correct normalized_time behind it."*

So the engine **already anchors XC to outdoor track** — the thing I spent two
turns proposing. `anchor_shift = −0.05830`, identical across every pool. The
open question was never *whether* to anchor there; it is that the anchor is a
**mean**, and ISSUES A says track difficulty is the least believable cell we
fit.

Measured grass cost vs the expected 5.83%: hs_m +0.04, hs_f +0.08, college_f
+0.21, college_m −0.29, ms −0.36/−0.43, pro −0.40/−0.48, **elem_f −1.19 and
elem_m −1.14** (flagged).

★ **This is the median-vs-mean argument again**, pointed at the anchor rather
than at a race's voters — same fix as ⏳ F.1, same reason, and here a single
wild track venue moves *every XC course at once*. Cheap and targeted.

#### Not taking

* **Pairwise differencing as a replacement** for the joint solve. It throws
  away every runner who raced one course and is far less efficient. We have
  both; keep both as rungs.
* **Freezing closed seasons.** Right in principle — a published number should
  not move, and an A/B against historical races currently measures model
  change and corpus drift together. ❌ **Deliberately deferred** (owner:
  "freeze — not until methodology correct"): freezing before the methodology
  is right makes the wrong numbers permanent.
* **Crowd-sourced injury/community input.** Well designed — the report clears
  when they race again — but it does not feed their ratings and should not
  feed ours.
* **Their gap handling.** "Ask, don't infer" is right for *is this athlete
  injured*, which is genuinely unidentifiable from results. It is wrong for
  *is this gap normal*, which IS identifiable because we have the field. That
  one is ours and it is better than theirs.

### The ordering that follows

The model run comes first, and it is a PREFIX run, not the full corpus
(HANDOFF 13.3 and 15). Both fixes above invalidate any extraction, so the
weights from this run are a proof of the architecture and a way to light
up `/predictions`, never the final model. Sinking twenty GPU-hours into a
target that is about to change is the thing to avoid; an hour on two
million examples is not.

## 2026-09-16 — the extremes, the row kills, and two schools with one name

### ⏳ J. The shipped distance curve is PRE-FLOOR, and the XC extremes are junk

H said the spline is not the problem and that stands for what H measured —
the round trip `time → normalized_time → time` at one pool, which is
self-consistent by construction and 0.081% clean. It does **not** test
whether the exponent is *right*. `scripts/distance_curve_check.py` does, and
the shipped `engine/data/distance_spline.pkl` (built 8 Sep) answers:

```
college_m|XC   8000->10000   0.920 !     <- and 8000 is its own anchor
elem_m|XC      past 3200     0.982 !
elem_f|XC      past 3200     0.964 !
hs_m|XC        1.102 at 3200->5000 RISING to 1.137 past 6000
ms_f, college_f|XC           1.033-1.034 at the long end
```

**20 segments outside [1.04, 1.20], every one of them XC, all at the ends.**
An exponent under 1.0 says a runner's pace *improves* as the race lengthens.
This is the owner's "at extremes it's going faster", measured.

Two separate causes, both now fixed in the fitter:

1. **`MIN_LOCAL_EXP = 1.04` landed on 13 Sep; the artifact is from 8 Sep.**
   The live site is running the 0.920. ⚠ **A refit is required for any of
   this to take effect** — the floor and the smoother are fitter-side.
2. **`EXT_SLOPE_BAND` was (0.85, 1.30)** — so a *measured* beyond-span
   extension slope of 0.92 was **accepted** and then overwritten by the
   floor two lines later. Two rules disagreeing about one number; the band's
   low end is the floor now, read at call time so `--min-exponent` moves both.

And the floor alone was never enough, which is the answer to *"how can we
best fit a clean line?"*:

* A floor clamps the low side and **cannot see a wiggle**. With it applied,
  `college_m|XC` still runs 1.065 → 1.077 → 1.099 → 1.056 → 1.040 across
  3200–10000 — up then down, through every distance college XC is raced at —
  and `hs_m|XC` still *rises* after 5000.
* `MONOTONE_SPORTS` was `("TF",)`. **That is why every bad segment is XC**:
  TF's curves had already been smoothed to a non-increasing local exponent
  and had zero flagged segments. XC is in it now. Floor then monotone leaves
  **0 of 20** flagged, and the order is safe only that way round (a pooled
  block's mean is never below its own minimum).
* The old defence was "grass fades are not one shape". A fade is a fact about
  the **runner**; the surface's cost is what course difficulty is for. The
  0.920 is what the freedom actually bought.

**The reference line.** `--records` now prints the world records' own implied
exponent beside each curve (`recordSegments`, from `record_pace`): 1.088 at
1500→3000, 1.069 at 3000→5000, 1.056 at 5000→10000, monotonically falling.
Not a target — a record holder fades less than a ninth grader, so a pool
should sit a little **above** it — but it says which way is up.

🔎 **And it exposes a bigger one.** `hs_m|XC` runs 1.12–1.14 where the same
athletes' `hs_m|TF` runs 1.075 and the records say 1.06. Same runners, same
fade, ~0.05 apart by surface — which is **2.2% on a 5000 → 8000 conversion,
about 32 s on a 24:00 8K.** That is the size and the sign of the 8K
complaint (⏳ C). Either XC's longer races carry a confound a same-athlete
pair does not cancel (championship timing, harder courses, stronger fields),
or the cost is real and course difficulty should be absorbing it. **Open
question for the engine discussion: fit the distance curve on the track,
where the confounds are least, and let XC's difficulty carry the rest.**

### ✅ K. A deleted row cannot fix the pool that deleted it

Owner, 2026-09-16: *"undo all the indiv rows overrides, and then redo them so
they don't catch college ahtlets (do it by hs-equivalent scale not own pool
scale)"*, against *"for college runners a lot of them have all their races
killed bcs their grade is untrusted / their rows were overrode"*.

The row kills are `impossible_result`'s pool-floor branch. The floor was the
row's **own** pool's, read off the `rating_pool` the last go-live wrote — so
the test depended on the pooling, and the rows it catches are by construction
the ones the pooling got **wrong**. A college runner or a professional whom
the club rules filed `ms_m` had every real race measured against a middle
schooler's floor and deleted; with the races deleted the solve never sees
them, so the next run cannot repool the athlete off them either. **A ratchet:
one pooling mistake and the career is gone for good.** Those 2,645 condemned
track races are the evidence — mostly open 1500s run in 3:54 by adults the
club pooling had called elementary schoolers. The races were never wrong.

Now: one scale for everybody, the **hs-equivalent** floor
(`record_pace.poolFactor`, 1.01 × the open record). "Is this time physically
possible" is about the time; "is this athlete really a middle schooler" is
about the pool, and belongs to `pool_resolve` — which now has the feeds' own
team levels to answer it with. The per-level numbers survive as
`ownPoolFactor` for the prefilter and for a census line that says, each run,
how many rows came back.

**The undo is the rerun**: both tables are built as `_new` and swapped, no
state accumulates, `normalized_time` is never cleared. One
`engine/impossible_race.py --write`.

### ⏳ L. Two schools with one name — "Oregon" (IL) and "Oregon" (OR)

Owner, 2026-09-16: *"use the school locations from anet and the school
ids/names to separate schools (where id != 0) and separate them by location
otherwise. If any school has id == 0 we should just put them in pro."*

**The pooling half — done.** When a grade cannot answer, `poolFor` falls
through to `levelForSchool`, which reads `school_level_graph` and
`school_levels.pkl` — both keyed on the **normalised name and nothing else**.
So one string is one level for everyone wearing it: `"Oregon"` is Oregon High
School (Ogle County, IL) *and* the University of Oregon; `"Williams"` is a CA
high school *and* Williams College, MA. Whichever level the map holds, the
other school's gradeless rows are pooled on it.

The row already carries the identity the name lacks — anet's `team_id` (its
own level, state and city in `anet_team`) and tfrrs's slug (state, then
level). `resolvePool` now prefers that `team_level` for exactly the rows the
name map would have decided: unreadable or untrusted grade, no `grade_fix`
verdict. A trusted grade still decides alone; `grade_fix` still outranks it.
And a level the feed **states** no longer trips the three "we do not know"
verdicts into returning no pool — that kill exists because the fallback is
the school name, and this is not that.

`team_id = 0` is anet's *no team*, and it is **pro, unconditionally** — asked
whether to gate it on the season majority like the club rules, owner:
*"unconditional (these atheltes don't matter enough for me to let them corrupt
boards)"*. So it does **not** go through `team_level='club'`, which would hand
it to the majority gate and the grade guards; `resolvePool` takes it as
`no_team`, decided before every grade, verdict and field rule. ⚠ The cost,
stated: a high schooler's one unattached summer race is a professional row and
their season splits across two pools. `--zero` prices it.

**Measure it before the next go-live**: `scripts/diag_school_collisions.py`
— B lists the colliding names with each team's level/state/city, C says how
many of those teams the name map levels **wrong**, and `--zero` answers
whether `team_id = 0` really is unattached (if it turns out to be a sentinel
on ordinary school rows, the `'club'` read must go).

**The identity half — done too** (owner: *"go ahead"*).
`school_identity.py`'s header says *"THE DATA HAS NO SCHOOL IDS"*. That
stopped being true when `scripts/anet_teams.py` landed: `anet_team` carries a
`team_id` per school with its own level, **state**, city and zip, and
`results.team_id` puts every anet row on one of them. The clusters were still
being drawn from `person_home_state` — the state an athlete *races* in most —
which cannot separate these cases even in principle:

* an athlete has **one** home state, so a kid who ran high school in CA and
  then Williams College in MA is one state for both names;
* a college's home state is a **travel mode** (Air Force came out OK, Oregon
  CA, Furman FL — `school_identity.teamState`'s own docstring);
* two schools of one name **in one state** never separate at all.

So `build_school_identity` now:

1. `anetContestedStates` — the names anet places in **more than one state**.
2. `buildTeamStates` — for those names only, the modal state of the athlete's
   **own anet team**, over both sports' raw rows (`si_team_state`). ⚠ One
   filtered pass over `results`/`results_tf`; everything else in this step
   reads `athlete_season` because of the 15 Sep gateway timeout, and the name
   filter is what keeps this affordable. `team_id = 0` is excluded — it is not
   a school.
3. The clusters CTE reads `COALESCE(ts.state, ph.state)` — anet's state for
   the athlete's own team, the inference only where there is no team id
   (tfrrs, `team_id = 0`, a name anet places in one state).
4. `anetSaysTwoSchools` — the co-racing merge **refuses** to join two clusters
   of one name that anet places in two different states, *before* the
   shared-meet threshold, because the merge spreads by union-find: one
   accepted pair pulls in every cluster already joined to either side.

Everything degrades: no `anet_team` → no contested names → no scan → every
`COALESCE` falls through to exactly the old answer.

**Still inferred, on purpose:** tfrrs rows (no anet team id — the slug's state
token is the obvious next step) and names anet places in a single state.

### ✅ M. The verdict kill was dropping professionals, and two other pro rows never had a pool

Found while testing L. Three separate ways a row established as professional
still failed to reach a pro pool:

1. **The verdict kill ran before the pro repool.** A gradeless row with a
   `no_evidence` verdict returned `None` even though `is_pro` was already set
   — dropped because `grade_sanity` could not name a *grade* for it. Of course
   it could not: there is no grade to name. Both rules approved on 16 Sep
   (unconditional `no_team`, and a club season sweeping every grade) land in
   exactly this case, so `is_pro` is decisive here now.
2. **Stage 2 repools a pro by *swapping the level* of the pool the grade or
   the school produced** — so when neither could produce one, there was
   nothing to swap and the row was dropped. That silently undid the rule for
   the rows it matters most for: an unattached entry whose `school` is
   whatever the athlete typed, and **the elite squads in `_PRO_TEAMS`** —
   `HOKA NAZ Elite` is in no school level map because it is not a school, so
   the hand-written list was catching rows that then vanished. `_proPoolFor`
   builds `pro_m`/`pro_f` from the sex alone. Only with a known sex:
   `pro_unknown_gender` is not a pool anything can rate against.
3. And **a club season now sweeps every grade** (rule 2 above), so an elite
   squad's "11" no longer stays on the high school boards.

### The rules the owner approved on 2026-09-16

| | rule | gate |
|---|---|---|
| 1 | `team_id = 0` → pro | **none** — before every other rule |
| 2 | a club with a professional in it → *every* row of that season pro | the season majority (`clubSeason`), upstream |
| 3 | school identity keyed on anet's team id and location | — |

The cost of 1 and 2 is real and was priced: an unattached summer race splits a
high schooler's season, and a sponsor's youth squad (`HOKA Aggie Running
Club`, `Asics Aggies` — grades 9-12) pools pro when its athletes race mostly
for it. Owner: *"these atheltes don't matter enough for me to let them corrupt
boards."*

### ⏳ N. The first identity fix changed nothing, and the data says exactly why

Owner ran 10b: *"oregon and williams are still exactly the same."* They were.
What the tables said afterwards:

```
school_identity          school_state_alias
 Oregon   | IL | 1019 | 1.0000 | primary       Oregon   | 36 home states -> IL
 Williams | CA | 1401 | 0.9986 | primary       Williams | 26 home states -> CA
 Williams | AK |    1 | 0.0007                 (OR among Oregon's, MA among
 Williams | SC |    1 | 0.0007                  Williams's)
```

Three separate defects, and the fix needed all three:

**1. anet cannot see the college half.** Every hs-vs-college collision is one
anet school and one tfrrs school — and tfrrs XC rows carry **no anet team
id**, while `results` has no `team_slug` either (only `results_tf` does). So
`buildTeamStates` placed 4.2M pairs and not one of them was the half that was
wrong. anet knows both Oregons (16586 IL, 21242 Eugene OR) but only one
Williams (685, CA). What knows the other is **`college_directory`** — 2,000
NCAA/NAIA names and their states, already built, already read by
`applyCollegeDirectory`. Authoritative states are now anet's **∪** the
directory's, and a college-pooled season is placed by the directory in the
assignment itself.

**2. The refusal was pairwise; the merge is transitive.** Refusing IL↔OR does
not keep IL and OR apart — IL merges with CA, CA merges with OR, and all 36 of
Oregon's home states land in one group **with that pair never tested**. 737
refusals fired and Oregon still came out as one cluster. The guard is now on
the **group**: each union-find root carries the authoritative states inside it,
and a merge whose result would hold two of them is refused. And a group holding
a named school now **resolves to that state**, instead of by row counts — a
college's rows are mostly away, which is how "Oregon (CA)" and "Furman (FL)"
happened in the first place.

**3. The site re-derived membership from `person_home_state`.** This is the one
that would have kept it broken even with correct clusters.
`stateFilterSql` — the roster, the chips, the splits — narrowed to *"athletes
whose **home state** is OR"*, i.e. who races in Oregon, not who runs for the
university. The clusters were counted from one expression and the page filtered
by another. So the assignment is written down now:
`school_athlete_state (school, person_id, state)`, built from the same
`si_assign` the counts come from, **after** the merge and directory folds, and
swapped in the same transaction as `school_identity`. `stateFilterSql` reads it
first and falls back to the old clause when the caller names no school or the
table is absent. Contested names only.

⚠ **`_LABELS` is a per-process cache.** `loadLabels` fills it once per gunicorn
worker, so no rebuild shows on the site until the workers recycle. Restart the
app after 10b.

**Still keyed on the name:** `school.py`'s own header says *"KEYED ON THE
SCHOOL NAME, NOT AN ID"*. `/school/Oregon` is one URL; the state arrives as a
query parameter that the label, crest and link now agree on. Giving a school a
real id in the URL is a bigger change and is not this.

### ⏳ O. The crest follows the cluster, and two things still leaked into Oregon (OR)

Owner after the second 10b: *"Williams worked perfectly"*, but *"the logos are
still the old logo (for oregon)"*, *"Williams... just have no logo anymore"*,
and *"Oregon (OR) contains hsers still"*.

**The logo is already stored — nothing needs re-scraping from the schools.**
`anet_teams.py` has been writing `anet_team.mascot_url` since it landed, and it
already has a pass that fetches that image and records it in `school_logo`
under `(school, state)`. The catch is *which* pairs: its queue is
`school_identity` JOINed to the modal anet team per `(school, state)` — so

1. every crest already stored sits under the **old** pairs. A name that has
   just split has a badge for the state it used to be and none for the new one,
   which is exactly "Williams has no logo any more". `--redo` re-files them.
2. its `state` came from **`person_home_state` alone** — where the athlete
   *races*. For a college that is a travel mode, so the modal team for
   (Oregon, OR) was decided by whoever happens to race in Oregon. It now reads
   `school_athlete_state` first, the same order `si_assign` and
   `stateFilterSql` use. Three places, one answer.

⚠ **And `--redo` is 38 hours, which is the wrong job.** `Manners` paces one
request per second *per host*, so re-asking all 40,927 teams for two or three
API calls each against `www.athletic.net` is a day and a half. Nothing about
the metadata changed — the `(school, state)` pairs did — and `mascot_url` is
already in the table. So:

```bash
python scripts/anet_teams.py --write --logos-only --missing
```

`--logos-only` makes **no anet API call at all**: it reads the stored
`mascot_url` and fetches only the image, which lives on googleusercontent.
`--missing` restricts the queue to the pairs the site currently has no crest
for — the same three conditions `loadCrests` serves on — which after a split is
exactly the new clusters. One image request per pair.

Williams College is **not in `anet_team` at all** (only the CA high school is),
so its crest has to come from the website route: `build_school_websites.py
--wikidata --write`, then `scrape_school_logos.py --write --retry-failed`. And
`school_logo.override` is the manual lever — a URL forces that image, the
string `'none'` suppresses the crest entirely.

**Two leaks into Oregon (OR),** both fixed:

* **`bool_or` meant *ever*, not *mostly*.** Any college-pooled season under the
  name placed the athlete at the college — and the pooling is the thing still
  being fixed, so one mis-pooled race moved an Illinois high schooler onto the
  university's roster. The level the athlete **mostly** raced at decides now,
  ties to college, which is `applyCollegeDirectory`'s own convention.
* **`si_assign` INNER JOINed `person_home_state`.** An athlete with no racing
  state had no assignment row at all, so the site's COALESCE fell through to
  the **primary** cluster — which for a contested name is now sometimes the
  college. LEFT JOIN, and only a row no source can place is dropped.

### ✅ P. "Williams is not in anet" was a query matching on a name

Owner, with a link to athletic.net team 21570: *"Like idk why you think williams
isn't on anet."* They're right, and the reason is worth writing down because it
is the same defect the section above is about.

The query I asked for filtered `anet_team` on
`lower(btrim(school)) IN ('oregon','williams')` and returned one row, so I said
Williams College isn't in anet. That tests **anet's spelling**. anet stores the
college as `Williams College`; the filter walked past it. A conclusion keyed on
a name, in the middle of fixing a bug about keying on names.

**And `authoritativeStates` had the same flaw.** It grouped `anet_team` by
`lower(btrim(school))` and matched that against the **feed's** school string —
so a team anet calls `Williams College` never joined the rows that say
`Williams`, and the college half of that collision was invisible to the anet
leg. (Williams became contested anyway, via the college directory, which is why
it split at all. Oregon worked because anet happens to spell the university
`Oregon`.)

The rows carry the join that needs no spelling: **`team_id`**. The states now
come from `results`/`results_tf` joined to `anet_team` on `team_id`, grouped by
the **feed's own string**. It cannot miss a team over a name and cannot invent
one. Cost: one aggregate pass over both row tables, joined to a 40k-row table —
`buildTeamStates`' scan is still filtered by the list this produces.

**Check whether we hold the team at all**, which is a different question from
whether anet has it — `anet_team` only holds teams a run has fetched, and the
queue only asks for the modal team of a `(school, state)` pair with ≥3
athletes:

```
 team_id |  school  | state | level | has_mascot |  fetched
     685 | Williams | CA    |     4 | t          | 2026-09-13
   16586 | Oregon   | IL    |     4 | t          | 2026-09-13
   21242 | Oregon   | OR    |     8 | t          | 2026-09-14
                                                     -- 21570: NO ROW
 results_tf 21570  16,434        results 21570  4,356
```

**20,790 rows name a team we have never fetched.** (And the level codes are
confirmed: `4 = hs`, `8 = college`.) `teams()` asks for the modal team of a
`(school, state)` pair **that already exists in `school_identity`** — so
`(Williams, MA)` was never on any list, and without the team there is no level,
no state and no mascot for it. That is the bootstrap: the college half of a
collision cannot be seen by anet until somebody asks anet about the college.

`--unfetched` starts from the rows instead: every `team_id` they use that
`anet_team` has no row for, biggest first, each keyed to the modal
`(school, state)` of its own rows.

```bash
python scripts/anet_teams.py --write --unfetched --limit 2000
```

⚠ It scans both row tables, so it is a maintenance command, not a pipeline
step. The biggest-first order is the point — a few thousand teams carry most of
the corpus's weight.

### ⏳ Q. The plan: identity is `(team_id, name, location)`, and tfrrs links in through athletes

Owner, 2026-09-16: *"We have a bunch of teams, which currently we are combining
by name/location. We need to combine by (team id, name, location) to help tfrrs
combine. What we should do is scrape anet for all the team ids we need. Then we
combine with tfrrs using our already combined athletes that contain both
schools with races from tfrrs and anet. That should get us over the hump, and
any remaining tfrrs schools just put as their own schools still but keep
separate and under current logic so they don't fuck with the overwhelming
majority. Same for any team id = 0 teams."*

This is the frame the last four attempts were missing. Every one of them tried
to join the two feeds **by a string**, and each failed in a different direction,
silently:

| attempt | what it joined on | how it failed |
|---|---|---|
| home-state clusters | athletes' racing mode | a college's mode is a travel state |
| `anet_team` by name | anet's spelling | `Williams College` never met `Williams` |
| college directory | Wikipedia's spelling | 895 of 3,620 contested names |
| `crestState` fallback | one crest per name | gave the university's badge to the high school |

**The athletes are the join that needs no spelling.** `person_id` is already
merged across the feeds, so an athlete with anet rows on a team and tfrrs rows
in the same season *is* that team's athlete, and the tfrrs string they wear is
that team's name.

**Step 1 — every team id our rows name.** `anet_teams.py --write --unfetched`,
no limit. `--queue-only` sizes it first without making a single request
(`--dry-run` fetches; that is what it is for, and it is why the first
`--unfetched --limit 5` run fetched 5 teams just to count them).

**Step 2 — the link.** `scripts/link_tfrrs_to_anet.py`, dry run by default,
writes `school_team_link (tfrrs_school, team_id, state, level, n_athletes,
n_seasons, share)`.

⚠ **The trap, and the guard.** A person's HIGH SCHOOL anet rows and their
COLLEGE tfrrs rows share a calendar year — spring track for the high school,
autumn cross country for the college. A shared person and year alone would
therefore marry a high school to a college, which is the collision this is
meant to end. So a vote only counts when **the anet team's own level is
college** (`loadTeamLevels`; measured from the owner's query, `4 = hs`,
`8 = college`). A high school team cannot be voted onto a tfrrs college string
at all. Plus a majority: `MIN_ATHLETES = 5` and `MIN_SHARE = 0.60`, so one
transfer, one mis-merged person or one guest runner cannot mint a link.

**Step 3 — the remainder stays separate.** A tfrrs string with no link, and
`team_id = 0`, keep the name/home-state logic and their own clusters. That is
the owner's rule and it is also the safe one: the fallback is what the
overwhelming majority of schools already use correctly, and nothing that fails
the bars above should be allowed to move it.

**Then** `school_team_link` becomes the third source in
`authoritativeStates` — better than the directory, because it is measured on
our own athletes rather than matched on a name — and the crest queue keys on
the linked team.

### ✅ R. The race page asked the meet where a school is

Owner mid-`--unfetched`: *"I see Williams has a logo now, and their athletes
has correct school, but the races still say Williams(CA). I can also see things
like MIT(CT) and Tufts(CT) which have become diff 'schools' in races."*

The athlete page was right because a **season** line resolves through
`schoolLabelFor(school, pool, state)` → `teamState`, which asks the college
directory when the pool is a college one. A **race** page has neither a pool nor
a season: `race.html` labelled, linked and crested every school with
`header.state` — the meet's state. For a college that is a travel state, so a
Williams College row at a Connecticut meet asked "Williams in CT", found no CT
cluster, and fell back to the name's primary: the California high school.

And `MIT (CT)` is the same thing one step worse — CT *is* a cluster of MIT's
athletes' home states (New England away meets clear `CONTEXT_MIN_SHARE`), so
the context answer was CT, and `splitCollisionTeams` then scored it as a
separate team. That is the Amherst failure in this file's own comment, one layer
down: the alias fixed a name whose travel states were **merged away**, but a
name that legitimately **splits** keeps both clusters, so the split shatters the
team again.

**The row has a `person_id`**, and `school_athlete_state` says which cluster
that athlete of that school is in. Not a context to guess from — the answer.

* `meet_compile.stampSchoolStates` sets `row["school_state"]` for every row,
  and `race.html` prefers it (`{% set sst = row.school_state or header.state %}`)
  so the label, the link and the crest read one answer — `contextState`'s own
  "a mention resolves ONCE" rule.
* `splitCollisionTeams` reads the assignment before the home state. It is
  already folded through the merge, so it needs neither the alias nor the
  clamp — the clamp stays as the last resort, because nobody may vanish from
  scoring.

Both are no-ops without the table, and for a one-school name the assignment
*is* the only cluster, so nothing changes and it costs one indexed lookup.

⚠ **AND THE STAMP ALONE DID NOTHING** (owner: *"nOpe didn't work"*).
`contextState` **re-judges the state it is handed**: it accepts it only when
that state holds `CONTEXT_MIN_SHARE` (3%) of the name's athletes. That bar
exists to stop a stray away meet from labelling a school — it is a bar on a
**guess**. Williams College is a few dozen athletes against a California high
school's 1,401, so MA sits under 3% and the right answer was discarded one
function after being worked out.

So `contextState`, `schoolLabelIn`, `schoolHref`, `crestState`, `crestUrl` and
`crestImg` all take `trusted=False`, and a state that came from
`school_athlete_state` is used as given. A venue state is still a guess and
still judged. Every other caller is unchanged.

⚠ And `row.school_state is not none` was wrong in Jinja: a missing key is
`Undefined`, and `Undefined is not none` is **True** — which would have trusted
the *meet's* state on every row the stamp could not place. It is
`is defined and row.school_state`, verified against Jinja in
`tests/test_race_school_state.py`.

⚠ **Still on the meet's state:** every other template that mentions a school
(`meet.html`, the boards, search). They were wrong before this too, and each
needs the same stamp on its own rows; the race page is done because that is
where a collision is visible.

## 2026-09-16 (later) — the model, stated by the owner

### ⏳ S. A state belongs to the school, and a level belongs in the key

Owner, after three rounds of patching the clustering:

> *"The state of a school comes from the anet gps. Otherwise it comes from most
> raced state, and only the most raced state. It should be a school still, and
> it should be stable for athletes across races. A race with the same name but
> diff state for an athlete is the same name and most common state. (hell even
> if the name is a substring of the other point stands (brooks). The anet pools
> should match our school pools. If they don't, separate them."*

> *"team id: separates teams and coalesces athletes towards standardization
> across a season. If an athlete runs for a team with similar name but diff id,
> coalesce. if = 0, do by name, state... link with tfrrs by linked athletes and
> races... Always take anet's info as most important."*

**This is the rule every version of this step has broken**, including all of
mine. Clustering a name by its **athletes'** home states makes a school's
identity a property of whoever raced: MIT's New England away meets gave it a CT
cluster, Tufts the same, and `splitCollisionTeams` then scored them as separate
teams. Amherst showed it on 2026-09-13 — seven runners across four
pseudo-teams, none scoreable — and each time the answer was another patch on
the clustering. The clustering was the bug.

**Done now:** a contested name with no anet team and no directory entry is ONE
school in ONE state — the state its rows were mostly run in (`buildNameStates`,
`si_name_state`), read **before** the athlete's home state. Two athletes of
that name cannot land in different states and no athlete moves between races.
Splits come from **team ids**, which is what a split should mean.

**The Amherst logo regression, and why it is the same point.** The crest queue
picks the **modal team** of a `(school, state)` pair. Amherst Regional High
School has far more rows than Amherst College and both are `(Amherst, MA)`, so
the high school wins — and because "ANET WINS" is the default, its Falcons
mascot **replaced** the college's real crest, which had come from the college's
own athletics site (`kind='athletics'`, rank 1, against anet's 2). Right to
wrong in one run. anet no longer replaces a crest on a pair that holds more than
one institution (`school_level`, ≥2 non-bucket levels); it may still fill an
empty one, which is a coin flip we were already taking.

✅ **The crest key carries the level now** — *"The anet pools should match our
school pools. If they don't, separate them."*

* `school_logo` gains `level text NOT NULL DEFAULT ''` and its primary key
  becomes `(school, state, level)`. `''` means "any level", which is all a
  match by NAME from a school's own website can honestly claim.
* **Every crest already on disk keeps its file name:** `fileFor` folds the
  level into the hash only when it is non-empty, so an empty level reproduces
  the old digest exactly. Pinned by a test, because getting it wrong orphans
  the whole store.
* `crestState` and `pickRow` read `(state, level)` first, then the level-less
  row, then the old fallbacks — one order, on the cache and on the query, so
  the two cannot disagree about which crest a mention gets. `logoUrl` carries
  `&level=` or the route cannot re-derive the file name.
* `anet_teams` files a mascot under **its own team's level**, so Amherst
  College's crest and Amherst Regional's are two rows and neither can shadow
  the other.

**Yes, the damaged ones can be re-asked** — a crest is never one-way, and what
to re-ask is knowable exactly: a pair holding two institutions whose stored
crest is anet's mascot filed under no level (`damagedPairs`). That is the shape
of the damage: anet's modal team for the pair is the *bigger* institution, its
mascot went in as the pair's one crest, and "anet wins" replaced whatever the
colleges' own athletics sites had given.

```bash
python scripts/scrape_school_logos.py --write --fix-multi
```

It re-asks those **pairs**, not their names, so a district with one good crest
and one bad one is not re-asked wholesale. `--fix-multi` implies `--redo`, since
the point is to ignore what was stored last time.

⚠ **Two bugs of mine that the existing tests caught**, both worth keeping in
mind: `ensureTable` derives an `ADD COLUMN` from **every line** of the DDL body,
so an SQL comment inside it becomes a column named `--` and the first ALTER on a
real server is a syntax error (notes go above the string). And
`ensureLevelKey` runs at the top of every job including read-only ones, so the
probe is savepointed and returns False on anything it cannot read — an
un-savepointed failure poisons every statement after it in the caller's
transaction.

⏳ Still on `(school, state)`: `school_identity` itself, `/school/<name>`, the
search index and meet scoring. The crest was the visible half; the page is the
next one.

Also still open from the owner's list: **coalescing an athlete's season onto one
team** when they appear under similar names with different ids, and the
`link_tfrrs_to_anet` output being wired in as the authority above the directory.

### The owner's plan for tonight, and what it needs first (2026-09-16)

> *"start scraping the schools. Then rescrape anet/tfrrs new races. Then relaunch
> engine (update it with new ideas first). Then extraction -> train."*

**✅ The school scrape can start** — after the two bugs fixed in this commit,
both of which sit in exactly that path and both of which fail on the server:
`ensureLevelKey` assumed the primary key is called `school_logo_pkey` (a
restored dump can name it anything, and then the DROP finds nothing, the ADD
fails, and every INSERT dies on *"no unique constraint matching the ON
CONFLICT"*), and `--fix-multi` read `level` **before** the migration that adds
it.

```bash
python scripts/scrape_school_logos.py --write --fix-multi   # repair first
python scripts/anet_teams.py --write --unfetched            # then resume
```

**⚠ The rescrape needs a schema change first, or it happens twice.** Owner:
*"make sure tf venue names go in so we can not label our id as venue."*
`meets_tf` has **no venue-name column at all** — `div_id, meet_id, meet_name,
event_short, event_id, distance_meters, gps_lat, gps_long, state, is_indoor`.
So a TF venue key has nothing to be but an id, and that is not a scraping bug,
it is a missing column plus the loader that would fill it. It must land before
the rescrape.

★ **The whole plan, with the decisions taken, is `docs/PLAN-2026-09-16.md`.**
The owner overruled the item below on the same day: nuke on pool records, but
with the record taken at each pool's OWN anchor (college_m anchors at 8000, so
an 8k record, not a 5k one). See that file's §0 for the arithmetic and the two
conditions that make it survivable.

**🔎 One correction on the list contradicts what 2026-09-16 just fixed.** Owner:
*"If a race result normalizes to a 5k that is a wr in whatever pool (ms/hs/such)
is diff, nuke entire race ratings."* Nuking a whole race on a **pool** record is
the ratchet ISSUES K removed: the rows that beat a young pool's record are, by
construction, the ones the POOLING got wrong, and deleting the race deletes the
evidence that would repool the athlete — one mistake, career gone, and the race
was never wrong. The open record is safe to nuke a race on (no pooling can make
a time beat the world record); a pool record should stay a ROW verdict. Proposed
split, for the owner to accept or overrule:

* normalized 5k beats the **open** 5k WR → the race's distance or clock is
  wrong → nuke the race (what `impossible_race` already does, on the normalized
  scale instead of the raw pace, which is the new part).
* normalized 5k beats the **pool's** 5k record → that row is mis-pooled → drop
  the row, keep the race.

**🔎 And "anything without a team id in anet is pro" needs one guard:** a tfrrs
row has no anet team id *by construction* — `results` does not even have a
`team_slug` column — so the rule as written makes every tfrrs athlete
professional. It has to be "an **anet** row with team_id 0 or NULL", which is
what `no_team` does today.

The rest of the list is logged as work, not blockers: the sections/divisions
redo, the tfrrs column-shift guard, the 5-15σ season-median gate, culling
`corrections` (and backing it up) so the import is not 165 MB, pooling by anet's
level with the grades rewritten to match, tiny-team-is-pro, removing track
difficulty while indoor is priced 4% easy, the XC-race-duplicated-into-TF dedupe
(same day, same time, either sport), normalizing the 5k to a **track** 5k, and
labelling a school in the extraction as `(name, state, id)` rather than a string.

---

## 2026-09-17 — the XC-into-TF duplicate, measured, and what is left of it

Three findings, all measured against the live database rather than reasoned
about. The first is fixed, the second is the owner's call and is **logged, not
actioned**, the third is cosmetic and bigger than both.

### ✅ FIXED — one TFRRS meet was scraped as both sports

`prefill_tfrrs_queue` seeds every TFRRS id under **both** sports on purpose: an
id does not say what it is, so both are tried and the wrong one is meant to be
deleted when the page is classified. `run_tfrrs._isMeetPage` **took no sport
argument** — any page carrying `tablesaw-xc` tables was "a real meet", whoever
asked — and TFRRS serves the XC meet at the bare `/results/<id>` that the TF
claim fetches. Its own docstring said "the signal differs by sport"; the code
never looked.

Meet **27037**, 2025-11-15: 436 `results_tf` rows under `Men's 8k` (an 8k is not
a track event), 383 of them provably the same race as a `results` row — same
person, same day, same time to a hundredth. Its `meets_tf` rows have no meet
name, because the track parser found none on a page that is not a track meet
page, and `athlete.html` renders a nameless race as "Race results" — which is
what the owner saw, and is a template fallback, not scraped text.

Fixed in `_isMeetPage` (an XC page is never a TF meet, whatever it links to) and
purged by `scripts/purge_tfrrs_xc_in_tf.py`. Tests:
`tests/test_tfrrs_sport_classify.py`.

⚠ **Two purge queries were wrong before that one, in opposite directions, and
the reason is worth keeping.** v1 matched `meets WHERE source='tfrrs'` and found
nothing — `meets` is anet-only, TFRRS meet metadata lives in `meets_tfrrs`,
which is the entire reason `app.py` has a `_tfrrs_join`. v2 matched "has a
`meets_tfrrs` XC row AND has track rows" and proposed deleting **9,372 meets and
5,371,425 `results_tf` rows** — Penn Relays, the Houston ISD zone meet, "Men's
1500 Race Walk". Real track meets. The cause: `saveTFRRSMeetMeta` writes a
`meets_tfrrs` row whenever the meta panel parses, **whether or not a single
result was found**, so the old sport-blind classifier let an *XC claim on a real
track meet* leave a junk `sport='XC'` row behind — and v2 read that junk as proof
the *track* data was bogus. The lesson is in the script header: test for positive
evidence of the thing you want to delete, never for the presence of a row
somewhere else. It now also refuses above 200 meets without `--force`.

### 📋 LOGGED, NOT ACTIONED — ~1,590 anet XC/TF pairs are anet's own doing

Owner, 2026-09-17: *"yeah 1600 idrc about, just make sure to log it."*

anet numbers its XC and TF meets **separately**, and publishes some races in
both sections, so one physical race exists as two anet meets. Measured, matching
on (person, day, time to 0.01s), both sides `source='anet'`:

| event | pairs |
|---|---|
| 1600m | 624 |
| 5000m | 350 |
| 3200m | 323 |
| 3000m | 196 |
| 1mile | 47 |
| 2miles | 19 |
| 10-km | 17 |
| 800m | 12 |
| 8000m | 2 |

≈1,590 rows. Examples: anet XC meet 270744 / TF meet 624231, both named
`JERRY YOUNG: HARRIERS & THINCLADS`, 2025-11-06; XC 271776 / TF 631547, both
`Coach Williams Let's See What You Got 1600`, 2025-11-11; XC 268771
`King of the Track Classic` / TF 621183 `Moorpark King of the Track Classic 2025`.

! **A 1600m absolutely can be a cross country race** (owner corrected this
directly). These are November track races inside the cross-country season, and
the ones above are genuine. Nothing here is a scraping bug.

The cost: the race shows twice on an athlete page, and it is two rated results
instead of one, so it carries double weight in a season mean. 1,590 rows against
39M and 191M.

Options, for whenever this is picked up. Preference is the second: it fixes what
is visible, deletes nothing, and is reversible if the match rule is ever wrong.

1. leave it;
2. hide the duplicate at render time — `panels.py` already drops cross-source
   copies of one race with a `seen` key and first-wins; same idea, same place;
3. delete one side — cheapest for the ratings, but needs a rule for which wins,
   and the XC side carries the course and difficulty while the TF side carries
   the event name. Dry run first.

### 🔎 OPEN — 3.39M anet track results have no `meets_tf` row at all

Not a key mismatch and not a ratings problem. `scripts/diag_orphan_tf_results.py`
narrows it, over a 0.5% page sample of anet `results_tf`:

| joined on | matched |
|---|---|
| sampled | 787,216 |
| `meet_id` | 769,474 |
| `+ div_id` | 769,431 |
| `+ event_id` | 769,426 |
| `+ source` | 769,426 |

The whole drop is at **`meet_id`** — 2.25% of rows belong to a meet with **no
`meets_tf` geometry whatsoever**. Confirmed the other way: **15,971 meets have no
geometry at all**, against only **58** that have some and still orphan rows. So
the four-part key is sound and `meets_tf.source` is not the culprit (14,174,892
anet + 2,087,858 tfrrs = 16,262,750, the whole table, no NULLs).

**It is overwhelmingly historical.** Orphans by year: 2009 1,162,224 · 2010
744,961 · 2008 571,152 · 2007 337,637 · 2006 225,702 · 2005 103,622 — ≈3.15M, 93%
of the total, in 2005-2010. From 2011 the rate collapses to a few thousand a
year, with bumps at 2022 (34,492), 2024 (20,473), 2026 (10,555), 2021 (11,250),
2025 (7,777). So the cause is not fully dead and ~95k rows since 2021 came from
whatever still does it. (Also 15 rows dated `2222`.)

★ **The ratings are fine.** `speed_ratings_db` reads
`COALESCE(m.distance_meters::real, _eventMetersSql('r'))` — it derives metres
from the result's own `event_short` when the geometry row is missing, which is
exactly this case. What is lost is the meet **name**, the **venue** and the
**course**, so those races render as "Race results" with no location.

⚠ **There is no local source to backfill from.** These meets have no
`meets_tf_meta` name either — the nameless count (3,391,299) equals the orphan
count, so both tables are missing them. A name can only come from a re-scrape,
and the re-scrape will not reach them while their `meet_queue` TF rows read
`scraped=1`. Resetting those 15,971 ids to `scraped=0` is the candidate fix; the
open question is whether anet still serves 2005-2010 meets.

## 2026-09-18 — the failed-meet set has been laundered into "due"

**State:** you cannot currently ask "which meets failed". Rescraping them as a
set is not possible right now, and this is why.

`meet_queue.scraped` means: 0 due, 1 done, 2 failed, 3 in-progress/stranded,
4 the feed says no meet exists at this id.

The first version of `ANET_RETRY_FAILED` / `TFRRS_RETRY_FAILED` (2026-09-18,
since replaced) did this:

1. `UPDATE meet_queue SET scraped = 0 WHERE scraped IN (2, 3)` — reset every
   failure to *due*;
2. then claimed `WHERE scraped = 0`.

Step 2 is every due row, including the thousands a forward walk had seeded and
never scraped, so the "retry" was an ordinary scrape night wearing a flag. But
the lasting damage is step 1: **it destroyed the evidence of which rows were
failures.** They are now indistinguishable from ids nobody has ever tried.
`resetInProgress` / `resetTFRRSInProgress` do the same to state 3 at every
launch, by design, so stranded claims were folded in too.

That run happened on both feeds before the fix landed. So the 62 Unicode
`jsonb` failures and everything else that failed are sitting at state 0.

**What this does and does not cost.** Nothing is permanently lost: those meets
are due, so any normal scrape will eventually redo them, and the Unicode bug
that broke them is fixed (`database._Utf8Json`). What is lost is the ability
to do *only* them — which is exactly what was wanted, and why it is worth
writing down rather than discovering again.

**How to re-identify them, approximately.** A meet that failed to save is
queued *done* or *due* while having no result rows, and unlike a genuinely
scheduled meet it has a real meet row with a past date. So the signature is:

    a queue row at scraped IN (0, 1)
      AND a meets/meets_tf row exists for (meet_id, sport)
      AND its date is in the past
      AND zero result rows reference it

`queue_meets.emptyRecent` and `scheduledToRetry` already compute neighbouring
populations and are the place to build this from. It is a heuristic, not a
recovery: a meet legitimately held with no published results looks the same.

**The fix that is in place now**, so this cannot recur: a retry claims states
2 and 3 *directly* (`database._claimMeetBatch(..., states=(2, 3))`) and resets
nothing, the start-up reset is skipped in retry mode, and the reported count is
`failedCounts`, not `dueCounts`. `scripts/queue_status.py` prints what each
mode would claim, with the id span of each set, so a "retry" that is really a
corpus sweep is visible before it runs.

**Before any rescrape now**, run `scripts/queue_status.py --sample 10`. If
`normal run` is in the tens of thousands, that is the laundered set plus the
forward-walk seeds, and a full run will take a night at the paced rate.

### 2026-09-18, later — measured, and it is narrower than feared

`queue_status.py` on the live queue:

    anet   TF  due 469      done 536,894  stranded  75  not-a-meet 123,101
    anet   XC  due 4,734    done 171,428  stranded  46  not-a-meet  48,380
    tfrrs  TF  due 144      done  60,464  failed     1
    tfrrs  XC  due 73       done  16,784  failed     9  stranded 12

                        normal run            --retry-failed
      anet              5,203                 121
      tfrrs               217                  22

So the entry above was too gloomy in one specific way and right in another:

- **Right:** anet has **no rows at state 2 at all**. Every anet failure, the 62
  Unicode `jsonb` ones included, was reset to due — that evidence is gone, as
  described.
- **Too gloomy:** 121 anet rows survive at state **3** (stranded claims from
  crashed sessions), and tfrrs kept 10 genuine state-2 failures plus 12
  stranded. `--retry-failed` therefore has real work: 121 + 22 = **143 meets**,
  which is minutes rather than a night.

Note the id spans in that output. `--retry-failed` spans 271,052..675,239 and
`normal run` spans 275,739..675,708 — both wide, because the queue holds both
sports across two disjoint id spaces. A wide span is only evidence of a
laundered set when the *count* is also large; here the retry count is 143, so
it is the right set.

**Practical upshot:** after the team-id scrape finishes, run the retry (143
meets, quick) and then a normal run for the 5,420 genuinely-due ids.

---

## 2026-09-25 — 📋 LOGGED, NOT ACTIONED: eight features built but not wired

Owner: "log those 8 and we'll deal with after this." Each was checked for
callers with a grep over `racecast/templates` and `racecast/static/*.js`.
Closest to done first.

| # | Feature | What exists | What is missing |
|---|---|---|---|
| 1 | **Lineup search** — `/api/predict/lineup` (`app.py`), `predict.predictTeamLineup`, `race_sim.bestLineup` | Route, model and `tests/test_race_sim.py`. Returns best 7, bench, win chance, expected score, 5 alternatives, in the page's runner-row shape | Any caller: a "Best lineup" button per team row and a panel. `coaches.html` still says "Lineup simulator — being built" |
| 2 | **Head-to-head scoring** — `head_to_head=1` on `/api/predict/team` | Backend, and `predictions.js` already renders "Scored as if only these teams raced" | The page never sends it: a toggle. `team_rho` and `draws` are never sent either |
| 3 | **Model status** — `/api/predict/status` | Route over `predict.modelStatus` | No fetch, no banner |
| 4 | **Compiled meet JSON** — `/api/meet/xc/<id>/compiled` | Backend | No caller; `meet.html` / `compiled.html` render on the server |
| 5 | **Recruit projections on athlete pages** — `recruit_projection` | Built every run; the coach search sorts on it | `athlete.html`, `recruit.html`, `recruiting.html`, `recruitProfile` never read it. `coaches.html` still lists it as being built |
| 6 | **`team_identity`** — `racecast/build_team_identity.py` | Built every run (anet team id → school, state; Georgetown TX vs DC) | Only `diag_school_roster.py` reads it; `school_identity.py` and the pages do not |
| 7 | **Team records on track venue pages** — `venue_tf.html` "Team records - coming later" | The XC version: `get_course_team_records`, `course.html` | A TF query and the table |
| 8 | **Dead leftovers** | — | Remove: `/hello`, `templates/_result_row.html`, `static/hero.png`, `static/cards/athlete-1.png`; stale text: the athlete rank line's "Greyed scopes are coming soon" tooltip (no scope is wip any more), `coaches.html` "being built" items. `tilt.difficultyFor` has no caller |

Idea stage only, no backend: "Programme development", "Saved boards and
watchlists" (`coaches.html`).

**Update 2026-09-26.** #1 and #2 are wired: the predict page's "Best seven
for [team]" and "Dual meet [A] vs [B]" rows under the team table (`b704ebb`,
`1633639`); the lineup line is off the coaches page's "Being built" list.
#8 is done: `/hello`, `_result_row.html`, `hero.png`, `cards/athlete-1.png`
removed, the "greyed scopes" tooltip line dropped. Still open: #3-#7.
#4 closed 2026-09-26 by deletion (owner: "delete it"): the compiled-meet
JSON route had no caller and ran the full compile, uncached, for anyone.
#6 closed the same day: team_identity is read first by race scoring, school
links and the predict page (meet_compile.teamStates). #7 closed: track venue
team records are the relays. #10 closed: rating_outlier is built (09d) and
read, rank-only. #12 closed: the 30-day window is the default. #13 closed:
the Share box saves the request (/api/predict/share, table
shared_prediction) and shares /predictions?s=<id>; opening one -- or an old
long link -- restores mode, date, course, races, athletes and every card's
lineup, then predicts.

**Also logged 2026-09-26, not built (owner: "I don't want the per-row flag
but log it for future things"):**

| # | Feature | What exists | What is missing |
|---|---|---|---|
| 9 | **Per-result "report this" flag** — a faint flag on each result row (race and athlete pages) opening the report form under the row, pre-filled with the athlete, school, place, time and result id | The report form and `/api/report`, the `issue_reports` table, the status page's Open reports list with Resolve; page-level "Report it" links on athlete, race, meet, school and predict pages. The mockup is in the renders page of 2026-09-25 | The row flag, the inline form, and `issue_reports` columns for `person_id` / `result_id` so a report names its row |
| 10 | **`rating_outlier`** — `engine/rating_outliers.py` (the owner's 09-18 "more than N sigma from the season, do not rate or rank") | Builds the table; `tests/test_rating_outliers.py` | No reader and no pipeline step, so a wrong-distance or merged-person spike still reaches pages, boards and predictions |
| 11 | **Model upkeep** (HANDOFF-ENGINE §5) | `BASELINE_EWMA` baseline built; tfrrs-college training rows code-complete | Neither used in a trained model; missing weather is still a 0 with no `has_weather` flag; the predict page runs on ratings (`XCP_PREDICT_BASIS=rating`) with no backtest of those times |
| 12 | **30-day bracket window** — `XCP_BRACKET_WINDOW=30` (-1.28% held-out error) | Measured; commented in `deploy/solve_env.sh` | Switched on for one run on its own, after a verified run (the file's own rule) |
| 13 | **Shared prediction links** | The share URL carries the whole request | `restoreFromLink` reads only meet, race and sport: mode, date, course and edits are lost |

## 2026-09-26 — 📋 LOGGED, NOT ACTIONED: weather, race-page vs /conversions, black-ground crests

Owner, going to sleep: "I'm not certain the race conversions to the track
are much better now ... just log both as separate issues", and "I'm not
certain weather is correct rn, I've noticed extreme weather races not being
helped!"

### A. The weather correction does not seem to help extreme-weather races

**Owner's observation:** races run in extreme weather are not being credited
for it. Not tied to one page -- it is in the ratings themselves.

**One measured case** (`scripts/diag_row_weather.py --tf 258164858`,
`scripts/diag_track_conversion.py`): the owner's 9:01.10 3200 at the Arcadia
Invitational, 2025-04-12, hs_m, rating 136.59.

    grid cell (34.25, 242.0), local hours 9-20: apparent 24 C, wind 8.1, precip 0
    weather multiplier 1.00727: "-0.72% slower than run, ~0.9 rating points taken"

The track artifact (`engine/data/weather_correction_TF.pkl`) prices heat as
SLOW at 3200 m -- +1.0% at 70 F, +1.3% at 75 F against its 55 F reference
(`_rcsValue`, with the distance interaction) -- and wind at 0.00102 per unit
above 3.0. A 24 C, windy day should therefore CREDIT the run (roughly +1.3%
and +0.5%); this one lost 0.9 points. Candidates, in order:
1. the sign of the multiplier in the backfill path (norm = time x factor /
   wmult vs x wmult) -- check against an XC row on a known hot day;
2. the event's weather normal (`_weatherReference`, `venue_norms["by_event"]`)
   being hotter and windier than the day, so the day reads as BETTER than
   normal -- print the reference diag_row_weather compared against;
3. the window: every track race reads the whole local 9-20 window
   (`race_local_hours`); the Arcadia 3200 heats run in the evening (20:00:
   17.6 C, wind 6 km/h). Changing it is an engine-wide refit, the owner's
   call.
Then sweep: rows on the hottest, coldest, windiest and wettest race days
(both sports), and whether their weather multipliers point the way the
artifact's own curves say they should.

**2026-09-26, later -- rain in particular** (owner: "even just beyond the
Celsius weather doesn't feel right. I see a ton of rain races not getting
accurate benefit"). What the model has for rain, read off the artifacts:

- XC: +0.131% per mm of rain INSIDE the 8-12 local window at 5 km, less
  0.039% per mm for each extra 5 km -- a 10 mm morning is +1.3%.
- TF: +0.008% per mm over the 9-20 window -- a 10 mm day is +0.08%, i.e.
  track has effectively no rain term.
- Rain before the window (overnight, the day before) counts only through
  soil moisture, and only on courses with a fitted mud sensitivity.
- The fit is athlete + (venue, fortnight) fixed effects: only venues raced
  in the same fortnight across years, in different weather, teach it, and
  ERA5's 0.25-degree rain is a smoothed proxy for rain at the course.
  Both pull a slope toward zero.
- With the stale (pre-Celsius-fix) artifacts, a 12 C, 10 mm XC morning
  scored against the global reference
  nets -1.8%: the rain's +1.3% is swamped by the temperature term.

Measure before changing any of it: `scripts/diag_weather_credit.py`
compares every race's runners with their own other races within +-30 days
and buckets the gap by rain (in the window, and the day before), soil,
temperature and wind, next to what the model credited. A wet bucket below
the dry one is rain short-changed, by that gap. Run it after the weather
refit (RUNBOOK-2026-09-27 step 3), since the stale artifact muddies it.

### B. The race page's equivalents card and /conversions give different answers

**Owner:** the equivalents card on a race page and the /conversions page
disagree for the same race, and that disagreement is the complaint -- not
either number alone.

**Case:** Hayward High School, 4828 m, Boys Division 2, 2024-11-23, CA
(difficulty +4.8%; 54 F, wind 16 mph, humidity 89%, rain before the race).
The card read "On this course 11:40 -> On a track 5K 11:51, rating 170.0".
**11:40 is not a time in this race's results** (owner) -- so the value the
ruler opens on, or the rating it shows, is coming from somewhere other than
the results (check `equiv_lo` in race_xc: `min()` over `results`, which now
includes rows `_borrowTwins` adds; and the widget's own opening value).

**Why they differ, as built:**
- the card (`conversions.equivalenceLine` via `/api/equivalence`) runs a
  RATING through `normalized_to_time` for this course (its fitted
  difficulty) and for a typical track, with no race-day weather; since
  `e9dac11` the course side is shifted by `raceDayShift` (the median gap
  between the race's rated times and the ruler), which inherits whatever
  the weather term (A) gets wrong;
- /conversions from a specific result (`_norm_from_result`) inverts the
  STORED RATING, so it carries that row's weather multiplier, era and
  race-day term.
Make both go through one function for "this race's time -> a neutral
track", and compare the two for a handful of races before trusting either.

### C. Crests damaged by the black-ground key

The flat-ground key removed the ground's colour EVERYWHERE, so crests on a
black card lost their own black outlines, lettering and shading (the owner's
Jesuit (CA) screenshot). Fixed at the source in `251610e`
(`_edgeConnected`: only ground reachable from the card's edge comes off).
**The crests already stored are still damaged**: the PNGs were resized after
the key, so the lost black is not in the files. Repair, not yet run:

    /srv/venv/bin/python scripts/scrape_school_logos.py --rekey --dry-run --only Jesuit
    /srv/venv/bin/python scripts/scrape_school_logos.py --rekey --dry-run
    /srv/venv/bin/python scripts/scrape_school_logos.py --rekey --write
    systemctl restart xc-predictor

`--rekey` re-fetches only crests with transparency enclosed by the mark
(`enclosedHoles` ≥ 0.2%) from their `source_url` and writes them back only if
the fixed key changes them. Check afterwards: the Jesuit (CA) crest, and a
sample of the "re-keyed" list it prints for anything that got worse.

## 2026-09-26 — 📋 LOGGED, NOT ACTIONED: indexable meet preview pages

Owner: "I think the last big is good. For now just log it bcs I'd like to
fix the model and engine first." Deferred until the model work is done.

**The gap.** A shared prediction (`/predictions?s=<id>`) renders with the
title "Predictions" and a canonical of plain `/predictions`
(`templates/predictions.html` sets `meta_title`; `_meta.html` defaults the
canonical to `request.path`), and the tables are drawn by JavaScript. So
every meet's prediction reads to Google as one page, and none can rank
for "[meet] 2026 predictions" -- the search that peaks the week before
each invitational, conference and state meet.

**The build.**
- `/preview/<meet-slug>-<year>`, one per upcoming big meet, rendered on
  the server: predicted team scores and top individuals as HTML, title
  "[Meet] 2026 Predictions | Racecast", its own canonical, the share card
  as `og:image`.
- Which meets: the saved predictions (`shared_prediction`) and/or a
  hand-kept list of the season's big meets, dated before the meet.
- Into the sitemap (`build_sitemap.py`, a `previews` kind) and IndexNow.
- A "This weekend" strip on the home page linking them.
- After the meet: the same URL shows prediction against result (the
  follow-up that earns trust), rather than going away.

Until then, the no-code route: make the prediction 2-4 days out, post the
short share link where the meet is discussed (r/Cross_Country race
threads, LetsRun/MileSplit forums, state XC groups, team socials), and post
the predicted-vs-actual after.

## 2026-09-26 — pipeline speed: done, and what is next

Last full run: about 30 hours. Owner: "speed up every step in pipeline
possible". Done (commits 90fb044 through 54be799):

- **11b_indexes waited 626 min on 10f2_projection** (CREATE INDEX
  CONCURRENTLY waits for every older transaction; 10f2 was one 11-hour
  transaction). 10f2 commits per slice and starts after 11b.
- **Database caps: strict -> balanced** (XCP_DB_QUIET=1 now 2 parallel
  workers, 2GB maintenance_work_mem, two builder jobs; `strict` restores
  the old). cursor_tuple_fraction=1.0 on every pipeline connection, so
  streamed reads stop being planned as nested-loop probes. XCP_STREAMS 4,
  XCP_COURSE_SHARDS 3.
- **10_rankings_finish**: SET LOGGED before the index builds (it rebuilt
  every index a second time), one index per signature, per-sport temp
  tables in the shards.
- **07_pack**: majority gender from one table, not a subquery per row.
- Exact small ones: 08's needless changed-row count and capped-race loop,
  06c's pairing and inserts, the event-name parser cache, the weather
  spline's knot sort, the home-page board skipping rows that cannot place.

Next, larger (each needs a measured run to confirm):
1. **08_golive (3.5 h)**: warm-start the joint solve's conjugate gradient
   from the previous run's saved state (engine/run_joint.py saves it;
   joint_solve.py starts from zero); map by athlete/cell/race key, 0 for new.
2. **05_backfill (55 min x2)**: rewrite only rows whose normalized_time
   changed instead of rebuilding both tables; build its indexes three at a
   time; cache the weather-grid aggregate between runs.
3. **05b_anchor_repair (21 min)**: do the "is this row off its anchor"
   test in SQL on (distance, pool) factors, so Python sees only the rows
   that move.
4. **10b_school_ids**: one scan of results/results_tf instead of two each.
5. **07_pack**: meet_class once per meet instead of seven regexes per row;
   pack XC and TF in two processes.
6. **09b_fill**: fold its three passes over results_tf into the go-live
   write.

**Update, same day -- the larger items:**
- DONE 05_backfill: secondary indexes built several at a time (the primary
  key alone, with its old 8GB); the weather-grid aggregate cached until
  weather_grid's write counters move; and when at most 200,000 rows would
  change, only those rows are updated in place (no rebuild, no swap, no
  `_old` undo copy that run) -- otherwise the full rebuild as before.
- DONE 05b_anchor_repair: two set-based passes; Python sees only rows
  outside the band.
- DONE 10b_school_ids: one scan of the anet-team rows shared by both readers.
- DONE 07_pack: meet_class looked up per meet name; loaders out of the row
  loop.
- NO CHANGE 09b_fill: nothing provably safe without a measured run.
- NOT MERGED 08 warm start: it works (same answer to the solver's
  tolerance) but only pass one of five speeds up, ~20% on a normal day's
  change -- about 4% of the step -- for ~700 lines and +0.5 GB state.

## 2026-09-27 — 04c stuck over 24 h in track's dup_race_copy (FIXED IN CODE)

The run sat in 04c_twins for more than a day after `[TF] dup_cross_date
152,563 (5473s)`: the next rule, dup_race_copy, took 557 s on 2026-09-07.
Cause, reproduced on a scratch Postgres 16: the rule self-joins a CTE built
from a `HAVING count(*) BETWEEN 2 AND 8`, the planner puts that CTE at ONE
row, and joins `rows_ a` to `rows_ b` in a nested loop -- every row against
every row. With 2M repeated-time rows the old plan was still running when
cancelled at 3 min; with nested loops off, 45 s. On the server the CTE is
tens of millions of rows, so the old plan cannot finish. Whether a run got
the good plan or the bad one depended on the statistics that day.

Now (engine/twin_flag.py):
- the rules' session has `enable_nestloop = off`; every join in every rule
  has an equality key, so each is a hash join. This likely speeds up TF
  dup_cross_date as well (its `c2` self-join has the same shape).
- no rule may run past XCP_TWIN_RULE_TIMEOUT seconds (default 7200). A
  rule that does is cancelled and keeps LAST run's flags for that rule; the
  log says `TIMED OUT ... N flags kept from the last run`. XCP_TWIN_SKIP
  now keeps last run's flags too, instead of dropping them.
- `python engine/twin_flag.py --explain TF:dup_race_copy` prints a rule's
  plan without running it. "Nested Loop" in it is the problem.

## 2026-09-27 — good-weather years at hard courses rated too high? (MEASURING)

Owner: "part of the issue with races like Ultimook/Glendoveer is the
weather. When the weather is good the course is a lot less difficult, but
since weather corrections aren't doing enough, the easy years aren't
penalized enough." The course difficulty is per (course, 2-year era); a
single day's conditions reach a rating only through the weather correction
and the joint solve's shrunk race-day term. If those under-correct, a mild
dry year at a hard course is charged the course's average difficulty and
its runners rate high.

Test (scripts/diag_weather_credit.py): new "in-course" column -- each
race's gap (its runners here against their own other races within 30 days)
less its course's average gap, so a course is compared only with its own
other years. About 0 in every bucket = handled; mild/dry above 0 and
wet/hot below 0 = the owner's point, by the amount shown. `--course
Glendoveer --course Ultimook` lists their race days year by year.
The fix, if it measures: a race-day term that the weather cannot absorb
is shrunk less (or the weather betas are too small; the bucket's "model"
column says which).

## 2026-09-28 — the XC course scale x1.11 was the prior's signature; hard well-known courses overcharged (FIXED IN CODE, NEEDS 08)

Owner: "is difficulty tilt actually real?" This run's go-live tables:

    tilt by band (XC)      applied  implied       tilt by races per cell (XC)  applied  implied
    <100                     1.012    1.161       1 race                         0.968    1.220
    100-120                  0.970    1.075       2-3                            0.967    1.057
    120-130                  0.926    1.008       4-9                            0.964    0.977
    130-140                  0.897    0.996       10+                            0.960    0.917
    140-150                  0.867    0.997       -> course scale XC x1.110

The races table falls from 1.26 to 0.955 -- by its own legend "the prior,
not the sport's scale". In sample that is what shrinkage looks like: a
one-race course's number is its voters' reading times what the prior lets
it keep, so the same voters regressed on it return the inverse. The band
table's x1.11 averaged that over all voters (two thirds on 1-3 race
courses) and was applied to EVERY course, so the well-known hard venues
(10+ races, 0.955 before the scale) were charged about 16% more of their
difficulty than their runners pay: at a +10% course, ~1.6% of time, a
couple of rating points for everyone who raced there. It is the
"Glendoveer / Mt. SAC / Crystal Springs seem overstated" complaint.

Now `--course-scale fit` reads the scale from the 4-9 and 10+ buckets only
(bracket_engine.courseScaleFromRaces; about x1.00 on this run's numbers);
`bands` is the old reading. And a new table, tilt by band on courses with
4+ races only, is the tilt's own test without the prior mixed in: if
implied/applied is the same in every band, the slope is right; if it rises
at 140+, the elite tilt is too steep. Applies at the next 08 (from state:
no re-solve).

Also: diag_weather_credit read `results.distance`, which does not exist;
it now takes the race's distance the way grade_sanity does.

## 2026-09-28 — why good-weather years at hard courses rate high, and XC:fast (IN CODE, OFF UNTIL CHOSEN)

The mechanism behind the owner's Ultimook point is the 2026-09-06 decision
itself. The solve still fits a race-day term u, so a good-weather year's
speed goes into u and the COURSE keeps its all-years difficulty; but u is in
no rating. So every runner on the easy day is credited the full hard course.
The weather correction is the only thing that can take it back, and it is
small (XC betas: rain -0.02%/mm, wind -0.02% per m/s).

`XCP_RACE_EFFECT_SPORTS=XC:fast` puts only a FAST XC day into the rating
(u < 0, clipped at the cap as before); slow days stay out. The 09-06
objection ("a slow race is a slow race; the model cannot tell mud from a
jog") is about slow days: a field can jog, it cannot run collectively
faster than itself. Cost to know about: a championship where the whole
field peaks reads as a fast day too (the form curve takes some of that).
Off unless set; applies from state (no re-solve). Judge it with
diag_weather_credit's in-course column before and after.

Also: the conversions note said "how that meet ran" -- no rating carries
the day term, so it now says the venue and the measured weather.

## 2026-09-28 — the weather fitter fitted leftovers: rain credited backwards (FIXED IN CODE, NEEDS 04f + 05 + 08)

diag_weather_credit (XC since 2015, 108,630 races), in-course gap by rain
in the race window: 0-0.1 mm +0.07%, 1-3 -0.21%, 3-8 -0.37%, 8-20 -0.90%,
20+ -2.57% -- rain races rated low against the same course's other days --
while the model's RAIN term was NEGATIVE in every wet bucket (-0.02% to
-0.51%): the correction took credit AWAY for rain. This run's XC betas:
precip -0.00024/mm, wind -0.00018, snow -0.399/m; an earlier fit had snow
+0.748/m.

Cause: fit_weather_correction reads results.normalized_time, which 05 wrote
with the previous artifact divided in, and 04f runs before 05. Each refit
measured only the previous correction's leftover, and 05 then applied that
leftover instead of the effect, so refits alternate between the effect and
about nothing (the sign flips are that). Also: the athlete effect was one
per career, so a high schooler's yearly improvement sat in the residual of
the event (same venue and fortnight across years) and any drift of the
weather over the years leaked into the betas.

Now: the fitter divides the applied correction back out before fitting
(undoAppliedWeather; the backfill records the artifact it applied in
engine/data/weather_applied_<sport>.pkl, and without a record the current
artifact is taken as applied, which is true for this run's rows), and the
athlete effect is per (person, season). The temperature column (cool days
+0.4-0.7% high, hot days -0.2-0.6% low against the same course) is partly
season position (August time trials vs December championships at one
course); the refit holds the calendar fortnight fixed, so it measures only
the part that is weather. --course now also matches meet names (Ultimook's
course carries another name).

## 2026-09-28 — 04f too slow and silent (FIXED IN CODE)

Owner: "this is taking too long, also don't only print it to a file, print
to console too". The parallel runners (steps2, stepsN, shards, bgstep) now
stream every line to the console prefixed `[step]` as well as to the log
(`_live`; XCP_LIVE=0 for logs only; exit codes unchanged). The weather fitter:
the grid is aggregated only over the cell-days a race used (it grouped the
whole weather_grid first; same rows to float precision on a scratch DB),
rows load a batch of columns at a time, the undo runs once per race, the
two-way demeaning stops when a pass moves nothing, and each stage prints
its seconds.

## 2026-09-28 — 📋 LOGGED, NOT ACTIONED: summer 800s rated ~92, an 880y on top of all-time, foreign Olympians on the HS all-time board

Owner: "just log these for now".

**1. Post-collegiate summer 800m finals rated in the low 90s.** One athlete's
page (a college 1500 runner):

    Jul 19, 2025  Stumptown Twilight           800m Final    1:44.89  1st  92.4  0.0%  PR SR
    Jul 12, 2025  Sound Running Sunset Tour    800m Final    1:46.25  1st  91.2  0.0%  SR
    Jun 11, 2025  NCAA D1 Outdoor Champs       1500m Prelims 3:52.76  31st 138.6 0.0%

A 1:44.89 is worth far more than a 3:52.76 1500, not 46 points less. Both
summer rows are open/pro meets after the college season: suspect the pool
(the row priced in a pool whose mean does not fit -- masters, open, or the
wrong gender), or the 800 read as another distance. Check the rows' pool
and normalized_time (scripts/explain_joint_row.py --tf <id>, diag_row_weather).

**2. The HS all-time board's #1 is an 880-yard run.** "the best performance
of all time being an 880Y 1:53 isn't right": Jonathon Riley, Brookline MA,
1997-06-07, 159.8. An 880y is 804.67 m; 159.8 is what a 1:53 is worth if it
is read as 880 METRES (about a 1:42.7 800). Suspect the event parser taking
"880Y"/"880 yards" as 880 m. Others on the page from yards eras or odd
events to check the same way: Mike Flynn and Michael Charron (Griswold CT,
1999-2000), Will Jackson and Michael Katzeff (Brookline MA, 2009-01-21,
same day -- an indoor meet, maybe an unbanked or short track), Larry
Cardona (Garfield CA 2004), Michael Granville (Bell Gardens CA 1996).

**3. Foreign national teams on the US HS all-time board, as US towns:**
Joao Baptista N'Tyamba "Angola IN" (1992-08-09 and 1995-03-10), Vyacheslav
Shabunin "Russia OH" (1996-08-04), Clive Terrelonge "Jamaica CA"
(1992-08-09, the Barcelona Olympics). Angola, Russia and Jamaica are the
names NATIONAL_TEAMS left out ON PURPOSE because they are also US towns
(Angola IN, Russia OH), so the state gate then reads a US state. A national
team is an Olympic/World meet entry, not a town: the meet (Olympics, World
Championships) or the athlete's other rows say which. Also "Unknown, Puerto
Rico, CAROLINA" (2026-02-06, 147.9): a foreign row with no name.

**4. Two "Unknown" names on the board:** #29 (Puerto Rico) and #48
("Enclave DC MA", 2025-06-14, 147.3). fillUnknownNames covers race pages;
the all-time board needs the same fill.

**5. Non-school entries:** Carter Vangessel "Indiana BLAST Track Club IN"
(grade -, 2022-01-08, 154.8, #2 all time) -- a club row with no grade on the
HS board; check its pool and whether the mark is real.

## 2026-09-28 — the corrected weather refit (XC, measure-only on the server)

With the applied correction divided back out ([undo]: 161,127 races, median
0.46%, p99 3.28%) and athletes per season, XC signs are physical again:
wind +0.00072/(m/s) (was -0.00018), rain +0.00071/mm (was -0.00024), snow
+0.738/m (was -0.399; the older good fit had +0.748), heat +1.3% at 20 C,
+3.1% at 27 C, +4.8% at 33 C (5k), mud +1.8% at soil 0.50. Load took 6,518 s
at ~5,000 rows/s: a named cursor plans for 10% of its rows, and the weather
CTE (referenced once, so inlined) could be re-aggregated per row. Now the
CTE is MATERIALIZED and the session plans for the whole result
(cursor_tuple_fraction 1.0, 1 GB work_mem). The fit itself took 1,344 s.

## 2026-09-28 — 📋 LOGGED: Mt. SAC split, Foot Locker Nationals too easy, NCS 2024, from two athlete pages

Read off the live pages (Trey Caldwell /athlete/29406443, Tadhg Murray
/athlete/29603086), same team, same races:

**Mt. SAC 2024 was cannibalised by a distance split.** 2021-2023 are stored
at 4715 m and read +11.8%; 2024 is stored at 4828 m (3 mi) and reads +6.3%.
The difficulty cell is keyed on the distance rounded to 100 m, so 2024 is a
different cell with one race day, pulled toward the average by the prior.
Trey: 14:49 in 2023 = 140.6, 14:46 in 2024 (a faster time on a course
listed 2.4% LONGER) = 137.8. Either 2024 is the same 2.93 mi course listed
as 3 mi (a distance override, and the cell rejoins its history), or a
course's cells at distances within a few percent should share one history.

**Foot Locker Nationals (Morley Field) reads far too easy: +4.7%.** Trey
rates 131.5 / 132.1 / 131.4 there (12th, 25th, 30th at Nationals, 2022-24)
against 136-140 everywhere else that month, a week after 137.6-139.9 at
the West regional. The cell is Morley Field 5000 m, shared with the local
San Diego meets on other loops, so the Nationals days are diluted. The
fix is its own cell (meet_cells --meet "Foot Locker" was the open item).
West regional at Mt. SAC 5000 m reads +9.6% and lines up with his other
races. ⚠ The pending course-scale change (x1.11 -> ~x1.00) lowers every XC
course number by about a tenth of itself, Morley Field included; read this
again after that run.

**NCS 2024 (Hayward HS, Nov 23, rain before, 16 mph wind) is short-changed.**
Trey 130.1 (won) against 135-138 around it; Tadhg 123.7 against 129-131.
The race page itself says the field ran about 1.6% slower than an ordinary
day there -- the day term knows -- but no rating carries the day term
(2026-09-06: "a slow race is a slow race"). The old weather artifact gave
it little (rain credited backwards until the refit fixed in 9f2f099).
After the refit, check diag_row_weather --xc 59845239; what is left is the
slow-day question, which is the owner's call.

**Also on these two pages:**
- Tadhg's Foot Locker West 2024 row sits on a separate person, "Tadhg
  Murray -- Unknown (WA)", /athlete/13642378; Trey's FL rows merged fine.
- The 2021 Mariner Invitational is listed twice under two names ("38th
  Mariner-XC-Invitational", "38th P. Wilder Mariner XC Invitational"), same
  day, place and time: dup_cross_date needs the same normalised name.
- "Mid-Season Mania 1600m Invitational (XC Calendar Placeholder)", a 1600 on
  the TRACK, shows in the XC season with a course difficulty (+3.2%), and
  again in track. A placeholder meet in the XC calendar should not be XC.
- Michael Rynne, JAMBAR Dec 13, 2025: "2miles 9:35.09" and "3200m 9:31.74"
  are one race stored twice (9:35.09 x 3200/3218.7 = 9:31.6), not two
  events given one rating.

## 2026-09-28 — rankings boards down (FIXED IN CODE, 8b1c993)

Every ability board, the default board included, answered 400 "column
result_id does not exist": the USA scope's tfrrs clause (06d112b) went into
the shared filter builder, and the season boards read athlete_season.

## 2026-09-29 — college first-years rated on the HIGH SCHOOL scale (FOUND, NEEDS THE SERVER TO SAY WHY)

Bates NCAA DIII East Region Preview, 2026-09-05, men 4340 m
(/race/xc/282053/1126781): 45 "Unknown" rows, nearly all FR-1, person ids
33160712-33160785 (one batch), rated like high schoolers -- Brandeis's
2nd man 13:23.6 = 140.2 while the winner's 13:21.8 = 109.2; UMaine
Farmington 13:26.3 = 139.7. The named runners are on the college scale.
Not the grade: normalizeGrade('FR-1') -> 'Fr' -> college. Candidates: the
rows are newer than the last solve and 09b fill_ratings priced them with
a pool the fill chose (new persons, no gender/level yet), or 04a linked
them to a high-school career whose season verdict says hs. Settle with:

    /srv/venv/bin/python engine/explain_row.py --person 33160729

This is the outside review's issue 5 ("first-years not linked"), and worse
than it said: they are not just unnamed, they are on the wrong scale and
may be on the HS boards.

Also on that page: Bates' seven men finished 60-65 and 69 together (the
review's "pack workout" reading fits: one team, not the field), and the
race-day weather reads "wind 48 mph", which is not a plausible September
morning in Maine -- check the wind unit on the race page.

## 2026-09-29 — the 880y at #1 all-time: the parser is right, the rating is not (NEEDS explain_row)

Jonathon Riley (Brookline, /athlete/7145543), MIAA All State 1997-06-07,
"880y" 1:52.97 = 159.9, while his 4:05.72 mile the next week = 142.0 and a
1000 m 2:26.1 = 138.5. distanceFromEventShort('880y') is 804.67 m and the
solve's distance class rounds it into the 800, so neither the parser nor a
thin class explains ~18 points. Settle with:

    /srv/venv/bin/python engine/explain_row.py --result 49384026 --sport TF

Fixed on the way (event_parse): '880yd' (glued) and a bare '880' / '1320' /
'1760' / '660' were read as METRES; they are yards now (600 and 1000 stay
metric). Lands at the next 05 backfill.
## 2026-09-29 — Mt. SAC distance split and Foot Locker Nationals' cell (FIXED IN CODE, NEEDS 07 + 08)

The two course items logged on 2026-09-28, from Trey Caldwell's page.

**Mt. SAC d4700/d4800: one course, one history.** The cell key rounds the
distance to 100 m, so 4715 m (2021-2023) and 4828 m (2024) were two BASE
courses and the one-race 2024 cell rested on the sport's average.
bracket_engine.nearDistanceSiblings now gives a course's cells at distances
within 3% (SIBLING_DIST_TOL) one base: the era prior pulls d4800 toward
d4700's history, each distance keeps its own cell and number. Anchored on
the most-raced distance, not chained; a corpus with no near sibling is
bit-for-bit unchanged, and a course without one moves only through the
shared pin (0.03% in the planted world, tests/test_distance_siblings.py). Off:
XCP_BRACKET_SIBLING_TOL=0 (run_joint --bracket-sibling-tol). The log prints
`[bracket] distance siblings: N XC courses ...`. Needs 08 only.

**Foot Locker's own course key.** engine/champ_course.py: a meet named for
the Foot Locker / Champs Sports / Eastbay national final or a regional is
keyed `XC:champ:<slug>:d<m>` instead of the park's canonical id, so the
final is one cell across years, spellings and sponsors and no longer shares
Morley Field with the local San Diego meets. Read per name in
tmp_pack_meet_class (column `champ`); publishes as "XC:Foot Locker
Nationals" with canonical_id NULL. `scripts/meet_cells.py --meet "Foot
Locker"` now shows the champ key as the cell. Needs 07 (the pack) + 08.
⚠ NOT YET ON THE PAGE: app.py resolves a row's difficulty and day through
course_canonical -> (canonical_id, distance), so a Foot Locker row will SHOW
the park's (now local-only) cell and no day term while its RATING comes from
the championship cell. The joins need the champ key before they agree.
## 2026-09-29 — a high school race on four college athletes (FIXED IN CODE, NEEDS 04c)

Outside review: four NESCAC runners in college in autumn 2025 each carried
the same high school race, the Middlesex League Championship of 2025-10-26
(5000 m): Jared Rife (Middlebury) 15:17.1 rated 131.2 vs a college median
107.5; Tyler Johnson (Trinity) 15:44.1 127.3 vs 106.0; Nick Walker (Bates)
16:28.5 121.5 vs 106.8; Max Bennett (Conn College) 17:08 116.8 vs 96.8. It
reached the published seasons (Nick Walker's 2025 XC 108.9 vs ~104-106).

**How a person gets both.** anet rows are seeded `person_id = athlete_id` and
nothing in the pipeline re-points an anet row (only unlink.py, by hand, to
fresh ids). So an anet high school row sits on a college person when the
college person IS that anet id: a tfrrs career was welded onto an anet
person by NAME -- `link_freshmen` (04a pass 2, every run), or by hand
`link_idless_by_name` / merge_links -- and 04a pass 1 (fan-out by tfrrs id)
then carries that person to every new row of the tfrrs id, every run. The
other way it happens is not a link at all: the row is the athlete's own and
its date is wrong. `engine/level_conflict.py --person <id>` prints each
row's feed, athlete_id, grade, school and `person_link_log` rule, which
tells the two apart.

**Now:**
- `engine/level_conflict.py`: one person, one sport, one academic year with
  rows that can only be college (a tfrrs college team slug, or -- slugless
  -- FR-1..SR-6 at a known college: college_directory or a college slug on
  the same school string, vetoed by any high school slug on it) AND rows
  that can only be high school (numeric 9-12, on a team) is a conflict --
  unless every high school day precedes the first college day (a December
  graduate on a college indoor team). The side with fewer race days is
  flagged; a tie flags nothing and is reported.
- **First server run (2026-09-29), two corrections.** (a) The grade form
  alone was read as college, but tfrrs-hosted high school meets print it too
  (Winnisquam 'SR-4', Westminster Academy 'SO-2', The Benjamin School
  'JR-3'), so high schoolers lost races; it now needs the school or slug.
  (b) "A tie flags both" cost person 6339154 (Kenston HS + RPI) his season;
  a tie is no evidence. And the review's own race was a WRONG DATE: anet
  meet 227716 is a 2023 meet stored as 2025-10-26. `engine/meet_date_fix.py`
  (step 00_meet_dates) moves a meet's year when its graded high schoolers
  (their class year from their other rows) vote by a strict majority for
  another season, and logs every fix in `meet_date_fix` (`--undo <meet>
  --sport XC` reverses one).
- **The first cut's detector failed the owner's dry run (2026-09-29).** It
  read a meet's year off its 200 nearest meet ids: |date - neighbourhood|
  was XC p50 13d / p90 385d / p99 10,560d, TF p99 15,292d, so every meet was
  REPORT ONLY. anet ids are not chronological (Sunfair Invitational 1999 is
  id 244560, among 2024 meets). The grade vote is now the detector, over
  every anet meet in both sports; the minimum voter count is derived from
  the measured grade noise (fewer than one false FIX expected per sport),
  and a one-year vote that is its month's norm (a summer meet graded for the
  coming year) is held as a season-seam convention.
- **The grade vote's dry run (2026-09-29): 227716 FIX k=-2, Sunfair kept,
  but college and MS meets moved by two or three strays** (Texas A&M
  Arturo Barrios 219204 2/0/2 over 707 rows, Parkside 22054, Messiah
  653761 in track; Indiana MS XC Championships 232721, KLAA MS 149615).
  Two misgraded people are not independent witnesses, so "minimum voters =
  2" was far too low. Now every numeric grade 1-12 votes (an MS meet's own
  runners outvote its strays), and the GATE: the winning voters must be a
  strict majority of the meet's distinct anet athletes (people, not rows).
  The FIX table shows the athlete count; `meet_date_fix.n_athletes` records
  it. Needs a dry run: `engine/meet_date_fix.py --show 80`.
- The flag is `result_twin` reason `level_conflict`, a rule of
  `engine/twin_flag.py` (04c, before the pack), so the engine, fill, boards
  and athlete page already exclude it. Rebuilt every run; nothing is moved
  or deleted. `level_conflict.py --write` refreshes only these rows for a
  run that starts past 04c.
- `link_freshmen`: the first-year must hold a row that can only be college
  (a bare "Fr" is also a ninth grader at a tfrrs-hosted high school meet),
  and an anet "senior" who races high school again in the freshman year or
  later is not a graduate and is not paired.
- `link_idless_by_name`: a college row never joins a person with a high
  school row in the same season.
## 2026-09-29 — one runner, two people: teamless anet profiles (FIXED IN CODE, NEEDS A DRY RUN, THEN 04a2)

Logged 2026-09-28 from Tadhg Murray's page: his Foot Locker West Regional
(Mt. SAC, 2024-12-07, 16:34, 129.4) sits on /athlete/13642378, "Unknown
(WA)", while his De La Salle career is /athlete/29603086. anet filed that
race under a second profile with no school -- every runner at a Foot Locker
regional is listed under a HOMETOWN ("Danville CA") -- and anet rows keep
person_id = athlete_id for ever. Trey Caldwell's merged only because anet
had filed his under his real profile. Owner: a Foot-Locker-only rule is too
narrow; handle any anet profile with no real team.

**Now:** `scripts/link_teamless.py`, pipeline step `04a2_link_teamless`
(after 04a, before grade_sanity/twins/gender/the pack; in `_ALWAYS`). A
profile whose EVERY row has no team (anet team 0, Unknown, Unattached in
any spelling, a "City ST" hometown) joins the ONE same-name, same-gender
athlete with a real-team row within 60 days of each of its rows -- counted
before the grade filters, so two namesakes is a refusal -- when the grades
fit (class year within one; never a high school all-star onto a college
namesake) and two of three agree: rating within 6 of the namesake's median
there, the same state, a postseason meet in the 21 days before an all-star
race. Rating off by 15+ refuses outright; two teamless profiles claiming
one target are both refused. Tadhg: all three (129.4 vs 129.0; CA; CIF
State 7 days before).

Every moved row is in `person_link_log` rule `teamless`; the decision in
`teamless_merge`. The profile's `athletes` rows move too -- without that,
`person_redirects` calls the old id alive, 13c0 writes no redirect and the
old page renders empty; with it, 13c0 derives the same redirect from 01a's
snapshot (the script also writes it, for runs outside the pipeline). A
re-scrape's next teamless row for a merged profile is re-homed on the next
run. `--undo <id>` reverses one and vetoes it; `--undo all` reverses every
one (then `XCP_LINK_TEAMLESS=0`).

Not done: the Foot Locker row is still priced as `no_team` (pro) by
pool_resolve whichever person carries it -- that is 2026-09-06's
"team_id = 0 is pro, unconditionally" if its team id is 0, or the hometown
pseudo-team's level if not. A tfrrs-only namesake (no anet profile) is not
seen by the name search, which reads `athletes`.

## 2026-09-29 — 📋 LOGGED, NOT ACTIONED: St. Mary's Invitational and the next scrape

Owner: "We can go on st mary's soon after fixing the solves just log that and
the next scrape for now."

**St. Mary's Invite was never scraped.** First find out why, before
re-scraping: `scripts/find_meet.py` looks a meet up in every meet table and
says what its queue state means (due / done with 0 results / failed /
claimed / no meet / never seeded).

    /srv/venv/bin/python scripts/find_meet.py "St. Mary"
    /srv/venv/bin/python scripts/find_meet.py <anet meet id>             # id from athletic.net/CrossCountry/meet/<ID>
    /srv/venv/bin/python scripts/find_meet.py <anet meet id> --requeue   # only after reading the reason

**The next scrape** (runbook 2026-09-27), after the solves are fixed:

    /srv/venv/bin/python scripts/queue_meets.py                 # dry run: what it will seed
    /srv/venv/bin/python scripts/queue_meets.py --source tfrrs
    NO_VPN=1 ANET_RETRY_FAILED=1 xvfb-run -a /srv/venv/bin/python -u scripts/launcher.py        # failures first
    NO_VPN=1 PER_MEET_DELAY=3,6 xvfb-run -a /srv/venv/bin/python -u scripts/launcher.py         # then everything due

## 2026-09-29 — 📋 COMPILED: what the owner's check runs said (logs/checks 142744, 161158, 161425)

The owner, after two runner logs: "can we just compile and log after this,
feels like a lot of runaway". So: no new checks. This is the state.

### ⚠ BLOCKER FOR THE NEXT PIPELINE RUN: a graduating senior's summer race, normalised as college

**Ajani Salcido, Brooks PR 2021-07-02 (grade 12, Jesuit OR).** The live row
holds nt 823.4 (the pack, 19 h old, agrees with the database) and rates
149.4 in hs_m. The backfill's own replay on today's code writes **nt 1358.6**
for the same row: the college normalisation (the 8000 m anchor). Every other
senior-spring row of his replays at 862-906. The board still resolves the
row to hs_m, so the next 05 backfill would rate a college-normalised time on
the HS scale -- about 90 instead of 149.

**Tayvon Kitchen (29332123) is the same fault already live.**
athlete_season_level says TF ay 2024 = **college** while XC ay 2024 = hs, and
college_first_season is 2025 (2025-08-01). So his whole senior spring (grade
12, Crater OR, Dec 2024 - Jul 2025) is pooled college_m: 106-116 on the
college scale, HS-equivalent about 128-139. That is why his 8:43.94 at
Arcadia reads below the owner's 9:01. (His unattached winter meets at
college-hosted meets are the likely voters.)

**The fix, not yet written:** a season before the person's
college_first_season, with a school grade (<= 12), cannot be level college --
not in athlete_season_level, not in the backfill's season lookup, not in
resolvePool. Plus: check the backfill's season boundary for a July row
against season_year.seasonYearFor (Aug-Jul). Salcido's 2021-07-02 belongs to
ay 2020 (hs); ay 2021 is his first college year. Test cases: both people
above. **Do not run 05_backfill (pipeline --from 3 or --from 5) until this
lands.**

### Answered

- **Hanna Mosley**: clean. `person_collision --write` (11,186) is safe.
- **Amnesty**: 15 pardons, including NCAA DI 2025 (median 1790 s vs the
  deepest real field's 1789 s). The other 130 stay dropped (5:50-median 5Ks
  and similar).
- **Meet dates**: XC 25 + TF 50, the grades agree. `meet_date_fix --write`.
- **Sahlman (29347137)**: the TF why_unrated ran (sprint temp table fixed in
  3f961a1). His unrated rows are in the 161158 log; not read yet.
- **MS zone meet 200996/806899**: Lutkenhaus's 12:14 was normalised from
  ~2,400 m (nt 967.7, x1.32), Vijaykumar's 13:17 from 2 miles (nt 771.9,
  x0.97): same division, same ms_m pool. Only a per-result pin
  (_RESULT_OVERRIDE) gives one row its own distance; `peek_override
  --result` (355b572) can now see those pins -- the old lookup used a
  (meet, div) key and never could. Not yet run.
- **NCS 2024**: weather credited +0.7% (2023 got +1.3%); the day was +6.9%
  slow and stays out of the rating by the 2026-09-06 rule. With it: ~131.7
  instead of 123.7. Open decision below.
- **Boards**: still 4.2 s -- the NULLS LAST index lands when athlete_season
  is rebuilt, or now with the CREATE INDEX line in owner_checks.
- **Group means**: pools read ~100 on themselves. HS-equivalent: NCAA DI men
  128.7 (16:00 XC 5K), DII 120.3, DIII 116.5; DI women 130.0.
- **Indoor levels** written: under 200 m +0.91%, flat 200 +0.22%, banked
  -0.64%, 300 m+ -0.54%, unknown +0.68%; joint level +0.02%.

### Open decisions (the owner's)

1. **Outlier slow cut.** Measured cuts: 7.5 sigma fast, 8 slow (5,788 fast,
   77,536 slow flags; the slowest are broken rows rated ~2). Bobby Doyle is
   80.4 vs a neighbour median of 100.7, z = -4.2: kept at 8. A cut near 4
   catches it but flags ~427,000 slow races, a third of them genuine bad days
   by the tail fit.
2. **Distance curve by ability (B+C).** Held-out bias: HS girls XC -2.7% ->
   -0.3%, HS girls track 1600->5000 -6.1% -> -0.1%, HS boys XC -1.4% ->
   -0.3%. Worse: college men slightly, elementary track clearly (-2.3%).
   The pool still matters after ability for middle school (5K->10K -4%),
   hardly for college (0.2%). Recommendation: adopt with the per-pool
   residual applied (a small change, not made).
3. **The grass-to-track gap per pool.** Same athletes, same ability: HS
   girls' track rates ~6% under their XC, HS boys 2.5%, college ~0, MS +2.5%
   (diag_sport_gap_ability). One constant serves every pool today. Re-measure
   after (2) -- the HS girls' curves are also the most biased there -- then
   set per-pool, per-gender shifts from what is left.
4. **Slow race days in XC** (NCS 2024): still out by rule.

### Order, when the blocker is fixed

    meet_date_fix --write; person_collision --write;
    amnesty_division_drops --write && dump_overrides;
    rating_outliers --write (after decision 1);
    XCP_WEATHER_FIT=1 bash deploy/run_pipeline.sh --from 3

## 2026-09-29 — ✅ THE BLOCKER, FIXED: a senior's season pooled as college

**(b) Salcido's July row -- the backfill's own clock.** backfill_normalize's
`_academicYearOf` was the last private July seam (`month >= 7`): it keyed
2021-07-02 on season 2021 -- grade_fix 'FR', season verdict college -- so
normPoolFor wrote the row on the college anchor (nt 1358.6). Every other
reader (grade_fix/athlete_season_level joins in build_ranking_results,
fill_ratings._rowPool, speed_ratings.poolOf) keys it with
season_year.seasonYearFromIso on the August seam: season 2020, grade '12',
hs_m. season_level._academicYearExpr had moved to August with a "REBUILD"
note; its twin in the backfill never moved. It delegates to season_year now.

**(a) Kitchen's college track season.** Nothing stopped a 'college' season
verdict (votes from unattached indoor races at college meets) from
outranking a corroborated grade 12: resolvePool's field rule took it, while
normPoolFor let the 12 silence it -- so his rows were written on hs_m's
anchor and rated on college_m's. Now one rule,
`normalize_distance.seasonVerdictFor`: a college verdict on a school grade
(a number 1-12, not a class word) stands only from the person's first
collegiate season (college_first_season.first_date, academic year) on.
Applied at the source (season_level.refuseCollegeBeforeCollege: level NULL,
`refused` = 'college'), in resolvePool (incl. the unattached race ceiling,
screened by grade_sanity's grade only), and in normPoolFor, which now also
takes a standing college verdict over an hs grade exactly as resolvePool
does. college_first_season is read again -- as a veto only.

**Census.** The backfill prints "scale split": written rows whose pool's
anchor differs from the pool the last go-live rated them in
(results.rating_pool), by pair. 05b_anchor_repair still rescales them.

**Before the next run** (read-only count first; the two --write steps go
after meet_date_fix / person_collision in the order above, since both read
dates and person ids, and before `--from 3`; neither is in run_pipeline.sh):

    /srv/venv/bin/python scripts/college_veto_census.py --show 20
    /srv/venv/bin/python engine/college_flag.py --write
    /srv/venv/bin/python engine/season_level.py --write
    XCP_WEATHER_FIT=1 bash deploy/run_pipeline.sh --from 3

---

## 2026-09-30 -- results_tf block 491889 damaged; the pipeline now checks every page first

**What happened.** Run 20260929 failed 14 steps on `DataCorrupted` (invalid
page in block 491889 of results_tf); the backup's pg_dump failed on the same
page. The server's RAM has no error correction and the kernel logged
machine-check errors on Sep 22 and 25 (the NVMe reports 0 media errors), the
likely cause of this and the earlier corruption. Hardware ticket to the host
recommended; ECC on the next server.

**Repair (the recipe that worked).** `scripts/lost_rows.py --block 491889`
read the lost rows' keys back from the indexes BEFORE any reindex: 38 tfrrs
rows, none on a board, from 38 meets, re-queued (scraped = 0). Then
`SET zero_damaged_pages = on; VACUUM FULL results_tf` (208 s; a plain VACUUM
only zeroes the page in memory and never writes it). amcheck after: 0
problems, 192,380,710 rows.

**Now automatic.** Step `00_integrity` (`scripts/integrity_check.py`) runs
first on every pipeline run, `--from` included: amcheck `verify_heapam` over
every page of every table, one 1 GB segment per statement. A page too broken
for amcheck to read is found by splitting the range down to the one block.
Any damage stops the run before another step reads it, emails the owner, and
prints the lost_rows and VACUUM FULL commands for each damaged block. The
daily backup's pg_dump reads every page too and already emails on failure.

## 2026-10-01 -- after the repair: step 01a ran 3 h; and 18 duplicate team indexes

**01a_person_probe never finished, twice** (the run of 2026-09-30 20:49 and
the rerun after the host's hardware test). Not a lock: the query was
working. `VACUUM FULL results_tf` (the corruption repair) leaves the table
with an empty visibility map, so the GROUP BY person_id that used to be
answered from the index visited the table row by row. Fix:
`VACUUM (ANALYZE) results_tf; VACUUM (ANALYZE) results`. Now step 3 of the
repair recipe in `scripts/integrity_check.py` (its docstring and its
failure output).

**00_integrity measured: 80 s** for every page of the 360 GB database.

**The host's hardware test (2026-10-01) found nothing.** The MCE records of
Sep 22 and 25 are both on CPU 1 with no unit named (IPID 0, "bank
reserved"). If another MCE appears in `journalctl -k`, or 00_integrity
finds damage again, reply on the same ticket and ask for a chassis swap.

**Duplicate indexes.** `results` had 10 copies of `idx_results_team`
(`_f`, `_f_f`, ... 243 MB each) and `results_tf` 8 (1 GB each).
`scripts/add_page_indexes.py` (step 11b) rejected every partial index as
unusable, including the partial index its own team_id entry asks for, so
each run called it missing and built another under a fresh `_f` name.
Fixed: a partial index counts when the spec is partial and matches, and the
step drops the `_f` copies of an index it finds OK (DROP INDEX
CONCURRENTLY). The next run's 11b removes the 18 copies.

## 2026-10-01 -- the race-day trial, built (switches, all off by default)

Owner: "I'd like to do the race day effect but it seems too easy to game"
-> build it as a switch, see it live, then decide whether it is the final
methodology. What exists now:

- **The day is what's left after the weather.** The normalisation takes
  measured weather off every time first, so the solve's day term is the
  remainder, shrunk toward zero: it starts from the weather and moves only
  as far as the field agrees.
- **`XCP_RACE_KEY=venue`**: one day per venue and day across every race
  there (varsity, JV, boys, girls, every distance), so one tactical race
  cannot move it. Changes the solve; 08a and `scripts/scorecard.py` fit it
  too.
- **`XCP_RACE_EFFECT_SPORTS=XC`**: the day in XC ratings; track stays out.
- **`XCP_RACE_EFFECT_OWN=leave-out`** (the default whenever the day is in a
  rating): each runner is credited the day as the rest of the field ran it
  (`engine/joint_solve.py: raceEffectLeaveOneOut`), so nobody's own time
  moves their own day. Exact algebra, checked against dropping the row
  (tests/test_race_day_trial.py). A one-runner race gets the prior only.
- **The go-live judges it**: "day-term consistency" prints how much an
  athlete-season's races scatter about their own mean with the day out,
  in (leave-self-out), and in (the race's own u). Lower with the day in =
  real day effects; higher = noise, leave it out.
- **Site**: set `XCP_RACE_DAY_SPORTS=XC` in /etc/xc-predictor.env when the
  ratings carry it, so the difficulty hover says so.

**01a again, 8 hours (2026-10-01 15:22 -> 23:23).** Still stalled after the
reboot. The GROUP BY person_id was being walked through the person index
(a random heap read per row) on the rewritten table. `scripts/
person_redirects.py --snapshot` now switches the index paths off for its
own transaction (one sequential read per table, a hash aggregate) and stops
at `XCP_PROBE_TIMEOUT_MIN` (30): a stalled 01a is now a failed step that
keeps the previous snapshot, not a stalled pipeline.

**Run 20261001_232626 (7h 39m): FAILED 01a, 10_rankings_finish, 10a.**
- *10_rankings_finish*: `as_board_mean_idx`, `as_pool_mean_idx` and
  `as_pool_sport_mean_idx` were redefined with NULLS LAST on 2026-09-29;
  the live athlete_season still had the old definitions under the same
  names, so indexDefs listed old (catalogue) and new (canonical) under one
  shadow name. One was skipped by IF NOT EXISTS, and two built at the same
  moment failed on pg_class_relname_nsp_index. Fixed in indexDefs: a
  canonical name whose columns changed drops the catalogue's copy
  (tests/test_index_dedupe.py). The boards did not swap; the site kept the
  previous run's boards.
- *10a_board_sanity* (25 hard findings) read those previous boards; re-read
  after the rerun.
- *01a* hit its 30-minute limit even reading straight through (2 workers,
  512 MB under quiet mode, spilling). It now gets half the cores and 2 GB
  for its one transaction.

## 2026-10-02 -- site review in a browser (desktop 1366 + phone 390)

Fixed (this commit):
- **Phone race/course pages laid out 15,000-17,800px wide.** The track-
  equivalents ruler's scale (a strip thousands of px long that scrolls inside
  .eqc-ruler) was counted by the page's `min-width: min-content` rule, so the
  header, results and every paragraph stretched across it. The wrapper now has
  no intrinsic width; the ruler still scrolls (17,785 -> 685px on a 1993 NCAA
  D2 race; the rest is the results table, the owner's scroll-sideways rule).
- **Desktop Athletes/Coaches switch broken on every page** (stacked, the
  black tab over "Coaches", no border): a stray `}` in style.css made the
  next rule's selector invalid, so `.topbar .viewswitch` was dropped.
  tests/test_css_braces.py now fails on any stray brace.
- **"Xavier (NY) (NY)"** in NXN 2024's team scores: names that already carry
  their state got it appended again. school_identity.withState appends once;
  every label site uses it.

Found, not fixed:
- **Jackson Spencer (30178075)**: Sep 11 2021 "Tallmadge Middle School
  Madness Meet" (Ohio, 2172m 12:08.3) is on his page -- he is Herriman, UT.
  A different Jackson Spencer's row; it is the 73 dip on his chart.
- **Phone: text cut off at the right edge** on boards, race, school, search,
  meets, home, athlete pages: the page is as wide as its widest table (the
  2026-09-06 equal-width rule), so headings and paragraphs run off screen.
  Mocked alternative (tables scroll inside their own box, all one width;
  page fits the screen) -- owner's decision.
- **Phone: athlete sidebar (all-time and season bests) is off-screen** to the
  right of the main column (~x=1200), unreachable unless one scrolls sideways.
- **Owner's NCS 2024** (Nov 23, Hayward, 16:14.1, 123.7 vs 128-131 either
  side): the mud day, uncredited -- the race-day trial's test case.

## 2026-10-02 -- unknown athletes, track weather

- **Unknown athletes.** Race pages: 111 of 147 runners "Unknown" on 1993 NCAA
  D2 (meet 277780), 50 of 435 on a 2026 Texas invitational. Every such row
  has a person id; no name exists anywhere the site looks (athletes first/
  last on the person's rows, or results.athlete_name). Their athlete pages
  were titled "None – Adams State (CO)" and indexable: now "Unnamed athlete",
  noindex until a name arrives. `scripts/unknown_names.py` (read-only) counts
  them by sport, source, season and cause (no_person / blank_athlete -- the
  scraper's FK placeholder never filled / no_athlete) and lists the meets
  with the most, whole-meet vs partial. Fix by cause once it has run.
- **Weather may not be applied at all.** The weather artifacts in git
  (engine/data/weather_correction_{XC,TF}.pkl) carry the OLD apparent-
  temperature reference (55); normalize_distance refuses them ("NOT
  applied"). Unless the server refit them, neither sport is weather-corrected
  -- which is the under-credited rain races. `scripts/weather_card.py` prints
  what the artifact in force does per distance (heat, wind, rain, mud) and
  whether the corpus was normalised with it. Fix: the refit
  (XCP_WEATHER_FIT=1, step 04f) and the backfill after it -- the pending
  `--from 3` run.

## 2026-10-02 (later) -- weather read on the server; phone layout; stray rows

- **Weather IS loaded on the server** (refit artifacts; scripts/weather_card.py):
  XC heat 95F +5.3% @5k; TF heat 95F +0.6% @1500, +6.9% @10k, none below 59F;
  wind +0.35%/5 m/s; TF rain ~0. **Mud is small**: soil 0.5 -> +1.85% @5k, and
  the per-course sensitivity s_c is capped at 1. The card's "DIFFERENT
  artifact" line was a bug in the card (the applied record is
  {"artifact": ...}); fixed, it now says which, or that the ratings carry none.
- **Owner: no track race-day term** ("race day terms might downplay fields
  where comp is good so people run better ... a bigger one in TF").
- **Mud ceiling** (`XCP_MUD_SENS=eb` at the next weather refit): the course
  sensitivity's prior spread is measured (method of moments) instead of
  0.25, and the cap at 1 goes (the floor at 0 stays). Default unchanged;
  every refit now prints how many courses the cap cuts and to what.
- **Phone layout** (owner: "do as you need"): the page fits the screen and a
  wide table scrolls inside its own box (racecast/static/layout.js + the
  2026-10-02 block at the end of style.css). This REVERSES the 2026-09-06
  phone pass ("NO TABLE SCROLLS INSIDE ITSELF ... the PAGE scrolls
  sideways"). One-line revert: remove the layout.js include from
  _topbar.html. The athlete sidebar folds to the top as "Bests & PRs".
- **Stray rows** (`scripts/stray_rows.py`, read-only, owner: "be careful"):
  a row from a school AND state the athlete never otherwise runs for
  (Spencer's Tallmadge, OH row). A real move with one or two races at the
  new school is the false-positive shape -- read before any unlink.
- Desktop home is ~38px wider than a 1366 window (live before these
  changes); not chased yet.

## 2026-10-02 (evening) -- owner's notes after the review

- **Phone, second take** (owner: "start as zoomed out as possible and make
  every element the same width"): layout.js measures the page's natural
  width W (the 2026-09-06 phone pass: page as wide as its widest table) and
  sets CSS zoom = screen / W on the root. Every block is laid out W wide
  (mural, header, headings, tables), the whole width is on screen, pinch to
  read. CSS zoom, not the viewport tag: zooming the viewport widened the
  layout viewport past 700px in Chrome and dropped the phone stylesheet.
  The athlete sidebar stays on the right. The earlier "tables scroll in
  their own box" take is gone.
- **Athletes/Coaches switch**: one bordered switch on all 16 page types
  checked, both sizes (the stray-brace fix); the lit side is the edition.
- **Trey Caldwell (29406443)**: header read "Arkansas (AR) · 12 · CA D2 NCS
  D2 Tri-Valley EBAL". The class only followed the newest team season in
  the same pool, and the chips always read the latest XC year. Now the
  class follows the newest team season in any pool, spelled in that
  season's pool (grade_pool: FR-1); the chips come from the team season's
  rows (unitsForPerson year/pool); the school label/crest/link use the team
  season's level and state; "Recruiting: where you'd fit" shows for a
  high-school team only. Share cards: chips from the card's own season.
- **Unknown names, measured** (scripts/unknown_names.py on the server): XC
  316,498 rows nameless, 262,760 of them the 2026 season (anet placeholder
  athletes never named: the current XC scrape); TF 12,377,037 rows (6.4%) --
  12.17M anet rows with no person AND no named athlete, often whole meets
  (255955: 20,717 of 21,206). Cause not yet known; scripts/name_probe.py
  splits one meet's nameless rows (relay/field/event, scrape day, athlete_id,
  athletes row blank or missing).
- **Owner's 3200 (Arcadia 2025-04-12, 9:01.1, result 258164858)**: rated
  133.8 (136.6 on 2026-09-26). The "9:07" is the race page's equivalents
  card: it applies the venue (-1.1%, a fast track) and no weather; track
  race pages pass no weather. The weather grid reads 24C over 9am-8pm but
  the race ran ~8pm at ~18C, and the day term is -1.1% (fast). On the
  model's numbers 8:59 needs the venue ignored; the drop since 09-26 is
  more than weather (several changes landed in the 10-01 run) -- the SQL
  checks in the reply compare old and new rows.

**Mt. SAC / hilly CA / Foot Locker difficulty (read-only investigation, 2026-10-02).**
Live: Mt. San Antonio College 4715m +8.2%, 4828m +6.1%, 5000m +4.6%, 3218m
+8.3%; Foot Locker Nationals +6.7%, West +6.4%; Glendoveer (NXN) +10.1%;
Crystal Springs +5.8%, Toro Park +5.5%. (`/course/Mt SAC` is a different,
one-meet course; the real one is `/course/Mt. San Antonio College`.) The
race-day hover is positive on 21 of 25 of the owner's CA rows: the solve
keeps booking those days slower than the course number beside them.
Ranked causes: (1) the published number is the latest 2-year era, which
can be one race day leaning 2/3 on the course's mixed history (era prior =
2 races; small time trials weigh ~0.67 against the Invitational's ~1.0);
(2) CA is weakly tied to the rest of the country and the bracket solve
starts every course at 0, so a region moves only through runners who race
elsewhere ("keeps its sum where it started") -- check convergence (max 60
iterations); (3) a runner's level includes their other races on the SAME
course (Mt. SAC raced up to 3x in 30 days); (4) Foot Locker: champ keys
have no venue prior, latest era is two days, Brooks regionals are not
recognised as Foot Locker, and "Western/Southern" names fell out of the
regionals (fixed: champ_course "-ern"); (5) weather normals missing a
fortnight fall back to a flat baseline (minor). Ruled out: meet importance
(off), course scale (uniform), tilt.
- **Brooks = Foot Locker where the course is the same** (owner, 2026-10-02).
  The server's meet list: Brooks West at Mt. SAC (and Hilmer Lodge), Midwest
  at UW-Parkside, South at McAlpine, Northeast at Franklin Park, the final at
  Morley Field -- Foot Locker's venues. It also showed Foot Locker's own cells
  mixing courses: the West at Woodward Park 1993-97, the Northeast at Van
  Cortlandt and later Franklin Park, one "Nationals" at Shades of Green.
  champ_course now gates every cell by venue (VENUES): a race at another
  venue keeps its own venue's key, the Northeast splits into Van Cortlandt
  and Franklin Park, and Brooks XC/cross-country meets join (not "Brooks
  Pre-National Invitational"). Engine (speed_ratings_db._champKeySql) and
  site (app._champ_join) use the same gate. Takes effect at the next pack.
- **diag_course_island** on the Foot Locker cells: outside share 98-100%, own
  share ~100% -- they are NOT islands; the island mechanism does not explain
  Foot Locker. Mt. SAC and Glendoveer are keyed by canonical id, so the name
  search missed them (rerun with the ids from --list).

## 2026-10-02 -- ROOT CAUSE: the 10-01 run published without the model's settings

`deploy/run_pipeline.sh` never sourced `deploy/solve_env.sh`; only
`scripts/overnight_fit_pool_solve.sh` did. The run of 2026-10-01 was started
as `bash deploy/run_pipeline.sh --from 7 ...` (the recipe given in this
session) and ran with defaults: its 08_golive log has 0 `[bracket]` lines
(70+ in the 09-28 and 09-29 runs), i.e. the JOINT model's course numbers
were published instead of the bracket engine's, with the default era length,
no XCP_SPORT_LEVEL_POOLS, no gauge, the default importance, and so on. That is
the owner's list: Mt. SAC 5000 +9.6% (09-28) -> +4.6%, Foot Locker "not right
again", hilly CA courses low, track ratings down. The 09-28/29 runs also
showed the bracket solve stopping at its 60-pass cap (max change 0.0025, ~100k
cells still moving) -- a second, smaller question.
Fixed: run_pipeline.sh sources solve_env.sh after the env file and prints the
key settings at the top of every run; a command-line value still wins. Re-run
from 7 to republish with the production model (and pack the venue-gated
Foot Locker cells).

**Unknown names, part 2 (2026-10-02; owner: "I can promise you athletic net has the names").**
- **Track relays were most of the track count.** A relay row is a team: the
  saver stores it with no athlete id and no person on purpose, and
  unknown_names counted every one as "no_person". Relays are ~6% of a
  meet's rows, about the whole 6.4%. The race page also showed every squad
  as "Unknown" (get_tf_race_results never read is_relay). Now a relay row
  shows the team and its runners (meet_extras.relay_legs_json, names from
  the blob or `athletes`, linked); unknown_names leaves relays out and
  prints their count on its own line. Test: tests/test_relay_legs.py.
- **The rest is fixed by a re-scrape.** scripts/requeue_blank_athletes.py
  now finds every anet meet with a nameless non-relay row by the site's own
  test (no name in `athletes` by person or athlete id, none on the row), not
  only all-blank athletes rows; --apply marks them unscraped and adds the
  ones missing from meet_queue. The savers fill a blank athletes row or
  insert a missing one, and the results upsert fills a missing athlete_id /
  person_id (COALESCE: a dedup's person is kept). Dry run by default.
- **Server numbers after the relay split (2026-10-02):** TF relays 12,142,934
  rows (not unknown). Real nameless: XC 316,498 (anet blank_athlete
  286,712 -- 262,760 in 2026, the pre-09-25 XC saver; no_athlete 28,871);
  TF 2,839,160 (1.48%): anet no_person 2,631,608, no_athlete 180,126,
  blank_athlete 6,650. requeue dry run: 93,523 meets, 3.13M rows.
- **The track no_person rows: the name was thrown away at save time.** A
  result the feed does not tie to a registered athlete has no AthleteID,
  so no athletes row is written, and the result row never carried the
  name -- athletic.net's page shows it, we stored nothing. Both savers now
  keep the feed's name on the row (results.athlete_name /
  results_tf.athlete_name, fill-only on conflict; a relay keeps its runners
  "A, B, C, D"), which the race and athlete pages already read. A re-scrape
  of those meets therefore repairs them; before this change it could not.
  Test: tests/test_feed_name.py. requeue_blank_athletes.py gains --sport.
- Dates to look at some day: seasons 2221/2222 (525 rows) and single
  pre-1950 track meets carry impossible or doubtful dates.

## 2026-10-03 — race-day term in cross country; conversion check

- **Run 20261002_221401** (6h 46m, bracket settings applied): FAILED
  10a_board_sanity only (log requested).
- **The race-day term is ON for cross country** (owner: "let's add in
  race-day term for xc"; none for track, 2026-10-02). solve_env.sh sets
  XCP_RACE_KEY=venue, XCP_RACE_EFFECT_SPORTS=XC, XCP_RACE_EFFECT_OWN=
  leave-out (each runner's day is how the REST of the field ran; one day per
  venue and date). One run without it: `XCP_RACE_EFFECT_SPORTS= bash
  deploy/run_pipeline.sh ...`. The go-live prints "day-term consistency"
  (does crediting the day make an athlete's races agree: LOWER scatter).
- **The hover reads what was published.** app.RACE_DAY_SPORTS read its own
  env var, a second setting that had drifted from the solve once already;
  it now reads race_effect_sports from engine/data/pair_difficulty.npz (the
  go-live writes it), re-read when the file changes; XCP_RACE_DAY_SPORTS
  still overrides. tests/test_race_day_wording.py rewritten for it (it was
  failing: it forbade the pipeline from passing the switch at all).
- **XC -> track conversions** (owner: "a 25:30 8k at Keene State is not a
  9:28 3200m"; "maybe tf slightly overrated"). The card is right exactly
  when a dual-sport runner's XC and track ratings agree, so
  scripts/conversion_check.py measures that on the runners who did both:
  fall XC against the springs before and after (averaged, so a year's
  improvement cancels), per-athlete medians, split by pool, XC distance,
  track event and XC quartile. A uniform gap is the cross-sport level; one
  that moves with XC distance is the distance curve.
- **conversion_check on the server (2026-10-03), 2.53M pairs on 633,702
  athletes since 2010.** Bracketed gap (track - XC rating; - = the card's
  track time is too FAST): hs_m -1.81 pts (-1.60%), hs_f -0.94%, college_m
  -0.67%, college_f -0.61%. By event (college): 800 +2.0%, 1500 0, 3000
  -1.4%, 5000 -1.7%, 10000 -2.2%; hs_m 5000->3200 -2.05%, college_m
  8000->3000 -1.37%. By XC quartile the gap grew with ability (hs_m -0.2% to
  -2.1%), partly an artifact of sorting on XC (fixed: now the mean of both).
  The 800's + is largely WHO runs it (lower-rated XC runners), which the
  calibration's ability term absorbs.
- **The cards now carry the measured cross-sport leg**
  (racecast/conv_calibration.py; pipeline step 10a2 refits
  engine/data/conv_calibration.json from each run's boards). Per pool:
  ln(track/XC rating) = cell(XC distance, track event) + tilt(ability
  quartile), median polish, each cell shrunk to the pool level by its
  standard error against the cells' own spread. Applied in equivalenceLine
  and convert_spread, XC->track by exp(-gap), back by exp(+gap). Measured on
  the published ratings, so it reads zero if the ratings come to agree.
- **10a_board_sanity's 8 HARD findings.** (1) The 5 XC tilt bands at
  implied/applied 1.08-1.16 checked the ALL-course table; the scale is fitted
  on courses with 4+ races (courseScaleFromRaces, 2026-09-28) because thin
  courses carry the prior's shrinkage signature. run_joint now saves
  bracket_tilt_bands_known and the check reads it. (2) The sport level per
  level not held (hs read -0.0036 vs target -0.0092, ms -0.0061 vs -0.0191,
  elem -0.0127 vs -0.0186): real; the go-live's "winter gain per band"
  table says whether it is held in the solve's own sample (requested).
- **The card correction is OUT again (owner: "I said the 9:28 is too slow
  and you just said it's too fast. Isn't the issue with the gap not the card
  but the engine itself?").** Right on both. The bracket averaged the spring
  before and after, which assumes the summer and winter gains are equal; the
  data cannot separate the level from the two gains (before = level -
  summer, after = level + winter). If the winter/track season carries more
  of the year's gain, track SHOULD rate above XC and the card's track time
  is too slow, as the owner reads it. The engine already states that level
  (XCP_SPORT_LEVEL_POOLS, hs 0.92% track over XC) and the published boards
  do not hold it (hs reads 0.36%, ms 0.61% of 1.91%, elem 1.27% of 1.86%,
  college 0.17% of 0.37%): the engine is where the fix goes. conversion_check
  stays as a description of the two legs.
- **WHERE THE TRACK LEVEL LEAKED (the go-live's own table, 2026-10-02 run).**
  The shift hit each band's MEAN gap in one pass, applied interpolated by
  rating; the boards read the MEDIAN athlete. Pre-shift gaps were steep
  (hs_m +0.3% / +3.1% / +4.6% shift by band, hs_f up to +7.5%: the solve
  itself has hs track 2-7% under XC, more at the top). js.sportGainShift
  now iterates until each band's median gap, after the interpolated shift,
  reads -gain. Expected on the next run: hs/ms/elem/college track ratings
  up toward the stated level (hs about +0.5%, ms about +1.3%), and the
  10a level check passing. tests/test_sport_gain.py: median held as applied,
  and a skewed-gap case.
- **Phone layout, third take (owner had "zoomed out, same width"):** the CSS
  zoom rendered 3-4px text (zoom 0.25 on athlete pages) and is the likely
  cause of the overlapping text on iOS (Safari's text autosizing enlarges
  zoomed-small blocks unevenly). Now: one 12px gutter on every page, phone
  type a step smaller, tables in their own page-width box (wrap if that
  fits, else scroll inside with a fade), the athlete sidebar folds to "Bests
  & PRs". Checked at 390px in Chromium and WebKit on 21 page types: no page
  scroll, no overlapping or clipped text. Desktop unchanged.
- **Sign-in "hang":** the mail send ran inside the DB transaction with
  per-socket timeouts only (DNS unbounded, IPv6 fallback 10s per address,
  gunicorn kills at 60s). The token commits first; the send waits at most
  8s in a thread ("may take a minute" past that); the button shows "Sending
  your link..." and ignores a second tap. Account page: stray empty picker
  boxes ([hidden] lost to display:flex), 16px fields (no iOS zoom), photos
  shrunk on the phone before upload (nginx's 1MB default refuses phone
  photos), Sign out / Delete separated.

## 2026-10-04 — the cross-sport holdout

- **Owner: "we holdout certain things to test the actual solve, but we also
  don't holdout things like the conversions or what you think someone will
  run xc vs track".** The race/athlete/course splits leave every held-out
  athlete's other sport in the fit, so the cross-sport level, the track gain
  and the conversions were never scored on unseen races. New
  --holdout-kind sport: the TRACK season of 10% of dual-sport
  athlete-seasons held out whole and predicted from their cross country
  alone (sport-xc: the reverse). The log adds "cross-sport BIAS" by pool x
  distance and by rating quartile (+ = ran slower than predicted). Pipeline
  step 08a_sport_holdout (same settings as 08a). tests/test_sport_holdout.py
  (split, table, end to end: bias -0.0013 on an unbiased synthetic world).
- **Correction to my 10-03 reply:** raising the track level (the median fix)
  makes the card's XC->track times SLOWER, not faster -- the card converts
  at equal rating, and a higher track rating for the same time means a given
  XC rating maps to a slower track time. The owner's two asks pull against
  each other on one rating scale: "track rates above XC" puts the fall-to-
  spring improvement in the rating, "25:30 should convert faster than 9:28"
  puts it in the card. The cross-sport holdout says which the data supports
  (what runners actually ran next spring), and the card can then be defined
  as that prediction.
- **TODO (owner wants it, later): nginx client_max_body_size.** nginx's
  default refuses uploads over 1 MB (a phone photo is 3-12 MB); the account
  page now shrinks photos on the phone first, so this is the backstop. In the
  site's server { } block: `client_max_body_size 10m;` then
  `nginx -t && systemctl reload nginx`.
- **LACCTiC comparison (owner: "lacctic just seems more authoritative").**
  LACCTiC (college only) states every result as a track-5K equivalent
  (modern_tic = ln s) through a public API (api.lacctic.com/api_ranking/
  race_page/<id>/, runner_page/<id>/). The owner's "25:30 8k at Keene State"
  is their own race (KSC Invitational 2026-10-03, 25:35.4, 15th): LACCTiC
  course +0.36%, 15:23.0 track-5K, i.e. about 9:32-9:35 for 3200 -- our
  card's 9:28 is, if anything, the faster of the two. scripts/
  lacctic_compare.py lays the two systems side by side on the same races
  (matched by TFRRS meet id, name and time): level, within-race agreement,
  per-runner consistency over the races both have (the fair test without
  pre-race ratings), and track-minus-XC for the same runner-seasons.
- **Every year of a venue showed one difficulty (owner's page, 2026-10-04):**
  Newhall +7.6% on 2021-2024, Mt. SAC 4715m +11.1% on 2021-2023, Hayward
  +4.8% every year. course_difficulties publishes each venue's LATEST era
  under the bare key; each row's rating used its own era's cell. The go-live
  now writes the race's own era's course number into race_day_effect
  (course_effect), and the athlete page and race-page headers show it
  (falling back to the venue number before the next go-live; championship
  cells keep theirs). Takes effect on the next run's 08_golive.
- Glendoveer (XC:22331:d5000, NXN only): course_bracket's model-free reading
  on the board scale averages about +6.4% vs published +5.5..+6.7%; 2023-25
  read +7.3/+7.3/+8.1 vs +6.65/+6.65/+5.99. Not overinflated; if anything
  slightly low lately. No mixed-pool fields at that cell.
- LACCTiC vs ours on 3 D3 races (839 runners): within-race agreement 0.4-0.9%;
  level ours 2.3-3.1% faster as track-5K; gap grows down the field (Q1 -1.4
  .. Q4 -3.4%); track minus XC over 52 runner-seasons ours +0.26% vs theirs
  -1.38%. Consistency 1.35% vs 0.97% is not a fair test: LACCTiC fits each
  race's difficulty on that race (per-race, unshrunk), ours shrinks the day.
- **The day terms were never refitted after the bracket swap (owner: "race
  day tilt being the same for every race and not being put into course
  difficulty").** bracketDifficulties replaced the joint's course numbers
  and refitted the abilities, but kept the joint's u -- fitted against
  courses nobody publishes -- so where the engines disagree about a course
  the gap went into no term. u is now re-taken against the published
  courses (the solve's own shrinkage, alternating with the abilities),
  leave-self-out in the same frame, and the go-live prints "courses whose
  days lean one way" (|mean u / se| >= 3, 5+ races): the courses the
  runners say are mis-set. tests/test_day_lean.py; test_bracket_golive
  checks abilities against the refitted u.
- **Track weather looks like the culprit behind 13:19 vs 13:55** (Hammerand,
  WashU 10k 2026-03-26, 29:20.5 = 126.3 vs NCAA D3 10k 29:27.0 = 117.6, both
  tracks 0.0%, track day term off). The TF heat term uses the day's PEAK
  apparent temperature; big-meet distance races are often at night (the
  owner's Arcadia 3200: grid 24C, race ~18C). Pending diag_track_conversion.
- Mt. SAC: owner reads 4828 (+6.2%) as only 1-2% easier than 4715 (+11.1%),
  so 4715 is likely overrated. Canyon MS 3379m (+16.7%): owner says it is
  that hard.
- **Mt. SAC 4828 "way too easy": deep fields read a course as easy.**
  course_bracket: the giant Invitational races (depth ~103) read the 4828 at
  +6.4..+7.3% on the board scale, the elite sweepstakes races (depth
  110-118) at -1.9..-0.1%; the 4715 shows the same split (elite 1.5-2.5%
  easier). A race weighs n/(n+5), so two elite races pulled the 4828 to
  +2.9% against its runners' ~+6.5% (owner: 1-2% easier than the 4715).
  bracket_engine.fieldAdjust now measures the reading's slope on field
  depth per sport within courses and reads every race at its course's
  typical (voter-weighted) field; printed as "[bracket] field depth" each
  run; XCP_BRACKET_FIELD=off restores it. tests/test_field_adjust.py.
- lacctic_compare gains the fair tests: ORDER (each runner's ability from
  their OTHER races that season, scored on the finishing order -- the race's
  own course and day cancel) and XC -> TRACK 5000 (each system's XC number
  against the actual 5000 times).
- **LACCTiC fair tests (current ratings, before the field/day fixes):**
  ORDER ours 82.0% vs theirs 84.5% (9,432 pairs, 3 races); XC -> actual 5000
  spread ours 2.52% vs theirs 1.95% (bias -0.94 vs +1.07, 125 runner-seasons);
  track minus XC ours +1.60% vs theirs -1.08%. LACCTiC is measurably better
  on both fair tests; these two numbers are the target.
- **13:19 vs 13:55 is in the normalisation:** WashU 29:20.48 normalised to
  1304.17, NCAA 29:27.04 to 1401.07 -- 7% for 0.4% of time, before the
  engine. diag_track_conversion could not split it (no meet distance); it
  now parses the event name. AND THAT PARSER WAS WRONG: parseEventShort read
  "10-km" and "Men's 10,000 Meters" as 10 metres (race-page headers, the era
  band, the distance-exponent fit). Fixed; tests/test_event_parse_km.py.
- **scripts/run_report.py (pipeline 17c), the one scorecard per run:** fair
  tests, health, and the owner's sentinel cases, against the last run
  (engine/data/run_history.jsonl, <log dir>/REPORT.txt). 16b_lacctic feeds it.
- **13:19 vs 13:55 FOUND: the track heat credit uses the day's peak.**
  diag_row_weather: WashU 2026-03-26 grid apparent max 35C over 9am-8pm (33C
  at 3pm; 22-23C by 9-10pm, when the 10k ran) -> multiplier 0.9343, +7.03%,
  about 9.1 points at 130. NCAA 2026-05-23 had no grid rows -> no-op. The
  scorecard sentinel reads the gap at 8.74 points. TF's apparent_temp was
  switched from the window mean to the max on 2026-09-06 (a 3pm race read
  cool); neither is right for every race without start times. The TF weather
  fit now queries both on the same rows and keeps the one that explains more
  of the same meet's year-to-year slowdown ("[temp]" lines);
  XCP_TF_TEMP_AGG=max|avg forces. Needs XCP_WEATHER_FIT=1 and a run from 4
  (the backfill re-applies the correction).
- Baseline scorecard (20261004_010841): owner 3200 139.02; Hammerand 10k gap
  8.74; Mt. SAC 4715 11.11%, 4828 6.17% (gap 4.93; owner 1-2); Glendoveer
  9.54% (runners read ~6.4 on the board scale); day scatter 2.835%; sanity 1.

## 2026-10-04 — ⏳ the nightly light update (deploy/nightly_update.sh)

Owner approved: "nightly light update" (the newest meet on the site was Sep
17; data moved only when the full pipeline ran by hand).

- **What runs nightly** (01:17 Pacific, `sudo bash deploy/install_nightly_timer.sh`):
  scrape anet + tfrrs at once (NO_VPN, xvfb; at most XCP_NIGHTLY_SCRAPE_MIN =
  180 min, a quiet "NOTHING IS DUE" night is not a failure) -> the pipeline's
  own link/flag steps (00 dates, 01a snapshot, 02b venues, 03b age bands,
  04a/04a2 links, 04b chairs, 04c twins, 06 tfrrs names, 06b canonical
  --incremental) -> `backfill_normalize --new-only` -> `fill_ratings --venue`
  -> boards (10 prepare / 4 streams / finish), 10g rank lines, 11 teams,
  13c0 redirects, 13c search. Same lock as the pipeline: a full run that
  night means the update skips (exit 0). Failures mail the admins.
- **`backfill_normalize --new-only`:** rows with no normalized_time, dated
  from the start of last season; update mode; never records a full write.
- **`fill_ratings --venue`:** the old fill priced every row at the pool's
  MEDIAN course (K / nt) -- fine for rows the solve rejected, wrong for a new
  ordinary race (a September meet at a hard course reads points slow). With
  --venue a row is priced like the site's conversions price a time:
  100 pm / adjusted at the course's published (latest-era) difficulty, tilt,
  event offset and winter gain, as a fixed point in the rating; no fitted
  course -> the median race, as before. Only the nightly passes --venue.
- **What a nightly number lacks:** no race-day term (a new race reads as a
  typical day), no weather until the grid reaches it, last full run's course
  numbers and levels. The next full pipeline re-rates everything.
- **Measure it:** `engine/fill_ratings.py --check 3000` re-prices solved rows
  of the newest season with the nightly formula; the median gap should sit
  near 0 and the spread is roughly the day term. Not yet run on the box.
- Not in the nightly: grade_sanity, gender verdicts (04d), the course and
  meet pages, panels. A brand-new athlete's grade/level verdict waits for the
  full run (the row is pooled on its own grade until then).

## 2026-10-04 — ✅ site batch: sign-in, layout, speed, explainer, state meets, breakouts

Merged from three agents (80a638b); restart the site to take effect.

- **Sign-in:** email links never worked on the live site -- no XCP_MAIL_PROVIDER
  / XCP_MAIL_KEY, so every link went only to the server log. /login now
  shows Google only until mail is set up. The 13+ box no longer blocks the
  Google button: it is asked on a "One last step" page before any account
  exists (accounts already 13+ are never asked).
  **To turn email on:** verify racecast.co in Resend (DNS records in
  Cloudflare), then in /etc/xc-predictor.env: XCP_MAIL_PROVIDER=resend,
  XCP_MAIL_KEY=re_..., XCP_MAIL_FROM="Racecast <login@racecast.co>"; restart;
  `racecast/accounts.py --check` and `--send-test you@...`.
- **Layout:** desktop sideways scroll (hidden tooltips), phone filters,
  phone rankings Rating column off-screen, topbar "More" menu (fits
  1201-1600 px on one line), phone freeze on 900-row races (layout.js 7.8 s
  -> 4.4 s busy at 4x CPU slowdown).
- **Speed:** search answers cached 10 min per worker; /meets with a state
  reads the newest 800 meets instead of an arbitrary 3,200. Re-time
  `/meets?sport=TF&state=CA&zz=1` after deploy (was 9.9 s).
- **New:** "How was this rated?" (click any rating; /api/explain/<sport>/<id>),
  course "Year by year", /projections (who wins state), /breakouts.
  Check after deploy: `curl -s http://127.0.0.1:8000/api/explain/tf/277459367`
  -- the "other" step should be ~1% or less.
- **Open (data):** top HS Boys boards show "Unknown" names (ranks 2, 4, 8 on
  the ability board) -- the requeue_blank_athletes --apply + scrape is the
  fix; the nightly scrape now drains that queue.

## 2026-10-05 — 📌 PICK UP HERE: everything open, in one place

Run 20261004_125010 (`XCP_WEATHER_FIT=1 ... --from 4`) finished in **17h 44m**
with **04f_weather_fit_tf FAILED**. Owner: "I haven't done any of 1-6 bcs
this just finished. Please log them all so we can come back to it."

### A. The 04f failure — ✅ fixed, ⏳ needs a re-run
KeyError 'apparent_temp_max': my 2026-10-04 max-vs-avg comparison (61af379)
added two load-only columns to QUERIED_FEATURES and never took them out;
fitWeather's _featureCounts looks every queried feature up in REFERENCE.
The TF fit died before printing a `[temp]` line, so **the run published
with the previous TF weather correction** -- Hammerand's WashU heat credit
is unchanged. Fixed (restore the list right after the load);
tests/test_weather_tf_compare.py runs main() end to end on synthetic rows
and reproduces the KeyError on the old code.
Next, cheap first (no write, minutes):

    /srv/venv/bin/python engine/fit_weather_correction.py --sport TF --refresh --measure-only 2>&1 | grep -E "\[temp\]|\[load\]"

**RESULT (2026-10-05, from the cache):** weather explains 1.163% of the
within-meet variance with the daily max, 1.167% with the 9am-8pm mean
(rss 19414.49 vs 19413.69 over 20,340,911 rows) -- a tie in aggregate; the
mean is chosen. On a tie the mean is the safer reading: it removes the
worst misfire (an evening race on a hot day, WashU +7.03%) at the cost of
some credit for a genuinely hot afternoon race. Note: the 04f corpus query
took 8,414 s (2.3 h, ~2,400 rows/s) of the run's 17h44m -- a speed item;
never pass --refresh unless the weather grid or the rows changed.
Then, the re-run that applies it (from 4 again, or from 4f
if the step plan allows). **Ask first: why 17h44m?** Paste
`logs/20261004_125010/SUMMARY.txt` (per-step times) before another run.

### B. Server commands not yet run (after `git pull` + restart)
1. **St. Mary's Invitational, anet XC meet 273503** (athletic.net/CrossCountry/meet/273503):
   `scripts/find_meet.py 273503`; the queue rows 273495-273511
   (`SELECT meet_id, sport, scraped FROM meet_queue WHERE source='anet'
   AND meet_id BETWEEN 273495 AND 273511`) and `max(meet_id)` of anet
   results -- never seeded, scraped too early (done, 0 results), failed,
   or "no meet". No `--requeue` until the reason is read.
2. **Explain check:** `curl -s http://127.0.0.1:8000/api/explain/tf/277459367`
   -- the "other" step should be <= ~1%.
3. **Nightly:** `engine/fill_ratings.py --check 3000` (median gap ~0), one
   hand run (`tmux new -s nightly 'bash deploy/nightly_update.sh'`, send
   logs/nightly_*/SUMMARY.txt), then `sudo bash deploy/install_nightly_timer.sh`.
4. **Re-time** `/meets?sport=TF&state=CA&zz=1` (was 9.9 s); look at
   /projections, /breakouts, a course page's "Year by year".
5. **Names:** `scripts/requeue_blank_athletes.py --apply` (ranks 2, 4, 8 on
   the HS Boys ability board read "Unknown"); the nightly scrape drains it.
6. **Run report** for 20261004_125010: `scripts/run_report.py` (17c ran; paste
   the table) -- the first run with field depth + day refit + race-day XC.

### C. Owner setup, whenever
- Admin emails: `XCP_ADMIN_EMAILS=a@gmail.com,b@gmail.com`, restart,
  `racecast/accounts.py --check`.
- Mail (email sign-in, failure notices -- "[notify] no mail provider" on
  this run): verify racecast.co in Resend (DNS in Cloudflare);
  XCP_MAIL_PROVIDER=resend, XCP_MAIL_KEY, XCP_MAIL_FROM="Racecast
  <login@racecast.co>"; restart; `accounts.py --send-test you@...`.
- nginx `client_max_body_size 10m` (wanted, later).

### D. Engine and data, open
- LACCTiC beats us on both fair tests (order 84.5 vs 82.0; XC -> 5000 spread
  1.95 vs 2.52). The scorecard tracks them every run.
- Mt. SAC 4715 vs 4828 gap 4.93% (owner: 1-2%) -- re-read after this run.
- Hammerand 10k 13:19 vs 13:55 (8.7 points) -- the TF weather peak; see A.
- Bracket course solve not converged at 60 iterations.
- Woodward Park split across 3 canonical ids; 525 rows dated 2221/2222.
- Early season: XCP_SEASON_TIE (season leans on the last + measured
  improvement) is off; turn on if the ladder's season-tie rung wins. Plus a
  "1 race" marker on boards and a projected early-season view.
- Athlete page "Cross Country | Track & Field" looks like a toggle but jumps.

### E. Features — approved, not started
- Follow athletes/teams + weekly email (needs mail, C).
- Accuracy section on About (from engine/data/run_history.jsonl).

### F. Feature ideas — not yet approved (recommended order)
1. "What it takes": per state/division the rating (and time at your course
   and the state course) that made state / finals / podium; "2.1 points
   off last year's cut" on athlete pages.
2. Shareable athlete cards: link-preview image + downloadable card.
3. Head-to-head: record in shared races, side-by-side compare.
4. Projected trajectory: what past runners at this grade and rating ran later.
5. School and course records: all-time top 10 by event.
6. Training paces from the athlete's own races (athlete_paces / critical speed).
7. Meet calendar: queued future meets with course difficulty, last year's times.
8. Next year's team: seniors removed, returners projected.
Deferred: upcoming-meet previews (no entry lists).

## 2026-10-05 — pipeline speed (e6b82dc) and rain

From run 20261004_125010's summary.log (17h44m): 08b_ladder 18,193 s, 08_golive
9,408 s, 04f 8,767 s (8,414 s of it the corpus query), 07_pack 3,502 s,
08a_holdout + 08a_sport_holdout 5,686 s, 10_rankings 4,550 s, 04c_twins 2,379 s.

- ✅ **04f corpus query staged** into analysed temp tables (same rows, both
  sports, checked against the old query). ⏳ Re-time on the box -- the next
  fit is a cache miss (new query text).
- ✅ **08b_ladder runs only when the model changed** (hash of engine/*.py,
  solve_env.sh, the ladder and the model's XCP_ settings; engine/data/ladder_stamp).
  XCP_LADDER=1 forces, 0 skips.
- ✅ **Every step log line is time-stamped**, so the next run shows where
  08_golive's 2.6 h, 07_pack's hour and 04c's 40 minutes go. Profile those
  from the next run's logs.
- 💤 08a_holdout and 08a_sport_holdout could run side by side (memory?).

**TF temperature: per-race choice, asked and declined.** Picking, per race,
whichever temperature reading explains that race best uses the race's own
slowness to choose its correction: any slow race (a weak field, a wrong
distance, a headwind) would take the hotter reading and be credited for heat
it never ran in. Only a start time can say which reading a race deserves,
and the feeds have none. The 9am-8pm mean stays (a tie in aggregate, the
smaller worst case).

**Rain, "not doing enough" (owner).** New `scripts/diag_rain.py`
(read-only): the published race-day term is what is LEFT after the weather
correction; binned by window rain, rain in the 24 h before the window, and
soil moisture, a leftover that rises with rain is the part the correction
misses, and its slope is the size of the fix. Track has no race-day term in
its ratings (owner's rule), so track rain rests on the correction alone; the
diagnostic still reads the solve's track day terms if published. ⏳ Run it.

## 2026-10-05 — ✅⏳ race-day hover missing on some athlete-page difficulties

Owner: "why do some race difficulties on athlete pages not have the dropdown".
The hover appears only when race_day_effect has the race. Two causes:
1. **Venue race key (default since 2026-10-04):** a race is a venue on a day
   across every distance; joint_golive kept ONE cell per race, so only one
   distance of each venue-day got a row (the boys' 5000 had the hover, the
   girls' 4000 the same day none, and its era course number fell back to the
   latest era). Now one row per (race, distance cell) -- joint_golive.racePairs,
   tests/test_race_day_rows.py. ⏳ Takes effect with the next 08_golive.
2. **Championship courses** (Foot Locker etc.) are written under 'XC:<name>'
   with no canonical id; the athlete and race pages joined by id only. Both
   now join a championship's days by its own name. ✅ on restart.
Still no hover, correctly: races not in the solve (a corrected-distance
division, a race with no rated rows) and courses with no fitted cell.

## 2026-10-05 — ✅ predictions: team prediction of an upcoming meet errored

errors.log: `/api/predict/team failed ... _parseDate ... strptime() argument 1
must be str, not None`. The target date came only from the meet's earliest
RESULT (`min(r.date)`), so a meet not yet run -- the one you predict -- had
none. Now: earliest result, else the meet's own scheduled date
(meets.meet_date / meets_tf_meta.meet_date), else the page's date, else
today. tests/test_predict_upcoming_date.py. Restart to take effect.

Also seen in the same journal, not yet chased:
- gunicorn `Request Line is too large (7962 > 4094)` from 127.0.0.1 at
  15:27:16 -- some page built a ~8 KB GET; the predictions page posts past
  1,800 chars, so it is another one. Find it by the access log at that time.
- `athlete: equiv time failed (TypeError: float() ... NoneType)` on athlete
  pages, repeatedly -- the equivalents line gets a None somewhere.
- `db blip on GET /athlete/...: InterfaceError: connection already closed`
  (retried) -- pooled connections dropped, likely while the pipeline ran.
- ✅ `equiv time failed (TypeError ... NoneType)`: an athlete whose every
  season is unrated (a sprinter or thrower -- both logged pages were track
  athletes) still gets a header season, and its rating is None; the "5K
  equivalent" line converted None. Now left out without a log line.
- ✅ `Request Line is too large (7962 > 4094)`: gunicorn's default 4 KB
  limit sat under nginx's 8 KB buffer. deploy/server_setup.sh now starts
  gunicorn with --limit-request-line 8190; apply on the box with
  `sed -i 's/--timeout 60 \\/--timeout 60 --limit-request-line 8190 \\/'
  /etc/systemd/system/xc-predictor.service && systemctl daemon-reload &&
  systemctl restart xc-predictor`. Which page builds an 8 KB GET is still
  unknown (nginx access log at 15:27:16).
- 🔎 `db blip ... connection already closed`: the pool probes every
  connection on the way out (SELECT 1), and the retry succeeded, so the
  backend died MID-request and the first error was swallowed somewhere.
  The Postgres log says what ended it (terminate, OOM, restart).

## 2026-10-05 — ✅⏳ a shared school name lent one level's identity to another

Owner: an unnamed grade-5 runner (athlete 33083078, Amherst WI, elementary
pool) read "Amherst (WI) · 5 · NCAA DIII · Mideast · NESCAC", his season
"Amherst (MA)", and he joined Amherst College's roster on the predictions
page. Three lookups, each keyed on the school NAME without the level:
1. **Result-row units (build_ranking_results._unitsOf):** the college
   directory's campus state was tried for EVERY row, so any "Amherst"
   became (Amherst, MA) = the college. Now by level: a college row takes
   its campus state, then the race state, then the best-attested college
   of the name; a school row takes the school in its state, and the bare
   name only when one state holds a school of that name.
2. **Season-row units (_stampSeasonUnits):** the campus pass could take a
   high school's row (Amherst MA is both), and the last pass took the
   best-attested row of the name at any level. Every pass now matches the
   level; school seasons fall back by name only when the name is unique.
3. **Season label / team key (school_identity.teamState):** Wisconsin
   holds under 3% of the name's athletes, so the label fell to the
   primary (MA). For a school-level pool the season's own state stands
   when the name has any cluster there; the 3% bar stays for colleges.
tests/test_school_level_units.py. 1+2 take effect at the next
10_rankings build; 3 on restart. The predictions roster half is with the
predictions speed work (squad selection by school identity and level).

## 2026-10-05 — ✅ predictions and squads faster; squads by school identity

Owner: "speed up the predictions and the loading of the squads". Merged
1f0f257 (c1cbd80, 49d1110):
- **The win/score simulation** (sim=1, always asked) scored 2,000 draws one
  at a time: 1.5 s on 420 runners, 2.9 s on 660 (live meet 277556: 2.2-6.4 s).
  Vectorised over draws: 0.26-0.35 s and 0.74 s; identical numbers
  (old loop kept in tests/test_race_sim.py, compared on 40 random fields).
- **Predict sent races one after another** -> in parallel, four at a time.
- **"Squads: Everyone" was one request per team per race** (30-80+ behind 8
  workers) -> /api/predict/squads, up to 120 schools a call, chunks of 40,
  four at once; in-flight requests shared; squads cached 5 min per worker.
- **Cold start** (4.4 s first prediction per worker) -> each worker warms
  the model in a background thread at boot (XCP_PREWARM=0 turns it off).
- **Amherst on the NESCAC roster:** the squad request took the level from
  whichever race had focus and from "Everyone" sent none, so no filter.
  Every squad request now carries its race's gender and level, the card's
  state and the meet; with no level the server uses the meet's; a name
  shared by several schools narrows to the requested state's school
  (college runners are never dropped by state -- their home state is their
  high school's). tests/test_squad_identity.py.
Check after restart: `journalctl -u xc-predictor | grep "\[predict\] warm"`;
re-time /api/predict/team?meet_id=277556&sport=XC&mode=meet;
`scripts/diag_predict_time.py --meet 277556 --sport XC`.

## 2026-10-05 — ✅ predictions: freshmen at 20:25 for a college 8K; HS scale; model only where needed

Owner's D3 prediction: fifteen freshmen at the top (20:25-21:55 for an 8K,
bands of +-10%), "lol". They had no rated race before the target date (a
new college profile; a meet re-run before their first race), so they kept
the MODEL's own time, and the model's college 8K times run ~20% fast
(Tucker Presnell: model 19:58, rating 25:15) -- the rating guard had always
hidden it. Now a runner with no rating keeps the model's time only when its
band is within _GUARD_MAX_SIGMA (8%, the guard's own "not a prediction"
bar); otherwise listed, unscored, with the reason.
🔎 OPEN: why the model's college 8K times are ~20% fast (its output scale vs
the race distance for college pools) -- serve-side it no longer shows.

"Those speed ratings should be HS equivalent": the scale toggle defaults
to HS-equivalent, but the predicted results, the dual and the best seven
were injected without the repaint, so they always showed own-pool numbers.
They repaint now.

"Still not fast enough": with the simulation vectorised a 195-runner meet
still took 2.3-2.9 s with sim=0. Under the default basis (rating) every
rated runner is served their rating's time, and the model ran for the whole
field to fill a hover. It now runs only for runners with no rating (basis
"guard" still runs it for all). Re-time /api/predict/team?meet_id=277556.

## 2026-10-05 — ✅ predictions: "as it ran" freshmen; stale grades; what is predicting

- **As it ran included this year's freshmen.** Squads: Everyone added every
  CURRENT runner: _currentSquads turns a past season into this one (grades
  aged, graduates out, returners carried, and this academic year's race
  entrants added from results). A new as_ran mode reads the meet's own
  season (predict.meetSeason) and that season's racers only; the page sends
  when=asran with every squad request.
- **Grades not aged ("predicting a race this year from last year keeps the
  same grades").** The page sends its field, and _athleteEntries filled the
  grades from _currentSeason -- the boards' season, which between seasons is
  the finished one (track reads 2025 all autumn). Now aged to the academic
  year, the clock _currentSquads uses. As it ran keeps the as-raced grade
  (_exactField overrides).
- **Is the model predicting?** Barely. The served basis has been "rating"
  since 2026-09-25: each runner's recent ratings (recency-weighted, a fall
  dropped) turned into a time at the target course and distance, band from
  their own race-to-race spread. The model serves only runners with no
  rating, and since today only when its band is within 8%. Its own college
  8K times run ~20% fast (OPEN). XCP_PREDICT_BASIS=guard serves the model
  where it agrees with the rating within 8%; =model is the raw network.
tests/test_predict_asran_and_grades.py.

## 2026-10-05 — 🔎 rain is credited; the published day terms are NOT centred

`scripts/diag_rain.py` (2026-10-05, 158,896 XC and 290,934 TF race days):
- **Rain is not under-credited on average.** The leftover day term (what
  the weather correction leaves) is flat across window rain (XC +0.028%/mm;
  8 mm+ days +0.35% vs dry), rain the night before (+0.018%/mm) and soil
  (+0.017%/0.1). TF flat. "Rain not doing enough" needs a named race.
- **But every bin sits at the same offset: XC median -2.87% (weighted
  -3.2%), TF +1.0% (+1.5%).** A constant across all days is not a day: it is
  a level gap between the bracket's course numbers and the solve, parked in
  u. XC ratings carry u (XCP_RACE_EFFECT_SPORTS=XC), TF's do not, so the
  constant moves XC against track inside each pool -- the direction of the
  owner's conversion complaints and of the cross-sport holdout (track minus
  XC +1.60%, LACCTiC -1.08%).
  ⏳ FIRST: did the 2026-10-04 refit (691cd9a) create it? The 08_golive log's
  "[joint] bracket: day terms refitted ... median move" line, and the mean
  of race_effect_joint vs race_effect. If so, centre u per sport (runner-
  weighted) after the refit and let the scorecard's cross-sport and LACCTiC
  lines judge it.
- **ANSWERED (2026-10-05, owner's server output):** published means XC
  -2.74% (runner-weighted -3.24%), TF +1.11% (+1.40%); the 2026-10-04 refit
  moved the terms a median 1.30% (p95 4.71%), so it widened a trend that
  was already there. Newhall's "Year by year" shows what it is: the field
  "ran" +0.6% (2002), -0.1% (2010), -1.4% (2018), -3.4% (2023), -4.8% (2025)
  beside a course number of +2.9..+4.3% -- the ERA (a faster population,
  the shoes), read as fast days. XC ratings carry u, so recent XC ratings
  lost ~3-5% against track. Also why 19,435 courses "lean one way".
  ✅ FIX (engine/run_joint.centreDaysBySeason): after the refit the day
  terms are centred per (sport, season), runner-weighted, and the shift
  moves into those athletes' abilities -- the fitted values are unchanged,
  only the split between athlete and day. XCP_DAY_CENTRE=0 turns it off.
  ⏳ Takes effect at the next 08_golive. Expect: recent XC ratings up a few
  points against track; the scorecard's cross-sport bias and the LACCTiC
  XC -> 5000 bias/spread to move; "courses whose days lean" to collapse;
  the race-day dropdowns to read near 0 on ordinary days.
- **Harkness Memorial State Park:** race_day_effect held only the 6000 (the
  women's race) for 2025-10-18 and 2025-11-01 -- the one-cell-per-venue-day
  bug fixed today (racePairs); the men's 8000 rows arrive with the next
  go-live. Also TWO canonical ids for one park (3851 and 16451), like
  Woodward Park's three: a course_canonical merge to do.
- Model: owner, "leave in stasis" until the engine is fixed.

## 2026-10-05 (evening) — 📌 PICK UP HERE, UPDATED (supersedes the list above)

### 1. Next on the box, in order
1. `git pull && sudo systemctl restart xc-predictor` (site fixes below).
2. gunicorn 8 KB request lines: `sed -i 's/--timeout 60/--timeout 60
   --limit-request-line 8190/' /etc/systemd/system/xc-predictor.service &&
   systemctl daemon-reload && systemctl restart xc-predictor`.
3. **The run:** `XCP_LADDER=0 tmux new -s run 'bash deploy/run_pipeline.sh --from 5'`
   -- carries the TF weather (9am-8pm mean, fitted and saved), the day-term
   centring, one race-day row per distance, and units by level. No `git
   pull` while it runs. After: REPORT.txt, the `day terms centred` line of
   08_golive.log, and the time-stamped 08_golive/07_pack/04c logs.
4. After the run, check: race-day dropdowns (UCSB Gaucho 2024-08-31 on
   /athlete/29603086, Harkness 8000s on /athlete/29603084), Mt. SAC gap,
   Hammerand gap, owner's 3200 (139.02 -> 136.20 last run: why?).

### 2. Waiting on the box (not run yet)
- St. Mary's (anet XC meet 273503): `scripts/find_meet.py 273503` + the
  meet_queue rows 273495-273511; no `--requeue` until the reason is read.
- Explain check: `curl -s http://127.0.0.1:8000/api/explain/tf/277459367`
  ("other" <= ~1%).
- Nightly: `engine/fill_ratings.py --check 3000`, one hand run, then
  `sudo bash deploy/install_nightly_timer.sh`.
- Names: `scripts/requeue_blank_athletes.py --apply`.
- Dropped DB connections: `grep -iE "terminat|FATAL|killed|out of memory"
  /var/log/postgresql/postgresql-16-main.log | tail -20`.
- Re-time `/meets?sport=TF&state=CA&zz=1` (was 9.9 s).

### 3. Engine and data, open
- ⏳ Day terms carried the era (XC -3.24%, TF +1.40%) -> centred per (sport,
  season); judge by the next REPORT (cross-sport bias, LACCTiC XC -> 5000).
- LACCTiC, last run: order 84.1 vs 84.5; XC -> 5000 spread 2.02 vs 1.95
  (was 82.0 / 2.52) -- nearly level.
- Mt. SAC 4715 - 4828 = 5.00% (owner: 1-2%).
- Hammerand 10k gap 8.51 points -- the TF weather change lands this run.
- Duplicate course ids: Woodward Park (3), Harkness (3851, 16451).
- 525 rows dated 2221/2222.
- Early season: XCP_SEASON_TIE off; "1 race" marker; projected view.
- Model: college 8K times ~20% fast. **In stasis** until the engine is
  fixed (owner); the page serves ratings, the model only the unrated.
- Pipeline speed still to do: 08_golive 2.6 h, 07_pack 1 h, 04c 40 min
  (profile from the stamped logs); 08a holdouts side by side.
- Site: which page builds an ~8 KB GET; athlete-page "XC | TF" looks like
  a toggle.

### 4. Owner setup, whenever
- Admin emails (XCP_ADMIN_EMAILS); mail via Resend (XCP_MAIL_PROVIDER /
  KEY / FROM) -- also turns on failure emails and email sign-in; nginx
  client_max_body_size 10m.

### 5. Features
- Approved, not started: follow athletes/teams + weekly email (needs mail);
  accuracy section on About.
- Ideas, not approved: what it takes (state cuts), shareable cards,
  head-to-head, projected trajectory, records, training paces, meet
  calendar, next year's team. Deferred: meet previews (no entries).

### Done today (2026-10-05), for the record
TF weather fit crash + max-vs-avg (avg chosen); 04f query staged; ladder
only on model change; stamped logs; diag_rain (rain is credited);
predictions: upcoming-meet date, 10x faster (sim vectorised, squads
batched, model only for the unrated), freshmen not placed on unsure model
times, HS-scale results, "as it ran" squads, grades aged; squads and units
by school identity and level (Amherst); race-day rows per distance and
championship joins; equivalent-time error; gunicorn request line.

## 2026-10-05 — ✅ predictions: "as it ran" and "this year" showed one roster

Owner (a Tufts card of 37 in "run it this year", most of them graduates):
"As it ran vs run it this year doesn't actually change roster, so one being
wrong can mess up the other." Flipping WHEN called loadField, which only
fetches races with NO field -- every race had the other mode's -- so the
field, the hand edits and the Everyone additions (an old season's whole
roster under "as it ran") carried straight over. The flip now clears the
edits and re-reads every race for the mode chosen. A page restored from
before the fix keeps its saved cards until the mode is flipped once or the
meet is re-picked.
Also 5b14bc6: a whole roster added to a men's race brought the women's
team (no gender on an all-races/mixed field); falls back to the race's own.

## 2026-10-05 — ✅ predictions read a meet by id alone (anet/tfrrs ids collide)

Owner: "NCAA Division III Cross Country Championships 2025 ... ran
2009-10-13 · Warinanco Park", races "Varsity Boys 5000m (22)" and "Varsity
Girls 5000m (8)" beside the D3 8K/6K. The tfrrs D3 meet and a 2009 anet NJ
high-school meet share one meet_id (15,096 XC ids collide; track too). The
meet and race pages split them (?alt= / ?r= pins, app.meet_sources /
pick_source); the predictions page never did: the race list merged both,
the date was the 2009 one, and "as it ran" read the 2009 season -- the
Tufts card of long-gone runners. Owner's rule kept: as it ran + Everyone =
that season's whole roster (seniors included). In progress: one source per
meet, carried through every /api/predict/* call and the page.

## 2026-10-05 — ⏳ some schools' sections / states / divisions are wrong

Owner: "remember to log some sections/states and stuff are off so we'll need
to fix." Some schools have wrong unit assignments (state, section,
section_div, state_div). These feed every HS board and rank line
(rankings.HS_UNITS, build_season_ranks, cards' unit lines). Now that
"What it takes" reads section and state qualifying marks from them, it
inherits the errors too. That page guards itself by showing cell sizes and
skipping thin cells, but that is not a fix. Same family as the 2026-09-17
"sections/divisions redo" item.

To do:
- Measure it. Find schools whose section or state disagrees with where they
  race (their section/state championship meets), and schools whose unit
  differs across seasons with no realignment.
- Fix at the source (the unit assignment), then rebuild the boards.
- Owner to give known-wrong examples when seen.

## 2026-10-05 — ✅ new features: install, accuracy, CSV, coming up, team widget, runners like you

Owner approved the list ("sure to all"). Each is live after a pull and restart,
except where noted.
- **Install to home screen.** /manifest.webmanifest plus icons drawn from
  favicon.svg (scripts/make_app_icons.py), and the apple-touch tags in
  _meta.html. No service worker, so nothing stale can get stuck on phones.
- **About → How accurate is it** (accuracy.py). run_report's fair tests in
  plain words, newest run first. Only our own numbers.
- **Download CSV** (csv_export.py). ?format=csv on /api/rankings (the board
  page as shown), /school/<n>/prs, /race/xc and /race/tf. Each row links back
  to the athlete, and formula cells are neutralised.
- **Coming up on /meets** (weekend.py). Meets the feeds have posted for the
  next 7 days (meets.meet_date, meets_tf_meta, meets_tfrrs), biggest first by
  races posted, with a state filter. Each links to the predictions page's
  "This year" mode. No entries are posted, so there is no field. A home-page
  block is not built yet.
- **Nightly now rebuilds panels** (13_panels_xc/tf). "Latest results" and
  /meets were only rebuilt by full runs, so meets scraped by the nightly had a
  page but appeared on no list.
- **Team widget** /embed/school/<name> (?sport, ?state, ?level, ?theme=dark).
  It shows each pool's top 7 who have raced this season, team board ranks and
  the 3 newest meets. It is the only frameable path (CSP frame-ancestors *).
  The school page has a "Put this team on your website" snippet.
- **Runners like you** (build_comps.py → season_comps, step 10f3; comps.py;
  shown on /recruit/<id>#like, linked from HS athlete pages).
  - **Who matches:** past runners at the same grade, rated within the
    combined standard error of the two seasons (sigma/sqrt(n), sigma measured
    per pool). When both runners have a season before, their gains must also
    match within the combined error.
  - **What it shows:** the matches' next-season and senior-season spread,
    and their college share and divisions, counting only futures that have
    already happened. The model's one-year projection (recruit_projection)
    sits beside the matches' next season as a check on the model.
  - ⏳ **Server:** run `racecast/build_comps.py` once, or the section stays
    empty until the next full run.
- **Goal times:** /conversions already turns a rating or a time at one course
  into times everywhere. The missing piece (the gap to the next qualifying
  mark, as times at your own courses) went into the "What it takes" athlete
  line.
- **Already existed,** so the agents were redirected to audit instead:
  athlete share cards (cards.py, issue 281) and head-to-head /compare.

## 2026-10-05 — ✅ race pages: "Ran above their level"

Owner liked the idea (above_level.py; race.html and race_tf.html).
- **The column:** each rated row gets a "vs level" column, its rating
  against the athlete's season rating from their other races that season
  in the same pool. That rating is the site's own statistic, the 80th
  percentile after the 20-point outlier rule. The other feed's copy of the
  same race (same day and distance) is not counted as another race.
- **Units:** the gap is in %, so it reads the same on the HS-equivalent
  scale.
- **The box:** "Ran above their level" lists the runners who beat their
  level by more than sigma, the pooled within-season swing of these same
  runners, measured on the page. It is not a chosen threshold.
- **CSV:** the race CSVs carry the column too.

**Update (16dee83): fixed.** The predictions page picks one meet per id the way the meet page does
(`?alt=`, biggest source by default) and every predict call carries it: race list, date, course,
field, state, season ("as it ran" now loads the right year's roster), labels, share cards, and the
"Predict this" links from meet and race pages. With no source given, old paths read as before.

## 2026-10-05 — 🔎 UI audit: the patchwork, catalogued (decisions pending)

The live site was screenshotted page by page at 1280 and 390 px. What differs from page to page:
1. **Toggles and tabs: 7 styles.**
   - Rankings: boxed tabs with a navy fill.
   - Home: black-filled squares plus outlined chips.
   - Recruiting: black rounded pills.
   - Coaches: outlined pills, with no selected state shown (a bug).
   - Athlete: white segmented control, plus a dark rating-scale segment.
   - Course: tiny black chips, plus blue pills.
   - Meets: a black segmented control.
2. **Filter bars: 5 styles.** Rankings is a grey card with uppercase labels and a blue Apply button. Meets is a beige card with a black Filter button. Recruiting has bare selects and a white Apply button. Coaches has inline labels and a blue Search button. Breakouts has inline label/select pairs.
3. **Page width: 3 widths.** Rankings and athlete pages are full width. Meet, course, recruiting, predictions and the landing pages are centred at about 1080 px. Breakouts and projections use page-wide.
4. **Page header: 4 variants.** Title plus share button, a lead line in one of three sizes, and a link floating top right on some pages.
5. **Dates: 2 formats.** "2025-09-02" on the meet, course and school roster pages; "Dec 6, 2025" on athlete pages and Breakouts.
6. **Level and gender words.** HS Boys / High School (M) / Boys / Men / boy / HS boys.
7. **Help icons: 2.** An "i" circle and a "?" circle.
8. **Phone.** The athlete page's race tables push the page sideways (no scroll box).
9. **Crests.** A broken-image icon shows where a school has no crest (landing and rankings boards).
10. **Data, not UI:** "Unknown" names on rosters and boards (unnamed freshmen; the names requeue).

The plan: one small set of shared parts (header, tabs, chips, filter bar, primary/secondary
button, date format, labels), applied page by page after the owner picks the variants.

## 2026-10-05 — 🔎 race day vs a peaking field (NESCAC 2021); dropdowns still missing on some

**Race day.** Owner: NESCAC 2021 at Wickham Park had rain and 85% humidity, a day term of +2.7%
slow, and nearly the whole field 3-8% above its season level. Why isn't the day term higher?
- **Hypothesis.** The solve reads a day against each runner's season ability, one number for the
  whole season. A championship field is peaking, so a slow day and a fast field cancel and the
  term records the net. If so, it is an ENGINE gap: the ability model has no within-season trend,
  so day terms under-credit bad championship days, and season-opener days, run by unfit fields,
  read slow.
- **The test.** `scripts/diag_day_terms.py --season-trend` gives the runner-weighted day term by
  week of season. A line that falls through the season means form is leaking into the day term.
- **The fix, if it leaks:** a within-season form term in the solve (per pool, by week), so the day
  term carries only the day.

**Measured (2026-10-06, owner ran --season-trend): it leaks.** Day terms run about +2-3% slow in
weeks 0-2 and about -1% fast by weeks 10-13. The pooled slope over weeks 0-12 is -0.291% per week,
-3.49% by week 12. The same shape holds every season from about 2004 to 2025.
- **Correction to the hypothesis above.** The solve does HAVE a within-season term: joint_solve's
  form curve, one piecewise-linear curve per pool over the academic year, with 13 knots at 30 days
  each, smooth 1.0 and a gap weight of 100. The bracket refit subtracts it before refitting u. So
  the curve is fitted too flat or constrained away from the drift, not missing.
- **Candidate causes:**
  - smoothing stiffness;
  - 30-day knots, too coarse for a 12-week season;
  - amp(a) scaling;
  - the gap penalty;
  - the season-ability tie absorbing the mean.
- **Next.** The `[joint] curve:` lines from the latest run's 08 solve log show the fitted curve
  per pool. Compare its Aug-Nov drop with the 3.5% left in the day terms.
- **The curve table (run 20261005_172914) is a STRAIGHT LINE in every pool.** For example, hs_m
  falls 0.012 every 30 days from August to July, with second differences of 0.001 or less.
  - **Cause, in the code.** joint_solve sets the curvature penalty at
    `lam = CURVE_SMOOTH * rows_per_pool / n_knot`, with an unscaled data term. As a prior that
    is a curvature sd of sqrt(sigma2 / lam), about 5e-5 log-time for hs_m. In effect, no bend.
  - **Why the day terms soak it up.** The curve's only competitor for a date-level shift is
    each race's u, and u's prior is per RACE (sigma2 / sigma_u^2, about 5). So a bend costs
    about a knot's worth of ROWS, while the same shift in the u's costs about a knot's worth of
    races times 5. The straight part is free and the curve takes it; every bend (steep in the
    fall, flatter later) goes into the day terms. That is the measured drift.
  - **Not all of it need be form.** August and September are hot, and the weather correction is
    a no-op while its artifact is stale, so heat sits in u too. `diag_day_terms.py --heat` splits
    the week slope into form and temperature before the engine changes.
  - **Fix, once the split is known:** a curvature prior on the same footing as the others,
    `sigma2 / curve_sd^2`, with curve_sd estimated each outer pass the way tau and sigma_u are.
    Heat goes to the weather refit (XCP_WEATHER_FIT=1), not the curve.
- **--heat result (owner, 2026-10-06): no drift on the weather subset.**
  - 2,532 race-days have an hour-9 weather row. On those, the week slope is +0.042%/week, and
    +0.040% with temperature held. Temperature has no effect (-0.001%/degree C).
  - That contradicts --season-trend's -0.29%/week. So either the drift lives outside the weather
    subset, or the two measures differ: --season-trend pools without demeaning by season.
  - --heat now also prints the within-season slope on EVERY day row, for all seasons and for
    the weather rows' seasons, plus the weather rows by season. That settles which it is.
  - The straight-line curve is a fact of the code either way. Whether it costs anything is now
    the open question.
- **--heat, second run: the drift is real; the heat split is not possible yet.**
  - Within season, on EVERY day row, the slope is -0.302%/week over 202,164 race-days.
  - The weather rows are 2,417 race-days of 2026 plus a handful of older ones: the weather
    grid only reaches this season. So the heat split must wait for historical weather.
- **FIX (2026-10-06): the curvature prior is fitted.** `CURVE_SMOOTH = "fit"` (joint_solve)
  prices the curvature at sigma2 / curve_sd^2 per pool, with curve_sd estimated each outer pass
  (curveCurvatureVar: the mean squared second difference plus its sampling variance).
  - The log prints `[joint] curve curvature sd per pool`.
  - `XCP_CURVE_SMOOTH=1` (or `--curve-smooth 1`) restores the old weight for an A/B run.
  - **Synthetic test** (tests/test_curve_bends.py): a season that gains 4.25% to 1 Oct and then
    flattens. The old weight fits a straight line and leaves a 1.2% hump in the day terms;
    "fit" recovers the bend (3.8%) with the day terms flat to 0.3%.
  - **Learned on the way:** the curve is scaled per runner by amp(rating). A world that
    generates an unscaled curve misleads the fit, because amp is what identifies the curve
    within a race.
  - **Two tests are pinned to the old weight, with notes:**
    - test_joint_year.test_full_recovery: a fitted-level config the pipeline doesn't run; its
      error sits on the 0.01 line either way.
    - test_joint_kernels: arithmetic equivalence; differences of 2e-8, inside CG_TOL.
  - **To check after the next run:**
    - the curve table bends;
    - `diag_day_terms.py --season-trend` slope falls toward 0;
    - the holdout sd and its by-rating rows;
    - Nov->Mar per pool (it moves the XC-TF relation);
    - 08's CG iterations (a weaker prior can mean slower convergence).
- **Also seen in that log:** in the tilt table, the <70 band has applied h 1.112 against an
  implied 0.528, and 70-80 has 1.074 against 0.388. Look at this when the curve is redone.
- **Dropdowns, re-run.** Only 2 of 30 races on 29603086 still lack a day row:
  - Foot Locker West 2024-12-07: a champ-cell row exists under 'XC:Foot Locker West Regional',
    so the page's name join is what misses;
  - the Mid-Season Mania 1600m placeholder: nothing to show.

**Era.** Owner: does day/era association mean the era term isn't doing enough? The day terms'
per-season means (-2.7 / -3.2% XC before 03e87af) are year-level shifts the era curve left
behind. Centring now moves them into abilities. Run REPORT's `day_centre_xc` per season against
the era curve: shifts that trend with year mean the era curve is mis-shaped.

**Dropdowns.** The owner's page has 6 of 75 XC races with no race-day dropdown: Gaucho
Invitational 2024, Roughrider 2023, De La Salle Nike Invitational 2021-23, and the Cal scrimmage.
`scripts/diag_day_terms.py --person 29603086` names, for each, the race_day_effect rows at that
venue: a different distance cell, a different date, or none at all.

## 2026-10-06 — Rankings: CSV under the pager; "Unknown" names; out-of-state athletes; state pages

The owner's two URLs: /rankings?board=ability&pool=hs_m&sport=TF&state=CA&year=2024 and
/rankings/tf/hs-boys.
- **CSV.** It sat inside the pager row and squeezed it. It now has its own line under the
  pager, the pager spans the board, and the CSV line hides on an error.
- **✅ "Unknown" at the top.** These are people split off by unlink/person_collision (ids from
  2e9), who have no athletes row. rankings.nameLateral now falls back to the name on their result
  rows (results, then results_tf), as the athlete page does. This applies to every board and to
  breakouts, comps and accounts.
- **✅ Out-of-state athletes on a state's board.** athlete_season.state was mode(result state),
  i.e. where the meets were. Tanner Chada (Gazelle Sports Elite, MI) ranked 3rd in CA and Brady
  Keller (Leduc T&F, Alberta) 10th. build_ranking_results._homeStates moves a season to its
  school's primary state ONLY when the race state is none of that school's school_identity
  clusters ("Jesuit" CA/LA/OR keeps CA). Unattached seasons are untouched. It takes effect at the
  next 10_rankings_finish (nightly or full).
- **✅ The national track page was all-time.** homepage_meta leaves season_year_TF blank in the
  autumn gap by design, and the landing route then passed year=None. It now falls back to the
  newest rated season, labelled the meta's way.
- **State pages UI.** One filter card: sport as joined tabs, pool as chips, state as a picker.
  This replaces two lines of underlined links; the full state list stays at the foot for crawlers.
- **diag_board_people.py on the CA 2024 track board** (owner ran it, 2026-10-06):
  - **Eric Van Der Els** (2000000023): UConn SR-4 in 2022, then ZAP Endurance pro rows graded
    "11"/"12" in 2024-25. Rule 5b (post_collegiate) should already pool these pro. Likely cause:
    the id comes from a manual person_collision split made after the last grade_sanity, so
    grade_fix is keyed on the old id (32786559). Re-check after this run.
  - **Bobby Poynter** (2000000845): grade 12 two seasons running, at the Olympic Trials and USATF
    championships. Also a collision split, so the same re-check.
  - **✅ Elijah Ocegueda:** "grade 11" for Performance Elite RC in 2024-25, then a JR-3 at Cal
    Poly Pomona in 2025-26. Rule 5b started college at the first definite row; it now starts it
    at row year - (class number - 1), so a JR-3 in 2025 means 2023.
  - **✅ Brady Keller** (Lacombe HS, Alberta) under "Leduc Track and Field", and Leo Young under
    "Newbury Park Athletic Club": a school-age season's label now prefers a non-club name when the
    season has one (_CLUB_NAME_RE in build_ranking_results). Club-only seasons keep the club.
  - **✅ Tanner Chada** (Gazelle Sports Elite, MI): grade_sanity RULE 8, the adult club's "12".
    A high-school-graded season pools pro (method adult_club) only when ALL of these hold:
    - every team that season is club-shaped (unattached or blank count against);
    - every race's ceiling is college or pro (no high schoolers on top);
    - either the club is adult (over half its athletes' club seasons come after their college
      began, OR over half were pooled pro/college by the last build), or the athlete's own
      earlier HS grade puts graduation before this season.
    - The --team run measured "after college" alone at 23% for ZAP Endurance (most pros carry no
      FR-1..SR-4 row), 0% for Gazelle Sports Elite (a store club with youth age groups) and 0% for
      Brentwood TC. Hence the pooled share as a second measure; Tanner is caught by graduation
      either way.
    Owner: "jackson spencer running one race at worlds doesn't mean he's not in hs", so a season
    with a school row, or with any race that had high schoolers on top, is never moved. The step
    prints the count and 12 examples. `diag_board_people.py --team "<exact name>"` shows a
    club's adult share. Tests: tests/test_adult_club.py, Postgres half included.
  - **National teams:** the pro_flag seed only fires on a COUNTRY name at a SENIOR
    championship, excluding U20/U18/U23. "USA" is on neither list, so a US junior at Worlds is
    untouched.
  - Clive Terrelonge ("Jamaica"): the 2026-09-29 national-team seed covers it, and the national
    page now shows the current season.
- **🔎 Still on those boards (not fixed):**
  - pro or club groups' athletes graded 12 in HS boys (ZAP Endurance, Railroad Athletics,
    Tracksmith);
  - "Jamaica (CA)", a national team, with grade "-";
  - club names as the school (Newbury Park Athletic Club, Brentwood Track Club,
    Performance Elite).
  These are pool/grade and school-identity questions for the level and school rules.

## 2026-10-06 — State meets UI; track same-day twins; upcoming predictions had no field; pipeline slow at 04

- **State meets UI.** Owner: "update the ui for the state meets predictions".
  - /projections: a "Find a state" box (typing narrows the list; Enter on a single match opens it)
    and the states as tiles, replacing four columns of underlined links.
  - /projections/<st>: one card shape for every division, with the name and school count stacked
    and Boys | Girls as a joined pair. The redundant state prefix is dropped.
  - Division page: when the early-season banner shows, the per-row THIN flags are muted text
    rather than fifty yellow badges.
- **twin_same_day on the track.** Owner: "for track do day + time + if it's cross source". The
  rule flags a tfrrs track row whose person has an anet row the same day at the same time to the
  hundredth. Relays and field events are out, and anet survives. XC keeps its 1-second tolerance
  (tfrrs posts whole seconds).
- **🔎 Predicting an upcoming meet doesn't work.** Owner: "where does the roster come from?"
  Nowhere: the races, date and field are all read from the meet's RESULTS, which an upcoming meet
  doesn't have, so the Upcoming list's Predict links open an empty page. The scraper doesn't fetch
  entries. ✅ FIXED (racecast/last_edition.py):
  - The races and date come from the meet's posted rows.
  - The field is the teams at the meet's last running (same feed, normalised name, earlier, with
    results; same venue, then same state, then most recent), with this season's squads.
  - The page names the running it borrowed. With no earlier running, it asks for teams.
  - The Coming-up link's &src= is now read, so an upcoming tfrrs meet no longer resolves to an old
    anet meet that shares its id.
  - Tests: tests/test_upcoming_meet.py (Postgres included) and tests/test_predict_upcoming.js.
  - Unverified on the real corpus: anet "Varsity" pairing assumes divisions keep their order year
    to year; an upcoming tfrrs meet may have no race list (it falls back to the whole edition).
- **✅ FIXED: 04_grade_sanity rule 7, track half.** pg_stat_activity: one CREATE TEMP TABLE tmp_elite
  running 5h57m, no lock wait. The statement joined every results_tf row to race_top_level on an
  md5 key before an unestimable SQL-function grade filter. Now one pass stages the elementary and
  middle school rows (tmp_elite_src, ANALYZEd), and the joins run on that with nested loops off.
  The rows were checked identical to the old statement on a scratch database (XC 694, TF 456).
  Restart from 04.
- **✅ Rule 7's track half never fired.** In run 20261006_024705 rule 7 ran in 163 s (XC) and
  573 s (TF) after the staging fix, but the track bars read 0:38-0:53 for 5 km, so "0 rows under
  them". The 1-in-10,000 quantile of 17.5M track rows is junk (field marks, wrong distances
  stored as times). A 5 km equivalent under the senior world record (M 12:35.36, F 14:00.21) is
  now dropped before the bar and the comparison. It takes effect at the next run's 04.
- **📌 Link meets across years** (owner: "maybe we should have some way to link meets across
  years"). Upcoming predictions need a meet's previous edition, and so would "this meet, year by
  year". A meet_series table (normalised name + venue + source, built in the pipeline) would give
  every reader one answer.
- **🔎 Pipeline 20261006_024705 is still at 04_grade_sanity after ~9 h.** It printed rule 7's XC
  bars, so it is in the track half of eliteFieldSeasons. On 2026-10-05 the whole run reached 08
  in 2h17m, and grade_sanity hasn't changed since 2026-09-30, so something is waiting (a lock?)
  or the track query has gone slow. Next: pg_stat_activity, and the log dir's summary.log.

## 2026-10-06 — Upcoming filter normalised; a race with no difficulty; a cross-feed twin

- **/meets Upcoming.** The state filter was a bare select in a sentence ("out of place"). It is
  now the Results view's filter card (.meet-filters), with State and a new Level field (HS &
  college matches both). The day heads are the Results view's month heads, and the tables have
  the same header row.
- **🔎 Panorama Farms 6000m, Sep 11 2026, "Course difficulty: -" with ratings (143.0).** The
  venue's own page shows +5.5% at 5000m and lists a 6000m tab. Likely there is no 6000 m cell
  in course_difficulties, so the header's (canonical, 6000) join is empty. Check with
  `scripts/diag_race_difficulty.py "<race url>"`, which prints the chain and the step that
  came up empty.
- **✅ FIXED (same day): twin_same_day.** A new rule in engine/twin_flag.py (step 04c, nightly
  and full pipeline) flags a tfrrs XC row whose person has an anet XC row on the same day within
  1 s. tfrrs posts whole seconds; anet keeps the copy. XC only: on the track one person runs
  several events a day. On the fixtures it also caught 9032 (one race in both feeds). Tests:
  tests/test_twin_flag.py, on a real Postgres.
- **By state tab.** The state ranking pages (/rankings/<sport>/<pool>[/<state>]) are now a tab on
  Rankings, Breakouts and the state pages themselves, for the pool and sport in view. "State
  meets" as a Predictions tab: owner wants to talk about it first.
- **🔎 Same race twice on an athlete page.** On Sep 4 2026, "Utah Valley Invitational" (anet,
  13:21.4, 146.6, +3.0%) and "2026 UVU Collegiate XC Invite" (tfrrs, 13:21, 148.2, difficulty
  -) are the same race at Scera Park, and both carry PR/CR flags. The cross-feed twin was not
  joined (canon_meet_id), and the tfrrs copy is rated without a difficulty.

## 2026-10-06 — accuracy at the extremes; rating-scale switch black

- **Accuracy by ability.** Owner: "I wonder if our accuracy changes as ability goes to
  extremes." The held-out scorecard (08a_holdout, 08a_sport_holdout) now prints error sd and
  bias by the athlete's own-pool rating, on the tilt report's bands. It has two columns:
  - all rows;
  - athlete-seasons with 6+ training rows, where regression to the mean is small.
  A mean that runs + at one end and - at the other is the model mis-scaling that end. Read it
  next to the tilt table: <70 applied h 1.11 vs implied 0.53.
- **Rating scale switch.** It is now a black TAB like the others. The search tabs, the predict
  view buttons, the quick race chips and the unit buttons now use the same black (--sel).
- **Tables stay square.** That was the owner's pick on 2026-09-13; controls are rounded.
- **Tabs are JOINED** (owner: "I prefer the buttons being together"). One bar per group, with
  outer corners rounded. On phones a bar scrolls sideways. Chips stay separate pills.

## 2026-10-05 — 📌 team widget: kept, but it's poor right now

Owner: keep `/embed/school/<name>` but it's "ass rn". To do before anyone is pointed at it:
- a real design (it reads like a debug table);
- choose what a team site actually wants (season results? the next meet? top 7 with times, not
  ratings?);
- a preview on the school page that shows what it looks like.

**Update: owner picked A-D, and the first pass is in.**
- **A, tabs and chips.** Two looks site-wide, in CSS only (the "ONE LOOK" block in
  style.css/pages2.css). Every page keeps its markup and scripts.
  - Tabs, for switching what the page shows: the Rankings board tab.
  - Chips, for narrowing what's shown: small outlined pills.
  - The coaches page's Boys/Girls now shows which one is selected.
- **B, width.** Breakouts and the state ranking pages run full width like /rankings;
  everything else keeps the centred 1080.
- **C, dates.** "Oct 4, 2025" on the meet, race, course, school roster, school PRs, venue
  and compiled pages (the `mdy` filter).
- **D, labels.** "HS boys / HS girls / College men / College women / MS boys / MS girls"
  on the home snapshot, rankings, conversions and the equivalence line.
- **Still to do:**
  - one page-header pattern;
  - one filter-bar layout (meets now has the Rankings card ground; recruiting and coaches
    still lay theirs out inline);
  - the help icons ("i" vs "?");
  - athlete race tables on phones (sideways scroll);
  - the broken crest icon.

## 2026-10-07 — ✅ XC race page, results first, with a rule-based summary

Owner: "the site is info overload" → mockup rounds → picked D5, minus the rule every 10 places →
"plug race page into site, and formalize summary so I can see it working".

- **Layout (race.html, static/race.css, static/race-page.js).**
  - Dark header: meet, division, course, distance, date; individual and team champion; the summary;
    course difficulty, race-day term and weather on one line.
  - The full results table is the page, 1-2-3 included (gold/silver/bronze place tags). Six columns:
    grade sits after the school, and vs level sits under the rating.
  - Team scores (top 10, top-5 places) beside the results on desktop. Tapping a team lights its
    runners; the team champion is lit on load (`?school=` lights that school instead).
  - Tabs: Results | Teams (the full 7-place table) | On the track (the equivalents ruler) | Predict
    next year. A finder box filters rows by name or school.
  - Nothing the old page showed is gone: share, distance-corrected star, withheld notice, HS scale
    toggle, "ran above their level" box (now in the sidebar), CSV, report, `?r=` row highlight.
  - Fonts self-hosted in static/fonts (Barlow, Barlow Condensed, JetBrains Mono; SIL OFL 1.1).
  - Without JavaScript every panel shows, one under another.
- **Summary (racecast/race_story.py).** Two sentences, each a template that fires only when its facts
  are on the page:
  1. winner, time, gap to second (a dead heat says so); "won a Nth straight title here" when the winner
     also had the fastest time in this meet's same-level race in each of the previous seasons
     (edition name via last_edition.editionName; division number ignored, level kept).
  2. team champion, points, margin to second (a points tie names the sixth-runner tiebreak), and the
     team's first runner with their place.
  - **No verdict words.** The mockup's "Campolindo won on depth, five scorers 46.2 s apart" was false:
    Oak Park (18.6 s) and Hart (19.7 s) had tighter 1–5 splits. Removed; the rules state margins only.
  - The repeat-title count is the only new query: own connection, 1.5 s timeout, any failure → no clause.
- **above-level.js** fills `.vs-level[data-rid]` (any element, was `td` only) so the value can sit under
  the rating. race_tf still uses a td; unchanged there.
- **Tests:** tests/test_race_story.py (17). test_race_school_state's template test now counts the
  school's two mentions per row (the phone line and the column).
- **Pre-existing failures, not from this change** (fail identically on the previous commit):
  test_race_day_hover::test_dv_with_and_without_a_day, test_race_school_state::
  test_the_scoring_split_prefers_the_assignment_over_the_home_state, test_meet_units::
  test_pipeline_and_template_carry_the_feature.
- **To check on the server:** the repeat-title clause has only run against a fake cursor here. Open
  /race/xc/236800/1011723 — it should read "won a third straight title here" if Noonan's 2022 and 2023
  CIF State rows are linked to the same person.

## 2026-10-07 — "site is much slower" / "off-center, weird spacing"

**Speed, measured on the live site (probe UA, a handful of requests):**
- Warm (cached) pages answer in 0.14–0.4 s. Uncached first hits are the slow ones: the CA state XC page took 3.7 s once,
  then 0.2 s; the race page 1.2 s, then 0.2–0.4 s. A restart empties every cache, so right after a deploy every page
  pays its first hit, and a running pipeline makes those first hits slower still.
- **One real regression from my side, fixed:** the track state pages' season probe (2026-10-06) filtered athlete_season
  on sport + year only. Every index on that table leads with pool, so each probe was a sequential scan, and the new
  season's probe (almost no rows) read the whole table on every uncached track state page. It now probes
  pool = 'hs_m' + sport + year (as_board_mean_idx's prefix): one season for the whole sport, as before, from its
  biggest board.
- The race page's new repeat-title query is one per uncached race page, by person_id, bounded at 1.5 s.

**Layout, measured edges at 1280 px:**
- Centered pages (meets, course, school, predictions) put content at 100–1180. Conversions was at 90–1190: now 1080
  like the rest. The new race page was at 124 and had zeroed the body margin, moving the top bar 8 px: now the site's
  margin and the 100–1180 column, and the Share button sits in the header instead of over its top edge.
- Full-width pages: /rankings and the athlete page at 32; the state ranking pages and breakouts at 40. Now all 32
  (1.5rem gutter).
- State ranking table: Rating and Races headers were left-aligned over right-aligned numbers, and # was a right-aligned
  number in a wide cell. Headers now align with their numbers, # reads like the rankings board's.

## 2026-10-07 — 🧪 the race page's look on every page, as a preview (?theme=rc)

Owner: "try this style on the other pages." Built as an opt-in preview so nobody else sees it until it's approved:
- **Switch:** add `?theme=rc` to any URL (remembered in this browser), `?theme=off` to turn it off. A few lines in
  _topbar.html read it client-side and load static/theme-rc.css; the HTML the server sends is identical for every
  reader, so edge caching is untouched. Every rule sits under `html.rc-theme`.
- **What it changes (CSS only, no template changes):** black full-width top bar with condensed uppercase nav and a
  red underline on the current section; condensed italic uppercase h1/h2; the paper background; black table heads,
  white tables, ink links; record flags as quiet red text (as on the race page); condensed uppercase tabs, chips,
  labels and buttons; white filter cards. The race page's dark band runs full width under the bar.
- Checked by loading the live pages with the stylesheet injected (home, rankings, state rankings, athlete, meets,
  meet, school, course, predictions, conversions, race, recruiting, coaches, about; phones for race, rankings,
  athlete, meets).
- **Not themed yet:** chart lines (athlete charts stay blue), the equivalents ruler's selected pill (blue), the home
  mural's own type. If the look is approved, those plus the per-page headers become real template changes and the
  switch goes away.
- Pre-existing test failures, unchanged by this: test_coach_view::test_the_per_reader_block_rides_the_topbar_s_one_request,
  test_recruiting_athlete (2), plus the three noted earlier today.

## 2026-10-07 — 🧪 ?theme=rc, second pass: every page, not just the shared pieces

Owner: "feels half-assed, only some things were put into new theme." Went through all 29 page types with the theme on
(screenshots of the live pages with the stylesheet injected) and themed what was still the old site:
- **The dark header band on every page.** static/theme-rc.js (loaded with the theme) gathers the page's title row and
  its header lines -- by a class whitelist: meta lines, lead, the athlete's stat strip and rank lines, a paragraph of
  meet-link buttons -- into one band at the top of the page's container, with the red rule under it. Tabs that sat
  above the title now sit under it. The athlete page (header is the body's own child) keeps its 1.5rem gutter.
  If approved, this becomes template markup and the script goes.
- **Leftover blue:** chart line (ink) and dots (red); the equivalents ruler's selected pills and big times; the head to
  head Compare button and A/B colours (ink/red); the recruit pills; `--accent` re-pointed to ink.
- **Leftover old details:** amber sub-labels (dist-note) and notice boxes (synthetic, withheld, banners) to the race
  page's red rule on white; every button in condensed capitals; h4 / section labels; square white inputs; ratings in
  the race page's mono (ratings only: mono times and dates overflowed the fixed-layout school PRs table).
- **The track race page in the cross country race page's layout** (live for everyone, like the XC one): dark header
  with the event, the winner (or the best mark across heats when there is no final) and the event's top-scoring school;
  race_story.tfStory writes the summary (winner and margin for running events -- hundredths; the runner-up's mark for
  field events, never subtracted because marks can be feet or metres; "N of M set personal records"); results first
  with heats, relays and their legs, wind and points kept; event points by school in the sidebar with team lighting;
  the equivalents ruler behind a tab. tests/test_race_story.py: 6 new tests.
- Pre-existing test failures unchanged (identical output before and after for the crest/label consistency tests).

## 2026-10-07 — flags, search box, what is a link, track venue, duplicates (owner's list + a sweep)

- **Flags:** solid tags instead of bare red text -- PR red, SR black, course records (CR/CSR) outlined red, rating
  records (R/RSR) outlined black; same tooltips. On the race pages for everyone, site-wide under ?theme=rc. (The
  variants had lost a specificity contest at first: everything came out red. Fixed.)
- **Search box (theme):** the field went white while its text stayed white -- two theme rules of equal weight, the
  later one winning. Now set by id: dark field, white text and caret.
- **What is a link:** a quiet underline under every link in a table (names, schools, meets), red on hover; header
  links underlined. Links that leave for another part of the site carry an arrow: the meet name above a race,
  the course/venue, "Predict next year" (now a link beside the tabs, not a tab).
- **Track race venue:** the venue's own name and city from meets_tf_meta (one primary-key read); "Venue page" when a
  meet has none, never the bare word.
- **Duplicates removed:**
  - race pages: "Full meet" was in the tabs and the sidebar and the meet name -- now only the meet name. The track
    page's "Venue" was in the meta and the sidebar -- now only the meta. The team champion was in the champion
    line, the summary and the sidebar's first row -- now the summary and the sidebar. The track page's "Most points"
    duplicated the sidebar's first row -- removed.
  - the summary no longer repeats the champion line: surname, margin, runner-up, streak; the team's margin and leader,
    not its points (race_story; tests updated).
  - athlete page: the sidebar's "Athlete rating / Best race" card repeated the header's stat strip -- removed.
  - course page: the equivalents card had its own row of distance pills under the page's own row -- removed; the
    page's row picks the ruler's distance too.
  - search results page: the query was pre-filled in the top bar and in the page's own search box -- top bar no longer.
- **Last blues (theme):** "Show 20 more" buttons, the course year-by-year bars and fast/slow colours, spinner, legend.
- **Noticed, left as is (owner's call):** the home snapshot's "Top athletes" and "Best performances" columns often list
  the same names (different measures); the athlete sidebar's per-season bests repeat each season header's rating; the
  race sidebar's top 10 teams are also the first 10 rows of the Teams tab.

## 2026-10-07 — softer contrast; the rating-scale "?"

- Owner: "too much white black contrast." Race pages (everyone) and ?theme=rc: "black" is now a warm charcoal
  (#24221f), "white" a warm off-white (#faf8f4 surfaces, #efece6 type on dark), the paper a step darker (#edeae3),
  and table heads a light warm grey (#e3dfd6) instead of a black bar. The header band and top bar stay dark.
  Every white surface in style.css / pages2.css / features.css (82 selectors) is re-pointed under the theme by a
  generated block wrapped in :where(), so it weighs what the white rule weighs and the site's selected states still
  win. (Regenerate: scan those files for `background: #fff|white` rules and emit `:where(html.rc-theme) <sel>`.)
- The rating-scale "?" was centred by line-height (glyph sat low) and the new flag rules styled it as a red record
  tag. Now flex-centred site-wide and aligned to the toggle's buttons, and excluded from the flag rules: the quiet
  grey ring the other help icons use.

## 2026-10-07 — race pages back to the site's own look; theme preview removed

- Owner: "this just feels like an affront to my eyes" → chose "Back to the old look".
- **Removed:** the ?theme=rc preview (theme-rc.css, theme-rc.js, the snippet in _topbar.html) and the self-hosted
  Barlow / JetBrains Mono fonts (static/fonts; only race.css and the theme used them).
- **race.css is layout only now:** no fonts, colours, dark band, solid flag tags or medal colours. Both race pages use
  `<main class="page-wide">` like the meet and course pages, so h1, tables, the coloured flag pills, links and the
  black joined `.seg` tabs are style.css's. Kept: results first, the two-sentence summary, team scores beside the
  table with team lighting (pale gold ground, gold left rule), the finder box, phone subline, vs-level under the
  rating, track venue name, the duplicate removals.
- Table cells give back some of the site's .9rem side padding (the sidebar takes 280px) so names stay on one line;
  the school wraps instead.
- Kept from the theme work (site-wide fixes, not theme): the rating-scale "?" centring, landing table alignment,
  .page-wide padding on brackets/landing, .conv width, the athlete-page and course-page duplicate removals.
- Tests: the 24 failures (plus 9 order-dependent ones in the full run) and 6 collection errors are identical with
  and without this change.

## 2026-10-07 — open: pipeline run 20261006_120609 failed 10a_board_sanity

- Full run finished in 24h 38m; every step ok except 10a_board_sanity. Logs: /srv/xc-predictor/logs/20261006_120609.
- Race-page revert (af5f973) deployed after the run. Owner: come back to 10a later. Not yet looked at.

## 2026-10-07 — the scrape: newest meets first; the name repair is queued

- Owner: "is this the scrape that will add new meet ids? did we fix the name issue?" queue_status on the box: anet
  due 98,780 (TF 73,340, XC 25,440), ids 3..675,708. Since 2026-09-18 done fell TF 536,894 -> 465,424 and XC
  171,428 -> 154,228: that is `requeue_blank_athletes.py --apply` (dry run said 93,523 meets), already run. So the
  name fix is in the savers (a5a9009, 3d56bd0, 48c842c) and its re-scrape is queued; nothing else to do.
- **Was:** the claim was ORDER BY meet_id and the forward walk seeded only when the whole queue was empty, so the new
  meets (always the highest ids) would have come after all 93k repairs.
- **Correction (same day, owner: "each track meet should only be one request of compiled results").** It is:
  every anet meet, XC and TF, is GetMeetData (meet, divisions, the JWT) + one GetAllResultsData for the whole meet.
  The per-event TF loop that TARGET_REQUESTS_PER_SEC_PER_IP paces has been dead since 6/22, so my "days" was wrong:
  the pace is PER_MEET_DELAY over 25 sessions -- at 3-6 s about 4 meets/s, ~7 h for 98,780 meets (+ page time);
  at the default 1.5-2.5 s about twice that. scrape_tuning.py's comment now says so.
- **Now:** anet claims newest first (database._CLAIM_ORDER; ANET_OLDEST_FIRST=1 restores the old order), and the
  launcher re-seeds the walk as soon as its block above the watermark is scraped (launcher._maybeExtendFrontier,
  one check per 30 s for all sessions; ForwardWalk.frontierDrained / extend(sports)). The dry-block stop is the
  same rule, counted per sport. New meets first, then this season's repairs, then older ones; the run can be stopped
  any time and the rest stays due for the nightly job.
- Tests: tests/test_newest_first_scrape.py (scratch Postgres: claim order, re-seed with old meets still due,
  two dry blocks stop the walk).

## 2026-10-07 — run 20261006_120609: where the 24h 38m went; evidence steps moved off the critical path

- Per-step (summary.log): 04_grade_sanity 460.7 min, 08b_ladder 351.1, 08_golive 157.5, 10f2_projection 81.2
  (background), 07_pack 58.3, 08a_sport_holdout 51.1, 08a_holdout 48.1, 10_rankings x4 45.4 (parallel), 04c_twins
  44.7, 10_rankings_finish 39.5, 05_backfill 26.8.
- **04_grade_sanity: one gap of 303.6 min right after rule 7's "[7] 21 seasons ... -> pro" line.** What runs there
  prints nothing until it ends: rule 5b's _COLLEGIATE_SQL, _COLLEGE_START_SQL and rule 8 (adultClubSeasons, new on
  2026-10-06; it prints "[8] ... (Ns)" with its own time). On 10-05, before rule 8 existed, 00-07 took 2h17m in
  all, so rule 8 is the suspect. Asked the owner for the step's timed lines to confirm before changing it. Other
  gaps: 50.1 min after "CREATE INDEX ON gradekind", 27.6 after "rows they hold", 25.1 / 20.4 for the two 2c rounds.
- **✅ The evidence steps no longer hold up the boards.** 08a_holdout, 08a_sport_holdout, 08b_ladder, 08d_diagnose,
  08c_anchor_check (451 min this run) publish nothing; they now run as ONE background chain at nice 19 from the
  go-live on, beside 09-16, collected before 17 (17_checklist and 17c_report read their logs). One chain, not
  five, so two solves never hold the pack at once. Failures still count (written to a file, merged into FAILED).
  Lines carry "[evidence]". XCP_EVIDENCE_BG=0 runs them in line as before.
- **🔎 10a's 2 HARD findings: the sport level per level is still not held** (hs read -0.0039 vs target -0.0092,
  ms -0.0100 vs -0.0191; college and elem within tolerance). The 10-02 change (sportGainShift iterating on the
  median) moved it (hs -0.0036 -> -0.0039, ms -0.0061 -> -0.0100) but did not close it. Next: the go-live's own
  shift table, to see whether the solve holds it and the season ratings lose it, or the solve does not.
- Noticed: the 08a holdouts are run without --sport-level-pools, --difficulty bracket, --race-effect-*, the gauge
  and the bracket window that 08_golive gets. Possibly intended (the bracket engine is scored by 08d's "SAME
  ROWS"); left alone, noted.

## 2026-10-07 — 04_grade_sanity: the 5 hours found; 10a's sport level held on the boards' statistic

- **The 303-min gap was rule 5d, not rule 8** (the run predates rule 8: its track bars still read 0:38-0:53, the bug
  fixed at 18:24 UTC). 5d fetched every graded allraces row (most of ~225M) into Python to count contradicted
  entrants per event. ✅ Now SQL (grade_sanity.thinFieldSeasons): the events are found from the contradicted seasons
  through allraces' (person_id, acad, race) index and only theirs are counted -- an event needs 70% of 4+ graded
  entrants contradicted, so one with none cannot pass. Same counts (rows, a person twice counts twice; float8 as
  Python). tests/test_thin_field_sql.py checks it against the old loop on random fields and at both edges.
- ✅ The fold's UPDATE (3,009 s) is a rebuild of allraces with the column (the table's own build took 335 s), indexes
  rebuilt after. Checked equal to the UPDATE row for row on a scratch database.
- ✅ Rules 5e, 6 and trust each read allraces whole for the same per-season race count; now one query.
- New timing lines: "[5b] ... (Ns)", "[5d] ... (Ns)", "[5e] ... (Ns)", so no stretch of this step is silent.
- **⏳ 10a's sport level.** The go-live held the track-over-XC gap on each athlete-season's MEAN row, over anyone with
  one row in each sport; the boards and the check read the 80th-percentile season number over 3+ races each. XC rows
  scatter more, so its upper quantile sits further above its mean and the published gap fell short (hs 0.39% of
  0.92%). js.sportGainShift now holds it on the 20th percentile of log adjusted time (= the 80th-percentile rating)
  with 3+ rows each; XCP_SPORT_GAIN_STAT=mean restores the old measure. A simulation with noisier XC than track
  reads the stated gap on the board statistic this way and >0.3 points short the old way (tests/test_sport_gain.py).
  Expect 10a's level check to pass next run.
- Pre-existing failures, unchanged by this (same on the old tree): test_bracket_golive, test_course_bracket,
  test_diagnose, test_era_publish (collection errors), test_meet_units, test_pipeline_shards,
  test_pipeline_step_guards, test_report_steps_report_only.

## 2026-10-07 — ✅ "How was this rated?" popup: the scroll bar

- Owner: "the scroll bar doesn't work well." Cause: the panel follows its row with a capture-phase scroll listener,
  which also heard the panel's OWN scrolling; each wheel tick re-ran place(), which clears the height cap to measure
  -- uncapped there is nothing to scroll, so the position snapped back and the bar jumped. And the whole panel
  scrolled, so the bar ran up through the sticky header and was clipped by the rounded corners.
- Now: the panel's own scroll events are ignored, page scrolls are coalesced to one place() a frame, and place()
  keeps the body's scroll position. The panel is a column: header fixed, .rx-body the only scroller (thin bar,
  clear of the bottom corners). Checked in Chromium at 1280 and 390 with a 15-step breakdown: wheel scrolling
  moves 480px and holds (old: the panel scrolled and the body never did); a page scroll leaves the panel's
  position where it was.

## 2026-10-07 — speed round 2: 04c in two sessions, athlete_season in four, 07's season levels trimmed

From run 20261006_120609's gaps (line before and after each):
- **✅ 04c_twins (44.7 min): the two sports side by side.** A sport's rules read only its table and write only its
  sport's rows (key (sport, result_id)); staging tables are TEMP. result_twin_new is committed first, both sessions
  write it, and it is dropped if either fails. Order within a sport kept. Expect ~31 min (the track half).
  XCP_TWIN_PARALLEL=0 restores one session. tests/test_twin_flag.py: the parallel build writes the one-session
  build's flags row for row.
- **✅ 10_rankings_finish: the athlete_season GROUP BY (1,036 s) in 4 shards by person.** Ordered-set aggregates
  get no parallel plan and the sort spilled at 512MB; each shard sorts a quarter, at work_mem 1GB, in its own
  session, into the committed shadow. Checked equal to the single statement on scratch Postgres (1,963 seasons,
  negative and NULL person ids included). XCP_SEASON_SHARDS=1 restores one statement.
- **✅ 07_pack (21.3 min gap before "season levels"): only the per-sport verdicts that change an answer.** poolOf asks
  `by_sport.get(...) or combined.get(...)`; a per-sport verdict equal to the season's combined one is left in the
  database. Checked: every lookup identical on random data (8,973 -> 5,435 per-sport rows there); streamed in chunks
  with interned level strings.
- Not changed: 04_grade_sanity's three field queries (~20-25 min each) -- set-based already; need EXPLAIN on the
  real tables to pick a change. 08_golive's solve (warm start measured at ~4% in September).

## 2026-10-07 — 🔎 race importance in the season number: a bake-off, before any change

- Owner: "race importance could be good for deciding someone's rating" (with XCRI-26A's guide: one rating per
  athlete, every race equal, validated by "matched the next weekend's races"). Today the boards' season number is
  the 80th-percentile race, every race alike; the engine's importance term (off) is a course-difficulty covariate,
  not a weight on a person's races.
- **scripts/season_estimator_bakeoff.py (read-only).** Holds out each completed athlete-season's LAST race and
  scores candidate season numbers from the races before it by how well they order the athletes who met in that
  race (pairs; a race-wide taper or course cancels) and by race-centred MAE. Candidates: q80 (the board), mean,
  median, decayed mean, and q80 / mean weighted by exp(beta x importance), importance = field front (top-5 mean,
  standardised per pool-year) or season-end share. beta is chosen on even person ids from a grid that includes 0
  and scored on odd ones -- out of sample, and "no weight" stays possible. Board rules: rated rows, the season
  outlier cut. tests/test_season_estimator_bakeoff.py.
- **What the simulation already says (not data, a warning):** (1) if easy races are run consistently BELOW
  level, weighting important races does NOT help ordering -- athletes with deep races get unbiased numbers,
  those without keep the shallow bias, and the mix misorders them; (2) where important races are only less
  noisy, the weighted MEAN gains and the weighted q80 barely moves; (3) a field's front can track its noise
  (noisy races throw fast outliers), which out-of-sample scoring is there to catch.
- Next: the owner runs it on the box; if a weighted estimator wins out of sample, it goes into
  build_ranking_results as a switch, measured again on the following run.

## 2026-10-07 — scoring each assumption; the knobs; the XC day term says "leave it out"

- **🔎 XC race-day term (run 20261006_120609, 08_golive):** scatter of an athlete-season's races about its own mean,
  robust: day out 3.478%, day in (leave-self-out) 3.535% -- the log's own verdict "the day term adds noise -- leave
  it out". One run without it prices it: `XCP_RACE_EFFECT_SPORTS=` (empty) on the pipeline command. Owner's call
  (the XC day term was the owner's 2026-10-03 choice).
- **How each assumption is scored, or would be:** course difficulty -- race holdout, by course thickness (08a,
  08d SAME ROWS); cross-sport -- sport holdout (08a_sport_holdout; last run bias 0.001, sd 0.059); tilt/ability --
  holdout error by ability band (since 10-06); day term -- day-term consistency (above); season number -- the
  bake-off; sport level -- 10a's level check. NOT yet scored directly: the distance spline (proposed: a "distance"
  holdout -- hold out one distance of a multi-distance athlete-season whole, predict from the others, bias by event
  and distance gap) and the season/era curve (the "forward" holdout exists; proposed: race-holdout residual by week
  of season).
- **Knobs that are stated, not fitted** (candidates for a holdout grid in the ladder, on its 15% sample, run in the
  background chain): era years (2), era drift sd, bracket window (30 d), voter weight n/(n+5) and min voters (3),
  course prior (one race) and era prior (two), damping (0.5), distance prior/walk sd, LINK_WEIGHT, SLOPE_RIDGE,
  FRONT_K, the season outlier cut (20 pts), _SEASON_Q (0.80), decay (0.996), the indoor level (0.003). Already
  fitted: the curve curvature per pool, the day noise, the variance components, the course scale, the event offsets.

## 2026-10-07 — the race-day term: why it does not help on ordinary days; a heavy-tailed prior, measured beside it

- Read correctly, run 20261006_120609's day-term consistency says the term HELPS on big days and HURTS on ordinary
  ones: SD 4.190% -> 4.122% (-1.6%, the tails: mud, heat, long courses fixed) but robust 3.478% -> 3.535% (+1.6%,
  the bulk). The verdict line reads only the robust number. Cause: the day is a ridge estimate (one normal prior,
  variance sigma_u2), and a variance sized by all days -- tails included -- is too wide for ordinary days, so
  their noise reaches the ratings.
- ✅ js.dayMixtureFit / dayMixturePosterior: a two-normal scale mixture fitted by EM to each race's raw day (mean
  residual, its noise sigma2 / sum w h^2), per sport; each runner's leave-self-out raw day shrunk under it. The
  go-live now prints, per sport, the one-normal sd against the mixture's share and two sds and the log-likelihood
  gain ("heavy-tailed" or "about normal"), and the consistency report gains a "day in, heavy-tailed" line beside
  the ridge's. XCP_DAY_PRIOR=mixture puts it in the ratings. Same season-level centring as the ridge.
  tests/test_day_mixture.py: heavy tails found and ordinary days estimated >20% better; normal days cost nothing.
- ✅ Race titles: "Men's 5k Race - Women Boys". A college/pro field says Men/Women, a school field Boys/Girls (the
  rows' pools); nothing is appended to a division that already names a gender (app.raceTitle; track pages too).
- 🔎 Fairborn Community Park 2026 (college men's 5k): club and freshman runners rated 170-214 (an 18:58 at 172.7)
  and returning college runners unrated; Eli Whetsone (Wittenberg, a man) predicted 1st at the women's NCAA
  championships. A rating that high is another pool's scale, and a man in a women's field means his pool ends in
  _f -- the pre-09-25 XC saver wrote blank names AND genders (the re-scrape now queued repairs both).
  scripts/diag_person_pool.py "<name>" prints every profile's gender, person_gender, the season pools and the
  raw rows with their division labels, to confirm which table did it.

## 2026-10-07 — new holdouts: the distance law and the season curve; the tuning grid

- **✅ Distance holdout** (`--holdout-kind distance`, pair_validate.splitByDistance): of athlete-seasons (one sport
  each) that raced 2+ distance classes (100 m), 10% are picked and ONE class -- chosen at random -- is held out
  whole, so each held-out row is predicted from the same athlete's other distances. Its log prints "distance
  holdout BIAS" by held-out distance per pool, and by the log-distance gap to the nearest distance raced (a bias
  growing with the gap is the spline's slope; one at a single distance is that event's offset). Pipeline step
  08a_distance_holdout, in the background evidence chain (XCP_DIST_HOLDOUT=0 skips).
- **✅ The season curve, scored:** every holdout now prints the error by sport and calendar month ("by month") --
  a month whose mean runs + or - is where the form curve's shape is wrong.
- **✅ The tuning grid** (scripts/ablation_ladder.py --tune; XCP_LADDER_TUNE=1 in the pipeline): era-2 (what ships)
  against era-1, era-3, era-2-tight (0.5%/era), era-2-loose (3%), and the distance walk halved / doubled. A knob
  moves only when a neighbour beats era-2 on the race AND the forward holdout. A tuning run does not stamp the core
  ladder as done. The bracket engine's knobs are scored by 08d, not here.
- Tests: tests/test_distance_holdout.py (one whole class of multi-distance seasons only, ~10%; the gap; the
  breakdowns find a planted bias at 1600 and in September).

## 2026-10-07 — 🧪 new UI from scratch: three directions, judged blind

- Three designers built home / athlete / race / rankings from one brief (the owner's likes and dislikes, real
  saved pages for data): Editorial (warm paper, serif headlines, quiet coloured-text flags, written summary),
  Instrument (cool light grey, Inter, dense hairline tables, one cobalt accent, sparklines), App (mobile-first,
  big rating hero, PR tiles, rounded cards, ember accent, bottom tab bar). Files: scratchpad/ui/{A_editorial,
  B_instrument,C_app}/ (HTML + NOTES.md + 1280/390 screenshots).
- Blind pairwise judging: 12 comparisons (4 pages x 3 pairs), X/Y labels randomised, screenshots only, by three
  judges -- a HS runner on a phone, a coach on desktop, a design critic holding the owner's stated taste.
  Wins (of 24 each): Editorial 14, Instrument 14, App 8. By judge: runner App 8 / Instrument 3 / Editorial 1;
  coach Instrument 7 / Editorial 5 / App 0; critic Editorial 8 / Instrument 4 / App 0 (Editorial won every page
  type for the critic except rankings, where Instrument won).
- Read: no single direction wins every audience. The critic's (owner-taste) pick is Editorial; the coach wants
  Instrument's density (rows per screen, one-line rows, fixed rating column, filters up front); the runner wants
  App's big rating and PR tiles and the thumb-reach tab bar. Suggested next round: one hybrid -- Editorial's type,
  paper, summary and quiet flags; Instrument's table density and rankings; App's phone athlete header (big
  rating + PR tiles) -- then judged blind again against each parent.

## 2026-10-07 — bake-off verdict; Fairborn explained; the field outvotes a wrong division label

- **Season-number bake-off (server, 2022-2025, test half, out of sample).** The board's q80 beats the mean
  (-0.44 pts order, XC), median (-0.93) and decayed mean (-0.31) everywhere. Importance weighting: by field front,
  nothing (q80 +0.00, mean -0.32); by season-end share, the weighted MEAN edges q80 by +0.11 pts XC (88.64% vs
  88.53%), +0.10 TF, and in every pool (+0.04 to +0.45; MAE 2.65 vs 2.66), at beta 2 (a championship ~7x a mid-season
  race); the weighted q80 barely moves (+0.01/+0.04). Read: race importance is real but small -- about one in a
  hundred of the pairs q80 gets wrong. Not adopted; owner's call (it changes what the season number means).
- **Fairborn Community Park 2026, explained (diag_person_pool).** The page the owner saw is the TFRRS copy (meet
  27559 div 0) of an athletic.net race (meet 282280): the returning runners' tfrrs rows are cross-feed twins and
  unrated there (their rating is on the anet copy: Sayer 107.69, McGraw 108.02) -- the "-"s. The rated ~200s are
  tfrrs-only runners (Eli Whetsone has no athletes row at all; this "Isaiah Lanoy, Ashland (OR)" is not the
  Shawnee State one) whose gender came from the tfrrs division's label, "Women": a men's 15:17 at 206 is the
  women's college scale, and their pool ends _f, so they were predicted into the women's NCAA championship.
- **✅ person_gender: a decisive field outvotes the label.** The field vote (3+ known runners, 9 to 1) applied only
  where the label was empty; now it wins over a contradicting label too, and counts runners by PERSON (a tfrrs
  row carries no anet profile id, so a tfrrs field counted almost nobody). tests/test_person_gender_field.py adds a
  men's field labelled "Women". Takes effect at 04d; the boards follow at 07/08.
- 📋 Open: a tfrrs race page that is a twin of an anet race shows "-" for every twin; it should say so and link to
  the copy that carries the ratings.

## 2026-10-07 — ✅ the anet scrape's crash loop claimed the whole queue

Every session failed `BrowserType.launch: Target page, context or browser has
been closed`. The session loop printed "Session crashed" and went straight
back for another 50 meets. A crash mid-batch left that batch at state 3, so
104,680 rows sat "in progress" with 0 due and nothing scraped. A second fault
fed the loop: the browser was relaunched only when `browser is None` or every
`RESTART_EVERY` (200) meets, so a dead browser was kept for every new batch.

**Fixed in `scripts/launcher.py` and `scripts/database.py`:**
- `database.releaseClaims(pairs, to_state)` hands back the crashed batch's
  unreached rows. It touches only `source='anet'` rows still at 3: 0 on a
  normal run, 2 on a retry run (which claims 2).
- `_afterCrash` releases those rows, closes the browser, and sets it to None
  so the next meet relaunches. It then waits `CRASH_BACKOFF_S` (30s), doubling
  each time: 30, 60, 120, 240s, which is 7.5 min, or five 90s VPN rotation
  windows. The session stops with a printed reason at `SESSION_MAX_CRASHES`
  (5) crashes in a row. Any recorded meet resets the count.
- `restartBrowser` no longer leaks a Chrome that launched but could not open
  athletic.net.
- Test: `tests/test_launcher_crash_loop.py`.

**The stranded 104,680 need nothing extra.** A normal launcher start resets 3
to 0 (`resetInProgress`). **🔎 Open:** why Chrome stopped launching. Next
time, check `ps aux | grep -c chrome`, `free -g`, `df -h /dev/shm`. Each
session launches with `--single-process`, which Chrome does not support and
which is a known source of exactly this error under memory pressure.

**Follow-up, same day.** The relaunch kept crashing. `scripts/diag_chrome_launch.py`
(read-only) prints the machine's state and launches Chrome three ways: the
launcher's exact flags (now `launcher.CHROME_ARGS`), the same without
`--single-process`, and bare. It prints each one's full error, which the
launcher's tail cuts off. Run it with `xvfb-run -a /srv/venv/bin/python
scripts/diag_chrome_launch.py`. Also: under NO_VPN=1 the Linux rotator's
`rotate`/`checkRotation` were not no-ops. Past the rotation threshold, every
meet tried an empty pool and printed "all 0 configs failed" (seen in tfrrs).
Both now return at once. The runbooks' `xvfb-run -a scripts/launcher.py`
lines could never run (the file has no `#!`); they now name the venv python.

**Second follow-up, same day.** On the box, `diag_chrome_launch.py` launched
Chrome 153 cleanly all three ways, with 125 GB RAM, 62 GB /dev/shm, and no
leftover processes. One thing was low: open files, **1024**. Changes:
- The launcher raises its open-file limit to the hard limit at start
  (`raiseOpenFileLimit`). This is a suspect, not a proven cause: 25 Chromium
  instances launched fine at 1024 in the sandbox.
- A crash now prints Chrome's own stderr lines (`[pid=N][err] ...`) as
  `chrome:` lines, which the traceback's tail buried.
- The diagnostic also launches NUM_SESSIONS Chromes at once, before and after
  raising the limit.
- **✅ tfrrs deleted anet queue rows.** `run_tfrrs._deleteQueueRow` deleted by
  `(meet_id, sport)` with no source (since 2026-09-19). Every tfrrs event leaf
  also removed the anet row with the same id and sport, done or due. It is now
  scoped to `source='tfrrs'`. Repair for the name-repair set: the dry run of
  `scripts/requeue_blank_athletes.py` shows "not queued" per season; `--apply`
  re-adds them.

## 2026-10-07 — ✅⏳ "Unknown" on list pages for people with a name; requeue looked hung

**Unknown.** `app._athlete_lateral` is the shared name lookup behind race
pages, the boards and the other lists. It matched one `athletes` row,
`athlete_id = COALESCE(person_id, athlete_id)`. When that profile was blank,
the row read Unknown. The athlete page reads `WHERE person_id = …` across
every profile, so it showed a name from another one. The lookup now takes the
person's profiles plus the result's own athlete row, and its ORDER BY picks a
named one. It uses two indexed branches. **Needs** `idx_athletes_person_id`:
run `scripts/add_page_indexes.py`, which builds it concurrently if missing;
the athlete page already depended on this lookup. Test:
`tests/test_name_every_profile.py`. The rankings boards had their own copy
(`rankings._NAME_LATERAL`) with the same one-profile match, now fixed too.
Names are looked up when a page loads, so a restart applies the fix; no
rebuild is needed.

**Requeue "hang".** `requeue_blank_athletes.py` printed nothing until all
three scans finished, and the middle one reads every anet row of `results`
and `results_tf`. It now prints a timed line per step.
`--sport XC --since 2025` narrows the long step.

## 2026-10-07 — ✅ from the round-3 mockups: school bands on the career chart, the field on one scale

The owner rejected all three round-3 designs but kept two ideas: "putting their
school on the graph" and "the field on one scale". Both are now built into the
current site's look.
- **Athlete page, rating charts:** each school gets a band, shaded every other
  school, with a hairline at the move and the school's name at the top. A
  point's school is its **season's** majority school
  (`athlete_chart_data._seasonSchools`), so one club race or relay entry does
  not split a career. A season with no school joins its neighbour. Bands
  appear only when a career has more than one school.
- **XC race page:** above the results table, every rated finisher is a dot on
  the rating axis, stacked per whole rating point. The lit team is dark and
  sits on top of its columns. It follows team clicks and the HS/college
  toggle; hovering a dot names the runner, and clicking jumps to the row.
  Height fits the deepest column (96–160 px).
- **Fixed in passing:** teams whose name schools in two states share (Oak Park,
  Hart) have a key `school\x00state`, and the sidebar line "…'s runners are
  lit" printed the hidden separator. Both places now show only the school.
- Test: `tests/test_school_bands_field.py`. Not on track race pages yet.

## 2026-10-07 — ✅ the race page's arrangement on the meet and course pages; more colour

Owner: "I like the new race page, compared to the old one. Maybe just try to
emulate that in other places? Also I feel like we just use very little color,
not good!"
- **Colour** (`style.css`, COLOUR block; tokens `--xc --tf --m1 --m2 --m3
  --harder --easier`). Each colour has one job: green XC and orange track as
  a dot or coloured word wherever a sport is named (header tag, sport tabs,
  the athlete page's section heads); gold, silver and bronze discs for places
  1–3 in results and records tables; course difficulty and the race-day word
  red when harder or slower, teal when easier or faster (`_explain.dv`, the
  facts line); the lit team gold everywhere, including the field plot. No
  filled badges, no bands.
- **Meet pages** (`meet.html`, `meet_tf.html`): race-page header (sport tag,
  name, course and date, a facts line), then a tab bar (Races | Compiled, or
  Events | Team scores), the predict or compiled link, a finder, and the
  table first. The winner cell carries a gold disc.
- **Course page** (`course.html`): header with the difficulty; the distance
  picker under it; tabs for Best ratings / Team bests / Distances (overview)
  or Records / Team records / Best ratings (one distance), plus Year by year,
  Meets, and "On the track" (the ruler, as on the race page). Boys and Girls
  tables stack full width inside the layout; side by side they truncated
  every name and school.
- `race-page.js` defaults to the first **tab**. It used to assume "results"
  and would have hidden every panel on a page without one.
- Checked with real templates rendered against sample data, at 1280 and 390,
  with no page-level horizontal scroll.
- **School page** (`school.html`): race-page header (sport tag, crest and
  name, facts row: state, athletes, years racing; School PRs and Recruiting
  links), the sport and season pickers on one row, then tabs for Roster /
  Best athletes / Best performances / Races. Medal discs on the two "best"
  tables. On a phone, wide tables scroll inside themselves.
- **Athlete header** (`athlete.html`): left has name, school line, and Athlete
  rating and Best race as large label-over-value numbers (best race with its
  sport). Right has one sentence saying what the numbers mean (season,
  percentile, where and when the best race was) without repeating them, the
  races and seasons facts, and the links plus Share. The stat tiles are gone;
  everything they held appears once. The sport tabs carry the sport dots.
