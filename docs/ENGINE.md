# The engine, intimately

> **2026-09-13.** The published course numbers can come from the bracket
> engine (`engine/bracket_engine.py`, `run_joint.py --difficulty bracket`,
> `XCP_DIFFICULTY=bracket`): the owner's same-athlete window method, fitted
> on this solve's residual over this solve's cells, with a race as the unit
> of evidence. This document describes the joint solve, which still fits
> every other term. See `HANDOFF.md`.



*Written 2026-09-11. This is the document I wish had existed two days ago.
It is about `engine/joint_solve.py` and `engine/run_joint.py` — what the
model is, what it can and cannot see, why every constant is the number it
is, and the exact ways it has gone wrong. The failure section is the most
valuable part: every one of those shipped.*

---

## 1. What it computes

One number per row: `y = ln(normalized_time)`. `normalized_time` already
corrects distance, geometry, era and weather — **but not the course**. The
engine adds the course.

The model for a row (`joint_solve._predictSlice`, line 852):

```
y  =  a[athlete-season]                      the ability. THIS IS THE RATING.
   +  h · ( mu[group] + d[cell] + u[race] )  level, course, race-day — all tilted
   +  beta[athlete] · sc                     per-athlete sport offset
   +  amp · curve(day-of-season)             13-knot season form curve
   +  first · r[pool]                        season-opener rust
   +  e_w · e[distance class]                distance offsets (TF only)
   +  g[athlete] · lz                        per-athlete endurance slope
   +  k[group] · alt                         altitude, log-time per km
   +  imp[pool, sport] · front               field strength, 2026-09-11 (front = the race's top-5 mean rating above the median race, per 10 points, from the model's own ratings)
   +  h · ind_fixed[pool] · is_indoor[cell]  the indoor level, ASSERTED (+1.2%), 2026-09-11; --indoor-level fit estimates it
```

Since 2026-09-11 `mu` can be **asserted** instead of estimated
(`--sport-level G` → `Design.mu_fixed = [0, −G]`): the block leaves theta
and `h·mu` comes off `y` every pass. The two new terms, the E-step change
and the conversion fixes are written up in
`docs/RESEARCH-ENGINE-2026-09-11.md`, Part II.

Everything is log-time and additive. A rating is a monotone transform of
`a`; that is why "a 133 here and a 133 anywhere else are the same
performance."

### The tilt `h`

```python
h = 1 + TILT_K * (rating - 100) / 10          # TILT_K = -0.031: a 120 pays 94% of a course, a 140 88%
rating evaluated inside TILT_RATING_LO..HI = 40..200   # safety rails only, since 2026-09-11
```

A hard course costs a slow runner more than a fast one. `h` multiplies
`mu`, `d` **and** `u` together — and that "and `u`" is not cosmetic. See §3.
The slope was measured over ratings 70–140 and used to be held flat
outside that band (a 150 was charged as a 140). It now extrapolates, and
`run_joint.reportTiltByBand` prints, per rating band, the `h` applied
against the `h` the residuals imply (residual regressed on the course
effect), so the run itself says whether the line holds above 140.
(`AMP_TILT_PER_POINT = 0.01135` is a different tilt: the season-form
amplitude shrinking with ability, clipped to 0.15–1.80.)

---

## 2. The single most important fact in the engine

> **Within a race, `d` (course) and `u` (race-day) are EXACTLY collinear.
> The split between them is decided by `tau²` against `sigma_u²` and by
> nothing else.**

Every row of a race shares one cell and one race id, and both terms carry
the same `h`. There is no information in the data that separates "this
course is hard" from "that day was slow." Only the two priors do:

```
share = tau² / (tau² + sigma_u²)      what a ONE-RACE course keeps of
                                       what its single race showed
```

The solve prints this every run:

```
[joint] XC: race-day sd 0.03050 (fitted 0.03050), course prior 0.03640
        -- a one-race course keeps 0.59 of what that race showed
[joint] TF: race-day sd 0.01450 (fitted 0.01450), course prior 0.00892
        -- a one-race course keeps 0.30 of what that race showed
```

**If a difficulty number looks wrong, look at these two lines first.**
Most of §9 is this fact being violated.

