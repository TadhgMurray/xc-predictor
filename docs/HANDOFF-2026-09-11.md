# Handoff — 2026-09-11 (engine research and fixes)

Everything below is on `claude/youthful-gates-o25yq9`, unmerged. The
previous state of the server and the site is `docs/HANDOFF-2026-09-08.md`;
the engine's own description is `docs/ENGINE.md`; the survey of how other
systems solve this engine's problems, and what changed today because of
it, is `docs/RESEARCH-ENGINE-2026-09-11.md`. Read that one first.

**Nothing today touched the database or ran on the box.** The sandbox has
no Postgres and no pack. Every change is covered by a synthetic test that
runs here; the first live log decides.

---

## 1. WHAT CHANGED, IN ONE TABLE

| where | what | why |
|---|---|---|
| `joint_solve.nestedPosteriorVar` | the EM E-step's `Var(d)`, `Var(u)` are the exact (cell + its races) block, not `sigma²/A_ii` | the diagonal goes to zero with more rows of ONE race; the truth does not. `sigma_u` was under-stated and every thin course kept too much of one day |
| `joint_solve` + `run_joint` | **field-strength term** `imp[pool, sport] · front`: a race's front is the mean rating of its top five above the median race of its pool and sport, per 10 points, from the model's own ratings each pass (`joint_solve.fieldStrength`); the season-end share is kept as `--importance season-end` for the ladder | owner: "shouldn't do the championship thing, instead by rating: a top-heavy race should get some refund to its difficulty" |
| `joint_solve` + `run_joint` | the **indoor level asserted** at +1.2% (`IND_LEVEL_DEFAULT`, `--indoor-level`; `fit` estimates it), off theta like the sport level; outdoor and indoor cells recentred separately; a last-indoor / first-outdoor pair check in the log | indoor is season: the page read every oval 2.4 to 3.8% EASIER than outdoors |
| `joint_golive.latestEraKeys`, `speed_ratings_db._splitVenueKey`, `deploy/run_pipeline.sh` | `XCP_ERA_YEARS=2` splits every course into two-year eras (built before, never wired); the go-live publishes each venue's latest era under its bare key, so the page finds it | owner: "I have a feeling recently is a lot faster than previously" (Ultimook) |
| `racecast/conversions.venueEffect` | a track conversion with no venue uses difficulty 0.0, the average outdoor track, not the median applied row effect | owner: "conversions still off ... not using 0.0 difficulty for the track" | a championship-only venue booked the taper as an easy course (Foot Locker, state meets). Beyer's class pars, FIS's zero penalty: a class effect, never a venue's |
| `joint_solve` + `run_joint` | indoor as one coefficient per pool, folded into `delta` | 1,574 indoor cells each rediscovered a ~1% effect under a 1.6% prior; no location hosts both |
| `joint_solve.Design.mu_fixed`, `run_joint --sport-level G` | the XC/TF level ASSERTED and taken out of theta | sport is season; nobody identifies this from results (Tully, NCAA, WMA all state it). `--merge-sports` left `mu` free and unpenalised |
| `joint_solve.DIST_BANDS` (4 bands), `engine/distance_tables.py` | a top band from 135; the World Athletics / Purdy relation as the prior mean of an event offset the pairs cannot calibrate | the exponent rises as ability falls; the tangent extension beyond 3200 is unfounded (college 10k rode four pairs) |
| `racecast/conversions.py` | the forward map solved as a fixed point; the offset continuous in the rating; stored rows invert their own rating | #21: −3.34% reproduced as one inter-band step taken on one leg only |
| `engine/joint_golive.py` | `engine_scale.anchor_shift` = the anchor applied; zero = the average OUTDOOR track | the shift had not followed the display anchor; every named-venue conversion carried the XC/TF gap |
| `racecast/panels.py`, `racecast/app.py`, `athlete.html` | no second tilt on the home boards; the athlete header's equivalence goes through the page's inverse and names the pool's distance | tilting twice; "about a 12:40 5K" for a college man was an 8000 m time |
| `deploy/run_pipeline.sh` | `XCP_SPORT_LEVEL`, `XCP_NO_IMPORTANCE`, `XCP_NO_INDOOR`, `XCP_NO_DIST_TABLE`, on 08 and 08a | the holdout must score the shipped model |
| `joint_solve.altDistanceFactor`, `run_joint` | altitude exposure and credit scaled by the event's share of the 5000's cost (800 a fifth, 10k a bit more) | NCAA tables: one coefficient per sport over-credited the 800 and under-credited the 10k |
| `scripts/ablation_ladder.py`, `run_joint --diag-var` | rungs `diag-var`, `no-importance`, `no-indoor`, `no-dist-table`, `stated-level` | every new term is scored on held-out races by 08b |
| `speed_ratings_db`/`speed_ratings`, `engine/meet_class.py` | a pack column `meet_class` (meet name → 0/1/2/3, tfrrs flag → 2) kept as a DIAGNOSTIC only: the solve cross-tabulates the season-end share against it | if the finals do not show the highest share, the calendars the share is read off are wrong, and that line says so |
| `joint_solve.TILT_RATING_LO/HI`, `run_joint.reportTiltByBand` | the tilt extrapolates past 140 instead of clamping (rails 40/200 are safety only), and every run prints the tilt the residuals imply per rating band | owner: "measure further; extrapolate rather than remain constant" |

