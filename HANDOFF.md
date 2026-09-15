# Handoff — 2026-09-13 (the bracket engine goes main; run 22)

Read this first. `docs/HANDOFF-2026-09-11.md` is the two-day log it
replaces, kept for history (the taper and field terms, the indoor
assertion, the era split, each crash and fix, in the order they
happened). The engine's own description is `docs/ENGINE.md`; the survey
of other systems is `docs/RESEARCH-ENGINE-2026-09-11.md`. Everything
below is on `master` (and on `claude/youthful-gates-o25yq9`, the same
commits). Nothing here ran against the database from the sandbox; the
server runs are the owner's, and their logs are quoted.

---

## 1. WHERE THINGS STAND

- **Live on the site:** run 21b, the joint solve with two-year eras,
  the field term off, the indoor level asserted at +0.3%, the sport
  level asserted at 0.0583, no day term in ratings.
- **Decided:** the owner's method, the bracket engine, publishes the
  course difficulties from run 22 on (`XCP_DIFFICULTY=bracket`). The
  joint solve keeps everything else: the season curve, the tilt, the
  event distance offsets, the altitude credit, the sport level, the
  race-day terms, the abilities' machinery. The owner's words: "the
  bracket engine could help ... specifically the course ordering, so
  we'd only have to worry about the cross-sport ordering and the
  distance spline."
- **Why, in one number:** on the 537,107 held-out rows both engines
  cover, with the joint model's own per-row predictions aligned to the
  pack, the bracket engine's error sd is 0.0423 against 0.0472, and it
  wins in both sports and in every band of course thickness
  (`logs/bracket_holdout.txt`, "SAME ROWS, BOTH ENGINES").
- **Not yet run:** run 22 itself.

## 2. THE COMMANDS

Run 22, in tmux, from step 8 on the run-19 pack. The holdout and the
ladder are 6 to 8 more hours that do not change the site; skip them:

```
cd /srv/xc-predictor && git pull
set -a; . /etc/xc-predictor.env; set +a
XCP_SPORT_LEVEL=0.0583 XCP_ERA_YEARS=2 XCP_ALTITUDE=1 XCP_INDOOR_LEVEL=0.003 \
XCP_IMPORTANCE=none XCP_DIFFICULTY=bracket \
bash deploy/run_pipeline.sh --from 8 --skip 08a_holdout,08b_ladder 2>&1 | tee logs/run22.out
```

The solve is the hours (about 2.6 at five outer passes); everything
after it is minutes. The solve's whole output is written to
`engine/data/joint_difficulty_state.npz` the moment it exists. Any later
change to the engine's knobs or the go-live reruns from there, no solve:

```
XCP_FROM_STATE=engine/data/joint_difficulty_state.npz XCP_DIFFICULTY=bracket \
XCP_SPORT_LEVEL=0.0583 XCP_ERA_YEARS=2 XCP_ALTITUDE=1 XCP_INDOOR_LEVEL=0.003 XCP_IMPORTANCE=none \
bash deploy/run_pipeline.sh --from 8 --skip 08a_holdout,08b_ladder
```

The flags must match the solve's; the state is checked against the
design (rows, athlete-seasons, cells) before anything runs.

After the run, what to read:

```
grep -A6 "difficulty = BRACKET ENGINE" logs/run22.out     # the swap's report
/srv/venv/bin/python scripts/diagnose.py --era-years 2 --out-dir logs \
    --venue Glendoveer --venue "Foot Locker"               # all four diagnostics, ~2.5 min
grep -A12 "SAME ROWS" logs/bracket_holdout.txt
/srv/venv/bin/python scripts/meet_cells.py --meet "Foot Locker" --meet "Champs Sports" --meet Eastbay
/srv/venv/bin/python scripts/meet_cells.py --tf-loc 99426 --tf-loc 88489
```

Run scripts with `/srv/venv/bin/python` or `$PY` set; `diagnose.py`
re-executes itself under the venv if it lands on the system python.
When the pipeline says another run holds `.pipeline.lock`, the holder's
pid is the last field of the FLOCK line in `/proc/locks` (`fuser` is not
installed).

## 3. HOW THE ENGINE WORKS NOW

**The joint solve** (`engine/joint_solve.py`, `engine/run_joint.py`)
fits, on 62.8M rows: one ability per athlete-season, a difficulty per
(course, two-year era) cell tied to its neighbours by a random walk, a
race-day term per (cell, day), the season form curve per pool, the tilt
(a fast runner pays less of a course), the track event offsets by
rating band, the altitude credit, and asserted levels for XC against
track (0.0583) and indoor against outdoor (+0.3%). The field-strength
term exists (`--importance field`) and is off (`none`).

**The bracket engine** (`engine/bracket_engine.py`) is the owner's
method as a solve. For each row, the athlete's level is read off their
own other races within 21 days, each less the difficulty of its course.
A race's difficulty is the mean over its top half by rating of (time
less level) over the tilt. A (course, era) cell is the weighted mean of
its races. The level is pinned per sport and era. Iterated to a fixed
point with damping 0.5 (two cells whose runners race only each other
swap for ever under a full step).

**A race is the unit of evidence** (owner: "a 10 result venue should
not have +17%"). A race weighs n/(n+5) voters, so two hundred finishers
count about one race; a race needs three voters to count. A course is
pulled toward its sport's average with one race's worth of prior (seen
once keeps half, three times 75%, ten times 90%); an era of a course
toward the course's history with two. All stated constants, printed.

**The switch** (`run_joint.bracketDifficulties`, `--difficulty bracket`).
After the solve, every term but the course, the day and the ability is
taken off each row, and the engine is fitted on that residual over the
design's cells with the solve's ratings and tilt, so the curve, the
event offsets, the altitude and the sport level are not left for a
course to absorb. Its numbers are recentred as the solve centres its
own (outdoor cells' unweighted mean per sport is the zero; indoor cells
keep their level), the sport level is added back, each ability becomes
the solve's weighted mean of its rows against the new courses (the
ability block has no penalty, so that is the solve's ability given
these courses), ratings follow, and the cell variance is the fitted
race-day variance over the races behind the cell. The go-live publishes
the result through the same code. The npz carries `difficulty_source`,
the joint's numbers as `delta_joint`, and the votes and races per cell.
Cells with no race of three voters sit at their sport's average; the
report prints how many. If the swap throws, the joint's courses are
published so the run completes, and the swap is redone from the state
file.

**The go-live** (`engine/joint_golive.py`) publishes each course's
latest solved era under its bare key, rates each result from its own
row less the course (and the event offset, altitude, and the day term
only for sports named in `race_effect_sports`), and derives season
ratings from abilities.

## 4. WHAT WAS MEASURED THIS WEEK, WITH THE NUMBERS

- **Two engines, same rows.** Bracket 0.0423 / joint 0.0472 overall; XC
  0.0464 / 0.0511; TF 0.0372 / 0.0421. By training races behind the
  course: 1 race 0.0576 / 0.0606; 2-3 0.0500 / 0.0537; 4-9 0.0472 /
  0.0522; 10-29 0.0420 / 0.0471; 30+ 0.0378 / 0.0429. The first
  comparison read 35% overlap and 0.0625 for the joint because the
  solve sorts the pack before its holdout and the dump indexed sorted
  rows; the dump carries file row numbers now and the comparison
  refuses to run unless the dump's own times land on the rows they map
  to.
