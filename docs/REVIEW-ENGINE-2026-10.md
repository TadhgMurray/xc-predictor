# Engine review, October 2026

*Written 2026-10-24 for the owner, revised the same day after "the entire
point is you need to figure this out ... what a track rating is in terms of
xc" and "none of this forecast bs for ratings or 5k equivalent". It maps the
rating engine as it runs today, says what is redundant and what fights,
proposes one model to replace the stack, measures the XC↔track relation
inside it instead of asserting it, and gives the one run that settles it on
real data (§9). Nothing here touches production. The engine is
`engine/statespace.py`; `scripts/ss_eval.py` runs it on the pack.*

---

## 0. The short version

**The XC↔track relation can be measured, and there is a textbook way to do it.**

1. **What identifies it.** Every runner has one fitness that moves through
   time: a Kalman state with a walk, a personal season trend, a step each
   summer, a taper at the season's last race, and recovery after a break.
   Grass, the indoor oval and altitude are levels against the flat outdoor
   400. They are estimated by GLS *through* each runner's own trajectory
   (regression with state-space errors, de Jong 1991).

   The information comes from people who race on both surfaces close
   together in time, and each such pair counts by how far that runner's
   fitness could have moved in between. Examples:
   - an XC race and a fall track race two weeks apart;
   - a June 3200 and an August XC opener;
   - a November state meet and a December indoor mile.

   No window is chosen and no gap is asserted.
2. **What it needs, found on a planted world** (§4.4, §6):
   - **Several kinds of bridge.** December bridges alone (December XC
     finals, the first indoor races) are biased by the taper and the
     December break. They missed the gap by 1.3–3.5% seed to seed. With
     every kind in, it landed within 0.5% (XC) and 0.65% (indoor) of the
     planted value.
   - **Bridges that run on venues also raced in-season.** A venue raced only
     at bridge time has its own course number to absorb the gap.
   - **Many such venues.** With 10 regions the XC-vs-track level of the
     ratings wandered ±1% from seed to seed. With 40 regions it was within
     0.3%. The real corpus has thousands.

   The run in §9 counts all three on real data and refits without each kind
   of bridge. If those refits disagree, the gap is not determined by the
   data, and the report says so rather than printing a number.
3. **What each level reads.** The gap is measured per level (hs_m, hs_f,
   college_m, …). Each level is read at its own median runner, with a slope
   in ability fitted from runners seen on both surfaces.

   That slope needs care. A runner's ability is estimated from the same
   rows, so a naive regression is biased. On the planted world it read +7%
   per unit of log time with nothing planted. The EM step with the
   posterior covariance brings it to −2%.

   The course tilt has the same problem and does not yield to that fix, so
   it stays at the current engine's value. It moves ratings by tenths of a
   percent (§4.4).

   Kailo's published conversion has slope 0.72 (3.3% at 14:00, 8.7% at
   16:00 for men). That looks like the same dilution from the other side.
4. **The 5K column is a conversion, not a forecast.** A result's 5K
   equivalent is `exp(performance)`, where performance = log time less its
   course, day, race, grass or indoor level, altitude and event offset, all
   on the day it was run. Nothing is projected and nothing is calibrated on
   top. It is right exactly when the measured gap is right, and §9 checks
   it against LACCTiC's per-race track 5Ks.
5. **What LACCTiC and Kailo actually do** (research, §4.5):
   - **LACCTiC.** Since 21 October it scores each race against "how fit its
     runners are expected to be on the day, … an average improvement of
     0.2% a week": one rate for everyone. How it pins XC to the track 5K is
     not documented; the FAQ says the conversion is "for interpretability".
   - **Kailo.** It regresses track on XC. Its fitness has no in-season
     growth term.
   - **Neither** models a runner's own trajectory, and neither measures the
     surface gap through one. That is the opening.

**The rest of the engine**

6. **The engine is seven estimators in series, each correcting the one
   before it.** In order: normalisation, the joint solve, the bracket
   engine, the race-day refit, the go-live corrections, fill, and the board
   gates. Between them they carry about 460 module-level constants and 144
   `XCP_` switches. Most recent bug classes come from two early choices:
   - **ability is one number per athlete-season**, so a runner's fitness
     cannot move within a season;
   - **the race-day key is the venue-day**, so a division run on the wrong
     course cannot have its own day.

   The season curve, the 30-day window, the field check, the island check,
   season centring, the division guard, `flaggedFastDay` and `XC:fast` each
   patch one of the two.
