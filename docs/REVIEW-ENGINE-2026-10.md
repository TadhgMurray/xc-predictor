# Engine review, October 2026

*Written 2026-10-24 for the owner. It maps the rating engine as it runs
today, says what is redundant and what fights, proposes one model to replace
the stack, and gives the evaluation that decides between them. Section 9 is
the paste block. Nothing here touches production. The prototype is
`engine/statespace.py` and runs on the pack.*

---

## 0. The short version

**Findings**

1. **The engine is seven estimators in series, each correcting the one
   before it.** In order: normalisation, the joint solve, the bracket
   engine, the race-day refit, the go-live corrections, fill, and the board
   gates. Between them they carry about 460 module-level constants and 144
   `XCP_` switches. Most of the recent bug classes come from one structural
   choice made early: **ability is a single number per athlete-season**. The
   season curve, the 30-day bracket window, the field check, the island
   check, season centring, the form trend and the season tie each patch the
   fact that a runner's fitness actually moves.
2. **The second root cause is the race-day key.** A venue-day spans every
   division and distance at the venue. A division run on the wrong course
   therefore cannot have its own day. The division guard, the 10% cap,
   `flaggedFastDay`, the division lean, the course guard, `XC:fast`, and
   most of the distance-override machinery are all ways of coping with that.
3. **Several pieces estimate the same quantity twice.** The XC↔track level
   is set in three places: `XCP_SPORT_LEVEL` in the solve,
   `XCP_SPORT_LEVEL_POOLS` at go-live, and the season curve's free
   fall-to-spring step. "Did this race run fast?" is answered by four: the
   race-day term, the field check, the island check and season centring.
   Course difficulty comes from two engines that use different tilts and
   different guards.