- **Indoor is barely harder than outdoor.** Same athlete-season, same
  distance, read at a gap of zero days (raw medians against the median
  gap, one point per window): HS boys 0 to +0.5% (800 m most, 3200 m
  least), HS girls about +0.3%, college about 0, middle-school girls
  higher on thin samples. The +1.2% assertion credited an indoor mark
  0.9% too much; run 21 asserted +0.3%. Fitting it is not an option:
  with a free winter curve the level and the curve are collinear on
  every indoor row. Under the bracket path the level is MEASURED from
  the March transitions, and on the planted world the engine read the
  indoor courses within 0.5% where the joint solve, pinning their mean
  to the assertion, was off by 4.7%.
- **Outdoor tracks: the meet mix is not why they differ.** Across 8,207
  tracks the board spread is 0.98% sd, the same-athlete bracket spread
  0.69%, correlation +0.43; front, championship share and league share
  explain 4% of the board. Within a track, championship rows run 0.25%
  faster than ordinary rows and a field 13 points stronger 0.5% faster.
  The bottom of the board (cells at -6 to -12% with brackets near zero,
  ids `TF:loc:994xx:out` and `TF:loc:88489:out`) is a distance that lies,
  most likely 1500/3000 booked as 1600/3200 (6.8%). Not fixed.
- **Glendoveer is consistent with its runners.** Its cell is NXN. The
  runners run 3.5% slower there than at their regionals three weeks
  before; those regionals sit at +5 to +7; bracket plus reference lands
  within 1% of board plus day every year. If NXN reads too high on the
  site, the question is the championship cluster's level, not Glendoveer.
- **Foot Locker is keyed in pieces.** `XC:22029` holds three race days
  in twelve years; the rest of the regional and the national final sit
  under other canonical ids. `scripts/meet_cells.py --meet` lists them.
  Not merged.
- **Two data faults the day term absorbed:** Brookside Reservation
  3200 m in 2024 at -20.7% (a short course), one 12-row Brooks City
  Base day at +31%.
- **The diagnostics run in 150 s on the corpus** (79 s the disk load) in
  one process, `scripts/diagnose.py`; the four scripts still run alone.
  The old `np.unique(axis=0)` race coding was ninety seconds per script.

## 5. OPEN, IN THE ORDER THE OWNER RAISED THEM

1. **Run 22, then read the swap's block** (corr with the joint's
   courses, moves by races behind the course, ability shift) and the
   published indoor cells' mean against `diagnose.py --only indoor`. If
   they disagree by more than half a percent, pin the engine's indoor
   level the way the solve pins its own.
2. **"TF seems underrated"**: the cross-sport level. The sport level is
   asserted (`XCP_SPORT_LEVEL=0.0583`, track this much faster than an
   average XC course); the bracket engine pins each sport's mean and
   inherits it. A check that exists: the go-live's winter-gain table
   and `scripts/diagnose.py`'s athletes with both sports. A check that
   does not: the same-athlete bracket across the November-to-March
   seam, which the curve makes hard.
3. **"800s/1600s overrated compared to longer distances"**: the event
   offsets (`dist_offset`, by pool and rating band). The same-athlete
   design that settled indoor would settle this: 800 against 1600
   against 3200 for the same athlete-season inside a window, with the
   curve taken out. Not built; one more grouping in
   `indoor_outdoor_check.py`'s machinery.
4. **Track cells at -7% with brackets of zero**: look them up
   (`meet_cells.py --tf-loc`), fix the booked distances, and consider a
   stated prior of half a percent on outdoor track cells.
5. **Foot Locker's cells**: merge or key the national final and the
   regionals so one venue is one cell.
6. **The holdout and the ladder** for run 22's model, when there is
   time (`--from 8a`, or the pipeline without `--skip`). The same-rows
   comparison then reads the joint's holdout against the engine as
   fitted on run 22's solve.
7. **Indoor per facility class** (banked 200, flat 200, 160, 300): the
   prior mean of a thin indoor cell by its geometry, from `meets_tf`'s
   track type and length. Under the bracket path each facility already
   has its own number; this is only about thin ones.

## 6. WHERE THE CODE IS, AND THE TESTS

- `engine/bracket.py`: the shared window bracket, `packCodes` (the
  athlete-season, race and solve-cell codes over the whole pack, so any
  subset of rows indexes the solve file), `cellsFromKeys`, the dense
  sample helpers, `loadInputs`.
- `engine/bracket_engine.py`: the engine (`fit`, `predict`, the priors).
- `engine/run_joint.py`: `bracketDifficulties`, `saveState` /
  `loadState` / `checkState`, `guardedReport`, `pairEngineGap`, the
  holdout's per-row dump (`XCP_HOLDOUT_DUMP`), `raceCodes` with one
  composite key.
- `scripts/diagnose.py`: the four diagnostics in one process;
  `scripts/course_bracket.py`, `indoor_outdoor_check.py`,
  `track_variance.py`, `bracket_holdout.py` alone; `scripts/meet_cells.py`
  the meet-to-cell and track-location lookups (database).
- `deploy/run_pipeline.sh`: `XCP_DIFFICULTY`, `XCP_FROM_STATE`,
  `XCP_INDOOR_LEVEL`, `XCP_IMPORTANCE`, `XCP_ERA_YEARS`, `XCP_ERA_DRIFT`;
  step `08d_diagnose` runs the three diagnostics that need no names.
- Tests: `tests/test_bracket*.py`, `tests/test_diagnose.py`,
  `tests/test_era_publish.py`, `tests/test_track_diagnostics.py`,
  `tests/test_course_bracket.py`. `python -m pytest -q tests/` as a
  whole does not run here: about forty test files are scripts that call
  `sys.exit` at import and two need the database or flask; run the
  engine files by name, or ignore those. Six failures elsewhere
  (`test_hs_factor_per_pool`, `test_meet_units`, `test_pager_last`,
  `test_pipeline_shards`, `test_pipeline_step_guards`, `test_sport_gain`)
  predate this work and fail on the unchanged tree too.

## 7. CONVENTIONS

Commit on `claude/youthful-gates-o25yq9`, then fast-forward `master`
and push both (another session pushes to master too; merge it in first
when it moved). Tests before every push, checking pytest's exit code,
never through `tail`. Commit trailers as in the log. No model
identifiers in files. The pipeline takes one lock; `tmux kill-server`
does not release it if a child survived.

---

## 8. 2026-09-13, SECOND SESSION: THE RUN-22 COMPLAINTS, DIAGNOSED

Nothing here ran against the database or a pack; every fix is code with
a planted-world test, and each one names the command that shows it on
the box. In the order the owner raised them.

### 8.1 "TF difficulty weirder now, too much variance; shrink outdoor, let indoor keep its difficulty"

**Mechanism.** The bracket engine pulled every course toward its
sport's average with ONE race's worth of prior, sized for grass, where a
course's day-to-day spread (3.05%) and the course-to-course spread
(3.64%) are alike. On an outdoor track they are not: day sd 1.45%
against a course spread of 0.89%, so a one-race track kept half of one
day when the arithmetic says a quarter, and the TF board's scatter was
weather and fields, not tracks. Indoors the differences are real (banked
against flat, 160 against 300) and the weather is not.