7. **On the planted world, the proposed engine beats the current engine's
   skeleton on every held-out test** (§6, three seeds):
   - median time error is 0.2–0.7% lower;
   - head-to-head is 2–4 points higher on spring track predicted from the
     fall;
   - rating error is about a third lower on ordinary rows and halved in
     mis-measured divisions.

   Today's skeleton gets the gap asserted at the truth in these runs and
   still loses. A planted world proves the code does what it claims; only
   the pack says whether it wins on real races (§9).
8. **The Oct 23 drop is probably not the tilt alone** (§1). The "before"
   numbers come from OLD's ratings, frozen since the Oct 19 solve. Between
   that solve and the Oct 23 run, three commits moved college XC meets onto
   venues for the first time, which is exactly the population every test
   measures.

**Recommendations, in order**

1. **Run §9 once.** It takes about 6 hours, reads the pack, and writes
   nothing the site reads. It produces:
   - the measured gaps per level, with their bridges, the leave-one-kind-out
     refits and a regional check;
   - the paired forward scorecard against today's engine;
   - LACCTiC's order and XC→5000 tests on the new numbers, forward in time;
   - Kailo's rating snapshots scored on the races after each one.
2. **If the gaps hold across bridge kinds and the scorecard wins, build it
   out in the order of §8.** The fitness core comes first, then the race
   key, then retiring the go-live corrections it makes redundant.