`h` on `u` exists because of it (issue 156). Untilted, `d` and `u` were
separated *only* by `h`, so the pair `(d = +c, u = −c)` cost almost nothing
under the priors and bought an ability-shaped spread of `(h−1)·c` per row.
The solve used it as a free spread parameter: Great Park read `d = +0.19`
with `u ≈ −0.2` on **every one of its seven days**, net zero, and elite rows
there lost 7-9%.

---

## 3. What the model structurally cannot see

These are not bugs. They are identification limits, and knowing them saves
days of chasing "wrong" numbers that the model had no way to get right.

**A venue that hosts only one kind of race.** `u` is estimated as a
deviation around *that cell's own mean*. If a venue hosts only championship
races, "everyone tapered" is constant across all its races — `u` cannot see
a constant, so the taper lands in `d` and the course reads easy. Foot
Locker, state meets, nationals. The mirror case is a venue that only hosts
early-season invitationals. The fix (issue #22, 2026-09-11) is the
**field-strength** covariate estimated globally: per race, its front (the
mean rating of its top five above the median race of its pool and sport,
per 10 points, from the model's own ratings each pass,
`joint_solve.fieldStrength`). A stacked field runs fast because of the
competition, and without the term the venue that hosts only stacked
fields reads easy; with it the course keeps its difficulty (the owner's
"refund") and the fast time stays a real performance in the rating.
Identified off venues that host a mix and off athletes who race in both
strong and weak fields. The season-end share (`--importance season-end`)
is the calendar alternative, kept for the ladder.

**Indoor is season.** No location hosts both an indoor and an outdoor
track and nobody races indoors in May, so the curve's December-to-March
level and the indoor cells' mean are one free direction: the smooth curve
interpolates the fall-to-spring gain through the winter and the indoor
cells absorb the difference (the page read every oval 2.4 to 3.8% EASIER
than outdoors). The indoor level is therefore ASSERTED (`IND_LEVEL_DEFAULT`
+1.2%, the NCAA facility factors), off theta like `mu_fixed`, and the
indoor cells are recentred to it every pass; `run_joint.indoorTransitionCheck`
prints the last-indoor / first-outdoor pair measurement as a check.

**Indoor vs outdoor.** Measured 2026-09-11: 1,574 indoor cells, 25,629
outdoor, and **zero locations have both**. A `location_id` is one or the
other. So the `:in`/`:out` suffix carries nothing the location did not, and
there is no within-venue contrast anywhere for `d` to learn the surface
from. It can only arrive through athletes who race both — which routes it
through the date-indexed form curve, which will happily book it as spring
fitness. A banked 200m oval is ~1-2% slower for a distance race; `tau[TF]`
is 0.89%, so **the effect is larger than the entire spread the prior
allows any track to have.** A shared physical effect belongs in a shared
parameter, beside altitude — not in 27,203 cells each rediscovering curve
radius from its own thin evidence.

**The XC/TF level.** Sport is season, so nothing in the corpus identifies
the between-sport level. `--merge-sports` *asserts* it is zero rather than
estimating it, and the form curve then carries autumn-to-spring movement as
fitness. This is a choice, stated in the pipeline, not a measurement.

**A one-race cell.** `d` and `u` are unidentified there, full stop. Hence
`identified_priors=True`: `tau²` and `sigma_u²` are estimated **only on
cells with 2+ races** (43,946 of 74,366 cells; 541,988 of 569,935 races).
Letting one-race cells vote on the priors collapses them.

---

## 4. Every prior, and where its number came from

| constant | value | provenance |
|---|---|---|
| `TAU_MAX_DEFAULT` | XC 0.0364, TF 0.0161 | split-half reliability, `difficulty_reliability.py` |
| `SIGMA_U_FLOOR` | {0.0, 0.0} | **both must stay zero** — see §9.1, §9.2 |
| `MEASURED_TRUE_SD` | XC 0.0351, TF 0.0122 | the same split-half, used by `checkPriors` |
| `ERA_DRIFT_SD` | 0.010 per era | stated, not fitted (§6) |
| `DIST_PRIOR_SD` | 0.03 | prior on TF event offsets |
| `ALT_PRIOR_MEAN` | 0.035 /km | physiology; `ALT_PRIOR_PEN_FIXED = 1e9` pins it |
| `HUBER_SLOW / FAST` | 2.5 / 1.5 | asymmetric: a slow outlier is likelier real than a fast one |
| `CURVE_N_KNOTS` | 13 @ 30 days | one season |
| `CURVE_SMOOTH` | 1.0 | a prior, explicitly **not** tunable by held-out error |
| `WINTER_GAIN` | 0.0 | an identification assertion, not an estimate |
| `DIST_BANDS` / `DIST_BAND_ANCHORS` | (105, 120, 135) / (90, 112, 127, 145) | four bands since 2026-09-11: the tables put the 800→1600 exponent higher at lower ability, and one band over 120 handed a 150-rated half-miler a 4:10 miler's relation |
| `IMP_PRIOR_MEAN` / `IMP_PRIOR_SD` | 0 / 0.02 | one coefficient per (pool, sport) on the race's FRONT (`FIELD_TOP_K` 5, `FIELD_UNIT` 10 rating points, `FIELD_CLIP` −4..+6; `joint_solve.fieldStrength`, recomputed from the model's ratings each pass, zero on the first). Zero mean: no evidence, no term. `FIELD_EXPECTED` −0.005 per unit is what a healthy fit looks like; `run_joint.reportFieldByBand` prints the residual by band. A stated SD, never pseudo-rows. `--importance season-end` swaps in the season-end share (`SEASON_END_DAYS` 14, `SEASON_END_MIN_RACES` 3, `SEASON_CLOSED_DAYS` 21); the name classes in `engine/meet_class.py` are a diagnostic only |
| `IND_LEVEL_DEFAULT` | +0.012 | the indoor level ASSERTED (indoor is season, §3); the indoor cells' mean deviation is held at zero each pass, outdoor tracks are recentred without the ovals voting. `--indoor-level fit` estimates it with `IND_PRIOR_MEAN` / `IND_PRIOR_SD` +0.012 / 0.01 (NCAA facility factors 2012, WA short-track tables 2025) |
| `ERA_YEARS_DEFAULT` / `ERA_DRIFT_SD` | 0 / 0.010 | `XCP_ERA_YEARS=2` splits each course into two-year eras tied by a random walk with this drift per era; the go-live publishes each venue's latest era under its bare key (`joint_golive.latestEraKeys`) |
| `nested_var` | True | the E-step's conditional variance is the exact (cell + its races) arrowhead, not the information diagonal (§5) |

