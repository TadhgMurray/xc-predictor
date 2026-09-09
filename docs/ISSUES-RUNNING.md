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
