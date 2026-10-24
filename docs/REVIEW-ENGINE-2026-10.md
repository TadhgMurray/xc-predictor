# Engine review, October 2026

*Written 2026-10-24 for the owner, revised the same day after "the entire
point is you need to figure this out ... what a track rating is in terms of
xc" and "none of this forecast bs for ratings or 5k equivalent". It maps the
rating engine as it runs today, says what is redundant and what fights,
proposes one model to replace the stack, measures the XC↔track relation
inside it instead of asserting it, and gives the one run that settles it on
real data (§9). Revised again after "go deeper ... bad distances, course
matching, what can be removed, settle Oct 23": §0 leads with that round.
Nothing here touches production. The engine is `engine/statespace.py`;
`scripts/ss_eval.py` runs it on the pack.*

---

## 0. The short version

**What this round settled or built** (owner, 2026-10-24: "go deeper").

1. **The Oct 23 drop gets settled by an experiment, not an argument** (§1,
   §9 part 2). Today's nightly solve runs four times as a dry run: tilt
   `own` or `hs`, tfrrs venue placement kept or undone, on one frozen pack,
   with LACCTiC's pages cached so their side is identical. About 5 hours,
   nothing written. Until it runs, my read is unchanged: the placement is the
   likelier cause, because every test is college XC on tfrrs meets.
2. **A race's distance is weighed, never picked.** The race effect is a
   mixture: its ordinary effect, or that plus the offset of a distance its
   listed one is commonly confused with (5000↔3 mi, 6000↔8000, 3000↔3200,
   …). The rating carries the weighted offset. No distance is written, and
   no race is condemned for one. On a planted world with 4% of divisions
   mislabelled:
   - the worst tenth of their runners' rating errors fell from 6% to 2.4%;
   - the likeliest true distance was right for 93–94% of the races it
     flagged;
   - it flagged 45 of 31,223 ordinary races.

   The flagged list goes to a CSV for a person.
3. **Courses are matched from the runners who raced both.** Two cells are
   one course if their numbers differ by exactly what their listed distances
   say (the Mt. SAC 4715/4828 case). How often nearby cells are one course is
   learned per band of distance apart. Pairs are tied in proportion, never
   merged.
   - It found 163–170 of 180 planted alias pairs.
   - It tied none of 28–66 different courses placed 150–600 m apart.

   The first version judged pairs on the cells' own numbers and tied 15–18
   of those. Ratings barely move when the aliases are well raced. Its value
   is replacing four hand rules and producing a list a person can check.
4. **Every hand-set constant and correction is listed**
   ([REVIEW-ENGINE-2026-10-CONSTANTS.md](REVIEW-ENGINE-2026-10-CONSTANTS.md),
   generated from the code, with a test that fails on anything new and
   unlisted). There are 737 constants and 143 switches. The new engine makes
   unnecessary **184 constants and 54 switches**, and fits 28 more itself.
   13 are left unclear, each with its reason; the largest is the board's
   season aggregate.
5. **The engine went deeper.** Each piece was tested against a world that
   plants exactly what it is for (§6, table 5):

   | piece | effect on the planted world |
   |---|---|
   | each course's number walks season to season, step fitted | best next-season forecasts of three keyings |
   | weather fitted inside | ratings 14–45% closer; hot-day bias −1.3% → −0.4% |
   | a team running tempo together | tempo rows' error 4.8–5.5% → 3.3–3.7% |
   | each runner's own distance curve, as a fourth state | track ratings 2.1% → 0.7–0.8%; cross-country forecasts 0.03–0.04% worse, so it is priced separately on real data |
   | a rating SD on every result | available for gating boards |
   | a two-sided residual (bad days slow only) | made things worse; off |

**The XC↔track relation (the point of the review).**

6. **It is measured, not asserted.** Every runner has one fitness moving
   through time. Grass, the indoor oval and altitude are levels against the
   flat outdoor 400, estimated by GLS through each runner's own trajectory
   (regression with state-space errors). The information comes from people
   racing both surfaces close together in time: fall track during XC, summer
   track before August, December XC beside December indoor, and the
   indoor/outdoor overlap. No window is chosen.
7. **What it needs** (planted world, §4.4): several kinds of bridge
   (December alone missed by 1.3–3.5%), on venues also raced in-season, and
   many of them (±1% with 10 regions, 0.3% with 40). The run refits without
   each bridge kind; if those disagree, the report says the gap is not
   determined instead of printing a number.
8. **The 5K column is a conversion, not a forecast:** `exp(performance)`,
   the time less everything measured about the day. It is right exactly
   when the measured terms are, and §9 checks it against LACCTiC's track 5Ks
   and runners' own 5000s.
9. **The current engine is seven estimators in series**, each correcting
   the one before it. Most recent bug classes trace to two choices: ability
   is one number per athlete-season, and the race-day key is the venue-day.
   On the planted world the proposed engine beats today's skeleton on every
   held-out test, even with that skeleton handed the true gap (§6).

**Recommendations, in order**

1. **Run §9 once** (about 11 hours, all three parts in one paste each;
   start at 07:00 UTC). It writes nothing the site reads. It produces:
   - the Oct 23 answer;
   - the measured gaps with their bridges and leave-one-kind-out refits;
   - the distance and course lists;
   - the forward scorecard against today's engine;
   - LACCTiC (40 races, forward) and Kailo on the new numbers.
2. **Decide the tilt and the tfrrs placement from part 2** before anything
   new goes live.
3. **If the gaps hold and the scorecard wins, build it out in §8's order.**
4. **Stop judging runs on the three nightly tests.** That is 3 races
   scored in-sample, and one Hammerand pair whose gap is a known weather
   artifact. §7 replaces them.

---

## 1. The 20261023_171317 drop