4. **The XC↔track gap cannot be identified from results, and for prediction
   it does not need to be** (§4.4). A forecast from XC to track depends on
   the gap plus the season curve's fall-to-spring step, and only weakly on
   how that total is split (through the curve's smoothness). That is why
   "measuring" it has failed every time, and why the honest move is to
   state it once and choose the curve's smoothness by the spring-track
   holdout. The 5K column should be the model's forecast of the runner's
   spring track 5000. That is observable, so it is calibrated by
   construction, with no correction layer on top.
5. **LACCTiC now models fitness gain**, though crudely. Since mid-October
   their FAQ says each race is scored against "the field's expected fitness
   that day, one rate for everyone, +0.2%/week" (ISSUES-RUNNING, 2026-10-22).
   They have no per-runner trajectory, and neither do we. That gap is where
   both systems can be beaten.
6. **On a planted world, the proposed model beats the current engine's
   structure** on every held-out test (§6):
   - +1.3 to +1.5 points of head-to-head on held-out races, and +2.8 to
     +3.2 on spring track predicted from the fall;
   - 0.2–0.4% lower median time error;
   - 0.8–0.9% median rating error on mis-measured divisions, against 3.8–4.1%
     today (4.9–5.1% with `XC:fast`).

   A planted world proves the code does what it claims. **Only the pack can
   say whether it beats the engine on real races.** §7 and §9 give the run
   that settles it, in minutes rather than hours.
7. **The Oct 23 drop is probably not the tilt alone** (§1). The "before"
   numbers come from OLD's ratings, frozen since the Oct 19 solve. The
   Oct 23 run was the first solve on NEW, and four days of changes lie
   between the two. Three of those changes moved college XC meets onto
   venues for the first time: GPS, venue-name matching, and no-state
   matching. All three tests are college-only, and two of them are D3 XC
   only. §9 has the read-only commands that split the candidates.

**Recommendations, in order**

1. Run the §9 attribution block before changing anything else. Then run the
   prototype against the current engine on the forward validation season
   (§9 part 2). It reads the pack and writes nothing the site reads.
2. If the prototype wins on the paired scorecard, build it out in the order
   of §8. It replaces the season-ability core first (that is where the gain
   is), then the race key, then the go-live corrections it makes redundant.
   Normalisation stays as is until the end.
3. Whatever the engine, change how accuracy is judged:
   - The nightly LACCTiC test is three fixed D3 races, scored in-sample. A
     2.4-point move on it is mostly about which courses those 150 runners
     raced.
   - Use the forward scorecard as the gate.
   - Use the Kalman filter's one-step forecasts (§7.2) as a free prequential
     test on every race in the corpus.
   - Run the Kailo comparison only on frozen, pre-race records.

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

For row *i*: person *p*, date *t*, race *r* inside venue-day *v*, course
cell *c* (course × distance, as today), and sport group *g* ∈ {XC,
indoor, outdoor}:

```
y_i = θ_p(t) + f_ℓ(phase_t) + β_p·xc_i + h_i·(μ_g + c_c + w_v + v_r) + e_i

  y      ln(time) on ONE scale: the flat outdoor 5000 m equivalent
  θ_p(t) the person's fitness on that date: a state, not a parameter
  f_ℓ    the population's average season shape for level ℓ (smooth, fitted)
  β_p    grass specialisation, N(0, s_β²)
  μ_g    group level; outdoor = 0 is the gauge
  c_c    course, N(0, τ_g²), with the course's eras tied by a random walk (as today)
  w_v    venue-day, Student-t(ν, s_w,g)   — weather, wind, the field's day
  v_r    race inside the venue-day, Student-t(ν, s_v,g) — the division's own course and pace
  h_i    1 + κ·(θ_p(t) − θ_ref)          — the tilt on absolute speed
  e_i    Student-t row residual (falls, bad days, tactical sit-and-kick)
```

**Fitness as a state.** θ follows a local-level model with a per-season
trend:

```
inside a season:   θ(t+Δ) = θ(t) + s_p,season·Δ + η,   η ~ N(0, q·Δ)
at the summer seam: θ ← θ + J_ℓ + ζ,                   ζ ~ N(0, q_J);   s redrawn ~ N(0, s_s²)
```

- The walk `q` absorbs ordinary week-to-week form.
- The season trend `s` is the runner who improves faster or slower than
  their level; this is the fitness gain neither LACCTiC nor we model today.
- The seam `J_ℓ` is the year of growth: a 9th grader to a 10th grader, a
  senior to a college freshman, and so on.
- One person is one trajectory across pools and sports. A senior's spring
  and their first college fall are adjacent states, not two "athletes".

### 4.2 What each piece replaces

| piece | replaces |
|---|---|
| θ as a state, smoothed by Kalman/RTS | athlete-season `a`; the 30-day bracket window (the walk's variance *is* the window, fitted); opener rust; the form trend; the season tie; season centring; the island check; the per-pool anchors' scale juggling (`rescaleToPool`, `_scalePool`, the HS factor, `13b_pool_consts`) |
| `f_ℓ` smooth, fitted | the 13-knot curve, its amplitude tilt and rails, the pinned knot. Kept, but now a population mean that individuals deviate from, not the only fitness model. |
| `v_r` race inside `w_v`, both heavy-tailed | the division guard, `RACE_DAY_CAP` (all four of its jobs), `flaggedFastDay`, the division lean, `XC:fast`, the course guard; most of the distance-override work becomes a flag list |
| `h` on θ | `TILT_K` stated, `XCP_TILT_SCALE`, the rating rails, the course scale's re-estimate |
| one scale (5000 m) | per-pool anchors in the solve. Pool ratings become a display transform. |
| Student-t rows | Huber's two constants, the ability weights, the effort term |
| everything fitted by EM, two numbers by cross-validation | τ caps, priors in races, `RACE_SAT`, the floors, the gates |

### 4.3 What a rating is

A **race rating** is the performance with course and conditions removed, on
the runner's level scale:

`perf_i = y_i − h_i·(μ_g + c_c + w_v + v_r)`, then the same pool transform
as today (`100·pm/exp(perf)`).

- Both directions of the day are credited, shrunk by how many known runners
  measured it. A slow day is credited exactly as much as the evidence says.
- A tactical race is a slow *race*. Its `v_r` reads slow because the
  runners' own trajectories say they are fitter than that.
- The `XC:fast` question then becomes an empirical one: does crediting slow
  days improve the forecast of the next race? §7 answers it.

A **season rating** (and the boards) can stay the 80th percentile of race
ratings, or become the trajectory's peak, `max_t θ_p(t)`, which has a
posterior SD. **Boards should rank on the posterior**, e.g. require
SD < 1.5%. An island season such as COVID 2020, with no runner continuity to
2019 or 2021, then has a wide SD and does not top an all-time board. It
needs no rule, because its runners' own 2019 and 2021 states pin it.

The **5K column** is `exp(θ_p(t*) + f(phase_t*))` on the gauge track, where
t* is the next outdoor season's peak. In other words, the model's forecast
of the runner's spring track 5000. That is a quantity the data can check
(§7, test C). The clock is right when the forecast is right, not because a
correction was layered on top.

### 4.4 What the data cannot tell, said once

- **The XC↔track level.** Nobody runs XC in May. Any forecast from XC to
  track goes through `μ_XC + f(spring) − f(fall)`, so only that sum is
  identified. The planted world shows it (§6, last table):
  - the training fit keeps improving as the asserted gap rises past the
    truth;
  - the spring holdout prefers 0.09 when the planted gap is 0.06;
  - the smooth curve cannot follow a December break, so the gap soaks up
    the difference.

  So: **assert μ_XC for display** (6%, as today, or whatever reads right for
  course difficulty). Let `f` carry the rest. Choose `f`'s smoothness by the
  spring-track forward holdout. One number, one place, one gate.
  `XCP_SPORT_LEVEL_POOLS` and the go-live sport-gain shift are no longer
  needed, because `f` is per level.
- **The indoor level** is the same story (indoor is season). The NCAA
  facility factors stay as the stated μ_in. Per-oval deviations are
  identified.
- **Altitude for closed communities.** Residents' altitude and their
  ability are one direction. Keep the physiological coefficient as a prior;
  the walk lets visitors measure it.
- **One-race cells.** `c` and `w` are collinear on one day. Keep
  `identified_priors`: τ is estimated where a second day exists.
- **The tilt against the grass offset.** κ and β_p both explain "fast
  runners lose less on grass". κ is identified from variation in course
  difficulty within XC. On the planted world it comes back at 0.21–0.25
  against a planted 0.30. The current structure, fitted the same way, gets
  the **sign** wrong (−0.35 to −0.52). That is why `TILT_K` had to be
  stated.

### 4.5 Fitting it, and what it costs

Block-coordinate EM:
1. Every person's Kalman filter and RTS smoother, given the rest: one pass
   over the rows, parallel over people (numba).
2. Cells, venue-days and races as shrunk weighted means. The t-weights are
   the heavy tails.
3. μ, κ, `f` and every variance from the smoothed moments: q, q_J, s_s, s_β,
   τ_g, s_w,g, s_v,g, and σ.

There is no CG, no window and no cap. The hyperparameters with no EM update
are ν (the t degrees of freedom) and `f`'s smoothness. Both go to the
scorecard (§7).

Measured on 4 cores: **0.49 µs per row per pass**. For 60M rows that is
about 30 s a pass on 4 cores, and a few seconds on 24 cores (bincounts
included, not measured at that size). At 25–40 passes, **minutes, against
08_golive's hour and the 3-hour solves before it**.

This does not touch 04_grade_sanity, 03_pro_flag or 07_pack, which are most
of the nine hours. Those are data steps, not the model; §8 point 6 says what
can go.

A held-out row is a row the filter predicts through without updating on it.
So a held-out race, a forward season and a cold start all run through the
same code path. The filter alone, with no smoother, is a **one-step-ahead
forecast of every race in the corpus from strictly earlier races**, which
§7.2 uses as a free test.

### 4.6 Constants: fitted, cross-validated, stated

| kind | which |
|---|---|
| fitted by EM | σ, q, q_J, s_s, s_β, τ_g, s_w,g, s_v,g, J_ℓ, κ, μ_in (or stated), the curve, every effect |
| cross-validated on the forward scorecard | ν (row and day tails), `f` smoothness, the era walk sd, whether slow days are credited |
| stated, with a reason | μ_XC (display only, §4.4), the gauge (flat outdoor 400 = 0), the altitude prior, the NCAA indoor factors as μ_in's prior |

---

## 5. How each current hack disappears

| today | under the model | caveat |
|---|---|---|
| **division guard**, `RACE_DAY_CAP` as threshold/clip/trigger, `flaggedFastDay`, division lean (Midlothian, Shamrock 2025/26, Mt. SAC 2024) | `v_r` is its own heavy-tailed effect inside `w_v`. A short-course division is one big `v_r`; the venue-day and the other divisions are untouched. Planted: 0.8–0.9% rating error in such divisions against 3.8–4.1% today. | needs a race key = (meet, div), which the pack already carries |
| **borrowed distances**, wrong-distance overrides, Dan Conway, Monte Vista +19% | A wrong distance is a near-constant log-time offset on one race, i.e. `v_r`. It is absorbed when the race has known runners, and listed (`|v_r| > 5%`) for a human. Overrides only for races with no known runners. | first-timer fields (MS) still need the distance |
| **`XC:fast`**, slow days never credited (Ultimook) | symmetric credit, shrunk by evidence; the walk tells a tactical race from a slow course | scored on the forward test, not decided by hand |
| **field check** (Marmonte at Pepper Tree) | That race's `v_r` reads fast, because the runners' trajectories say what they should run that week; the league venue's other days set `c`. The check measured exactly this against season means, which is what the smoother does properly. | |
| **island check** (CSU/Wyoming 2020-21), **Michigan early-2000s** rows | Runners carry their state through the island, so it is pinned to 2019 and 2021. Where no runner connects (old, isolated data), the posterior SD is wide and boards gated on SD do not show it. | gating changes what boards show for thin eras: owner's call |
| **season centring** (Newhall) | a fast season is fitness, so it is in θ | |
| **the 30-day bracket window**, **form trend**, **season tie**, **opener rust** | `q`, `s_s`, `q_J`, fitted | |
| **tilt `TILT_K` stated, `XCP_TILT_SCALE`** | κ fitted on θ, an absolute scale; no pool label enters `h` | |
| **`XCP_SPORT_LEVEL_POOLS`**, go-live sport-gain shift, the conversions' XC median effect | per-level `f`, one stated μ_XC (§4.4) | |
| **per-pool anchors**, `rescaleToPool`, pro → college scale, `13b_pool_consts`, the HS factor | one 5000 m scale in the model; pools become a display transform | normalisation keeps its curves until §8 point 5 |
| **5K clock calibration** (rejected) | the 5K column is a forecast, checked by test C | |
| **course guard** (+35% Pima CC) | a course that far out is `c` with few known runners, so a wide posterior SD; flag and gate | |
| **ceilings `POOL_CEILING + 10`** | a posterior SD gate does this honestly | keep as a sanity net at first |
| **bracket engine** as a second course estimator | gone. One course estimate, with its SD. | |

---

## 6. The prototype, on a planted world

`engine/statespace.py` is the model of §4, minus:
- the course-era walk (eras only split);
- altitude;
- track event offsets;
- weather inside the model.

`engine/statespace_world.py` plants:
- high schoolers who get fitter within and across seasons, with a personal
  season trend of 5% a year;
- some who go on to college;
- XC, indoor and outdoor track;
- courses, venue-days and divisions, 6% of them on a mis-measured course;
- 5% bad days;
- a tilt of 0.30;
- an XC gap of 6%.

`scripts/ss_synthetic.py` prints the tables. Rung **A** is the same code
held to today's skeleton:
- athlete-season ability;
- one day per venue-day;
- Gaussian days;
- the gap asserted.

It has no bracket engine, field check or island check, because those are
fixes for what A gets wrong. Each later rung adds one piece. 280k rows,
6,600 people, two seeds (seed 0 / seed 1):

| held out | rung | median \|e\| | race-centred \|e\| | head-to-head |
|---|---|---|---|---|
| 10% of venue-days, last 3 seasons | A | 1.67 / 1.67% | 1.30 / 1.26% | 86.1 / 86.1% |
| | +walk & trend | 1.54 / 1.53 | 1.18 / 1.15 | 87.3 / 87.5 |
| | +nested days | 1.55 / 1.56 | 1.16 / 1.14 | 87.4 / 87.6 |
| | **B** +heavy tails | **1.50 / 1.50** | **1.17 / 1.14** | **87.4 / 87.7** |
| forward: the rest of the last XC season from mid-October | A | 2.08 / 2.28 | 1.32 / 1.32 | 88.7 / 88.8 |
| | **B** | **1.80 / 2.01** | **1.28 / 1.28** | **88.9 / 88.9** |
| spring outdoor track from everything before it | A | 2.75 / 2.55 | 2.25 / 2.27 | 75.8 / 76.6 |
| | **B** | **2.37 / 2.18** | **1.93 / 1.91** | **79.0 / 79.4** |

Rating error against the planted performance (all rows fitted, median
\|figure − truth\|):

| | ordinary rows | rows in a mis-measured division |
|---|---|---|
| A, every day credited | 1.04 / 1.05% | 4.06 / 3.77% |
| A, `XC:fast` | 0.87 / 0.87% | 5.13 / 4.94% |
| **B, every day credited** | **0.82 / 0.83%** | **0.89 / 0.81%** |

B recovers:
- κ 0.25 / 0.21 (planted 0.30; A: −0.35 to −0.52);
- trend sd 4.0% a year (5%);
- XC τ 3.2% (3.5%);
- XC day sd 1.0–1.1% (1.2%, t₃).

What this does and does not show:
- **The walk carries most of the gain.** Nested heavy-tailed days add a
  little to prediction and nearly everything to the ratings of broken
  divisions.
- **`XC:fast` helps A's ordinary rows and hurts its broken ones.** That is
  the signature of a day term that is noisy because its key is wrong, not of
  slow days being unreal.
- **The gap cannot be identified** (§4.4).
- **It is a planted world.** It contains what the model models and none of
  what it does not (weather errors, mislinked people, timing errors). Real
  gains will be smaller and could be negative on some segment. §7 decides.

`tests/test_statespace.py` pins all of the following:
- B beats A on held-out races and the spring track;
- a short division's ratings stay within 1.5× ordinary;
- κ has the right sign;
- `scripts/ss_eval.py` runs end to end on a pack-shaped file, including the
  paired scorecard.

---

## 7. The evaluation plan

### 7.1 One gate, three held-out designs, the same rows for every engine

The unit of evidence is the **race**. Every held-out design removes whole
races, and every SE is a bootstrap over whole races, as
`forward_holdout.pairedLines` already does.

| design | train | score | what it tests |
|---|---|---|---|
| **F, forward (the gate)** | every row before the validation season's cut | every rated row of the validation season (the academic season before the sealed one); the sealed season once, at the end | what the site is asked: predict races not yet run |
| **R, race** | all rows except 10% of *venue-days* (whole days, so no sibling race leaks the day) | the held-out days | course + ability interpolation; comparable with 08a |
| **S, sport** | everything before 1 March | that spring's outdoor track | the XC↔track bridge and the 5K column |

**Metrics, all on the rows both engines predicted.** e = ln(actual / predicted).

1. **bias** = mean e. **median |e|** and **p90 |e|**.
2. **head-to-head** = per race, the share of pairs of scored runners in the
   predicted order (ties in prediction count ½). One number per race, then
   the mean over races; a pair group is (race, pool, distance) as in
   `forward_holdout.pairGroups`. This is the order test, generalised from 3
   races to tens of thousands.
3. **race-centred median |e|**: e minus the race's median e. This is
   ability only, with the unknowable day removed. It is the honest form of
   the "XC → 5000 spread".
4. **calibration**: the share of rows inside the model's 80% interval, and
   CRPS. Both engines publish uncertainty, so both should be judged on it.
5. **breakdowns**: sport, pool, the athlete's training races (1, 2, 3-5,
   6+), new or seen course, month, and championship or not.
6. **paired change and its SE** over 201 race resamples. A change is real
   beyond 2 SE.

**The decision rule.** The prototype replaces the core if, on F:
- it is better on head-to-head and median |e| overall, beyond 2 SE;
- it is not worse beyond 2 SE in any pool with more than 2,000 rows;
- on S, its spring-track bias is within ±0.5%.

The sealed season is looked at once, after the choice.

### 7.2 Free, every run: the filter's forecasts

The forward filter's prediction for each row uses only that person's earlier
races. The course and day effects come from the full fit, which is a small
leak, so state it. Scoring those forecasts on the last two seasons gives a
prequential test over millions of races, every night, at no extra cost.

It replaces nothing above. It is the smoke alarm the nightly LACCTiC test
was trying to be.

### 7.3 Against LACCTiC

LACCTiC publishes per-race track-5K equivalents and a runner ability, not
pre-race predictions.

1. **Freeze their numbers.** Snapshot `race_page` and `runner_page` for the
   season's races (not just three) into `engine/data/lacctic/<date>/`. Their
   numbers change retroactively; Oct 19-21 moved every past season.
2. **Forward order.** For each race in `--season 2025 --top 40` (and then
   every D1/D2/D3 regional and national race), each system's ability for
   each runner comes **only from that runner's races before that race**:
   - theirs: the median of their TICs on earlier dates;
   - ours: the filter's state on the day, or for the current engine the
     median of earlier race ratings.

   Metric: per-race head-to-head, mean over races, race-bootstrap CI on the
   difference.
3. **Forward XC → track.** Predict each runner's spring outdoor 5000 (best
   and median) from **fall XC only**:
   - theirs: the median fall TIC;
   - ours: test S's forecast.

   Metrics: bias, robust sd, race-centred spread. Report indoor and outdoor
   5000s separately; the current test's substring match mixes them.