### Why `tau` is the OBSERVED spread and not the true one

This has been walked into twice. Split-half gives XC reliability 0.928
(observed sd 3.64%, true 3.51%) and TF 0.574 (observed 1.61%, true 1.22%).
The instinct is to set `tau` to the *true* spread. That publishes a board
too narrow, because posterior means are shrunk:

```
published sd  =  tau · sqrt(reliability)
```

`tau = 1.22%` with `r = 0.574` publishes **0.92%** against an observed
1.61% — a board barely more than half as wide. Sized the other way:

```
tau  =  true_sd / sqrt(r)  =  observed_sd       (they cancel)
```

**This is a display-fidelity choice, not a Bayesian one.** `tau = true_sd`
minimises squared error per course and is what you want for a *prediction*;
`tau = observed_sd` reproduces the spread and is what you want for a
*board*, where systematically flat numbers read as a broken scale. The
shrinkage that matters is still there — it falls on thin cells. The
`free-tau` ladder rung measures what either costs.

---

## 5. The solve

Conjugate gradient on a symmetric positive-definite operator
(`_Operator.matvec`), wrapped in an EM outer loop (`--outer`, default 5).
Each outer pass:

1. CG solve for `theta` (all blocks at once), `CG_TOL = 1e-8`, max 600 iters
2. update `sigma2` (residual), `tau2` per group, `sigma_u2` per group —
   each as `mean(estimate² + Var(estimate | y))`, with the variance from
   `nestedPosteriorVar` (the exact inverse of each cell's `(d, u_1..u_m)`
   arrowhead, abilities held). Until 2026-09-11 it was `sigma2 / A_ii`,
   which for a one-race cell goes to zero with more rows while the truth
   stays at `sigma2/(P_c + P_u)`: an under-stated `sigma_u`, and every
   thin course keeping too much of one day
