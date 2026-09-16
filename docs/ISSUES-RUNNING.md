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

⏳ **What is left, and it is the owner's own sentence:** *"The anet pools should
match our school pools. If they don't, separate them."* `school_logo`,
`school_identity` and `/school/<name>` are keyed on `(school, state)`, which
**cannot hold two institutions** — one key, one crest, one page for Amherst
College and Amherst Regional. The key has to carry the level:
`(school, state, level)`. `school_level` already computes exactly that triple
and marks a primary. That change touches the crest key, the school route, the
search index and meet scoring, so it is the next piece of work rather than a
patch inside this one.

Also still open from the owner's list: **coalescing an athlete's season onto one
team** when they appear under similar names with different ids, and the
`link_tfrrs_to_anet` output being wired in as the authority above the directory.