| test | before (OLD's Oct 19 solve, read Oct 22) | 20261023_171317 | LACCTiC |
|---|---|---|---|
| LACCTiC order, 3 D3 races, 150 runners | 86.1% | 83.7% | 85.9% |
| XC → actual 5000, robust sd | 2.12% | 2.52% | 2.13% → 2.08% |
| Hammerand 10k, 29:20.5 vs 29:27.0, rating gap | 7.6 | 9.7 | — |

**What changed between the two solves.** The question names three things.
There were more.

| change | reaches | can it move order / 5000 / Hammerand? |
|---|---|---|
| `XCP_TILT_SCALE=hs` (first run) | `h` on every course, day and indoor term in the solve. College reads ~1.26× its own rating, so `h` falls ~0.1 for every collegian, and the slope of `h` against own rating steepens 26% inside college. | Order and 5000: **yes**, through course credit that now differs by course mix. Hammerand: **barely**; both tracks sit at the flat-400 zero and track days are not credited. |
| `XCP_POST_COLLEGIATE=1` (first run) | 4,384 post-collegiate athlete-seasons move to pro pools, about 1,154 of them out of college. | Order: **unlikely**, since the D3 runners are collegians in season and `e97f4b7` skips two-people ids. It can shift college pool means slightly, which is a level shift, not an order change. Hammerand: no. |
| `2b2a4cf`, `e97f4b7`, `b1b86fe`: tfrrs XC meets placed on venues (06a) by GPS, by `venue_name`, and by a no-state name that is "one location everywhere" | The pack's course cells, the bracket engine's **place and venue priors** (a college 8k cell is now pulled toward the HS cells at the same venue), the **venue-day race key** (college and HS races at one venue on one day share a day), **altitude** (elevation now known), and the field and island checks (both grouped by venue-day). Weather enters only at the next backfill, because 06a runs after 05. | Order and 5000: **yes, directly**. All three nightly races and the runners' other races are tfrrs D3 XC. `b1b86fe` can put a no-state meet on a same-named venue in another state, which brings the wrong cell, the wrong altitude and the wrong day. Hammerand: no. |
| The migration: first solve on NEW (Postgres 18, fresh load, env copied from OLD) | Everything, if `/etc/xc-predictor.env` on NEW differs from `env.old` in any model knob. | Any of the three. A one-line diff answers it (§9). |
| `39d64b4` (data_checks pins), `ebfa678` (virtual meets), `467b1dc` (form trend, off), data scraped since Oct 19 | normalized_time of re-pinned races, and the fields themselves | Possibly, through the pins. |
| LACCTiC's own side | Their numbers move nightly. The 5000 spread for theirs went 2.13 → 2.08. | Theirs only. |

**Best read.** The order and 5000 drops are most likely the tfrrs venue
mapping, with the tilt as a contributor:
- The tests are college XC on tfrrs meets.
- The location change is the only one that can move a single course's
  number by several percent.
- The tilt moves college course credit by roughly 0.1 × (difference in
  course difficulty), a few tenths of a percent between courses.

Hammerand cannot come from any of the three named changes. Both rows are
track rows at the flat-400 zero, in the same pool and at the same distance,
so pool-level and tilt effects cancel in the gap. What is left is row-level:
- the heat credit (the known cause of this pair's gap, 35 °C peak vs a night
  race);
- the altitude credit;
- the 10k distance offset band, if the ratings straddle a band edge
  (DIST_BANDS 105/120/135 on own rating).

§9 prints both rows' factors.

**How it is settled: one experiment, two by two, on the same data**
(§9 part 1). Today's nightly solve is run four times as a dry run, with
the tilt `own` or `hs` and the tfrrs venue placement kept or undone. All
four use the same pack, code and LACCTiC pages, and write nothing the site
reads. Each arm is the pipeline's own 08_golive command, read out of
`deploy/run_pipeline.sh` at run time, with `--golive` swapped for
`--golive-dry`.

| arm | tilt | tfrrs placement |
|---|---|---|
| A | own | undone |
| B | hs | undone |
| C | own | kept |
| D | hs | kept (what runs now) |

- **The placement undone** (`scripts/oct23_variant_pack.py`): the placed
  meets are those with GPS now that had none in the migration's dump of OLD
  (`/srv/migrate/db`). Their rows' course keys go back to
  `XC:name:<venue>:d<dist>`, the key they had before. The weather the
  placement switched on comes back off. Each row is normalised again with
  and without that day's weather and compared with its stored value, so the
  script finds out from the data whether the pack carries it. Championship
  keys and rows placed through an athletic.net meet stay.
- **The tilt**: `XCP_TILT_SCALE` per arm. The arm's ratings are converted
  to track 5Ks through the site's own `conversions.fiveKForRating`, with
  that arm's `engine_scale`, `sport_gain` and `distance_offset` rows pinned
  in place of the live tables (`scripts/oct23_score.py`).
- **Scored** on the nightly's three tests, plus the order test forward in
  time. `lacctic_compare --cache` keeps LACCTiC's side identical across
  the four arms. A table shows how far each arm moved every pool's
  ratings.

Reading it:
- **Tilt effect** = (B − A + D − C) / 2.
- **Placement effect** = (C − A + D − B) / 2.
- **Interaction** = what is left.
- If neither effect reaches the size of the drop, the cause is elsewhere:
  the migration's env (part 1 prints the diff), the data_checks pins, or
  the data scraped since Oct 19.

Each arm takes about an hour on the solve. Run them one after another, and
not across 06:00–07:00 UTC (the arms read `venue_elevation` at start).

**Separately, how much the three nightly tests can say.**
- The order test is 3 races, so races, the unit that varies, number three.
- Both systems' numbers were fitted with the scored race in.
- The 150 runners are sampled on LACCTiC's ability order.
- Its 95% interval on ours-minus-theirs was −0.19 to +1.06 points.
- The 5000 test is in-sample for us: the joint ability already saw those
  track rows. It also pairs spring track with the *following* fall.
- Hammerand is one pair, judged against an assumption that the conditions
  were equal.

These are good smoke alarms and poor judges. §7 replaces them as the gate.

---

## 2. The current model, mapped

A rating today is built in seven stages. Each table lists the term, what it
fixes, and whether it is fitted or stated. File:line references are at commit
`679cb6a`.

### 2.1 Normalisation: raw time → `normalized_time` (backfill, step 05)

`nt = t_raw × F_dist × F_geom × F_era ÷ w_mult`
(`engine/normalize_distance.py:1394-1425`).

| factor | form | fitted / stated | what it is for |
|---|---|---|---|
| distance (pool mode, default) | `exp(g(log target) − g(log d))`. One curve shape per pool, XC and TF pairs merged since 09-19. The anchors are asserted: elem 2414, ms 3200, hs 5000, college_m 8000, college_f 6000 (`fit_distance_exponent.py:125-138`). | shape fitted from same-athlete pairs within 21 days; anchors stated | a time at any distance → the pool's anchor distance |
| distance floors and shape rules | `MIN_LOCAL_EXP` 1.04, monotone exponent, `EXP_SANE` (0.85, 1.30), `K_PRIOR` 1.06 | stated | stop the curve doing something unphysical where pairs are thin |
| geometry | length and banking spline | fitted; **off** (`XCP_GEOMETRY`) | lane, length and banking. Track cells carry it instead. |
| era | `exp(−level[season])` per sport, band and gender; XC tied to track by `BRIDGE_BANDS` | fitted; 2020-21 benchmarks distrusted | shoes and population drift |
| weather | temperature and soil splines against the venue's fortnight normal, plus wind, precip and snow | fitted, two-way FE; accepted band [0.75, 1.25]; reference 13 °C | the day's conditions |
| distance resolution | ~15 rules in `backfill_normalize.py`: per-result pins, (meet, div) overrides and drops, tfrrs pins, overrides may only *lower* a distance, the borrowed-distance rule (college only since 10-24), per-pool raw floors, pace sanity [3:00, 20:00]/mile, `_POOL_MAX_DISTANCE_XC`, virtual/TT/repost withholds, 0 m treated as none, and the 165 MB `corrections.py` | hand rules + generated overrides | mis-listed distances, mixed divisions, fake times |

### 2.2 The joint solve (`engine/joint_solve.py`, `engine/run_joint.py`; step 08)

`y = ln(nt)`, and row by row:

```
y = a[athlete-season] + h·(mu[group] + d[cell] + u[race]) + beta·sc + amp·f(pool, day)
    + r[pool]·first + e[event, band] + g·lz + q·tz + k[group]·alt + imp·front + h·ind·indoor
    − fixed offsets (asserted level, asserted indoor, division-guard excess, effort E[s])
```

| term | per | prior | production | fixes |
|---|---|---|---|---|
| `a` ability | athlete-season | flat | fitted | the rating |
| `mu` sport level | sport group | — | **asserted** `XCP_SPORT_LEVEL=0.0583` | XC vs track, which the data cannot identify (sport is season) |
| `d` course | course×distance, 2-year eras | N(0, τ²), τ EB-fitted on 2+-race cells, capped `TAU_MAX` XC 0.0364 / TF 0.0161; era walk `ERA_DRIFT_SD` 0.010 stated; venue-distance tie `VENUE_DIST_SD` 0.025 | fitted, but the **bracket engine's** numbers are the ones published | course |
| `u` race day | `XCP_RACE_KEY=venue`: one per venue-day | N(0, σ_u²), EB-fitted | fitted | the day |
| tilt `h` | row | `TILT_K = −0.031` per 10 points, stated; rails 40..200 | `XCP_TILT_SCALE=hs` since 10-23 | slow runners lose more on a hard course |
| `beta` sport offset | athlete | ridge 0.5 | **off** under `--sport-level` | grass specialists |
| `f` season curve | pool | 13 knots at 30 days, curvature EB-fitted; knot of 1 Oct pinned; amplitude tilted `AMP_TILT_PER_POINT` 0.01135, rails 0.15–1.80 | fitted | in-season fitness, as a population average |
| `r` opener rust | pool | none | fitted | first race of a season |
| `e` event offsets | pool × track distance × 4 rating bands | table prior, `DIST_PRIOR_SD` 0.03, walk 0.02, calibrated share 0.9 | fitted | what the distance curve gets wrong on the track |
| `g` endurance slope | athlete-season | ridge 2.0 | fitted | per-runner distance specialism |
| `k` altitude | sport group | mean 0.035/km, ~sd 0.01 under `--altitude-fit`, floor 600 m, acclim 0.5, event shares | fitted, near its prior | altitude |
| `imp` field strength | pool × sport | N(0, 0.02²) | **off** (`XCP_IMPORTANCE=none`) | stacked fields |
| indoor | pool | — | **asserted** 0.003 | indoor vs outdoor (indoor is season) |
| effort `s≥0`, season tie, form trend `q` | — | — | off (on in `solve_env_v2.sh`) | bad days, season-to-season continuity, per-runner trend |
| row weights | row | asymmetric Huber 2.5 slow / 1.5 fast | on | outliers |
| division guard | (meet, div) inside the venue-day | flag at 10% (`RACE_DAY_CAP`) from the race's median | on | one division on the wrong course moving the venue-day (Midlothian) |

Solve: CG under an EM loop of 5 outer passes, about 1,560 CG iterations,
about 3 h on 60M rows historically (now inside 08_golive's hour).

### 2.3 The bracket engine (`engine/bracket_engine.py`, published courses)

Course readings come from the top half of each race, each runner against
their own other races within ±`XCP_BRACKET_WINDOW` = 30 days.

| constant | value | fixes |
|---|---|---|
| `RACE_SAT` | 5 | "a 10-result venue should not have +17%" |
| `PRIOR_GROUP` / `PRIOR_GROUP_BY` / `PRIOR_RACES` | 1 / XC 1, TF:out 2.5, TF:in 1 / 2 | shrinkage toward the group and the era history |
| `FIELD_ADJUST` | on, slope fitted ≥50 races | "Mt. SAC +6.2 is way too easy": deep fields read easier |
| gauge `flat400`, `assume400` | each flat outdoor 400 = 0.0 exactly | the see-saw zero |
| `INDOOR_CENTRE` + `pin` + gates (−0.3%, +2.0%) clamp | 0.003 | indoor read easier than outdoor |
| `XC_LEVEL_MODE` | off | pinned 09-21, reverted 09-22 |
| `PLACE_RADIUS_M` / `PRIOR_PLACE` / venue links | 400 m / 2 / on | courses at one place pulled together |
| `WELL_KNOWN_RACES` | 4 | course scale read only from well-raced courses |
| window | 30 days | fitness drift, measured |

### 2.4 Go-live (`engine/joint_golive.py`): per-row rating

`rating = 100 · pm_c / (nt / exp(eff))`. `eff` is the sum of:

| term | constants | fixes |
|---|---|---|
| course `h·delta` (bracket) | — | — |
| race day, XC only, leave-self-out, refit 4× against the bracket courses, division guard re-run, division lean, **season centring**, clip ±10%, `XC:fast` = min(u, 0), `flaggedFastDay` | `RACE_DAY_CAP` 0.10, `XCP_DAY_DIVISION_SHRINK`, `XCP_DAY_CENTRE` | Midlothian, Newhall (fast season), Ultimook (good-weather years), Shamrock 2025/26 |
| track distance offset, altitude credit, per-level sport gain on track rows | `XCP_SPORT_LEVEL_POOLS` college .0037 / hs .0092 / ms .0191 / elem .0186 | track under-rated |
| **field check** | `FIELD_CHECK_MIN` 10, `_FLOOR` 0.01 (opener 0.02), `_CAP` 0.06; deflate only | Marmonte 2018 at Pepper Tree: a hard-raced league meet rated above its runners' seasons |
| **island check** | `ISLAND_CHECK_MIN` 10, `_FLOOR` 0.03, `_CAP` 0.06; deflate only | CSU and Wyoming 2020-21 topping the all-time college board |
| **course guard** | 15% from the median XC cell → unrated | Al Shirley at Pima CC, +35% |

### 2.5 Fill and boards

- `fill_ratings` prices unsolved rows at a per-pool constant.
- Boards then drop a row for:
  - twins, wheelchair, impossible, outlier;
  - out of scope, low grade trust, outside the pace band;
  - dist-corrected, HS XC over 5,250 m, no distance;
  - record pace, anchor mismatch;
  - rating outside 20..200;
  - **rating above the pool's ceiling + 10** (`pool_ceiling.py`: hs_m 150, college_m 130, ...).
- A season rating is the 80th percentile of the race ratings, with rows 20 or more points below the median dropped.

### 2.6 The count

| layer | module constants | env switches touched |
|---|---|---|
| normalisation and its fitters | ~230 | — |
| joint solve + run_joint | ~104 | 144 distinct `XCP_` names across the rating path |
| bracket engine | 37 | |
| go-live | 18 | |
| boards | 54 | |

Some of these are honest physics: the altitude event shares, the NCAA
indoor factors. Most are thresholds for a correction.

---

## 3. What is redundant, and what fights

**One quantity, several estimators**

| quantity | estimated by | consequence |
|---|---|---|
| "this race ran fast or slow" | race-day `u` (shrunk, leave-out, clipped, fast-only, division-leaned), the field check, the island check, season centring, the course guard | Each sees what the others left over, through different keys and thresholds. The field and island checks stack to −12% on one race and claw back a `flaggedFastDay` credit. The published per-race day is not the day the rating used. |
| XC ↔ track level | `XCP_SPORT_LEVEL` (solve), `XCP_SPORT_LEVEL_POOLS` (go-live), the season curve's free fall-to-spring step, the conversions' XC median effect and `sport_gain` | Three answers to one unidentifiable number (§4.4). They agree only when the tables match the rows (issue 306). |
| course difficulty | joint `d` (fitted, then thrown away) and bracket `D` (published), with different priors, guards and windows; the bracket reads the joint's tilted residual | The division guard runs in both frames, with different flags (`run_joint.py:3320` vs `3528`). |
| fitness through time | season curve, opener rust, 30-day bracket window, form trend (off), season tie (off), era cells (`a` per year vs `d` per era is an exact null direction), season centring | Five partial answers to "a runner's fitness moves". None of them is a per-runner trajectory. |
| distance | the distance curve (05), event offsets `e` (08), the venue-distance tie, XC cells keyed by distance, the division guard, the course guard, `corrections.py`, data_checks, the anchor gate, and the board distance gates | A distance error is handled up to nine times, and most of those handlers only remove rows. |
| the slow tail | Huber weights, ability weights, the effort term (v2), the board's 20-point season drop, `XC:fast` | |

**Terms that fight**

- **`d` vs `u` within a race** are exactly collinear (ENGINE.md §2). Under
  the shipped `venue` key, races no longer nest in cells, but
  `nestedPosteriorVar` and `cellOfRace` still assume they do. The E-step
  for τ and σ_u is therefore approximate in production.
- **The tilt against the course scale.** `TILT_K` is stated. The bracket
  path then rescales every course by a per-sport scale chosen so that
  "implied = applied", which re-estimates the tilt under another name.
  `XCP_TILT_SCALE=hs` changes `h` in the solve. The site's `tilt.py` reads
  the same variable but computes its factor with a different estimator
  (`pool_view.repFactor`).
- **The field check vs `FIELD_ADJUST` vs the race-day term.** The bracket
  makes a deep-field venue read easier, the day term credits the fast day,
  and the field check then deflates the race against its runners' seasons.
  All three respond to one fact: those runners ran fast that day.
- **The field and island checks vs abilities.** Both use the model's own
  ratings of the runners' other races, which were fitted with the deflated
  race in. This is feedback, never iterated to a fixed point.
- **Season centring vs `XC:fast`.** Centring runs first, so in a slow season
  ordinary days become "fast" and get credited.

**Defects found in passing** (not fixed here; each is small):
- **`08a` and the ladder do not score the shipped model.**
  - Rung `base` uses run_joint's defaults: the field term, the cell key, no
    eras, indoor 0.012.
  - `no-altitude` ≡ base on race splits, because altitude is forced off
    (`run_joint.py:2064-2072`).
  - `no-sigma-u-floor` ≡ base, because the floor is already {0, 0}.
  - `venue-day` changes the split itself.
- **`D_te` covariates are computed on the held-out subset**:
  - `openers()` marks most held-out rows as openers;
  - `lz` and `sc` are centred to about 0;
  - `e_idx` is never re-banded.

  Held-out predictions therefore carry rust, but never the endurance slope or
  the right event band (`run_joint.py:997`, `398-409`; `joint_solve.py:1613`).
- **`run_report` keeps the last regex match.** The stored `day_scatter` is
  therefore TF, not XC.
- **The Mt. SAC sentinel uses `LIMIT 1` with no `ORDER BY`.**
- **`fill_ratings` re-prices every NULL row.** That includes rows the course
  guard deliberately unrated; only the pace band keeps them off the boards.
- **`solve_env.sh` defaults `XCP_RACE_EFFECT_SPORTS=XC`**, while the go-live
  comments describe `XC:fast` as live. One of them is stale. §9 prints which
  one ran.

---

## 4. The proposed model

### 4.1 One equation

For row *i*: person *p*, date *t*, level ℓ (the pool: hs_m, college_f, …),
race *r* inside venue-day *v*, course cell *c* (course × distance, as
today), and surface *g* ∈ {XC, indoor, outdoor}:

```
y_i = θ_p(t) + β_p·xc_i + η_p·x_i            the runner: fitness that day, grass, their own distance curve
    + f_ℓ(phase) + J_ℓ·seams + τ·peak_i + ρ·recovery_i     the population's share of fitness
    + G_g,ℓ(θ)                               the surface level: XC or indoor against the gauge
    + α_g·altitude_i + e_ℓ,d + W_g(weather)  conditions: altitude, event offset, temperature, wind, wet
    + h_i·(c_c,s + w_v + v_r + t_r,team)     course (this season), venue-day, race, team in race
    + ε_i                                    Student-t row residual

  x_i      ln(distance / 5000); η_p fitted only from distances inside one
           surface, so it never trades with grass
  c_c,s    the course's number this season: a walk from season to season
           whose step is fitted ("era_walk"); pairs of cells that may be one
           course are tied in proportion to P(same) ("course_tie")
  v_r      the race effect, or that plus a standard distance's offset when
           the race may not be its listed distance, by posterior weight
           ("dist_mix"); no distance is ever written

  y        ln(time) on ONE scale: the flat outdoor 5000 m equivalent (the
           pool's normalised time moved to 5000 m by its own curve)
  θ_p(t)   a Kalman state: random walk q per day, the runner's own trend this
           season (redrawn each summer), a step at each summer seam
  G        μ_g,ℓ + λ_g·(θ − C): the XC (indoor) level for level ℓ, with its
           slope in ability; outdoor = 0 is the gauge
  h        1 + κ·(θ + fitness − ref), κ stated at the current engine's
           TILT_K (0.31 per unit log time); see §4.4
```

**What a rating removes and what it keeps.**
- **Removed (conditions):** G, the course, the day, the race (with its
  likely distance error), a team's shared shortfall, altitude, the weather
  and the event offset; and the runner's own distance curve, which moves a
  result to their own 5000.
- **Kept (the runner that day):** fitness, the taper, the summer step,
  recovery from a break, and their grass specialisation.

A championship run on a taper is a real performance, and so is a slow first
race back.

### 4.2 What each piece replaces

| piece | replaces |
|---|---|
| θ as a state, smoothed | athlete-season `a`; the 30-day bracket window (the walk's variance *is* the window, fitted); opener rust; the form trend; the season tie; season centring; the island check; the per-pool anchor juggling (`rescaleToPool`, `_scalePool`, the HS factor, `13b_pool_consts`, `XCP_TILT_SCALE`) |
| f, J, peak, recovery by GLS | the 13-knot curve, its amplitude tilt and rails, the pinned knot; `XCP_SPORT_LEVEL_POOLS` and the go-live sport-gain shift (the fall-to-spring step is now *measured* per level, as fitness) |
| G per level, measured | `XCP_SPORT_LEVEL` (0.0583), `XC_TRACK_GAP`, `XCP_BRACKET_XC_LEVEL_MODE`, the indoor centre / pin / gates / geometry levels, the conversions' XC median effect |
| `v_r` race inside `w_v`, both heavy-tailed | the division guard, `RACE_DAY_CAP` (all four of its jobs), `flaggedFastDay`, the division lean, `XC:fast`, the course guard; most distance overrides become a flag list |
| altitude by GLS | `ALT_PRIOR_MEAN`, `ALT_ACCLIM`, the resident/visitor credit logic (a resident's state is their sea-level fitness, and the coefficient comes from runners seen at several elevations) |
| Student-t rows | Huber's two constants, the ability weights, the effort term |
| everything else by EM | τ caps, priors stated in races, `RACE_SAT`, floors |
| the distance mixture on `v_r` | `own_race_shift` and the race condemnations it feeds, `propose_distances`, the override audits (`audit_overrides`, `kill_bad_overrides`), and every machine-written distance in `corrections.py`. A record-beating time no distance explains stays a data error (`impossible_race`). |
| course pairs judged on shared runners | `PLACE_RADIUS_M` / `PRIOR_PLACE` (the 400 m place prior), `SIBLING_DIST_TOL`, `VENUE_DIST_*`, and hand venue merges (Mt. SAC 4715/4828) |
| the course walk | `--era-years 2` and the era drift prior |
| the weather inside | the backfill's weather multiplier and its night-race errors (the race effect takes what a grid cell misses) |
| η_p, the runner's distance curve | `SLOPE_RIDGE`'s per-athlete-season endurance slope, the rating-band split of the event offsets (`DIST_BANDS`) |
| a team in a race | the league-meet tempo problem (owner, 2026-10-18), which today has no term |

### 4.3 What the 5K column is

`five_k_s = exp(perf)`, where perf = y less the conditions above. It is the
seconds that performance is worth on a flat outdoor 400 at 5000 m, that day.
Nothing is projected forward, nothing is calibrated on top, and there is no
pool clock.

- For a track 5000 on a flat outdoor oval, it is the time run, less that
  day's and that race's measured conditions and any altitude.
- For an XC race, it is the time less that course, that day, that race and
  the measured XC level at that runner's ability.

If the column reads 2% fast against real track times, one of those measured
terms is wrong by 2%. That is a finding about the term, not a cue for a
correction layer. §9 checks it against LACCTiC's per-race track 5Ks and
against each runner's own track 5000s.

### 4.4 How the XC↔track relation is measured

**The problem.** Nobody races XC in May, so a fall XC race and a spring 5000
differ by the surface *and* six months of fitness. Earlier attempts in this
repo hit this wall:
- `xc_tf_bridge.py` found the XC−indoor, XC−outdoor and indoor−outdoor pair
  differences failing to close by 1.44%, at 20–90 SE;
- the merged gauge measured +1.27% against an expected +5.8%;
- the go-live then stacked three asserted numbers on top.

**The textbook answer.** Regression with state-space errors.
1. Every runner's Kalman filter runs on the times and on every shared
   column at once (the augmented filter).
2. The innovations give the exact GLS estimate of the levels, the curve,
   the taper and the rest, with each runner's trajectory integrated out.
3. A bridge counts in proportion to how sure the filter is of that runner's
   fitness between the two races:
   - two races 10 days apart weigh a lot;
   - a June 3200 and an October XC race weigh little;
   - the population's average drift is estimated from everyone's
     within-sport sequences, so it is not asked of the bridge.

**What carries the information.** Times when both surfaces are raced:
- December XC (NXN, Foot Locker, JO, club) beside December indoor;
- fall track meets during XC;
- summer track before August XC;
- February–March, when indoor and outdoor overlap (this one pins indoor).

**The traps, each found on the planted world and each handled:**

| trap | what it did | what fixes it |
|---|---|---|
| December only | taper and break bias: off by 1.3–3.5% | the peak and recovery terms, and fall and summer bridges |
| a bridge venue raced only at bridge time | its own course number absorbs the gap | counted per kind (`bridgeReport`: "raced otherwise"); a kind whose venues are all bridge-only gives no evidence |
| few venues | the ratings' XC level ±1% with 10 regions | many venues: within 0.3% with 40 |
| the courses' average sliding against μ | μ_XC = +0.22 on a world planted at +0.06 | each group's courses, days and races recentred to mean 0 (the old `recentreLevels` move) |
| the filter's prior mean (θ near 7 in log seconds, prior 0) | a pseudo-row moved the gap +1.3% along its flat direction | diffuse start (P0 = 1e4) and z centred; the kernel matches a direct person-fixed-effect solve to 6e-6 |
| the ability slope from the same rows | +0.07 slope with none planted (and −0.44 tilt) | the slope as a loading on θ, by EM with Var(θ) and Cov(θ, grass), from runners seen on both surfaces only: −0.02. The tilt stays stated. |
| indoor and outdoor as one "track" season | the indoor taper went unexplained: indoor −1.2% | the taper per surface |

**How it is checked on real data** (§9, `ss_eval --mode gap`):
1. **The bridge table:** per kind and level, rows, runners, venues, and the
   share of those venues raced otherwise.
2. **Leave one kind out:** the fit is rerun without each kind. Agreement
   means the gap is determined; disagreement means it is not, and the report
   says which kind carries it. On the planted world, dropping fall track
   moved XC by 0.8–3.2% and dropping the indoor/outdoor overlap moved indoor
   by 1.1–4.4%; every other kind moved its level by a few tenths. Those two
   are the load-bearing bridges there; real data will name its own.
3. **The regional check:** in each 3°×3° cell, the dual-sport runners'
   grass offsets should average zero. A cell that does not is a region
   whose XC sits wrong against its own tracks. On the planted world this
   check tracks the true regional error (r = 0.93–0.96, at about 0.4 of its
   size).

   ⚠ It cannot see a region that is an island, where runners, courses and
   tracks shift together. That is a connectivity limit, shared with track,
   and it is what the island check was patching.
4. **A level beyond ±25% is not printed as a number.** It is flagged as
   either a scale mismatch between a pool's XC and track anchors or no
   bridge.

**What stays stated.**
- **The gauge:** a flat outdoor 400 is 0.
- **The tilt κ.** Held-out error on the planted world is flat in it to the
  third decimal (0 to 0.6), and its EM step is biased by the course
  estimates' own errors. It stays at the current engine's value, and the
  training-likelihood profile is printed on real data (`--kappa-profile`).
  At real course spreads it moves a rating by tenths of a percent.

### 4.5 What others do (researched 2026-10-24)

| system | XC↔track | in-season fitness | source |
|---|---|---|---|
| LACCTiC | not documented. TiC "can be read as 5k equivalents of a typical race"; the conversion is "for interpretability and is not necessarily an indicator of 5k fitness". A separate "projected 5K" is fitted from December ratings to the next spring's 5Ks, which bundles the off-season with the conversion. | since 21 Oct 2026: +0.2%/week for everyone until 1 Dec | FAQ and constants in their public JS bundle |
| Kailo (KPACE) | ln(track) = 0.7235·ln(XC) + 1.8396 (men), 0.8135 / 1.2534 (women). The sub-1 slope looks like regression dilution. | none; fitness updated race by race | blog of 23 Oct 2026, JS bundle |
| Tully Runners | additive constant (+114 s men), fitted on fall-2020 COVID track 5Ks against prior XC ratings | judgement | tullyrunners.com |
| Slaney | none | a month slope and a year slope; reports them trading against course difficulty ("ill-posed") | CrossCountryStats README |
| NCAA indoor facility factors 2012 | same athletes on different facilities over 5 seasons; checked by excluding early- and late-season marks | — | NCAA PDF |
| swimming SCY↔LCM | no official model; closest-date same-swimmer matching gives ≈1.04, not the folklore 1.11; taper bias noted | — | Vercruysse 2019 |

Physiology for scale:
- grass costs about 5% more energy than a track at the same speed (Sassi
  2011), with no speed interaction;
- a taper is worth 0.5–6%, about 3% typical (Mujika & Padilla 2003);
- 14 days off costs 4% of VO₂max (Houmard 1992);
- in-season improvement runs about 0.2–0.35% a week (LACCTiC; Slaney;
  NIRCA, Karr et al. 2025).

### 4.6 Fitting it, and what it costs

Each pass:
1. **Shared effects by GLS:** the augmented filter per runner, numba,
   parallel over runners.
2. **Runner states:** RTS smoother.
3. **Courses, days and races:** shrunk means, t-weights, recentred.
4. **Slopes and every variance:** by EM.

Measured on 4 cores, for the whole pass on a 1.2M-row planted world with
two levels: 2.6 µs per row, at about 1.5 GB. The GLS part grows with the
square of the shared columns a runner touches. With the real pack's dozen
levels, budget under a minute a pass on 24 cores for 30M rows (6 seasons),
and about 40 GB at peak. Thirty passes is about 30 minutes for the whole
fit, against 08_golive's hour plus the bracket engine and corrections.

The other big pipeline steps are data work this review did not profile:
04_grade_sanity, 03_pro_flag, 07_pack, rankings, 04e_weather_grid and
10f2_projection.

### 4.7 Constants: fitted, cross-validated, stated

| kind | which |
|---|---|
| fitted (EM or GLS) | σ, the walk q, the season trend sd, the seam sd, the grass sd, τ per surface, the day and race sds, the curve's curvature, every level's gap, its slope in ability, the taper, recovery, altitude per surface, the event offsets |
| cross-validated on the forward scorecard | the t degrees of freedom, the era walk once added, whether the track race day is credited |
| stated, with the reason printed | the gauge (flat outdoor 400 = 0), κ (§4.4), the 600 m altitude floor, the event shares of the altitude cost (NCAA tables), Riegel's 1.06 for a wrong distance's cost (the same K the distance tools use), the standard distances a race may really have been, the 21 days after which a season counts as over (the taper), the temperature knots |
| search bounds, not decisions | course pairs looked for within 3 km and 1.35× in listed distance; a pair is judged only with 3+ runners who raced both in one season |

---

## 5. How each current hack disappears

| today | under the model | caveat |
|---|---|---|
| **division guard**, `RACE_DAY_CAP` as threshold/clip/trigger, `flaggedFastDay`, division lean (Midlothian, Shamrock 2025/26, Mt. SAC 2024) | `v_r` is its own heavy-tailed effect inside `w_v`; a short-course division is one big `v_r`, and the venue-day and the other divisions are untouched | race key = (meet, div), already in the pack |
| **borrowed distances**, wrong-distance overrides, Dan Conway, Monte Vista +19%, **`own_race_shift` condemnations, `propose_distances`, the override audits** | a wrong distance is a near-constant log-time offset on one race. The race effect is a mixture: its ordinary effect, or that plus the offset of a distance the listed one is commonly confused with (5000↔3 mi, 6000↔8000, 3000↔3200, …), by posterior weight. The rating carries the weighted offset; the listed distance is never changed and no race is condemned for it. The likeliest other distance and its weight go to a CSV for a person | first-timer fields (MS) have no known runners: the weights stay near the prior and the rating is wide (its SD says so) |
| **the place prior** (400 m), **sibling distances**, **venue-distance ties**, hand merges (Mt. SAC 4715/4828) | every pair of cells of one surface within 3 km is judged on the runners who raced both in a season: one course if their numbers differ by what the listed distances say, two if not. The share of same-course pairs per band of distance apart is learned; a pair is tied in proportion; nobody linking it, it is left alone | |
| **`--era-years 2`**, the era drift prior | a cell per course-season, tied season to season by a walk whose step is fitted | |
| **the weather multiplier in front of the engine** | temperature, wind and wet ground fitted inside (`ss_weather_export.py`, `--weather`); the backfill's multiplier is taken back off | normalisation keeps distance and era until §8 point 5 |
| **no term for a team at tempo** (league meets) | a team's shared shortfall in one race, nested in the race, heavy-tailed, out of the rating | needs the pack's team code (it has it) |
| **`XC:fast`** (Ultimook), and no track race day at all | symmetric credit, shrunk by evidence; the walk tells a tactical race from a slow course. The track heat credit's night-race errors (Hammerand: 35 °C peak for a race run at 22 °C) are the same kind of race-level offset | crediting the track day is scored on the forward test first |
| **field check** (Marmonte at Pepper Tree) | that race's `v_r` reads fast because the runners' trajectories say what they should run that week | |
| **island check** (CSU/Wyoming 2020-21), **Michigan early-2000s** | runners carry their state through the island; where no runner connects, the posterior SD is wide and boards gated on it do not show it | gating changes what thin eras show: owner's call |
| **season centring** (Newhall) | a fast season is fitness, so it is in θ | |
| **30-day window**, **form trend**, **season tie**, **opener rust** | q, the season trend, the seam, recovery: fitted | |
| **`TILT_K`, `XCP_TILT_SCALE`** | the tilt reads θ, an absolute scale; no pool label enters | κ stays stated (§4.4) |
| **`XCP_SPORT_LEVEL`, `XCP_SPORT_LEVEL_POOLS`, indoor centre / pin / gates** | measured per level (§4.4) | only if the leave-one-kind-out fits agree |
| **per-pool anchors in the solve**, `13b_pool_consts`, the HS factor | one 5000 m scale; pools are a display transform | normalisation keeps its curves until §8 point 5 |
| **5K clock calibration** (rejected) | the 5K column is a direct conversion (§4.3); a wrong 5K is a wrong measured term, and it shows | |
| **course guard** (+35% Pima CC) | a course that far out has few known runners and a wide SD; flag and gate | |
| **pool ceilings + 10** | an SD gate does it honestly | keep as a sanity net at first |
| **the bracket engine** | gone: one course estimate, with its SD | |

---

## 6. The engine on a planted world

`engine/statespace_world.worldHard` plants the calendar's real overlaps and
biases:
- December XC for the best tenth only;
- indoor starting after a break, with the break costing 2.5% at three
  weeks;
- a 1% taper on every season's last race on each surface;
- fall and summer track for a few;
- 6% of divisions on a mis-measured course, and 5% bad days;
- a tilt of 0.30;
- **the gap at +6% XC and +1% indoor.**

`scripts/ss_synthetic.py` prints the tables below. Rung **A** is the same
code held to today's skeleton:
- athlete-season ability;
- one day per venue-day;
- Gaussian days;
- the gap **asserted at the truth**.

It has no bracket engine and no field or island check, because those are
fixes for what A gets wrong.

About 300k rows and 5,600 runners per world; seeds 0 / 1 / 2.

**1. Held out** (median |e|; head-to-head; e = ln(actual/predicted))

| held out | rung | bias | median \|e\| | race-centred \|e\| | head-to-head |
|---|---|---|---|---|---|
| 10% of venue-days | A | +0.22 / +0.14 / −0.06% | 1.60 / 1.56 / 1.53% | 1.14 / 1.14 / 1.15% | 90.4 / 90.6 / 90.1% |
| | **engine** | +0.36 / +0.17 / −0.00% | **1.43 / 1.39 / 1.36%** | **1.02 / 1.02 / 1.03%** | **91.3 / 91.9 / 91.0%** |
| the rest of the XC season from mid-October | A | −1.46 / −2.11 / −2.03% | 2.33 / 2.54 / 2.46% | 1.47 / 1.42 / 1.43% | 90.7 / 90.4 / 91.0% |
| | **engine** | **+0.02 / +0.03 / −0.28%** | **1.84 / 1.89 / 1.78%** | **1.42 / 1.36 / 1.38%** | **92.0 / 90.9 / 92.0%** |
| spring outdoor track from everything before it | A | −0.63 / −0.62 / −0.52% | 2.35 / 2.36 / 2.38% | 1.63 / 1.68 / 1.69% | 83.2 / 82.2 / 81.0% |
| | **engine** | **+0.13 / +0.11 / +0.11%** | **1.91 / 1.93 / 1.93%** | **1.35 / 1.34 / 1.35%** | **85.3 / 86.3 / 85.2%** |

**2. Ratings against the planted performance** (median |error|)

| | ordinary XC | ordinary track | mis-measured division | XC level of the ratings against track |
|---|---|---|---|---|
| A (gap asserted at the truth) | 1.65 / 1.33 / 2.34% | 0.60 / 0.57 / 0.56% | 2.37 / 2.56 / 2.94% | −1.52 / −1.06 / −2.33% |
| **engine** (gap measured) | **1.11 / 0.97 / 0.91%** | **0.44 / 0.42 / 0.37%** | **1.06 / 1.10 / 1.07%** | **−0.91 / +0.75 / +0.10%** |

**3. The gap, measured** (+ = slower than the flat outdoor gauge;
"realised" is the planted gap against the average of this world's planted
courses, which is how the model defines it)

| | XC (seeds 0 / 1 / 2) | indoor |
|---|---|---|
| realised | +7.06 / +5.56 / +5.09% | +0.84 / +0.96 / +0.79% |
| **engine** | **+7.00 / +5.48 / +4.62%** | **+0.20 / +1.37 / +0.75%** |
| naive 30-day pair difference | +6.34 / +6.28 / +4.48% | +0.72 / +4.49 / +1.98% |
| engine without December XC | +6.91 / +5.60 / +4.68% | +0.20 / +1.28 / +0.73% |
| engine without fall track | +9.82 / +2.25 / +3.86% | +0.57 / +0.87 / +0.63% |
| engine without summer track | +6.98 / +5.49 / +4.61% | +0.16 / +1.40 / +0.73% |
| engine without December indoor | +6.73 / +5.67 / +4.71% | +0.17 / +1.08 / +0.57% |
| engine without the indoor/outdoor overlap | +6.15 / +6.34 / +4.88% | −4.18 / +5.22 / +1.84% |

Taper, planted −1.00%: measured −0.78 / −1.19 / −1.00%.

**4. The XC-vs-track level of the ratings against the number of venues**

| regions (venues) | the ratings' XC level against track, seeds 0 / 1 / 2 | rms |
|---|---|---|
| 10 | −1.03 / +0.76 / +0.08% | 0.74% |
| 40 | +0.16 / +0.03 / +0.29% | 0.19% |

What this does and does not show:
- **The walk and the GLS do the work.** The engine measures the XC gap to
  within 0.5% and indoor to within 0.65% in every seed. Rung A has the
  true gap handed to it and its XC ratings still sit 1–2.3% off, because
  its season-constant ability puts the fall-to-spring change in the wrong
  place.
- **The naive pair difference misses indoor by up to 3.5%.** That is the
  repo's old closure error.
- **The leave-one-out rows show which bridges carry the gap in this world:**
  fall track for XC, the indoor/outdoor overlap for indoor. Real data will
  name its own.
- **Planted, not real.** Weather errors, mislinked people and timing errors
  are not in it. §9 decides.

**5. This round's terms, each against the world that plants what it is for**
(seeds 0 / 1; median |rating error| against the planted performance;
forward = median |e| on the held-out month; the tables above were made
before these terms existed)

| term | planted | without | with | forward without → with |
|---|---|---|---|---|
| distance mixture (`dist_mix`) | 4% of divisions ran another distance than listed (5000 that was 4000, 3 mi or 6000; 1600 that was 1500 or 2000) | mislabelled rows 1.21 / 0.98%, worst tenth 6.3 / 5.9% | **0.92 / 0.72%, worst tenth 2.4 / 2.4%** | ordinary rows unchanged |
| course pairs (`course_tie`) | 15% of meets key their course under a second name, half listed at 4828 for the same 5000; different courses 150–600 m from another | — | true pairs found **170 / 163 of 180**; nearby different courses tied **0 of 28 / 0 of 66** | ratings unchanged (the alias cells are well raced) |
| | the same, aliases thin (2.5% of meets) | — | found 82 of 127 / 75 of 104 (the rest nobody links); **0** wrong | worst tenth 2.25 → 2.22% |
| course walk (`era_walk`) | each course drifts 0.8% a season, re-routed now and then | one number per course: track 0.61 / 0.77% | **0.48 / 0.55%**; drift found 0.72 / 0.94% | next season **4.21 → 4.20 / 4.18 → 4.09%** (a number per season, untied: 4.38 / 4.46%) |
| team in a race (`team_race`) | 4% of (race, school) run 3–8% slow together | tempo rows 4.76 / 5.52% | **3.30 / 3.69%** | **1.909 → 1.876 / 1.976 → 1.908%** |
| the runner's distance curve (`endurance`, a fourth state) | 800 / 1600 / 3200 on the track, each runner's own slope (sd 3% per unit ln distance) | track 2.09 / 2.15% | **0.83 / 0.66%**; slope sd found 2.96 / 3.01% | track rows better at every distance; **cross-country 5000s 0.03–0.04% worse** (1.965 → 1.989 / 1.911 → 1.944% overall) |
| weather inside (`weather`) | race run up to 15 F cooler than the day's recorded peak; the stored multiplier keyed on the peak | stored multiplier: XC 1.44 / 1.06%, track 0.86 / 0.70%, hot days −1.28 / −0.54% | **XC 1.20 / 0.91%, track 0.47 / 0.45%, hot days −0.37 / +0.31%** | 1.880 → 1.909 / **1.946 → 1.870%** |
| two-sided residual (`two_piece`) | 5% bad days, slow only | 1.17 / 0.94% | 1.20 / 0.95% | 1.845 → 1.868 / 1.889 → 1.925%: **worse; off** |

Two of these were wrong the first time and are worth knowing about:
- **Course pairs judged on the cells' own numbers merged 15–18 nearby
  different courses.** Once two cells are tied, their days soak up the real
  difference, and the cells then look alike. With nobody racing both (a
  high-school course beside a college one), the difference is not
  identified at all. Judging only on runners who raced both cells in a
  season fixed it.
- **The distance curve as a step after the level made forecasts worse**
  (1.94 → 1.98%), because a noisy slope was subtracted as if known. As a
  fourth state in the runner's filter, its uncertainty reaches the level.
  That cut the loss to 0.03–0.04% on cross-country, and improved every
  track distance.

`tests/test_statespace.py` pins the following:
- B beats A on held-out races and the spring track;
- a short division's rating stays within 1.5× ordinary;
- the gap is recovered within 1% with and without December XC;
- mislabelled races: the tail of their ratings falls by 30%+, nothing
  else pays, the listed distance is never changed, and the likeliest
  distance is right for 85%+ of flagged races;
- course pairs: 80%+ of true pairs found, no nearby different course tied;
- the course walk finds its drift and forecasts no worse than one number
  per course;
- team tempo, the distance curve and weather each beat their absence on
  the world that plants them;
- `ss_eval` runs end to end (gap with jackknife, regional check and
  altitude; score; ratings) on a pack-shaped file.

---

## 7. The evaluation plan

### 7.1 One gate, the same rows for every engine

The unit of evidence is the **race**. Every held-out design removes whole
races, and every SE is a bootstrap over whole races
(`forward_holdout.pairedLines`).

| design | train | score | tests |
|---|---|---|---|
| **forward (the gate)** | rows before the validation season | that season; the sealed season once, at the end | what the site is asked |
| **spring track** | everything before 1 March | that spring's outdoor track | the XC↔track relation and the 5K column, end to end |
| **race** | all but 10% of venue-days | those days | interpolation; comparable with 08a |

**Metrics, on rows both engines predicted.** e = ln(actual/predicted).
- bias, median |e| and p90 |e|;
- head-to-head per race, mean over races;
- race-centred median |e| (ability only);
- interval coverage;
- breakdowns by sport, pool, training races, new/seen course and month;
- the paired change ± its race-bootstrap SE.

A change is real beyond 2 SE.

**The decision rule.** The engine replaces the core if, on forward:
- head-to-head and median |e| are better beyond 2 SE overall;
- no pool with 2,000+ rows is worse beyond 2 SE;
- the spring-track bias is within ±0.5%;
- the measured gaps agree across the leave-one-kind-out fits.

### 7.2 Against LACCTiC

`lacctic_compare --ours-npz` reads the engine's per-result 5K. `--forward`
builds the order test from each runner's **earlier** races only, in both
systems.

Run it over `--season 2025 --top 40 --runners 600`, which is 40 races
rather than 3. Report:
- **order**, with its race-bootstrap interval;
- **XC → track 5000:** each system's XC number against the runner's actual
  5000s;
- **consistency.**

Both systems' numbers are fitted with the season in, so the order test is
fair between them but flatters both.

### 7.3 Against Kailo

1. `ss_eval --mode asof --cuts <each saved snapshot's cut>` fits on races
   before the cut and writes every runner's flat 5K fitness on that day.
2. `kailo_ratings --ours-dir engine/data/ss` scores it beside our published
   basis and Kailo's `fitness_5k_s` on the races after the cut: order and
   level-free spread, on runners both rate.

This is the clean, foresight-free test. (The as-of fitness is used here
only to evaluate. It is never a rating.)

### 7.4 Free with every fit

The filter's one-step predictions use each runner's earlier races only (the
shared effects come from the full fit, a small leak, stated). Scored on the
last two seasons, they are a nightly smoke alarm over millions of races,
where the LACCTiC test has three.

---

## 8. If it wins: the order of work

Each step ships alone, is scored on the forward scorecard against the step
before, and keeps every output table's shape.

1. **The core.**
   - θ as a state on one scale;
   - the measured levels;
   - nested heavy-tailed days keyed (meet, div) inside venue-day;
   - altitude fitted.

   The go-live keeps its shape (rating = 100·pm/exp(perf_pool)) and the
   5K column becomes `five_k_s`.
2. **Turn off what the core makes redundant, one at a time, each scored:**
   - the division guard and lean;
   - `XC:fast`;
   - the field check;
   - the island check;
   - season centring;
   - `XCP_SPORT_LEVEL(_POOLS)`;
   - the indoor pin and gates;
   - the course guard (to a flag);
   - the bracket engine.
3. **Distances weighed, never written.** The race-effect mixture
   (`dist_mix`) goes live and the machine-written distance machinery is
   turned off, scored at each step: `own_race_shift`'s condemnations,
   `propose_distances`, the override audits. Owner-verified overrides stay
   as data. The flagged-race CSV replaces the worklists.
4. **Courses matched from runners.** `course_tie` replaces the place prior,
   the sibling-distance tolerance and the venue-distance ties; the
   course-pair CSV replaces hand merges. `era_walk` replaces `--era-years`.
5. **Posterior SDs** on ratings (`perf_sd`) and courses, published with
   `n_results`; boards gated on the SD.
6. **Conditions and people inside the model:** weather (the backfill's
   multiplier comes off; normalisation keeps distance and era), a team in a
   race, the runner's own distance curve (priced on real data first: on the
   planted world it fixes track ratings and costs cross-country forecasts
   0.03–0.04%). One log-distance curve in place of the pool curves is the
   step after.
7. **Pipeline time.** The solve becomes minutes. The data steps are a
   separate look.

---

## 9. The one run

Three parts, pasted into `tmux` on NEW as root in `/srv/xc-predictor`.
- Production's checkout stays on master. The review branch is a separate
  worktree at `/srv/ss-review` whose `engine/data` is a symlink to
  production's, so every script sees the real pack and fitted curves.
- Nothing writes a table the site reads, or a file in `engine/data` that
  the site reads. The Oct 23 arms write to `/srv/oct23`, the engine to
  `engine/data/ss/`.
- **About 11 hours in all.** Start part 2 at 07:00 UTC, right after the
  Postgres restart window, and run part 3 straight after it. Each part reads
  the database only at its start and its end; neither is reading it at 06:00.
- **Disk:** the Oct 23 part keeps two packs and four arms' solve files.
  Check that `df -h /srv` shows 60 GB free first.

**Part 1: what changed between OLD's Oct 19 solve and 20261023_171317.**
Read-only, a few minutes. Paste the output.

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
R=logs/20261023_171317
# 1. model knobs on NEW vs the env copied from OLD (an allow-list: no secrets)
KN='^(export )?XCP_(TILT_SCALE|POST_COLLEGIATE|RACE_|DIFFICULTY|SPORT_LEVEL|ERA_|ALTITUDE|INDOOR|GAUGE|BRACKET_|DAY_|FIELD_CHECK|ISLAND_CHECK|SEASON_TIE|FORM_TREND|EFFORT|IMPORTANCE|TEAM_POOL|XC_COURSE_SUSPECT|TF_TAU|GEOMETRY|DISTANCE_BY|CURVE_|EQUAL_TIME)'
echo "== env diff (< OLD, > NEW)"; diff <(grep -E "$KN" /etc/xc-predictor.env.old | sort) <(grep -E "$KN" /etc/xc-predictor.env | sort)
# 2. what the run itself recorded as changed
echo "== run_changes"; $P - <<'PYEOF'
import json, glob
for p in sorted(glob.glob("engine/data/run_changes/*.json"))[-2:]:
    d = json.load(open(p))
    print(p, "commit", str(d.get("commit"))[:9], "prev", str(d.get("previous_commit"))[:9],
          "previous run", d.get("previous_run_at"))
    print("  engine files:", ", ".join((d.get("engine_files") or [])[:40]))
    c = d.get("courses") or {}
    print("  courses compared", c.get("compared"), "moved 1%+", c.get("moved_1pct"), "new", c.get("new"))
    for r in (c.get("biggest") or [])[:15]: print("   ", r)
PYEOF
# 3. the three tests, both sides, last 4 runs
echo "== history"; tail -n 4 engine/data/run_history.jsonl | $P -c "
import sys, json
for l in sys.stdin:
    d = json.loads(l); m = d.get('metrics', d)
    print(d.get('run', d.get('log_dir', '?')), {k: m.get(k) for k in ('lacctic_order_ours','lacctic_order_theirs','lacctic_5000_ours','lacctic_5000_theirs','holdout_race_sd','day_scatter')})"
# 4. what 06a placed in this run, and how; and whether the migration's dump of OLD is still there
echo "== 06a"; tail -n 25 $R/06a_tfrrs_location.log
psql -P pager=off -c "SELECT substr(m.date::text,1,4) yr, count(*) meets, count(m.location_id) located, count(m.gps_lat) with_gps FROM meets_tfrrs m WHERE m.sport='XC' GROUP BY 1 ORDER BY 1 DESC LIMIT 6;"
ls -d /srv/migrate/db && du -sh /srv/migrate/db; df -h /srv | tail -1
# 5. Hammerand's two 10ks: time, normalized, the weather/altitude the row carries
psql -P pager=off -c "SELECT r.result_id, r.date, r.time_seconds, r.normalized_time, round((r.normalized_time/r.time_seconds)::numeric,4) nt_over_t, r.speed_rating, r.rating_pool FROM results_tf r WHERE r.result_id IN (277459367, 293998421);"
```

If a query names a column that does not exist on NEW, paste the error and I
will fix it. If `/srv/migrate/db` is gone, part 2 needs `--all-tfrrs` on
its first line (it then treats every tfrrs XC meet with GPS as placed).

**Part 2: the Oct 23 drop, settled** (§1; about 5 hours). Four dry runs of
today's nightly solve, two packs × two tilts, the same code and the same
LACCTiC pages for all four.

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
# the review branch as a detached worktree beside production
# ! the symlink comes off BEFORE any checkout, so git can never write through it
W=/srv/ss-review
git fetch -q origin claude/sharp-lamport-p381q5
[ -L $W/engine/data ] && rm $W/engine/data
[ -d $W ] || git worktree add -q --detach $W FETCH_HEAD
git -C $W checkout -q -f --detach FETCH_HEAD
rm -rf $W/engine/data && ln -s /srv/xc-predictor/engine/data $W/engine/data
O=/srv/oct23; mkdir -p $O
# today's pack, frozen (with its date), and the same pack without the tfrrs placement
cp --preserve=timestamps engine/data/packed_XC_TF.npz $O/pack_now.npz
$P -u $W/scripts/oct23_variant_pack.py --repo /srv/xc-predictor --pack $O/pack_now.npz \
    --out $O/pack_noplace.npz > $O/variant.log 2>&1
# the four arms: the pipeline's own 08_golive, dry (~1 h each)
for arm in "A own noplace" "B hs noplace" "C own now" "D hs now"; do
  set -- $arm
  PY=$P bash $W/scripts/oct23_arm.sh /srv/xc-predictor $O/pack_$3.npz $2 $O/$1 > /dev/null 2>&1 \
    || echo "arm $1 FAILED: tail $O/$1/arm.log"
  $P $W/scripts/oct23_score.py --repo /srv/xc-predictor --arm-dir $O/$1 > $O/$1/score.log 2>&1
done
$P $W/scripts/oct23_score.py --repo /srv/xc-predictor --table $O/A $O/B $O/C $O/D > $O/table.log 2>&1
# the nightly's three races, then 40 races of 2025 forward; LACCTiC read once, cached for all four
for a in A B C D; do
  $P $W/scripts/lacctic_compare.py --race 12194,12158,11632 --runners 150 \
      --ours-npz $O/$a/five.npz --cache $O/lacctic > $O/$a/lacctic3.log 2>&1
  $P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --forward \
      --ours-npz $O/$a/five.npz --cache $O/lacctic > $O/$a/lacctic40.log 2>&1
done
echo OCT23 DONE
```

**Part 3: the state-space engine on real data** (about 6 hours).

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
. deploy/solve_env.sh
W=/srv/ss-review; L=/srv/xc-predictor/engine/data/ss; mkdir -p $L
# 0. inputs: the venues' elevations; each row's race-day weather and the multiplier the backfill applied
psql -P pager=off -c "\copy (SELECT key, elevation_m FROM venue_elevation) TO '$L/venue_elevation.csv' CSV"
$P -u $W/scripts/ss_weather_export.py --repo /srv/xc-predictor --out $L/weather.npz > $L/weather.log 2>&1
# 1. today's engine, forward holdout, rows dumped (~2 h; production code)
$P scripts/scorecard.py --base "production --tilt-scale own" \
    --try "production --tilt-scale hs" > $L/tilt_scorecard.log 2>&1
# 2. the new engine without the runner's distance curve, on the same rows (prices it: it
#    fixes track ratings and cost cross-country forecasts 0.03-0.04% on the planted world)
$P -u $W/scripts/ss_eval.py --repo /srv/xc-predictor --mode score \
    --base-dump engine/data/scorecard/ladder_logs/try_holdout.npz --elevation $L/venue_elevation.csv \
    --weather $L/weather.npz --era-walk --drop endurance > $L/ss_noend.log 2>&1
# 3. the whole engine: gap (+ jackknife, regional check, tilt profile), the distance and course
#    lists, score on the same rows, ratings, as-of fits at two Kailo cuts (~3 h)
CUTS=$($P -c "import json,glob; c=sorted({json.load(open(p))['cut'] for p in glob.glob('engine/data/kailo/ratings_*.json')}); print(','.join(sorted({c[0], c[len(c)//2]})) if c else '')")
$P -u $W/scripts/ss_eval.py --repo /srv/xc-predictor --mode all \
    --base-dump engine/data/scorecard/ladder_logs/try_holdout.npz \
    --elevation $L/venue_elevation.csv --weather $L/weather.npz --era-walk \
    --kappa-profile --cuts "$CUTS" > $L/ss_eval.log 2>&1
R=$(ls -t $L/ratings_*.npz | head -1)
# 4. LACCTiC, 40 races of 2025: ours today; the new engine; the new engine forward (cached pages)
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --cache /srv/oct23/lacctic > $L/lacctic_now.log 2>&1
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --ours-npz $R --cache /srv/oct23/lacctic > $L/lacctic_ss.log 2>&1
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --ours-npz $R --forward --cache /srv/oct23/lacctic > $L/lacctic_ss_fwd.log 2>&1
# 5. Kailo: every saved snapshot, ours and the state-space engine beside Kailo
$P $W/scripts/kailo_ratings.py --ours-dir $L > $L/kailo.log 2>&1
echo DONE
```

Afterwards, take the review checkout away with
`rm /srv/ss-review/engine/data && git worktree remove --force /srv/ss-review`
(the symlink first, so nothing can reach production's data), and
`rm -rf /srv/oct23` once the numbers are in.

**What to paste back** (one block, a few hundred lines):

```bash
cd /srv/xc-predictor; L=engine/data/ss; O=/srv/oct23
echo "== OCT23"; grep -E "^\[variant\]" $O/variant.log | head -30
for a in A B C D; do echo "-- $a"; head -2 $O/$a/arm.log | cut -c1-300; grep -E "Hammerand|5K$|with a 5K|pools with no" $O/$a/score.log
  grep -A2 -E "ORDER \(" $O/$a/lacctic3.log | head -3; grep -A2 -E "XC -> TRACK" $O/$a/lacctic3.log | head -3
  grep -A3 -E "ORDER \(" $O/$a/lacctic40.log | head -4; grep -A2 -E "XC -> TRACK" $O/$a/lacctic40.log | head -3; done
head -60 $O/table.log
echo "== ENGINE"; tail -3 $L/weather.log
sed -n '/TRY AGAINST BASE/,$p' $L/tilt_scorecard.log | grep -E "SCORECARD|  (bias|median|p90|pairwise)|^    all " | head -16
grep -E "terms:|against the current|  (bias|median|p90|pairwise)|^    (all|sport: )" $L/ss_noend.log | head -30
sed -n '/=== GAP/,/=== SCORE/p' $L/ss_eval.log | grep -vE "^\[ss\] +[0-9]+ sigma" | head -220
sed -n '/=== SCORE/,/=== RATINGS/p' $L/ss_eval.log | grep -E "against the current|SCORECARD|  (bias|median|p90|pairwise)|^    (all|sport: |pool: )" | head -70
sed -n '/=== RATINGS/,$p' $L/ss_eval.log | grep -E "ratings ->|median 5K|as of" | head -20
for f in lacctic_now lacctic_ss lacctic_ss_fwd; do echo "== $f"; grep -A3 -E "ORDER \(|XC -> TRACK|CONSISTENCY|CROSS-SPORT" $L/$f.log | head -18; done
grep -B1 -A8 "SNAPSHOT" $L/kailo.log | head -80
```

**Reading part 2 (Oct 23).**
- Per arm: the order test on the nightly's three races and on 40 races
  forward, XC → 5000, and Hammerand. Tilt effect = (B − A + D − C) / 2;
  placement effect = (C − A + D − B) / 2.
- `variant.log` says how many placed meets and rows were re-keyed, and
  whether the pack carried the weather the placement switched on (it decides
  from the rows, not from a setting).
- The table: each arm's median rating move per pool against arm A. It says
  which change moved whom before any test does.
- If neither effect reaches the size of the drop (order 86.1 → 83.7, 5000
  spread 2.12 → 2.52), the cause is elsewhere. Part 1's env diff is the
  next place to look.

**Reading part 3 (the engine).**
- **The levels.** Each level's XC and indoor gap at its own median runner,
  with a slope in ability. Then:
  - the bridge table: kinds, venues, and how many venues are raced
    otherwise;
  - the leave-one-kind-out fits (if they agree within a few tenths, the gap
    is measured; if one kind moves it by percents, that kind carries it and
    its venues are what to inspect);
  - the regional check;
  - the tilt profile.
- **Distances and courses.** How many races the mixture thinks were not
  their listed distance, the 25 likeliest with meet and division, and the
  course pairs it thinks are one course under two keys, with how many
  metres apart. The full lists are `distance_<date>.csv` and
  `courses_<date>.csv` in `engine/data/ss`. These are for a person to look
  at; the ratings already carry them as weights. ⚠ A division that ran
  3.6% fast on a day its other divisions did not is exactly what a 5000
  that was 3 miles looks like; the rating is the same either way, but the
  "likeliest distance" is a question for a person, not a verdict.
- **The scorecard.**
  - The first block is the tilt: own against hs.
  - `ss_noend` is the engine without the runner's distance curve; the
    `ss_eval` SCORE block is the whole engine. Both are against today's
    engine on the same rows, so the difference between them prices the
    distance curve, by sport (on the planted world it helped every track
    distance and cost cross-country forecasts 0.03–0.04%). Higher pairwise
    and lower median |err|, each beyond 2 SE, is a win.
  - Rung A against today's says whether the state-space skeleton alone
    matters.
- **LACCTiC.** Ours today against theirs, then the new engine against
  theirs, season-wide and then forward. **Kailo** gets the same, on each
  snapshot.