3. robust reweight (Huber, asymmetric)
4. recompute `h` from the current ratings

`pen_cell = sigma2 / tau2[group]`, `pen_race = sigma2 / sigma_u2[group]`.
Both are **per group** (0 = XC, 1 = TF) and therefore **arrays** — see §9.6.

The final pass computes posterior variance by probing (`--probes`), which
is what licenses the published shrinkage. `--probes 0` skips it and uses
the information diagonal as a lower bound.

A full solve on ~59M rows is **about three hours**.

---

## 6. Era drift (new, 2026-09-11, off by default)

`--era-years N` splits each cell into `(course, era)` N years wide.
Adjacent eras of one course are tied by a **random walk**: the penalty is
on the *difference* between neighbours, not on each era's value. Same
device as Coulom's Whole-History Rating.

**The tying is the entire feature.** Splitting alone multiplies parameters
and divides evidence; every thin venue would shatter into era-sized noise,
which is worse than not splitting. With the walk, a well-measured venue
moves and a thin one is held together. Planted world, one course 6% harder
in year 6:

```
one difficulty for all time:  +1.25%  (truth −2.67% then +3.33%)  wrong both ways
2-year eras + random walk:    step found +6.00%  (truth +6.00%)
                              stable courses wander sd 0.179%
```

Three details that decide whether it works:

- pairs only between eras that **exist**, weighted `1/gap` — a random walk's
  variance grows with elapsed time, so a decade of silence ties loosely
  rather than pretending the two ends are neighbours
- **`tau` is divided by the course's era count.** It is a prior on a
  *course's level*; applied whole to each of eight eras it would shrink a
  long-lived venue eight times harder than a new one purely for longevity
- the split runs over **all** rows, not just `keep`, so a holdout design
  keeps the same cell ids as the full one

---

## 7. Cell keys and the anchor

```
XC    'XC:<venue>:d<distance>'      ':d<dist>' stripped before the DB write,
                                     the distance goes in distance_m
TF    'TF:loc:<location_id>:<in|out>'
eras  '...@e<era>'                   only with --era-years
```

`joint_golive` anchors: `anchored = raw − mean(raw[track cells])`, an
**unweighted** mean over TF cells — the average *course*, not the average
*result*. So **the average track is 0.0 and an ordinary XC course reads
about +7%**, and that +7% *is* the measured sport gap.

`racecast/difficulty_view.py` displays that number and **does not move
it**. The zero is defined in exactly one place (`joint_golive`) and read in
the view as a guard only. See §9.5 for why that rule exists, and §9.9 for
why the obvious "fix" to it is wrong too.

---

## 8. The diagnostics, and what each one answers

| script | the question it settles |
|---|---|
| `venue_check.py --venue X` | does the board agree with what the runners actually ran there? (`--search` finds the name) |
| `difficulty_reliability.py` | how much of the observed spread reproduces on independent races? |
| `indoor_outdoor.py` | is the curve paying for the surface switch; is indoor shrunk to an outdoor zero? |
| `ablation_ladder.py` | does each term earn its keep on held-out races? |
| `run_joint.py --holdout-only` | one held-out number, writing nothing |
| `shrinkage_audit.py` | which cells are being pulled hardest, and from what |
| `sitemap_budget.py` | (site) how big an ask are we making of Google? |

`checkPriors()` runs inside every solve and shouts when a sport's course
prior has collapsed. **It is silent on a healthy run.** If it fires, stop
and read §9.

---

## 9. Every way this has actually gone wrong