**Fix** (`engine/bracket_engine.py`): the prior is per group -- XC,
outdoor track, indoor track -- and is the ratio of the two variances,
FITTED from the courses with 2+ races after six warm-up passes on the
stated values (`PRIOR_GROUP_BY = XC 1.0, TF:out 2.5, TF:in 1.0`), clipped
to 0.25-8 races, printed with both variances. Each group is pulled
toward ITS OWN average, so a thin oval sits at the indoor level, not the
outdoor zero. `XCP_BRACKET_PRIOR` states it instead (`fit`, one number,
or `XC=1,TF:out=2.5,TF:in=1`). Planted worlds: grass at the corpus'
numbers fits about one race; a track world fits several and its board
lands nearer the truth than one race does; the once-raced oval sits with
the other ovals. The run's log prints one line per group under
`[joint] bracket prior:`; read those three numbers first.

### 8.2 Foot Locker against Glendoveer; "the hardest venues seem overstated"; "we built diagnostics and aren't using them"

**What the engine does with a bracket, and why the two venues part.**
A race's reading is `bracket / h + the board of the races it was
measured against`. Glendoveer's runners are read against NXR and state
courses the board has at +5 to +7; Balboa's against the Foot Locker
regionals (McAlpine fast, Van Cortlandt and Mt. SAC hard) -- a different
reference set, so equal raw brackets do not mean equal published
numbers. Then the priors: Glendoveer is twenty race days under one key
and keeps ~95% of its reading; Foot Locker's national final is split
across canonical ids with one to three race days each, and a cell with
one race keeps about 60% of it (era prior 2 races toward a history that
is itself shrunk). Merging the ids (`scripts/meet_cells.py --meet "Foot
Locker"`, open item 5 of section 5) is still the fix for that half.

**The tilt is the suspect for the hardest venues.** Every reading is
divided by the applied tilt h(rating); the national and sectional
courses are read almost entirely through 140-160 rated voters, and h
there is EXTRAPOLATED from a slope measured at 70-140 (a 150 pays 84.5%
of a course). If the true line is flatter above 140, every course read
by the elite is inflated by the same ratio, and the hard ones show it
most -- exactly the list (Mt. SAC, Crystal Springs, Glendoveer). The
run now measures it: `[joint] bracket tilt by band` regresses each
voter's own untilted bracket on the course's fitted D per sport and
rating band (`bracket_engine.tiltByBand`), beside the h applied. Implied
below applied at 140+ means the elite pay less of a course than charged;
the fix is then `TILT_K` above 140 (a second slope), not the courses.

**The diagnostics are now the engine's own arithmetic.** Under
`--difficulty bracket` the solve file carries, per cell, the era's raw
vote-mean, the course's history after the group prior and its votes,
the (sport, era) pin, the recentring and the published number
(`bracket_cell_raw`, `bracket_base`, `bracket_base_votes`, `bracket_pin`,
`bracket_shift`, `bracket_cell_fit`, `bracket_prior_group`), and
`scripts/course_bracket.py` (so `diagnose.py --venue`) prints, per race
day, the engine's voters, the tilt applied and its reading beside the
raw bracket, then the chain raw -> history -> era -> pin -> recentre ->
published per cell. `tests/test_course_bracket.py` proves the chain
rebuilds the published number and the readings match the engine's.

```
/srv/venv/bin/python scripts/diagnose.py --era-years 2 --out-dir logs \
    --venue Glendoveer --venue "Foot Locker" --venue Balboa --venue "Mt. SAC"
```

### 8.3 "TF underrated" (the cross-sport level)