Tests added: `tests/test_nested_variance.py`, `tests/test_shared_terms.py`,
round-trip cases in `tests/test_conversions_engine_scale.py`. Updated for
the new behaviour: `tests/test_joint_dist.py` (four bands),
`tests/test_conversions_distance_offset.py`, `tests/test_split_ability.py`
(two stale string matches that failed on master).

## 2. THE NEXT RUN

The pack from run 19 has everything these terms read (the field term uses
the solve's own ratings, the indoor check uses `dist_m` and `days`), so
from 8:

```
cd /srv/xc-predictor && git pull
set -a; . /etc/xc-predictor.env; set +a
XCP_SPORT_LEVEL=0.0583 XCP_ERA_YEARS=2 XCP_ALTITUDE=1 bash deploy/run_pipeline.sh --from 8 2>&1 | tee logs/run20.out
```

`XCP_SPORT_LEVEL=0.0583` states the XC/TF scale (track is the zero, an
average XC course +6%); leave it in, because with the level estimated the
average fall-to-spring fitness gain is absorbed into the level and spring
ratings sit level with fall ones by construction. `XCP_ERA_YEARS=2` is
new to the pipeline: every course becomes (course, two-year era) cells
tied by a random walk with a stated drift of 1% per era, so a venue with
evidence can move (Ultimook) and a thin one cannot; the go-live publishes
each venue's latest era under its bare key. Drop it to run one difficulty
per course for all time, as before.

The holdout (08a) and the ladder (08b) score the model and write nothing:
skipping them changes no rating. Nothing after 08 rewrites a rating 08
wrote; 09b prices only the rows 08 left unrated, and 10 onwards rebuild
the boards from the ratings, so the pages show the new numbers only after
those steps run.

## 3. WHAT TO READ FIRST IN `08_golive.log`

```
[joint] field strength: the taper term's covariate is each race's FRONT   # the term is on
[joint] indoor level ASSERTED at +1.20% log-time for every pool           # and the ovals are pinned
[joint] indoor check, the NCAA way: ... median +x.xx% trimmed mean +x.xx% # should bracket +1.2%; see below
[joint] eras: 2-year cells -> N (course, era) cells, M adjacent pairs     # XCP_ERA_YEARS=2
[joint] indoor: 1,574 of 74,366 cells are indoor tracks
[joint] XC: race-day sd ... course prior ... a one-race course keeps 0.xx  # vs run22: sigma_u a little up, share a little down
[joint] field strength, log-time per unit of front strength ...          # negative; a few tenths of a percent per 10 points
[joint] field strength by band: ... applied ... resid                    # resid flat near zero = the line fits; a bend says where not
[joint] indoor level ASSERTED, log-time per pool: ... +1.20% ...         # the indoor cells' mean deviation is zero by construction
[joint] tilt by rating band: applied h against implied h                 # 140+ rows: implied close to applied = extrapolation holds
[joint/live] eras: N solved (course, era) cells publish as M venues      # each its latest era under the bare key
[joint] indoor, log-time per pool                                        # +0.5 .. +1.5%
[joint/live] track zero: mean 0.000 ... over N track cells               # outdoor cells
[joint/live] difficulty zero = the average TRACK. Cross country lands at # the estimate, or "ASSERTED"
```

Then: `scripts/difficulty_spread.py` (the sd of difficulty must now RISE
with `n_results`, not fall); `scripts/venue_check.py --venue 'Balboa'`
and `'Glendoveer'` (championship venues read harder than before);
`scripts/convert_probe.py --tf --person 29603086 --seconds 541.1` (closes).

## 4. THINGS THAT BIT TODAY

- A prior stated in pseudo-rows on a shared coefficient competes with
  `sigma_u²` summed over every race that carries it. State it as an SD.
- A one-race-per-cell world with three races per athlete is unidentified
  for `sigma_u` **even with the exact posterior**; a test built on it
  would have blamed the estimator. The corpus has ~100 rows per race.
- `--merge-sports` never pinned `mu`; its test starts from zero.
- `engine_scale.anchor_shift` and the display anchor were two variables
  for one number, and drifted apart in one commit (9.5 → e6eaa63).
- The `'|'` in `rating_pool` that told solved rows from filled ones went
  away when the go-live started writing bare pools; every stored result
  then took the "filled" path.
- Seven script-style tests fail on master before today
  (`test_anchor_repair`, `test_capped`, `test_pipeline_failures`,
  `test_returning_teams`, `test_shadow_migration`, `test_sprint_boards`,
  `test_track_anchor`) and six pytest-style ones (`test_hs_factor_per_pool`,
  `test_meet_units`, `test_pager_last`, `test_pipeline_shards`,
  `test_pipeline_step_guards`, `test_sport_gain`) — all verified on a
  pristine checkout of the branch head; left alone. `test_rank_line_units`
  and `test_weather_fixes` need the box's `database` module and do not
  collect in a sandbox. Full pytest run here: 281 passed, 55 skipped.
- `test_thin_course_shrinkage` reproduces the OLD collapse and now pins
  `nested_var=False` to do it; `test_track_is_zero` pins the anchor's new
  variable name.

## 5. OPEN

`docs/ENGINE.md` §11, rewritten today: read the ladder (every new term now
has a rung), era drift, posterior sd and `n_results` on the page, the
curve rebuilt on equal-quality pairs, and a per-course tilt only if the
data ask for it. A hyperprior on `tau`/`sigma_u` is not worth building:
with 44k identified cells it is inert, and the observed-sd cap is a
display choice.

**The taper term is gone; the field-strength term replaced it (owner,
2026-09-11: "shouldn't do the championship thing. Instead should do by
rating. If there is a race that is very top-heavy, where people will run
fast because there's more competition, those races should get some
refund to their difficulty").** The covariate is the race's front: the
mean rating of its top five, from the model's own ratings each pass
(the way the tilt is), centred at the median race of its pool and sport
and counted in units of 10 rating points, clipped to −4..+6. A dual meet
sits a unit or two below zero, a national final three to five above.
One coefficient per (pool, sport), zero prior, fitted; it is the same
number for everyone in a race, so finishing order inside a race never
moves, and it is identified across races by the same athletes running in
stronger and weaker fields and by venues that host both. It is not in a
rating: the fast time in a stacked field is a real performance. What it
does is stop the fast times of a stacked field from reading as an easy
course, which is the refund. **How much** is the data's: a healthy fit
reads a few tenths of a percent per unit (`FIELD_EXPECTED` −0.5%), and
the log prints the residual by strength band so the shape can be read
rather than assumed; if the top band bends, that table says by how much.
On the planted world the coefficient is recovered and the stacked-only
venues' bias halves (`tests/test_shared_terms.py`). The season-end share
stays as `--importance season-end`, a ladder rung, not the default. The
name classes stay in the pack as a diagnostic only.

**Indoor was off, and the reason is the same as the XC/TF one (owner:
"I think indoor might be off").** The page read every indoor oval 2.4 to
3.8% easier than outdoors, which is backwards: the NCAA facility factors
and the WA short-track tables put a 200 m oval 0.8 to 1.8% slower. No
location hosts both an indoor and an outdoor track and nobody races
indoors in May, so the form curve's December-to-March level and the
indoor cells' mean are one free direction: the smooth curve interpolates
the fall-to-spring gain through the winter, and the indoor cells absorb
the difference. Indoor is season. The answer is the sport level's: the
indoor level is asserted (+1.2%, `IND_LEVEL_DEFAULT`, `--indoor-level`),
taken off `y` like `mu_fixed`, and the indoor cells are recentred to it
every pass so they keep only their own deviation, a banked BU below the
level and a flat 200 m oval above. The outdoor tracks are recentred
without the ovals voting. The log prints a check measured the NCAA way:
each athlete-season's last indoor race against its first outdoor race at
the same distance within six weeks, indoor minus outdoor, per pool. It
brackets the level rather than fixing it, because fitness gained in
between biases it up and a peaked last indoor race biases it down; if it
reads far from +1.2%, change the number. On the page: the athlete in the
message whose 1:54 indoor 800 rated 130 will rate about 135, above the
1:56 outdoor at 131, which is the right order. `--indoor-level fit`
restores the fitted coefficient, and the ladder has a `fit-indoor` rung.

**Run 20 crashed in the go-live under `XCP_ERA_YEARS=2` (2026-09-12),
three hours in, at `ref = solved & is_tf & ~is_indoor`: the go-live took
its cell keys from the pack (74,366 base courses) while the era-split
design has 219,715 (course, era) cells. Fixed: `buildLive` and the npz
save read the design's keys, an assertion checks the count against the
cell count before anything else runs, and `tests/test_era_publish.py`
runs the go-live on an era-split design end to end. Rerun from 8 with
the same command.**

**The second engine and the diagnostics (2026-09-12, after run 20).**
The owner's method is built as a second solve, `engine/bracket_engine.py`:
every row's log time less its point on the joint solve's form curve,
compared with the same athlete-season's other rows within a window
(21 days) each less the difficulty of the course they were run on; a
race's difficulty is the mean over the top fraction of its field; a
(course, era) cell is the vote-weighted mean of its races pulled toward
the course's history by twenty votes; the level is pinned per sport and
era; iterated to a fixed point in a handful of passes. No race-day term,
no field term, no taper. It is scored on the SAME athlete sample and
held-out races as the ladder's base rung, so the two engines are
compared with one number. The comparison is the open question.

**Run every diagnostic in one process (2026-09-12, "5 mins max, all of
them together").** `scripts/diagnose.py` loads the pack and the solve
file once, numbers the athlete-seasons, races and the solve file's cells
once (`engine/bracket.packCodes`), and runs the venue brackets, indoor
against outdoor, why tracks differ, and the bracket engine's holdout on
that, each on its own rows; each report lands in `logs/<stage>.txt` and
stdout carries a timing table. Pipeline step 08d runs the three stages
that need no names every run.

```
$PY scripts/diagnose.py --era-years 2 --out-dir logs \
    --venue "Foot Locker" --venue Champs --venue Brooks --venue Glendoveer
cat logs/bracket_venues.txt logs/indoor.txt logs/tracks.txt logs/bracket_holdout.txt
grep "error sd" engine/data/ladder_logs/base.log    # the joint model, same split
```

Measured, not estimated, on a ten-million-row synthetic pack shaped like
the corpus (2.5M athlete-seasons, 1.3M races, 75k era cells; one core
of a small sandbox, pack in the page cache): load 4.9 s, codes 7.9 s,
venues 3.9 s, indoor 1.5 s, tracks 8.9 s, holdout 4.6 s, total 31.6 s,
peak memory 2 GB. Everything in it is a sort or a gather, so the
63-million-row corpus is about six and a half times that: roughly three
and a half minutes and thirteen gigabytes, plus whatever the disk adds
to the load if the pack is not cached. Run it as
`/srv/venv/bin/python scripts/diagnose.py ...` or with `$PY` set; an
unset `$PY` runs the shebang's system python3, which has no numpy, and
the script now re-executes itself under the venv when that happens. `tests/test_diagnose.py` runs all four stages on two
million rows against a clock so a quadratic cannot creep back in. The
four scripts still run alone and take the same shared codes.

What made the earlier scripts slow, all fixed: `run_joint.raceCodes`
numbered races with `np.unique(axis=0)` on a stacked pair (19 s per ten
million rows, ninety on the corpus, in every script and in the solve's
own holdout split; now one composite key, same numbering, under 2 s);
each script loaded the corpus and recomputed the codes; the bracket
engine ran every pass over all 51M rows for a 15% sample, and mapped era
cells to base courses with `list.index` (eight billion string compares);
the sample helpers used a sort-based `np.isin` where a dense mask is one
gather. And the failure the owner saw, "the solve file's cells do not
match the pack with this --era-years": `track_variance` rebuilt era cells
from the sampled rows; every diagnostic now maps (course, era) through
the solve file's own keys and `era_base_year` (`bracket.cellsFromKeys`),
so any subset of rows lands on the file's cells.

**What the diagnostics said on run 20's file (2026-09-12, 150 s on the
corpus, 79 s of it the disk load).** Read `logs/*.txt` before deciding
run 21; the numbers are the corpus's, not a model's.

- *Indoor is barely harder than outdoor.* Same athlete-season, same
  distance, raw times inside 21 days: HS boys +0.46%, HS girls +0.29%,
  college about +0.03%; with the solve's form curve taken out, HS boys
  +0.12% (800 m +0.4%, 1600 m +0.1%, 3200 m about 0), HS girls and
  college about zero. The asserted +1.2% (`IND_LEVEL_DEFAULT`) credits
  an indoor mark about 0.9% too much. Run 21: `XCP_INDOOR_LEVEL=0.003`.
  Fitting it is not an option: with a free winter curve the level and
  the curve are collinear on every indoor row (issue 143 again), which
  is why it is asserted; and the curve-corrected number is partly
  circular for the same reason, so the raw short-window numbers are the
  evidence. The literature's +0.8 to +1.8% is flat 200 m ovals; the
  corpus's indoor rows are mostly banked, fast facilities.
- *Outdoor tracks: the meet mix is not why they differ.* Across 8,207
  tracks the board spread is 0.98% sd, the same-athlete bracket spread
  0.69%, correlation +0.43; front, championship share and league share
  explain 4% of the board (R2 0.04). Within a track, championship-class
  rows run 0.25% FASTER than ordinary rows (peaked, not tactical) and a
  field 13 rating points stronger runs 0.5% faster: real, small, and not
  the spread. The bottom of the board is not fast tracks: cells at -6 to
  -12% whose runners' brackets are within 1% of zero (a cluster of ids
  TF:loc:994xx:out, and TF:loc:88489 at -12.5%) are a distance that lies,
  most likely a 1500/3000 booked as a 1600/3200 (6.8%). Look them up with
  `scripts/meet_cells.py --tf-loc 99426 --tf-loc 88489`. Physics says a
  400 m track differs from another by half a percent (surface, wind,
  timing); a stated prior of that width on TF cells would shrink the
  scatter the brackets do not support. Not done; a decision for run 21.
- *Glendoveer is consistent with its runners.* Its cell is NXN (one race
  a year, front 150). The runners run +3.5% slower there than at their
  regionals three weeks before; those regionals sit at +5 to +7 on the
  board; bracket + ref lands within 1% of board + day every year
  (+7.2..+11.7 against +6.5..+11.5). If NXN still reads too high on the
  site, the question is the level of the championship cluster it is
  measured against, not Glendoveer alone.
- *Foot Locker is keyed in pieces.* `XC:22029` (Foot Locker Western
  Regional) holds three race days in twelve years; the rest of the
  regional and the national final are under other canonical ids (Mt.
  SAC's course, Balboa/Morley Field), each a thin cell answering a
  different question. `scripts/meet_cells.py --meet "Foot Locker" --meet
  "Champs Sports" --meet Eastbay` lists every cell those meets vote in,
  by year. Merge or key them before reading their boards.
- *Two data faults the day term absorbed:* Brookside Reservation 3200 m
  2024 at -20.7% bracket (a short course; day -11%), and one 12-row
  Brooks City Base 3200 m day at +31%. Without a day term on the site,
  the ratings from those days are wrong by that much.
- *The bracket engine's headline is not yet a comparison.* Error sd
  0.0428 on the 59% of held-out rows it covers, against the joint
  model's 0.0515 on 89%: the rows it covers are the easy ones. The
  ladder now writes each rung's per-row held-out predictions
  (`run_joint.holdout`, `XCP_HOLDOUT_DUMP`, `ladder_logs/<rung>_holdout.npz`),
  and `bracket_holdout` scores both engines on the rows both cover
  (`SAME ROWS, BOTH ENGINES`). That line exists after the next ladder
  run; until then the two numbers are not comparable. The engine's
  passes also stopped converging on the corpus (max change 0.161 from
  pass 4 to 30): two cells whose runners only race each other swap
  under a full step; damping is 0.5 now and the count of cells still
  moving is printed.

**Ultimook, and "recently is a lot faster than previously" (owner).**
The era split was built (`--era-years`) and never wired into the
pipeline, and turning it on would have broken the page: the solve keys
era cells `<key>@e<k>`, the page looks a venue up by its bare key, and
the go-live wrote the suffixed keys. Now `XCP_ERA_YEARS=2` reaches 08 and
08a, the go-live publishes each venue's latest solved era under the bare
key (`joint_golive.latestEraKeys`; the venue-key splitter drops the
suffix for the race-day table too), every row's own rating uses its own
era, and the ladder has an `era-2` rung. Adjacent eras are tied by a
random walk with a stated drift of 1% per era (`XCP_ERA_DRIFT` sets
it). **What an era can and cannot tell you, measured on a planted world
(`tests/test_era_publish.py`, checked term by term against the truth).**
The era cells track their rows faithfully. What no data can do is
separate an era's course change from the mean weather of the days in
it: an era-wide shift can sit in the era's difficulty or in its days'
race-day terms, the rows are indifferent, and only the priors split it.
The walk costs about `sigma2 / drift²` per era step; the days cost
`n_days · sigma2 / sigma_u²`. In XC, `sigma_u` is about 4%, so at the 1%
default the walk is the stiffer of the two until a venue has about
sixteen race days per era: a school course raced weekly moves freely,
while a once-a-year invitational with two to four days per era has any
real change booked into its day terms and its era barely moves. At 3%
the balance flips: such an era follows a real change, and also follows
the weather mean of its few days, which for four days is about 2%. That
is the whole tradeoff, and it is Ultimook's exact case. So read
Ultimook two ways after the run: its era cells (`scripts/venue_check.py
--venue Ultimook` lists the `@e<k>` cells) and its recent race-day terms.
Eras flat and recent day terms all negative is a course that changed
under a walk too stiff to follow it: run again with `XCP_ERA_DRIFT=0.03`
and read the `era-2-loose` rung against `era-2` and base. Eras flat and
day terms two-sided is a course that did not change, and the craziness
is elsewhere: its fields, which the field term now sees, or mud days,
which are race-day terms and stay so.

**The track conversion default (owner: "not using 0.0 difficulty for the
track conversions, which is now the default").** Right: a conversion
with no venue used the median applied effect over the sport's rows,
which is row-weighted, carries every indoor row, and was about a percent
off the anchor. For track it now uses difficulty 0.0, the average outdoor
track, the display zero (`conversions.venueEffect`); XC with no venue
still uses its median race. Both legs go through the same function, so
the round trip still closes (pinned).

**"The top is so far off the average" (owner).** That is what the
tilt-by-band table measures: per rating band, the course multiplier
applied against the one the residuals imply. Paste it, with the
field-strength-by-band table, and the two together say whether the top
needs a steeper tilt, a stronger field term, or neither.

**The clamp (owner: "what are you actually tilting?", "why not just
measure further? I'd prefer to extrapolate rather than remain
constant").** What is tilted is the course: a row's model is `a + h ·
(mu + d + u) + ...`, and `h = 1 − 0.0031 · (rating − 100)` is the share
of the course's difficulty (and of the sport level and the race day,
which are course-shaped) that a runner of that rating pays. A 100 pays
all of it, a 120 pays 94%, a 140 pays 88%. Until today `h` was evaluated
at the rating clipped to 70–140, so a 150 was charged for a course as a
140 would be. That is gone: the line now runs on (`TILT_RATING_LO/HI`
are 40 and 200, safety rails that keep `h` inside 0.69–1.19 and nothing
else), so a 150 pays 84.5% and a 160 pays 81.4%. On Mt. SAC that is
about 0.27 and 0.59 rating points less course credit than the clamp
gave. And the extrapolation is measured rather than assumed:
`run_joint.reportTiltByBand` regresses each rating band's residuals on
the course effect and prints the applied `h` against the implied one,
including for the bands above 140. If the 140+ rows show an implied `h`
well off the line, that is the next thing to change, with numbers.

Answers to the three questions asked on 2026-09-11, so they are on file:

- **Faster runners get less of a course's difficulty.** Yes, and it was
  already so: the tilt `h = 1 − 0.0031·(rating − 100)` multiplies the
  course, the sport level and the day, and since today runs on past 140
  instead of clamping (checked per band in the log). The new
  indoor term is tilted the same way; the distance offsets, altitude and
  the taper term are not, because those are costs of the event, the air
  and the field, not of the ground. The tilt is one global slope, not one
  per course, on purpose: an elite-only field cannot estimate a slope and a
  free per-cell slope is how the issue-156 exploit happened.
- **Fitness and rust are out of a course's difficulty.** Yes, by
  construction: the form curve and the opener rust are fitted jointly with
  the cells, so a course that hosts only November races is not charged
  with the season's form. The taper was the piece that was still landing
  in the course, and the season-end term is its home now.
- **Altitude depends on where the runner comes from.** In the solve, yes:
  a row's exposure is the venue's altitude less half the athlete-season's
  home altitude (`ALT_ACCLIM`), so residents' abilities are their
  sea-level speed and the cell is terrain, not the visitors' penalty. In a
  rating the credit is per race, the venue less half the field's mean home
  altitude, so everyone in one race keeps their finish order. Today added
  the event's share of the cost (an 800 a fifth of a 5000).