**9.1 — A shared `sigma_u` floor of 0.045 destroyed XC difficulty.**
`tau[XC]` is 0.035. A race-day prior *larger than the course prior* means
the day wins the split on every cross country race, and genuine course
difficulty leaks into `u`. Caught from the boards ("race day might be
taking some of the difficulty"), not from any test.

**9.2 — Then the same floor destroyed TF.** The comment block above
`SIGMA_U_FLOOR` diagnoses 9.1 and, in the same breath, keeps 0.045 on TF —
twice the 0.0228 the data fitted.

```
TF: race-day sd 0.04500 (fitted 0.02279, floor BINDING),
    course prior 0.00147 -- a one-race course keeps 0.00
```

`tau[TF]` = 0.147% against a **measured** 1.22%. Every track was published
as the average track, and indoor/outdoor was flattened to nothing. It
shipped. Both floors are now 0 and `checkPriors` exists so it cannot
happen silently again.

**9.3 — Scaling `pen_cell` by rows-per-race laundered difficulty into
`u`.** An attempt to shrink thin cells harder turned `n/(n+k)` into
`R/(R+k)` and moved the whole effect into the day: `sigma_u` 0.016 → 0.133,
`tau` → 0.005. Caught by three existing tests. The correct fix was
`identified_priors` plus a race-day floor — and then the floor became 9.2.

**9.4 — The √r under-dispersion trap.** Setting `tau` to the true spread
publishes `tau·√r`. See §4. Walked into twice, one file apart.

**9.5 — `difficulty_view` subtracted the corpus mean.** Cross country
dominates the corpus, so that mean was ~+6% and it came off **tracks too**;
a typical track displayed near −6%. The owner asked three times why track
was not 0.0. The engine was innocent every time.

**9.6 — `sigma_u2` became a per-group array and a report line didn't
notice.** Three call sites were updated, one was missed, and the step died
on `numpy.ndarray.__format__` **after a 3073-second solve**. There is now a
repo-wide lint against formatting `sigma_u2`/`tau2` with a scalar spec.

**9.7 — `--holdout` clobbered the diagnostics' own input.** It scored the
held-out races and then solved the full model on a 25% athlete sample and
wrote it over `joint_difficulty.npz` — the file `explain_joint_row` reads.
Half the step's wall clock for a solve nobody looks at. `--holdout-only`
now returns before the solve and before any write.

**9.8 — Row-level holdout leaks.** These are crossed random effects; a
held-out *row* has its own race in the training set, so its `u` is already
fitted and the score measures interpolation. Hold out **races**. The old
`pair_all` number (0.044325) is a row split and is **not comparable** to a
race split — the code now refuses to print it on anything but the row rung.

**9.9 — And the obvious fix to 9.5 is also wrong.** Making the display the
identity is correct for tracks and wrong for every XC course, which then
reads ~7 points harder than anyone would call it. Both "one corpus mean"
and "no mean at all" have shipped and both were wrong. **The owner's
stated preference is TF at 0.0 and XC higher** — i.e. the current
behaviour. Do not "fix" this again without asking.

**9.10 — Guessed column names, three times in one day.**
`course_difficulties.n_races` (it's `n_results`), `meets.venue` (it's
`course_name`), `meets_tf.course_name` (doesn't exist). Read the schema or
introspect it. `database.py:411` has the real definitions.

**9.11 — `venue_check` compared two different zeros**, used a ±150m
tolerance that grabbed the neighbouring distance cell, and ignored the
tilt. All three made published numbers look wrong that weren't. Fixed; the
lesson is that a diagnostic is code and gets the same scrutiny.

**9.12 — `engine_scale.anchor_shift` was not the anchor.** The display
moved to the unweighted mean over track cells (9.5, 9.9) and the shift the
conversions page reads to undo it stayed the results-weighted mean over
both sports, so every named-venue conversion carried the corpus-mix XC/TF
gap as a phantom venue effect. Fixed 2026-09-11: one variable, both uses.

**9.13 — The conversions page's forward map was two undamped passes of a
fixed point**, returning an adjusted time built from the effect at the
previous iterate; the inverse evaluated the effect at the rating that
adjusted time implies. With a hard band step between the two ratings that
was one inter-band step of the 3200 offset, −3.34%: the whole of #21.
Fixed: solved to 1e-10, the offset continuous in the rating whatever the
table holds.

**9.14 — The home-page boards tilted an already-tilted rating.**
`panels._tilted` re-applied `tilt.ratingFor` with the display-anchored
difficulty. The go-live's own warning says not to. Removed.

**9.15 — A prior sized in pseudo-rows.** The first cut of the importance
term put 50 pseudo-rows on a coefficient that the data cannot separate
from `sigma_u²` summed over every championship race; on a small world that
halved it. Priors on shared terms are stated as an SD (`IMP_PRIOR_SD`),
like `DIST_PRIOR_SD`, and `pen = sigma²/SD²` is recomputed each outer.

**9.16 — `--merge-sports` never pinned `mu`.** It refused to bank the cell
means into `mu` but left `mu` itself an unpenalised parameter, so CG parked
whatever it liked there along the near-null direction against the curve;
its test starts from `mu = 0` and never solves. `--sport-level` takes the
level out of theta (`Design.mu_fixed`).

---

## 10. Traps that are not the engine's fault

- `psycopg2` scans `%` **even inside SQL comments** — escape as `%%`
- f-strings eat `{4}` in regexes: `'^[0-9]{4}'` becomes `'^[0-9]4'`, a
  valid regex matching nothing. Use a module constant
- `results_tf.date` is **TEXT** — `EXTRACT` raises, use `substr`
- `getConn` is a `@contextmanager`, not a connection: `with getConn() as c`
  (there is a lint for this)
- `athlete_season.mean_rating` is `percentile_cont(0.80)` over
  **`ranking_results`** (gated), not a mean over `results`

---

## 11. Open, in rough priority order

Done 2026-09-11, unrun on the box (see `docs/RESEARCH-ENGINE-2026-09-11.md`
Part III for what to read in the first log): #21 (three consumer-side
defects, 9.12–9.14), #22 (the field-strength term; the `meet_class`
cross-check column needs a repack), the indoor level asserted, #9/#13 (the tables as the prior
mean of an uncalibrated event offset), the E-step (§5), a fourth distance
band, and `--sport-level` as the way to apply `XC_TRACK_GAP`.