3. **Stop judging runs on the three nightly tests.**
   - The LACCTiC order test is three D3 races, scored in-sample.
   - The "XC→5000 spread" is in-sample for us.
   - Hammerand is one pair, and its gap is a known weather artifact (the
     track heat credit takes the day's 35 °C peak for a 10k run at night).

   §7 replaces them.

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

**How to settle it without three 9-hour runs** (§9 part 1, all read-only):
1. Diff the model knobs in NEW's env against `env.old`.
2. Read `engine/data/run_changes/20261023_171317.json`, which lists the
   engine files changed and the courses that moved 1%+.
3. List the tfrrs XC meets placed by 06a in this run by season and tier, and
   which of the 150 runners' races are among them.
4. Price the tilt on the forward scorecard: `scripts/scorecard.py`, `own`
   against `hs`, same rows, about 2 h, writes nothing the site reads.

If the venue mapping is the cause, `b1b86fe` (no-state matches) is the first
thing to revert.

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
y_i = θ_p(t) + β_p·xc_i                      the runner: fitness that day, grass specialisation
    + f_ℓ(phase) + J_ℓ·seams + τ·peak_i + ρ·recovery_i     the population's share of fitness
    + G_g,ℓ(θ)                               the surface level: XC or indoor against the gauge
    + α_g·altitude_i + e_ℓ,d                 conditions: altitude, the track event offset
    + h_i·(c_c + w_v + v_r)                  course, venue-day, race (Student-t days and races)
    + ε_i                                    Student-t row residual

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
- **Removed (conditions):** G, the course, the day, the race, altitude and
  the event offset.
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
| stated, with the reason printed | the gauge (flat outdoor 400 = 0), κ (§4.4), the 600 m altitude floor, the event shares of the altitude cost (NCAA tables) |

---

## 5. How each current hack disappears

| today | under the model | caveat |
|---|---|---|
| **division guard**, `RACE_DAY_CAP` as threshold/clip/trigger, `flaggedFastDay`, division lean (Midlothian, Shamrock 2025/26, Mt. SAC 2024) | `v_r` is its own heavy-tailed effect inside `w_v`; a short-course division is one big `v_r`, and the venue-day and the other divisions are untouched | race key = (meet, div), already in the pack |
| **borrowed distances**, wrong-distance overrides, Dan Conway, Monte Vista +19% | a wrong distance is a near-constant log-time offset on one race, i.e. `v_r`. It is absorbed when the race has known runners, and listed (\|v_r\| > 5%) for a human | first-timer fields (MS) still need the distance |
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

`tests/test_statespace.py` pins the following:
- B beats A on held-out races and the spring track;
- a short division's rating stays within 1.5× ordinary;
- the gap is recovered within 1% with and without December XC;
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
3. **Posterior SDs** on ratings and courses, published with `n_results`;
   boards gated on the SD.
4. **The course eras' walk.** The prototype only splits by era.
5. **Distance and weather inside the model:**
   - one log-distance curve with a per-runner endurance slope (Blythe &
     Király's low-rank form) instead of the pool curves and event offsets;
   - weather as covariates in the same fit, with the race effect taking up
     what the hourly grid gets wrong (night races).
6. **Pipeline time.** The solve becomes minutes. The data steps are a
   separate look.

---

## 9. The one run

Paste each part into `tmux` on NEW, as root in `/srv/xc-predictor`.
- Production's checkout stays on master: the review branch is a separate
  worktree at `/srv/ss-review`.
- Nothing writes a table the site reads.
- Outputs go to `engine/data/ss/`.

**Postgres restarts unattended between 06:00 and 07:00 UTC.** Start part 2
between 07:00 and 12:00 UTC:
- the scorecard and `ss_eval` read the pack and finish in about 4–5 hours;
- the LACCTiC and Kailo steps read the database for about 30 minutes at
  the end;
- the whole thing is clear of the next restart.

**Part 1: what changed between OLD's Oct 19 solve and 20261023_171317.**
Read-only, a few minutes. Paste the output.

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
R=logs/20261023_171317
# 1. model knobs on NEW vs the env copied from OLD (an allow-list: no secrets)
KN='^(export )?XCP_(TILT_SCALE|POST_COLLEGIATE|RACE_|DIFFICULTY|SPORT_LEVEL|ERA_|ALTITUDE|INDOOR|GAUGE|BRACKET_|DAY_|FIELD_CHECK|ISLAND_CHECK|SEASON_TIE|FORM_TREND|EFFORT|IMPORTANCE|TEAM_POOL|XC_COURSE_SUSPECT|TF_TAU|GEOMETRY|DISTANCE_BY|CURVE_|EQUAL_TIME)'
echo "== env diff (< OLD, > NEW)"; diff <(grep -E "$KN" /etc/xc-predictor.env.old | sort) <(grep -E "$KN" /etc/xc-predictor.env | sort)
# 2. what the run itself recorded as changed
echo "== run_changes"; $P - <<'EOF'
import json, glob
for p in sorted(glob.glob("engine/data/run_changes/*.json"))[-2:]:
    d = json.load(open(p))
    print(p, "commit", str(d.get("commit"))[:9], "prev", str(d.get("previous_commit"))[:9],
          "previous run", d.get("previous_run_at"))
    print("  engine files:", ", ".join((d.get("engine_files") or [])[:40]))
    c = d.get("courses") or {}
    print("  courses compared", c.get("compared"), "moved 1%+", c.get("moved_1pct"), "new", c.get("new"))
    for r in (c.get("biggest") or [])[:15]: print("   ", r)
EOF
# 3. the three tests, both sides, last 4 runs
echo "== history"; tail -n 4 engine/data/run_history.jsonl | $P -c "
import sys, json
for l in sys.stdin:
    d = json.loads(l); m = d.get('metrics', d)
    print(d.get('run', d.get('log_dir', '?')), {k: m.get(k) for k in ('lacctic_order_ours','lacctic_order_theirs','lacctic_5000_ours','lacctic_5000_theirs','holdout_race_sd','day_scatter')})"
# 4. what 06a placed in this run, and how
echo "== 06a"; tail -n 25 $R/06a_tfrrs_location.log
ls $R | grep -i -E "tfrrs|location|leftover" | head
psql -P pager=off -c "SELECT substr(m.date::text,1,4) yr, count(*) meets, count(m.location_id) located, count(m.gps_lat) with_gps FROM meets_tfrrs m WHERE m.sport='XC' GROUP BY 1 ORDER BY 1 DESC LIMIT 6;"
# 5. Hammerand's two 10ks: time, normalized, the weather/altitude the row carries
psql -P pager=off -c "SELECT r.result_id, r.date, r.time_seconds, r.normalized_time, round((r.normalized_time/r.time_seconds)::numeric,4) nt_over_t, r.speed_rating, r.rating_pool FROM results_tf r WHERE r.result_id IN (277459367, 293998421);"
$P scripts/athlete_dump.py $(psql -At -c "SELECT person_id FROM results_tf WHERE result_id=277459367") 2>&1 | grep -E "10000|10,000|277459367|293998421" | head -8
```

If a query names a column that does not exist on NEW, paste the error and I
will fix it.

**Part 2: everything else, in one go.**

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
. deploy/solve_env.sh
L=/srv/xc-predictor/engine/data/ss; mkdir -p $L
# the review branch as a detached worktree beside production (production stays on
# master); its engine/data IS production's, so every script sees the real pack,
# fitted curves and Kailo snapshots
git fetch -q origin claude/sharp-lamport-p381q5
[ -d /srv/ss-review ] || git worktree add -q --detach /srv/ss-review FETCH_HEAD
git -C /srv/ss-review checkout -q --detach FETCH_HEAD
rm -rf /srv/ss-review/engine/data && ln -s /srv/xc-predictor/engine/data /srv/ss-review/engine/data
W=/srv/ss-review
# 0. the venues' elevations, for the altitude term (one small table)
psql -P pager=off -c "\copy (SELECT key, elevation_m FROM venue_elevation) TO '$L/venue_elevation.csv' CSV"
# 1. today's engine, forward holdout, tilt own vs hs, rows dumped (~2 h; production code)
$P scripts/scorecard.py --base "production --tilt-scale own" \
    --try "production --tilt-scale hs" > $L/tilt_scorecard.log 2>&1
# 2. the state-space engine: gap (+ jackknife, regional check, tilt profile),
#    score on the same rows, ratings, and as-of fits at two Kailo cuts (~3 h)
CUTS=$($P -c "import json,glob; c=sorted({json.load(open(p))['cut'] for p in glob.glob('engine/data/kailo/ratings_*.json')}); print(','.join(sorted({c[0], c[len(c)//2]})) if c else '')")
$P -u $W/scripts/ss_eval.py --repo /srv/xc-predictor --mode all \
    --base-dump engine/data/scorecard/ladder_logs/try_holdout.npz \
    --elevation $L/venue_elevation.csv --kappa-profile --cuts "$CUTS" > $L/ss_eval.log 2>&1
R=$(ls -t $L/ratings_*.npz | head -1)
# 3. LACCTiC, 40 races of 2025: ours today; the new engine; the new engine forward (~1 h, API-paced)
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 > $L/lacctic_now.log 2>&1
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --ours-npz $R > $L/lacctic_ss.log 2>&1
$P $W/scripts/lacctic_compare.py --season 2025 --top 40 --runners 600 --ours-npz $R --forward > $L/lacctic_ss_fwd.log 2>&1
# 4. Kailo: every saved snapshot, ours and the state-space engine beside Kailo
$P $W/scripts/kailo_ratings.py --ours-dir $L > $L/kailo.log 2>&1
echo DONE
```

Afterwards, take the review checkout away with
`rm /srv/ss-review/engine/data && git worktree remove --force /srv/ss-review`.
Remove the symlink first, so nothing can reach production's data.

**What to paste back** (one block, a few hundred lines):

```bash
cd /srv/xc-predictor; L=engine/data/ss
sed -n '/TRY AGAINST BASE/,$p' $L/tilt_scorecard.log | grep -E "SCORECARD|  (bias|median|p90|pairwise)|^    all " | head -16
sed -n '/=== GAP/,/=== SCORE/p' $L/ss_eval.log | grep -vE "^\[ss\] +[0-9]+ sigma" | head -160
sed -n '/=== SCORE/,/=== RATINGS/p' $L/ss_eval.log | grep -E "against the current|SCORECARD|  (bias|median|p90|pairwise)|^    (all|sport: |pool: )" | head -70
sed -n '/=== RATINGS/,$p' $L/ss_eval.log | grep -E "ratings ->|median 5K|as of" | head -20
for f in lacctic_now lacctic_ss lacctic_ss_fwd; do echo "== $f"; grep -A3 -E "ORDER \(|XC -> TRACK|CONSISTENCY|CROSS-SPORT" $L/$f.log | head -18; done
grep -B1 -A8 "SNAPSHOT" $L/kailo.log | head -80
```

**Reading part 2:**
- **The levels.** Each level's XC and indoor gap at its own median runner,
  with a slope in ability. Then:
  - the bridge table: kinds, venues, and how many venues are raced
    otherwise;
  - the leave-one-kind-out fits (if they agree within a few tenths, the gap
    is measured; if one kind moves it by percents, that kind carries it and
    its venues are what to inspect);
  - the regional check;
  - the tilt profile.
- **The scorecard.**
  - The first block is the tilt: own against hs.
  - The second is the new engine against today's, on the same rows: higher
    pairwise and lower median |err|, each beyond 2 SE, is a win.
  - Rung A against today's says whether the prototype's missing terms (era
    walk, weather inside) matter.
- **LACCTiC.** Ours today against theirs, then the new engine against
  theirs, season-wide and then forward. **Kailo** gets the same, on each
  snapshot.