### 7.4 Against Kailo

Kailo publishes pre-race predictions. `16c_kailo_record` already freezes
ours, so the comparison only has to be made on frozen records:
1. Score **recorded** races only (`kailo_compare.py` without `--record`;
   drop the "rebuilt" ones, whose course difficulty is in-sample). Kailo's
   side is the version published before `start_at`.
2. Use the same metrics as 7.1, per race, with race-bootstrap CIs.
   Separate the runners that both systems predicted from those only one
   did.
3. For the prototype: add a `statespace` basis to the recorder. Its forecast
   for a race is the filter's state on race day, plus the target course's
   `c` and the season curve. Record it beside `rating/guard/model/blend`
   from the day it exists. Until then, a retrospective score on past Kailo
   meets is fair only if the prototype is refitted with a cut at each meet's
   date. The filter does that in one pass.

### 7.5 Running it on the server

Everything below reads the pack (`engine/data/packed_XC_TF.npz`) and the
holdout dumps. None of it writes the site's tables.

| step | cost on NEW (24 cores) | Postgres |
|---|---|---|
| current engine, forward holdout at 15%, both tilt scales, dumped (`scripts/scorecard.py`) | ~2 h (two sides, ~50-60 min each) | reads nothing after the pack |
| prototype on the same sample and rows (`scripts/ss_eval.py`) | minutes (load ~2-5 min, fit a few) | none |
| prototype at 100% (`--sample-pct 100`) | ~10-20 min | none |
| LACCTiC frozen snapshot + forward order (`lacctic_compare --season 2025 --top 40 --runners 600`) | ~15-20 min (rate-limited by design) | reads `results` |