1. **Read the ladder.** `08b` scores `diag-var`, `no-importance`,
   `no-indoor`, `no-dist-table` and `stated-level` against base; a term
   that does not beat base on held-out races comes out.
2. **Not a hyperprior.** With 44k identified cells a half-normal or PC
   prior on `tau`/`sigma_u` is inert; the observed-sd cap is a display
   choice (§4) and stays one.
3. **Altitude per event is in** (`altDistanceFactor`: the 800 a fifth of
   the 5000's cost). What is still one number is the coefficient itself.
4. **#12 era drift is built but off.** Score it (`--era-years 2` on the
   holdout) before turning it on in `08_golive`.
5. **#18 per-cell posterior SD** back on, and `n_results` beside every
   published difficulty (every practical system flags thin evidence).
6. **Rebuild the track curve on equal-quality pairs** with the level and
   sex dependence inside it and retire the per-event offsets — the
   principled end state; today's offsets and table prior are the bridge.
7. **Read the tilt-by-band table.** If the 140+ bands' implied `h` sits
   off the applied line by more than its standard error across runs, fit
   the slope above 140 from that table rather than extrapolating.
8. **Read the field-strength-by-band table and the indoor check.** A bend
   in the residual by band is the shape the field term should take; an
   indoor pair measurement far from +1.2% is the number to change.
9. **Read the era-2 rung and Ultimook's eras** (`scripts/venue_check.py`
   shows the `@e<k>` cells) before leaving `XCP_ERA_YEARS=2` on for good.

---

## 12. How to work on this without losing a day

- **Read the two prior lines first.** `race-day sd` vs `course prior`, per
  sport. Almost every difficulty complaint is that ratio.
- **`checkPriors` silent ≠ correct, but `checkPriors` firing = stop.**
- **Never add a floor to fix a shrinkage problem.** Three of the failures
  above are floors. `identified_priors` is the principled instrument.
- **Score it before shipping it.** `--holdout-only --holdout-kind race`
  costs ~55 minutes on a 25% sample and writes nothing.
- **A diagnostic that disagrees with the owner is usually the diagnostic's
  bug.** §9.11, and the top-25% argument, and Ultimook. He has been right
  more often than the code has.