Still an assertion (`XCP_SPORT_LEVEL=0.0583`), and nothing in the
bracket engine can measure it: references never cross sports (an
athlete-season's key is sport-specific) and November to March is beyond
any window. Two things move an elite athlete's XC and TF ratings apart
by band without the level changing: the tilt (an elite pays h x 0.0583
for the sport level, less than a 100 does, so if h is over-tilted above
140 the elite's TF sits low against their XC), and the 800/1600 offsets.
Read the tilt table (8.2) before the level.

### 8.4 The 200s (Leo and Lex Young), "still happening"

**Mechanism.** `rating = 100 x pool_mean / normalized_time`; those rows
were normalised as hs_m (5000 m scale) and rated as college_m (8000 m
pool mean): x1.61. `engine/anchor_repair.py` rewrites the DATABASE
column toward the pool the LAST go-live rated the row in, at step 5 --
which never reaches a pack built earlier, and every run since 19 has
been `--from 8` on the run-19 pack.

**Fix** (`engine/speed_ratings.py::rescaleToPool`, the loader now
carries `time_seconds`): the pack itself puts every row on the scale of
the pool it decides for that row, in memory, by the same arithmetic as
the repair (the stored value's scale identified within 3%, only the pool
factor swapped, corrections kept; unidentified rows left alone and
counted). The pack's census prints `rescaled` and `scale_not_identified`.
Takes effect at the next pack (`--from 7`), which the next full run
does; `--from 8` cannot see it.

### 8.5 Distance normalization: it IS off, at the long end

`scripts/distance_curve_check.py` prints the shipped curve's local
exponent between events. Read from the artifact today: college_m|XC
8000->10000 at **0.92**, elem_m|XC past 3200 at **0.98** -- a runner
whose pace speeds up with distance, which no runner does, and the "10k
too low" symptom on the college boards (a 30:35 10k read as a worse 8k
than the runner's own 24:00). Same-athlete XC pairs do not cancel the
calendar (the 10k is the November championship) or the course. The
fitter (`engine/fit_distance_exponent.py`) now holds every curve to a
local exponent of at least `MIN_LOCAL_EXP = 1.04` (`--min-exponent`;
the health line says which segments it raised). The shipped pickle is
unchanged until refitted:

```
/srv/venv/bin/python engine/fit_distance_exponent.py            # rewrites engine/data/distance_spline.pkl
/srv/venv/bin/python scripts/distance_curve_check.py           # 0 segments flagged
bash deploy/run_pipeline.sh --from 5                            # backfill both sports, repack, solve
```

The TF short end (800->1000 at 1.15-1.18, 1600->3200 at 1.10-1.13) is
a population relation that the solve's per-band event offsets sit on
top of; that is the "800s/1600s overrated" question (section 5, item 3)
and is not changed here.

### 8.6 Search results landing on the wrong meet (NXN -> NXN South -> Hudson Valley Sportsdome)

The anet and tfrrs meet ids collide (15,096 ids in `results`), the
search index keyed on `meet_id` alone with `min(meet_name)` across both
meets, and its bare link resolved on the meet page to whichever meet had
more rows. Now `meet_agg_xc/tf` are keyed `(meet_id, source)`, named as
the page names them (`meets_tfrrs` for tfrrs), each link carries the
page's own `?alt=` (ranked exactly as `app.meet_sources` ranks: biggest
first, `source` as the tie-break, which it lacked), the XC race page
resolves the same `alt` and filters its header and results by source
(both meets' finishers were merged into one table before), the meet
page's division links and the race page's meet link carry it, and the
sitemap lists both meets. Rebuilds at `13c_search_index`.

### 8.7 "New images make page loads super slow" / "parts of the pipeline make the website super slow"

Pipeline, the must-fix: `build_school_units` DROPPED the live
`school_unit` and rebuilt it with ten index builds inside one
transaction, so every filtered board request queued behind the DROP
for the build and died on its 5 s lock_timeout as a 500;
`build_course_rank` built three indexes on the live name after the
rename; `build_course_boards`, `build_team_season`, `build_meet_units`
and the search index's meet aggregates swapped with no lock_timeout
(one long bot read, and every later reader queues). All six swap through
`dbfast.swapTable` now: shadow built and indexed with the live table
untouched, then one short transaction under `SET LOCAL lock_timeout
'5s'`, retried 24 times. And every pipeline step runs under `nice -n 10
ionice -c2 -n7` with BLAS capped at cores-2 threads
(`XCP_NICE`, `XCP_THREADS`) -- the solve competed with gunicorn as an
equal for hours. Not done, worth doing: the site's
`XCP_DB_STATEMENT_TIMEOUT_MS=55000` is what lets one reader hold a
table long enough for the swap siege to reach its terminate phase; 10-15 s
would let the polite rounds win.

Images: the home page shipped 8.6 MB of PNG because the template
preferred `.webp` files that did not exist -- they exist now
(`racecast/static/m1..m9.webp`, 528 KB in all, stamped and immutable
through `static_v`). Every crest was a request into a sync worker that
opened a pooled connection and ran a query before sending the file;
`/img/school/` now answers from the start-up cache (`school_logo.
crestPath`, no query), the cache skips a row whose PNG the disk no
longer has (each such row used to draw a tag that 404'd, uncached, on
every page naming the school), a 404 is cacheable for five minutes, a
`?v=` crest is immutable for a year, and `/img/` is exempt from the
maintenance 503. Still open: serve `/img/school/` from nginx (an alias
onto the logo directory) so Python never sees a crest at all.

### 8.8 Tests

`tests/test_bracket_engine.py` (priors, the oval, the spec),
`test_bracket_golive.py` (the chain rebuilds the published number),
`test_course_bracket.py` (the engine block), `test_pack_scale.py`,
`test_search_meet_alt.py`, `test_distance_curve_floor.py`, plus the
existing bracket, diagnose, era, track, logo, sitemap and guard files:
314 pass. `test_board_build_streams_the_sports_in_parallel` and the
`test_page_cache_headers` order-dependence fail on the unchanged tree too.

### 8.9 "Track difficulties are a lot more negative for college than for hs"

**Mechanism, and why it is not the tracks.** A college athlete-season
references only college races and a high-school one only high-school
races, so the level between the college-only ovals and the high-school-
only ovals rests on whatever links them: the tracks that host both. On
those, the college rows are mostly the conference or NCAA final
(tapered, stacked, fast) against the same athletes' invitationals
elsewhere; the engine names no taper and no field, so the difference
lands in the venues and the whole college cluster reads "easy". A
constant shift of one pool's cells never moves a rating inside that
pool (its pool mean moves with them), so what it costs is the board's
number for every college track and every conversion read off one.

**Fix** (`run_joint.trackPopulationShift`, on by default under the
bracket path, `XCP_TRACK_LEVEL_BY_POOL=0` leaves it): a cell's host
population is the level (hs, college, ms, elem) of the majority of its
rows (under 60% is "mixed"); each population's outdoor tracks are
recentred to the same zero, its indoor ovals move with it so the
indoor level is kept. The log prints, per population, the mean before,
the sd and the championship-class share of its rows -- the "something
else" made visible -- and the shift applied:

```
[joint] track level by host population ...
        population  outdoor indoor mean before     sd champ share   shift
```

If the college row's `mean before` is a couple of percent under the hs
row's and its champ share is several times higher, that is the whole
story. Physics says a 400 m track is a 400 m track, which is what the
recentring asserts.

### 8.10 Distance normalization: how to "finally get this right"

What a rating applies is the spline TIMES the solve's fitted event
offsets (per pool, distance and rating band, `dist_offset` in the solve
file), and only that product is worth arguing about. So:

```
/srv/venv/bin/python scripts/distance_curve_check.py                # spline alone, every pool
/srv/venv/bin/python scripts/distance_curve_check.py --npz engine/data/joint_difficulty.npz
```

The second prints, for each TF pool, the spline's local exponent
between events and then the EFFECTIVE exponent per rating band (spline
plus offsets: what the boards use). Read it against three outside
numbers: Riegel 1.06 for a trained runner, the 1500 -> mile at 1.077
(3:35 is 3:51.5), and 5000 -> 10000 at 1.06-1.07. A band whose 800 ->
1600 effective exponent sits far from the others is "800s overrated"
in one number, and the fix is then in the offsets' prior
(`engine/distance_tables.py`) or the fitter's pairs, not in a hunch.

The principled end state is unchanged (ENGINE.md 11.6): refit the track
curve on equal-quality pairs with level and sex inside it and retire
the offsets. Until then the floor (8.5) stops the curve saying things
no runner does, and this table says what the composite does.

### 8.11 "A better way to decide difficulty" when a venue is keyed in pieces

The engine's number for Foot Locker is right for what it was given: one
to three race days per canonical id. No estimator fixes evidence it is
not shown. Two routes, cheapest first:

1. **Key the pieces together.** `engine/course_merge.py` already
   collapses XC course names at one coordinate with a protected list
   for deliberate splits (the Mt. SAC rain course); `location_merge.py`
   does the same for duplicate TF location ids. `scripts/meet_cells.py
   --meet "Foot Locker" --meet "Champs Sports" --meet Eastbay --meet
   "Brooks"` lists the ids; if they sit at one coordinate and one
   distance, the merge tool is the fix and the engine needs nothing.
2. **A place prior in the engine.** Where the ids are legitimately
   different courses at one park (Balboa's loops), the honest structure
   is one level up from the era prior: cells within a few hundred
   metres and at one distance pulled toward each other's mean with a
   stated weight, so a thin id rests on its neighbours before it rests
   on the sport's average. That needs the venue's coordinates in the
   pack (a column at 07_pack from `course_canonical`), then one more
   `bincount` in `bracket_engine.fit` beside `base_of_cell`. Not built;
   build it only if route 1 leaves real splits behind.

What is NOT a better way: a stronger era prior or a weaker group prior.
Either moves every venue to help one.

---

## 9. 2026-09-14: THE PLACE PRIOR, THE POOLING REDO, THE EVENT CHECK, THE TEAM

### 9.1 The place prior (built)

The pack carries one (lat, lon) per course key (`speed_ratings.
attachCourseCoords`: an XC key's canonical id through `course_canonical`,
a TF key's location through `meets_tf`; the census line says the share
covered). In the engine (`bracket_engine.placeClusters`) courses of one
kind -- an XC distance, a TF surface -- within `PLACE_RADIUS_M` (400 m)
form a place, and a course rests on its place by `PRIOR_PLACE` (2 races'
worth) before it rests on the sport's average: the place's reading is its
members' vote-weighted mean shrunk toward the group by the group prior.
A place of one is the group prior exactly as before; a deliberate split
at one point (Mt. SAC's rain course) stays its own cell, twenty race days
outvoting the pull. Planted: the once-raced id beside a thirty-race +8%
course lands above +6.5 where the far one keeps about half. Flags
`--bracket-place-radius` (0 off) / `--bracket-place-prior`, env
`XCP_BRACKET_PLACE_RADIUS` / `XCP_BRACKET_PLACE_PRIOR`. The trace
(`course_bracket.py`) prints each cell's place as `#id(n)`. **Needs a
pack built at 07 from now on**; an old pack has no coordinates and the
log says "no places".

### 9.2 The pooling redo: the feed's word on the team

The loader now carries `team_id` (anet) and `team_slug` (tfrrs) -- NULLs
where a database lacks the columns. At pack time
`speed_ratings_db.loadTeamLevels` reads `anet_team` and LEARNS what each
anet level code means from our own rows' grades (a code whose rows carry
9-12 is hs, 6-8 ms, 1-5 elem; a gradeless code is college when a quarter
of its teams are schools tfrrs calls colleges, else club) and prints the
table; `XCP_ANET_LEVELS="4=college,5=club"` states a code outright. Read
that table on the first run: if a code is named wrong, state it.

`pool_resolve.resolvePool(team_level=...)`: a gradeless row on a **club**
is professional (repooled pro, as pro_flag does), so Nike Swoosh TC,
ASICS Furman Elite, Nomad, UA Baltimore, Melbourne TC leave the college
board when their anet team is a club; a club runner with a school grade
keeps the grade's pool; a **college** team's row is a college season
(the field rule's own outcome, now from the team). A tfrrs slug's
level token does the same for tfrrs rows. "Great Britain & N.I." and
"unattached" rows have no team level: national teams and unattached
professionals are still the hand list (`_PRO_SEASONS`) and pro_flag;
a country list would be the general fix.

### 9.3 The event check: is the 800 or the 10k overrated?

```
/srv/venv/bin/python scripts/event_check.py --era-years 2
/srv/venv/bin/python scripts/event_check.py --era-years 2 --no-offsets   # the spline alone
```

Per pool and per (shorter, longer) pair of distance classes: the median,
over an athlete-season's rows at the longer event, of the fully adjusted
log time (log normalized time less the curve, the tilted course and the
solve's event offset for the row's band -- the rating's own arithmetic)
minus the mean at the shorter event within 21 days; overall and per
rating band; in log % and points at 130. Positive = the longer race rates
worse for the same person (the shorter is overrated, or the longer
underrated). A pair near zero in every band is right; a pair that grows
with the band is the offsets' band shape; a pair off in every band is
the spline or its reference. Planted: a 1.5% overrated 800 reads +1.5
on 800->1600 and 800->3200 and zero on 1600->3200. That is the check.

### 9.4 The athlete's team is the latest season's, any sport, any race count

The header's rating still wants three races; the team no longer follows
it. A two-race track season at a new school now heads the page over the
last full cross country season at the old one (`racecast/app.py`,
`latest_team`).

### 9.5 Tests

`test_bracket_engine.py` (places, the thin course on its place),
`test_team_level_pool.py`, `test_event_check.py`, `test_pack_scale.py`
(the team columns), plus the earlier files. Pre-existing failures
unchanged.

### 9.6 A club with professionals has no middle schoolers

"Lots of club runners are labelled msers because they are in their 6th
pro year." An elite squad's grade 6 advances every season like a grade,
so no grade rule can tell it from a sixth grader; the club can.
`speed_ratings_db.loadClubPros` lists every team (anet id, or the school
string where there is no id) with a pro_flag professional in a season
they raced for it; on such a team, unless it is a college, a row with a
grade of 1-8 or none is repooled pro (`resolvePool(team_has_pros=)`).
A high-school grade on the same team is kept, since a sponsor's youth
squad and its elite group wear one name. The census prints
`club_with_pros_repooled_pro`. The pro team list (`_PRO_TEAMS`) and the
hand list stay as the floor under it.

**Only in a season raced mostly for the club** (owner: "a collegiate
runner running the Euros would be fine"): `speed_ratings_db.
loadClubMajority` lists the athlete-years in which more than half of
the athlete's rows, both sports together, are on a club-level team or
a team with professionals; both club rules (the club level and the
professionals) fire only there. The census prints
`club_row_in_a_school_season` for the rows the gate spared.

Foreseeable edges, in order of likelihood: a genuine seventh grader in
a club that also fields one professional is repooled pro (the rule you
asked for; the count says how many); an anet level code named wrong in
the learned table (state it with `XCP_ANET_LEVELS`); "unattached"
professionals at pro meets, still only the hand list and pro_flag; a
national team ("Great Britain & N.I.") is caught once any of its
athletes is pro-flagged, not before.

### 9.7 The spline's shape: a floor and, on the track, a non-increasing exponent

The fitter now stores every track curve with its local exponent held
NON-INCREASING with distance (pool-adjacent-violators over the segments,
after the 1.04 floor; `--monotone-sports ""` turns it off; cross country
is left alone). That is the physiology in one shape: highest at the
anaerobic end, falling to 1.06 by 5000, no bump at 600 or 1000 from a
cubic fitted on a few hundred outdoor pairs. Read before refitting:
`scripts/distance_curve_check.py --monotone TF --floor 1.04` shows what
changes -- on the shipped artifact the hs_m and college_m track curves
were already monotone, so if the 600 and 1000 still read hard, it is
their event OFFSETS (indoor rows, the +0.3% level) and not the spline;
`scripts/event_check.py` now carries a 600 class, and the 600->800 and
800->1000 rows per band are where that shows. The shape passes live in
`engine/distance_shape.py` (no fitter, no database) so the check runs
anywhere.

### 9.8 Where the code is

`master` is fast-forwarded to this branch as the repo convention says;
`git pull` on the box picks it up.

### 9.9 The event offsets are a curve: the random walk in log-distance

Read off the refit box's `distance_curve_check.py --npz`: the spline
rows are clean now (the track curves monotone, the floors binding where
the pairs said something no runner does), and every one of the 128 flags
sat in the BAND rows -- the solve's per-(pool, distance, band) event
offsets laid on the spline. Between 800, 1000 and 1500 the effective
exponent jumped by 0.1 and more in every pool and band; the 1000, the
2000 and the 6000 are thin classes, each fitted alone against a 3% prior,
and each wandered on its own. That is the jerkiness at the 600 and the
1000, and it is not the spline.

So neighbouring classes of one pool and band are now tied by a random
walk in log-distance (`js.DIST_WALK_SD` 0.02 per unit; the class beside
the pool's pinned reference is tied to its zero), the same device as
the eras' walk, in the solve's operator (`run_joint.distWalkPairs`,
`_Operator.pen_walk`). It is gentle by design: a class with thousands of
rows keeps its own number, a class with a handful rests on its
neighbours (planted: a six-row 1000 on a 5%-slow day comes home).
`--dist-walk` / `XCP_DIST_WALK` state it; 0 turns it off. If the 1000
still reads jerky with thousands of rows behind it, its rows genuinely
say so -- mostly indoor 1000s by 800/1500 types -- and
`scripts/event_check.py`'s 800->1000 row per band is the number that
decides whether that is a fair rating or a wrong one.

Also read from the refit: college_f|XC and the ms/elem XC curves came
out AT the 1.04 floor over most of their span (the pairs said less;
college_f|XC demoted to degree 1 and floored flat). Those curves are now
a stated assumption where the measurement was a confound; the
`distance_curve_check.py` spline rows say exactly where.

### 9.10 The bake-off: which distance curve is right

`scripts/curve_bakeoff.py --era-years 2` scores every candidate curve on
one number, the same-athlete gap of `event_check.py`: each track row's
adjusted log time is rebuilt with the candidate in place of the fitted
spline (the pack's normalization undone through the spline that made
it, redone through the candidate) and the medians per pool and pair are
summarised as a row-weighted mean |gap|. Candidates: `fitted` (the
shipped potential), `fitted+offsets` (what a rating applies today), `wa`
(the World Athletics 2025 tables by sex and band, from
`distance_tables.py`), `hybrid` (fitted between 800 and 3200, tables
outside), `riegel` (1.06, the straw man), `vdot` (Daniels-Gilbert).
`--all` prints every pair table; the default prints the best two. The
fitted curve was fitted on these rows' pairs, so read a narrow win for
it as a tie. Run it after the backfill and the pack (the script warns
when the spline file is newer than the pack). Planted: a 1.10 world
scores the matching curve near zero and Riegel at 2.8% a doubling.

### 9.11 The board sanity (2026-09-14, from the owner's read of the run-22 boards)

What the owner saw, and what each one was:

* **college_f headed by 170s, college_m by 160s.** Rows the backfill
  normalised on another pool's scale (a hs row is 5000-equivalent; a
  college_f row 6000-equivalent: x1.21; college_m x1.65) rated against
  the college mean. Two causes, both fixed: (a) `build_ranking_results`
  re-resolved the pool from its own joins and ignored the row's
  `rating_pool`, so a row the engine rated as `pro_f` was ranked as
  `college_f`; (b) the pack's scale identification (`rescaleToPool`)
  only knew the row's own gender-and-level pools, so a hs_m row landing
  in college_m was left on the hs scale.
* **Clubs with professionals on the college board.** The club rules of
  9.6 keyed on the anet team level and `pro_flag`; tfrrs club rows carry
  neither. `speed_ratings_db.loadClubTeams` now reads clubs off the rows
  themselves: a team string that is not a college (college_directory,
  tfrrs `_college_` slugs), not "unattached", not a name that says
  school (`isClubName`), with 20+ rows and under 5% of them carrying a
  school grade. `loadClubPros` merges it, so `team_has_pros` fires for
  those teams too (still under the majority gate of 9.6).
* **A 5:12 "mile" at 166, a 12:18 "5k".** A wrong distance. The builder
  now refuses any row faster than 98% of the open world-record pace for
  its distance and sex (`impossiblePace`, records of 2025 interpolated
  in log-distance). The 5:12 was actually a scale mismatch, caught by
  the anchor gate now that it runs on the rating pool.

The builder (`racecast/build_ranking_results.py`):

* pool = the row's `rating_pool` (both SELECTs carry it; `_sourceSql`
  substitutes `NULL::text` on a database before the column, and then
  `resolvePool` is the fallback as before). Counter `pool_from_row`.
* `isRankablePool` refuses `pro_*` (counter `pro_pool`): a professional
  rating has no board on the site.
* `impossible_pace` gate before the anchor gate.

The pack (`engine/speed_ratings.py`): `rescaleToPool` identifies the
stored scale against EVERY pool in both sports, takes the nearest factor
within 8%, and declines (`scale_ambiguous` in the census) when a factor
of a different size (>5% apart) is within 3% of the nearest; factors
within 5% of each other (hs_m/hs_f, XC/TF of one pool) count as one
scale and the nearest is used.

The check, `scripts/board_sanity.py`, pipeline step `10a_board_sanity`
right after `10_rankings_finish`, and it FAILS the step on a hard
finding (no `|| true`). For the top `--top` (60) rows of every (sport,
pool) board: anchor gate on the published pool, record pace, board pool
== rating pool, club team (by `loadClubPros` + `loadClubTeams`), margin
over the pool's own top-20 season means (`MARGIN` 12 points; the stale
`pool_ceiling.POOL_CEILING` is printed beside it), then athlete_season
rows in school pools on club teams, and the same-athlete TF minus XC
season-median gap per pool (the sport level as the boards show it).
Hard: anchor, pace, pool. Soft: club, margin (`--strict` makes them
hard). Every offender prints with name, school, time, distance, date
and result_id, so the row can be pulled up.

Run on the box after the pack + rankings:

    python scripts/board_sanity.py --top 60
    python scripts/board_sanity.py --top 200 --strict --show 50   # the long list

Tests: `tests/test_board_sanity.py` (rails, record pace, `_sourceSql`,
`isClubName`, ambiguity, the pure checks).

Still open after this, for the constants discussion: the sport level
(TF under XC by the boards' own gap row), the tilt above 140, the
track prior cap, and `POOL_CEILING`, which is a decade stale (college_m
130 against a board that tops at 150) and is only printed here, not
applied.

### 9.12 The record condemns the race; the pipeline goes quiet in Postgres (2026-09-14)

**Impossible times, the race rule.** The owner: "do for every but
college pool, and if this happens for a race, make the entire race
unrated / unranked". `engine/record_pace.py` is the one definition
(records of 2025, 2% slack, `exemptPool` = college). Step
`06c_impossible` (`engine/impossible_race.py --write`, before the pack)
keeps every row under 183 s/km in SQL (no record is slower), judges
those in Python by the athlete's sex and the loader's own distance,
and writes two tables built as `_new` and swapped under a lock
timeout: `impossible_race` (one line per race: key, rows, how many
beat the record, the fastest pace against the record, a sample row)
and `impossible_result` (every result_id of those races). A row's pool
is the `rating_pool` the last go-live wrote; a row without one counts
as college only when it came from tfrrs. Three readers anti-join the
table: the pack loader (`speed_ratings_db._impossibleFilter`, with a
count in the pack log and a banner when the table is absent), the
board query (`build_ranking_results._SQL` `__IMPOSSIBLE__`, filled by
`_sourceSql`) and the fill, which now prices through `_sourceSql` too
-- so a condemned race has no rating anywhere: not in the solve, not
priced, not ranked, a dash on the athlete page. The per-row pace gate
in `prepareRow` and in `board_sanity.py` stays as belt to that brace
and now skips college pools. Tests: `tests/test_impossible_race.py`.

Dry run on the box (lists the races, writes nothing):

    python engine/impossible_race.py
    python engine/impossible_race.py --write      # what step 06c runs

**The pipeline and the site.** 8.7 put nice/ionice on the Python and
lock timeouts on the swaps. What still hurt was inside Postgres: the
backends doing the boards' 61.6M-row COPY, the heap rebuilds of
`results` (tilt, fill) and the index builds ran at normal priority
with 2-8 GB work memory and 4-6 parallel workers per statement, three
index builds at a time, four board row walks side by side, three
course-page shards. Now:

* `XCP_DB_QUIET=1` (run_pipeline.sh exports it; the site's unit never
  does): every connection `scripts/database.getConn` hands out is
  capped once -- `work_mem 64MB`, `maintenance_work_mem 512MB`, no
  parallel workers, `synchronous_commit off`, `application_name
  xcp-pipeline` -- and, on a local server, the backend process is
  reniced (+10) and ionice'd best-effort; a denied renice prints once
  (needs CAP_SYS_NICE: run as root, grant it, or `ALTER ROLE ... SET`
  the caps server-side) and the SETs still hold.
* The builders' own SETs go through `database.dbSetting` /
  `dbJobs`: `merge_column` (was 2GB/4 and 8GB/6), `build_ranking_results`
  (index builds 2GB/4 x 3 jobs -> 512MB/0 x 1; the athlete_season sort
  asks 512MB then 256MB instead of 4GB), `dbfast.tuneSession` (every
  swap-through builder).
* `XCP_STREAMS` (default 2, was 4) board row walks at a time;
  `XCP_COURSE_SHARDS` (default 2, was 3).
* `XCP_DB_QUIET=0 XCP_STREAMS=4 XCP_COURSE_SHARDS=3` is the old speed.

The run will be longer (the index builds alone were 4x parallel). If
that is too long, the next knobs are `XCP_DB_QUIET_NICE` and, on the
server, `ALTER ROLE <pipeline role> SET ...` with a separate role for
the pipeline so the caps live in Postgres rather than in the client.
Still open from 8.7: the site's `XCP_DB_STATEMENT_TIMEOUT_MS=55000`
lets one reader hold a table long enough to stall a swap round; 10-15 s
is worth trying in the service unit. Tests: `tests/test_db_quiet.py`.

### 9.13 Run 23's two lessons (2026-09-15)

**The school-identity step was the gateway timeouts.** `10b_school_ids`
(`racecast/build_school_identity.py`) read the 61.6M-row, 23 GB boards
table SEVEN times end to end -- one full scan per question -- and each
scan pushed the site's pages out of the OS cache and put the disk to
work for the pipeline until nginx gave up on the eight workers. Every
question it asks is about athletes and seasons, so it now reads
`athlete_season` (12.9M rows, built from the same load by
`10_rankings_finish`): home state by races per (person, state), school
votes, the directory's college clusters, the level table. The two reads
that need meets (the co-racing merge) filter `ranking_results` by
school through `idx_rr_school`. It also commits per phase: the whole
build was one transaction, so a lock timeout in the swap rolled back
the finished build and the retry renamed tables that no longer existed.
The quiet mode's `work_mem` cap is 256MB, not 64MB: a smaller sort
budget spills a 61.6M-row GROUP BY to temp files, which is the I/O the
mode exists to prevent.

**The impossible-race step condemned 5.8M track rows.** The first cut
judged every timed row. The loader's event parser reads "60m" as 60 km
("< 100 means kilometres", right for "5k", wrong for a dash), so a 7 s
60 m was a 60 km at 0 s/km and every sprint race in the corpus was
condemned; cross country had 4,828,032 m "5ks" (a distance in the wrong
unit). The candidates are now rated rows only (`normalized_time IS NOT
NULL`, the engine's own universe -- a sprint has none) at a distance a
race is run over (XC 1,000-20,000 m, TF 800-15,000 m). A wrong unit on
a distance is the ballooned-distance census's business and the band's.
After the fix, on the box: `python engine/impossible_race.py --write`,
then the run from 10; the solve stands (the sprints were never in the
pack). Run 23's XC list (168 races, 9,456 rows) is what the rule was
for: 13 s "4.5ks", 34 rows at 4 s/km.

**Read from run 23, for the constants:** the bracket tilt-by-band table
(implied < applied means the band pays LESS of a course than charged,
i.e. the course is overstated) has XC's implied h ABOVE its applied h
in every band: 1.15 against 1.01 at <100, 1.07 vs 0.97 at 100-120,
1.01 vs 0.93 at 120-130, 0.99 vs 0.90 at 130-140, 0.99 vs 0.87 at
140-150 -- the voters' own brackets say XC courses cost them about
10-14% MORE than the solve charges, uniformly across bands, while TF's
implied and applied agree within 1% everywhere. So XC's course effects
are UNDERstated by roughly a tenth (the XC prior shrinks them too hard,
or the XC course scale wants a factor of ~1.1) and the tilt's slope is
right in both sports. That, with the gap row (college TF 1.2-1.7 under
XC, hs 0.4-0.9 under), is where the constants discussion starts.

### 9.14 The constants, measured: the course scale, the level per pool, two rails (2026-09-15)

The owner's call after run 23: "let's do that" (the XC course scale, the
sport level per pool, the two board rails); the Mantecon race "isn't an
issue, it's just too little tilt on a hard course" -- which the scale
addresses first and the band table then judges.

**The course scale per sport** (`run_joint.bracketDifficulties`,
`--course-scale`, `XCP_COURSE_SCALE`, default `fit`). Run 23's
tilt-by-band table had XC's implied multiplier a tenth above the
applied one in every band and TF's matching within 1%: the fitted prior
shrinks XC courses and the voters' brackets say so. Each sport's course
effects are now multiplied, after the recentring (the zero stays a
track), by the voter-weighted mean of implied/applied over its trusted
bands (`bracket_engine.courseScaleFromBands`; se <= 0.02, 5,000+
voters). TF comes out at 1.0 by its own table; XC near 1.1. `off`
leaves 1.0; `XC=1.1,TF=1` states it. Two new tables in the go-live log:
tilt by band is now labelled "BEFORE the course scale", and tilt by
RACES PER CELL (`tiltByRaces`) says whether implied/applied falls with
races (the prior pulling thin courses in -- then the prior is the
lever, not a flat scale) or is flat (the scale). The acceptance test is
the next run's band table: implied should equal applied. The trace
(`scripts/course_bracket.py`) shows the scale column; the npz carries
`bracket_scale` (per cell) and `bracket_course_scale` ([XC, TF]);
published = scale * (fit - recentre) + level.

**The sport level per pool level** (`--sport-level-pools`,
`XCP_SPORT_LEVEL_POOLS`, e.g. `college=0,hs=0.008,ms=0.012,elem=0.015`).
The go-live's existing winter-gain machinery (issue 194,
`joint_solve.sportGainShift`, per pool x band) now takes a gain per
POOL LEVEL, every band alike (`joint_golive.buildLive(gain_levels=)`);
a level not named is left as the solve put it, and it is kept alongside
`--sport-level` (the solve's level stays; this corrects what the boards
show). The number is measured, not asserted: `scripts/sport_level_fit.py`
reads athlete_season and prints, per level, the same-athlete TF-XC gap,
the XC-to-next-XC growth, and the stated gain = `--share` (0.5) of that
growth -- the spring sits halfway to next fall -- ending with the
`XCP_SPORT_LEVEL_POOLS=...` line to paste. The go-live table "winter
gain per band" shows the gap read, the target and the shift per pool;
the sanity script's gap row is the acceptance test.

**Two rails.** `build_ranking_results.prepareRow`: a RATED row with no
distance is never ranked (counter `no_distance`; the ms_m board headed
at 177 by "Hayward" rows with no distance -- nothing could check them).
`speed_ratings_db.loadClubPros`: a college name never enters the club
set (`_collegeNames`: the directory plus tfrrs's college slugs), so
Alabama, BYU and Stanford stop reading as "a club with a professional"
in the club gate and the sanity report.

Run it from the run-23 state, no solve (the go-live is minutes):

    git pull
    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python engine/impossible_race.py --write     # the fixed rule's tables
    /srv/venv/bin/python scripts/sport_level_fit.py            # ends with the env line
    XCP_FROM_STATE=engine/data/joint_difficulty_state.npz XCP_DIFFICULTY=bracket \
    XCP_SPORT_LEVEL=0.0583 XCP_ERA_YEARS=2 XCP_ALTITUDE=1 XCP_INDOOR_LEVEL=0.003 XCP_IMPORTANCE=none \
    XCP_SPORT_LEVEL_POOLS=<the line's value> \
    bash deploy/run_pipeline.sh --from 8 --skip 08a_holdout,08b_ladder 2>&1 | tee logs/run24.out

Then read: `grep -A16 "tilt by band" logs/run24.out` (implied vs
applied, before the scale), `grep -A10 "tilt by races" logs/run24.out`,
`grep "course scale per sport" logs/run24.out`, `grep -A40 "winter gain
per band" logs/run24.out`, and the sanity log's gap row
(`logs/<ts>/10a_board_sanity.log`). Tests: `tests/test_course_scale.py`.

**The constants are checked, not printed (2026-09-15, same day).** The
band table now rides in the solve file (`bracket_tilt_bands`, rows of
sport, band edge, voters, applied, implied, se) with `bracket_course_scale`,
and `board_sanity.py` (step 10a) fails the run on two more findings:
`level` -- the same-athlete gap per pool level in log-time against the
gain in `XCP_SPORT_LEVEL_POOLS` (tolerance 0.005, about 0.6 points) --
and `tilt` -- every trusted band's implied/applied, divided by the scale
the go-live applied, within 6% of 1.0 in both sports. So a constant that
drifts fails step 10a on the run that let it drift, and the trace to
the cause is the two tables above it in the go-live log.

---

## 10. 2026-09-15: RECRUITING, THE ATHLETE EDITION (282)

The owner: "a recruiting page (for athletes) ... for each college
school you can see the average speed rating of their recruits, lowest,
highest ... the prs that will probably get you in ... recruit/walk
on/full scholarship ... suggest some to you based on your times
(prestigious/faster schools first) ... tie in to the eventual
accounts". Built on `claude/nice-rubin-tj0kev`; the log entry with the
rules is issue 282 in `docs/ISSUES-2026-08-24.md`.

- **A new table, a new step.** `college_recruit` and
  `college_recruit_meta`, from `racecast/build_recruiting.py`, pipeline
  step `10f_recruits` after `10d_school_units`. It reads athlete_season,
  school_identity and school_unit, and runs alone in a minute or two:
  `$PY racecast/build_recruiting.py` (`--dry-run` for the counts). The
  site says "not built yet" until it exists; nothing 500s.
- **Read the build's log once.** It prints the classes kept, the HS-scale
  factors from pool_view, how many recruits are linked to a
  high-school season, and the FRESHMAN GAIN per gender and sport (the
  median of the first college season on the HS scale less the linked
  HS senior season). Under 50 pairs the gain is 0 and the log says so.
  If the linked share is small, the unlinked recruits' numbers are
  estimates and the pages mark them "est.".
- **The pages.** `/recruiting` (athlete edition, in the topbar),
  `/recruiting/school/<name>?state=&gender=`, `/recruiting/search`
  (the coach's search, renamed from `/recruiting`),
  `/api/recruiting/schools`. The subject is `?athlete=<id>` or
  `?time=&event=&gender=`; `recruiting.subjectFrom(...,
  account_person_id=)` is where accounts (283) plug in.
- **Tests.** `tests/test_recruiting_athlete.py` (offline: tiers, time
  parsing, the table and the subject on a fake cursor, the builder's
  shaping and gain, the wiring by source). The pages were rendered
  through Flask's test client against a fake database in the sandbox;
  the time conversions were exercised with a patched pool mean (a
  16:00 5K reads 127.9 and converts back to 15:59). Not run against
  the database: the build itself, and the conversions with the live
  engine constants.
- **Next, as the owner listed it:** the coaches' recruiting pages and
  log-ins (283).

---

## 11. 2026-09-15: ACCOUNTS, THE FIRST CUT (283)

The owner: three options at login (athlete, coach, neither), an athlete
links one or several pages, a coach picks a team or themself, 13+, no
verification yet. Built on `claude/nice-rubin-tj0kev` and on master.

- **To switch it on:** `docs/ACCOUNTS.md`. One command creates the
  tables (`racecast/accounts.py --init`, idempotent, never a pipeline
  step), a restart, and then env variables per feature: mail (Resend
  or Postmark; unconfigured, the link goes to the gunicorn log),
  Google (a console project, one redirect URI), Turnstile, admins.
- **The shape:** no passwords; magic links or Google; sessions in
  Postgres; roles as claims; the pages anonymous and cacheable with the
  topbar asking `/api/me`. The reasoning is the header of
  `racecast/accounts.py` and the log entry under issue 283.
- **Tests:** `tests/test_accounts.py` (offline: emails, the next path,
  tokens, claim forms, CSRF, the client IP, the feature switches, the
  wiring). The whole login flow (link, continue, consume, cookie,
  account page, claims, CSRF refusals, logout, delete) ran through
  Flask's test client on a stateful fake database in the sandbox. Not
  run: the real mail and Google calls, and `--init` on Postgres.
- **Next:** what a claim opens (a coach's roster tools, an athlete's
  fields and contact), verification, then the coaches' recruiting
  pages.
- **Later the same day:** the sign-in page became its own shell (the dark
  ground, the wordmark, one card); the recruiting page was redone as
  "you, then the schools" after screenshots in a real browser; `/account`
  is Settings with a display name and the account picture, which the
  athlete page shows in its header (`docs/ACCOUNTS.md` section 6; the
  files live in `racecast/static/photos/`, gitignored). Mail: Resend,
  and what `XCP_MAIL_FROM` means, in section 2 of the same doc.
- **311, the athlete page's uncached cost:** the rank line's scoped counts, now precomputed by `build_season_ranks.py` (step `10g_season_ranks`, run it alone once: `$PY racecast/build_season_ranks.py --verify 40`, then restart). The page reads one row of `season_rank`; the live counts remain the fallback.
- **312, the go-live crash (fixed):** `--sport-level-pools` without
  `--winter-gain-bands` left `gain_bands` None while the per-level path
  built the matrix, and the report loop under it still asked for
  `len(gain_bands)` -- so the run died after the solve with
  `TypeError: object of type 'NoneType' has no len()`. The loop is
  bounded by the matrix now. Rerun from the state file: `--from 8` with
  `XCP_FROM_STATE=engine/data/joint_difficulty_state.npz`, no solve.
  `tests/test_joint_golive.winter_gain_paths` covers all four
  combinations of the two arguments.

## 12. 2026-09-15: THE COACH EDITION (282/283)

The coach tools became their own product at `/coaches` instead of one
more link in the athlete nav, because the free/paid line falls there: the
athlete pages are the traffic and stay free, and a coach will pay for a
tool that picks a lineup, not for rankings they can already read.

- **The switch is a URL, not a cookie.** `_topbar.html` sets
  `coach_view = request.path.startswith('/coaches')` and lights one half of
  a two-item control beside the mark. This is not a style preference: a
  cookie-varying topbar keys the edge cache on the READER instead of the
  URL, and every page on the site would stop being shared between them.
  It also means a coach can send a colleague a link and have them land in
  the same edition.
- **`/coaches`** is public, cacheable and ungated: six cards to the tools
  that exist, and a plainly-labelled list of the ones that do not. The only
  per-reader block is "your teams", and `coaches.js` fills it from the
  `xcp:me` event `topbar-search.js` already dispatches -- one `/api/me`
  request for the page, not two.
- **`/coaches/recruits`** is the search that used to be
  `/recruiting/search`; the old URL is a 301, not a second copy of the
  page, because two routes rendering one template is how they drift.
- **Admin is env-only.** `XCP_ADMIN_EMAILS=you@example.com` in
  `/etc/xc-predictor.env`, then restart; `accounts.py --check` prints the
  list back. Nothing under `/coaches` is gated on it today -- the flag
  reaches `/api/me` and shows as a tag, and it is there for the tools that
  will need it.
- **⚠ A bar bug older than this change, found while measuring it.** On
  b1bf920 the topbar overflowed between ~1180px and 760px -- 23px at 1180,
  145px at 1024 -- and the account chip sat off the right edge of the
  window, invisible on an ordinary laptop, ever since it was added.
  `.search-wrap` was a rigid 320px and `.topnav` could not wrap, so the
  last element on the bar was pushed out instead of anything reflowing.
  The `max-width: 1200px` block lets the search shrink, the nav wrap and
  the switch tighten, in that order. Swept in a browser at twelve widths
  on both editions: zero overflow, chip visible at every one.
- **Tests:** `tests/test_coach_view.py` (routes, the 301, the path-keyed
  switch, no reader state in cached HTML, the reflow band).