⚠ **Postgres restarts unattended between 06:00 and 07:00 UTC.**
- Nothing in §9 part 2 needs the database once the pack is on disk.
- §9 part 1 (attribution) and the LACCTiC snapshot do read it. Start them
  before 05:00 UTC or after 07:00 UTC.
- Do not start a pipeline after about 20:00 UTC: at 9h22m it runs into the
  window.

---

## 8. If it wins: the order of work

Each step ships alone, is scored on the forward scorecard against the step
before, and keeps every output table's shape. The site does not have to
know.

1. **The core.** θ as a state on one scale; nested heavy-tailed days keyed
   (meet, div) inside venue-day; κ fitted. The bracket engine and joint `d`
   are replaced by the model's `c`. The go-live keeps its current shape:
   rating = 100·pm/exp(perf).
2. **Turn off what the core makes redundant, one at a time, each scored:**
   - the division guard and lean;
   - `XC:fast`;
   - the field check;
   - the island check;
   - season centring;
   - `XCP_SPORT_LEVEL_POOLS`;
   - the course guard (to a flag).
3. **Posterior SDs on ratings and courses**, published with `n_results`;
   boards gated on the SD. Then remove the island and Michigan special
   cases from consideration.
4. **The 5K column becomes the spring-track forecast.**
5. **Distance inside the model.** Replace the pool distance curves and event
   offsets with one log-distance curve plus a per-person endurance slope
   (Blythe & Király's low-rank form). Weather goes in as covariates in the
   same fit, which removes two separate fitters and their double-counting
   with the day term.
6. **Pipeline time.** The solve becomes minutes. Step 08 then drops from
   about an hour to about 10 minutes plus writes. The other big steps
   (`04_grade_sanity` 1h11m, `03_pro_flag` 51m, `07_pack` 46m, rankings
   ~1h26m, and `04e_weather_grid` and `10f2_projection` in the background)
   are data work this review did not profile. Worth a separate look once the
   model is settled.

---

## 9. Paste block

Run as root in `/srv/xc-predictor`. Nothing here writes a table the site
reads, and production's checkout stays on master. Paste back the outputs.

**Part 1: what changed between OLD's Oct 19 solve and 20261023_171317.**
Takes a few minutes and reads the database, so not between 06:00 and 07:00
UTC.

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
will fix it. Several column names above (`meets_tfrrs.date`,
`results_tf.time_seconds`) are taken from the scripts that use them, not
from the schema.

**Part 2: the tilt priced, and the prototype against the engine on the same
rows.** About 2h for the two scorecard sides, plus minutes for the
prototype. Needs only the pack. Start it after 07:00 UTC or before 03:00 UTC
so the scorecard's own reads finish clear of the restart.

```bash
cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
. deploy/solve_env.sh
mkdir -p engine/data/ss /srv/ss/engine /srv/ss/scripts
# the prototype's two files into a scratch tree; production's checkout stays on master
git fetch -q origin claude/sharp-lamport-p381q5
for f in engine/statespace.py scripts/ss_eval.py; do git show FETCH_HEAD:$f > /srv/ss/$f; done
# a) the tilt priced on the forward scorecard: own (base) against hs (try,
#    as 10-23 ran), 15% of athletes, same rows; both sides' held-out rows are
#    dumped to engine/data/scorecard/ladder_logs/{base,try}_holdout.npz
$P scripts/scorecard.py --base "production --tilt-scale own" \
    --try "production --tilt-scale hs" > engine/data/ss/tilt_scorecard.log 2>&1
# b) the prototype on exactly the rows the engine (as run, tilt hs) scored;
#    then the same code held to today's skeleton (rung A)
D=engine/data/scorecard/ladder_logs/try_holdout.npz
$P -u /srv/ss/scripts/ss_eval.py --repo /srv/xc-predictor --base-dump $D \
    > engine/data/ss/ss_fwd.log 2>&1
$P -u /srv/ss/scripts/ss_eval.py --repo /srv/xc-predictor --base-dump $D --static \
    --out engine/data/ss/ss_fwd_static.npz > engine/data/ss/ss_fwd_static.log 2>&1
sed -n '/TRY AGAINST BASE/,$p' engine/data/ss/tilt_scorecard.log | head -45
grep -E "^\[ss\] (fit|window|SAMPLE)" engine/data/ss/ss_fwd.log
sed -n '/SCORECARD on/,$p' engine/data/ss/ss_fwd.log | grep -E "SCORECARD|^    [a-z(]|^      (all|sport|pool)" | head -60
sed -n '/SCORECARD on/,$p' engine/data/ss/ss_fwd_static.log | grep -E "SCORECARD|^      all" | head -8
```

Run it inside `tmux` (or prefix the whole block with `nohup bash -c '...'`):
an SSH drop kills it otherwise.

Reading part 2:
- `tilt_scorecard.log`: a positive pairwise change beyond 2 SE means `hs`
  helps; negative means it hurts.
- `ss_fwd.log`: "change = try − base", where try is the prototype. A
  higher pairwise and a lower median |err|, each beyond 2 SE, is a win.
- `ss_fwd_static.log` is rung A on real data. If A is close to the current
  engine, the comparison is fair. If A is far worse, the engine's other
  terms are doing work the prototype lacks (altitude, event offsets, eras),
  and those are the first things to port.

The scorecard prints both sides' run_joint flags at the top of its log.
Check that "production" expanded to what 08_golive runs.
