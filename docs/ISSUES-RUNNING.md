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
| 4 | `engine/wheelchair_flag.py --write` — **run it again**: §1.10 finds the anet chair divisions, §1.11 makes it finish | you |
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

### 🔎 5.1 The 230/235 season — **next**
> *"235 isn't crazy because of anything mechanically, it's bcs of something
> we're doing extra, not something we're missing."*

Taking that steer: the candidates are the winter-gain band shift
(`XCP_WINTER_GAIN_BANDS`), the amplitude tilt (`AMP_TILT_PER_POINT`) and the
distance offset — all things applied *on top of* the solve. Needs the athlete
dump (§6).

### 💤 5.2 TF overrated vs XC (the sport gap)
Your read. Note it may be the same coin as 5.3: a systematic XC-difficulty
compression shows up as "TF looks overrated relative to XC".

### 💤 5.3 XC course difficulty compression
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
