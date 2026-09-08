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
| 2 | `python racecast/build_team_season.py` — read the `restated` line | you |
| 3 | `CREATE INDEX CONCURRENTLY IF NOT EXISTS rr_pool_year_dist_idx ON ranking_results (pool, year, distance);` | you |
| 4 | `python engine/wheelchair_flag.py --write` — read the `carried forward` line | you |
| 5 | Paste the two athlete dumps (§6) so the 230 and the chair case can be closed | you |

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

### ⏳ 1.6 Chair athletes keep un-detecting themselves (14) — **the real one**
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

**Run `engine/wheelchair_flag.py --write` and read the `carried forward`
line — a nonzero count is this bug, quantified.**

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

### 💤 1.9 The solve is slow (8h 14m)
`XCP_THREADS` defaults to 8 on a 32-core box. `rowPrediction` splits ~60M
**rows** across threads and is 0.9s of every 1.2s iteration — it scales with
cores, unlike the reductions. **Try `XCP_THREADS=24`.** `d744086`

---

## 2. THE SITE — BOARDS

### ✅ 2.1 One squad ranked as several teams
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

*Index added; build it now with `CREATE INDEX CONCURRENTLY`.* `4882c27`

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

### 🔎 5.6 Race-day tilt still visible for both sports
`--race-effect-sports` defaults to **none** for both, and `run_pipeline.sh`
does not pass it. Unresolved: whether what you are seeing is the **hover**,
which shows the term deliberately even when it is out of the rating.
**Settle it with:** `grep "race-day term" logs/20260908_124500/08_golive.log`
— both lines should read `OUT OF the rating`.

---

## 6. WHAT I NEED FROM YOU

`racecast.co` is blocked from my sandbox by the network egress proxy, so I
cannot open the athlete links. These two queries give me the same thing:

```sh
# the 230 season -- 23965611
scripts/q "SELECT r.date, m.meet_name, r.event_short, r.time_seconds,
                  r.speed_rating, r.rating_pool, r.normalized_time
           FROM results_tf r LEFT JOIN meets_tf m USING (meet_id)
           WHERE r.person_id = 23965611 ORDER BY r.date" > /tmp/a.txt
scripts/q "SELECT year, sport, pool, mean_rating, best_rating, n_races, grade
           FROM athlete_season WHERE person_id = 23965611 ORDER BY year" >> /tmp/a.txt

# the chair athlete -- 26155532
scripts/q "SELECT * FROM wheelchair_person WHERE person_id = 26155532"
scripts/q "SELECT r.date, m.division, r.event_short, r.speed_rating
           FROM results_tf r LEFT JOIN meets m ON m.div_id = r.div_id
           WHERE r.person_id = 26155532 ORDER BY r.date LIMIT 40"
```

For the chair one the question is specific: **is 26155532 in
`wheelchair_person` at all**, and if not, do any of their rows still carry a
label the regex would match? That separates "the evidence was deleted" (§1.6,
fixed) from "the rule never matched them" (a different bug).
