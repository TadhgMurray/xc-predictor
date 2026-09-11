# The engine against the field: what other systems do, and what changed

*Written 2026-09-11. Companion to `docs/ENGINE.md`. Part I is the survey
(what was researched and what it says). Part II is what was changed in the
code today, term by term, with the reason. Part III is what remains and how
to verify each change on the box. Nothing here was run against the
database: the sandbox has no Postgres and no pack. Every change is covered
by a synthetic test that runs here (`tests/test_nested_variance.py`,
`tests/test_shared_terms.py`, `tests/test_conversions_engine_scale.py`,
`tests/test_joint_dist.py`), and Part III says what to read in the first
live log.*

---

## Part I — the survey

The engine's open problems, restated so the survey can be read against
them: (1) within a race the course `d` and the day `u` are collinear and
the split rests on `tau²` against `sigma_u²`; thin courses were
under-shrunk; (2) championship-only venues read easy because the taper
lands in `d`; (3) the XC/track level is unidentified because sport is
season; (4) the track distance curve is a tangent extension beyond 3200 m
and under-credits fast 3200s and long events; (5) a 3200 converted to a
rating and back came back 3.4% fast; (6) indoor cells were shrunk to the
outdoor mean by a prior narrower than the effect; (7) elite-only fields
read compressed.

### 1. Practical rating systems that solve the same problems

Every system that rates race results across venues collapses **course and
day into one per-race number computed from competitors whose level is
already known**, and either never publishes a venue number or publishes it
as a long-run prior.

| system | per-race level | venue number | thin evidence | championship |
|---|---|---|---|---|
| **Tully Runners speed ratings** (Meylan, HS XC) | one correction in seconds from known runners: regression of database rating on today's time, outliers beyond 30 s dropped, or the median of (database − implied) over reference runners | none: "does not need to know where a race was run" | no shrinkage; a one-race runner has that value; early season flagged by judgement | same reference-runner method; a tapered field reads as a fast day and is corrected down |
| **runbritain / parkrun SSS** (UK road) | one Standard Scratch Score per race, central value of runners' (today − their best), 1 point ≈ 30 s over 5k | venue lists are averages of SSS, known to be biased by self-selection | none | none |
| **Beyer speed figures** (horse racing) | daily track variant: robust mean over the card of (projected − raw), projection from each runner's recent figures | class pars per track × distance × class, built from years of history | no par without history | class pars: a Grade 1 is compared with a Grade 1 par, so "everyone was good today" is expected, not absorbed |
| **Timeform / Racing Post** | going allowance: median over the card of seconds-per-furlong against standard times | standard times per course and distance from several years | provisional marks (`p`, `+`) | weight-for-age |
| **FIS points** (skiing) | penalty = (A + B − C)/10 from the five best-ranked finishers and starters; unranked athletes counted at the event maximum; category minimum penalties | none | bounded rather than estimated | World Cup / Olympic penalty pinned at zero |
| **IOF world ranking** (orienteering) | race level and scale from ranked runners: `RP = MP + (MT − RT)·(SP/ST)`; a different formula below 8 references | none | explicit formula switch at n < 8 | event-importance factor |
| **Portsmouth Yardstick** (sailing) | median corrected time per race (mean until 2025) | class numbers aggregated over many races, with data-quantity flags | minimum returns | none |
| **USGA course + slope rating** (golf) | Playing Conditions Calculation, −1 to +3, needs ≥ 8 scores | course rating and **slope** (5.381 × (bogey − scratch)), rated by inspection, bounded 55–155 | — | none |
| **Slaney, CrossCountryStats** (CA HS XC) | none: every day effect lands in the course | `race_time = (avg − month·m − year·y) · ability · course`, PyMC, top 25% of each race | wide Gamma prior | none; 1.88% prediction error |
| **NE England Harrier League** (arXiv 2405.09865) | the day through **covariates** (rainfall this month and last), no free race intercept | random course effects | — | — |

Lessons that transfer, in the order they mattered today:

1. **n = 1 is "no venue estimate", not a 59%-credible one.** The shipped
   one-race share of 0.59 (XC) came from an EM E-step whose conditional
   variance ignored the race–course coupling and under-stated `sigma_u`.
   See Part II.1.
2. **The taper is a field effect, never estimated from the venue.**
   Beyer's class pars and FIS's zero penalty at the top are both "the
   level of this field is known from outside the venue". Here the field's
   state is read off its own calendars (the season-end share), not off a
   label. See Part II.2.
3. **The cross-surface scale is stated, never estimated.** Tully publishes
   a speed-rating → 1600/3200 chart; FIS pins; WMA interpolates in
   log-distance; the NCAA publishes facility factors. Nobody identifies
   XC-vs-track from results. See Part II.3.
4. **Tilt belongs to the course and is bounded; the day is never tilted
   for free.** Golf's slope is a course property measured by inspection;
   its day effect is a bounded uniform shift. The engine's `h` on `u`
   (issue 156) is a symptom of `d` and `u` sharing a free tilt; a hard cap
   on `|u|` in the rating (`RACE_DAY_CAP`) is the golf pattern.
5. **Publish confidence with the number.** Timeform's provisional marks,
   PY's data-quantity classes, IOF's formula switch. A difficulty board
   without an n-count reads as broken exactly where it is merely
   uninformed. (`course_difficulties.n_results` exists; the page should
   show it.)

### 2. The statistics of nested effects with singleton groups

For race means `z_ij = d_i + u_ij + e_ij`, `d ~ N(0, tau²)`, `u ~ N(0,
sigma_u²)`: a course with one race informs only `tau² + sigma_u²`; `tau²`
is identified solely by the covariance between races of replicated
courses (Searle, Casella & McCulloch, *Variance Components*, ch. 3;
Pinheiro & Bates 2000). The engine's `identified_priors` (estimate on
2+-race cells only) is the right instrument and stays.

The EM update is `tau²_new = mean(d̂² + Var(d | y))` (Laird & Ware 1982),
and the conditional variance is **not** `sigma²/A_ii`. Within a cell the
`(d, u_1..u_m)` block of the information matrix is an arrowhead, whose
inverse is closed-form; for a one-race cell with many rows the truth is
`sigma²/(P_c + P_u)` while the diagonal goes to zero. The empirical-Bayes
posterior for a one-race course is `E[d | z] = tau²/(tau² + sigma_u² +
sigma²/n) · z` (Gelman & Hill eq. 12.1) — under a correct prior the sd of
published difficulty must **rise** with evidence, so the observed
8.3% → 2.4% fall was the bug signature.

Hyperpriors instead of floors: half-normal(0, 0.05) or a PC prior
(Simpson et al. 2017, `P(sd > 0.10) = 0.05`) on `tau` and `sigma_u`; for
MAP use Gamma(2, 1/A) (Stan prior-choice wiki) so the mode stays off zero
without a floor. Not built today; the nested E-step removed the reason.

### 3. Performance across distances

Every credible model has a **per-athlete distance slope** and its
ability-dependence changes sign with distance range: Blythe & Király 2016
(1.4M UK performances, rank-3 low-rank model, individual power-law
exponent), Emig & Peltonen 2020 (endurance index, `k ∈ [1.04, 1.12]`),
Riegel 1981 (open men 1.077, women 1.083, masters 1.054), Vickers &
Vertosick 2016 (mileage-dependent), critical speed (`D = CS·t + D′`: a
larger anaerobic reserve makes the *short*-range exponent higher for the
fast, the opposite of the marathon effect). The engine's `g[athlete]·lz`
is the individual slope; the population curve is the rank-1 restriction.

The published equivalence tables agree on three facts (computed from the
World Athletics 2025 coefficients `P = a(b − T)²`, cross-checked against
Purdy 1970/1974, Mercier and Cameron):

| segment | men, 1000 → 400 pts | women, 1000 → 400 pts |
|---|---|---|
| 800 → mile | 1.141 → 1.160 | 1.139 → 1.174 |
| mile → 2 mi | 1.100 → 1.112 | 1.100 → 1.133 |
| 2 mi → 5000 | 1.056 flat | 1.074 → 1.086 |
| 5000 → 10000 | 1.074 → 1.102 | 1.077 → 1.090 |

The exponent **rises as ability falls**, women are 0.01–0.02 steeper over
800–5000, and 3200 → 5000 is nearly flat for men. The live `hs_m|TF`
curve runs 1.138 at 1600 → 1.093 at 3200 → 1.075 flat above, i.e. right
at 800–1600, slightly steep at 1600–3200 and too steep at 3200–5000;
`college_m|TF` is 1.047 flat above 5000 from a tangent measured on four
pairs, against the tables' 1.08 (why the 10k reads slow). See Part II.5.

XC ↔ track: grass costs ~5% more energy than a hard surface at the same
speed (Sassi et al. 2011: 4.02 vs 4.22 J·kg⁻¹·m⁻¹) and the cost per metre
is speed-independent to first order (Minetti 2002), so a constant
multiplicative factor is the right shape; coaching practice puts it at
×1.03 (fast, firm) / ×1.06 (average) / ×1.08 (hilly) / ×1.10 (mud);
Tully's own XC → track mapping treats the reference course as ~7% slower
than a track 5000. `XC_TRACK_GAP = ln 1.06` is consistent with all of it.
**Mt. SAC is 4715 m** (2.93 mi) and a 15:40 there is a 9:39 3200 with a
×1.075 terrain factor; 9:01 needs a 5000 m assumption *plus* a hill credit
— the double count the handoff chased.

### 4. Season, taper and the triangle

A 2-week taper is worth about 2–3% (Bosquet et al. 2007 meta-analysis;
Mujika & Padilla: 3%, range 0.5–6%). HS runners improve 3–6% over an XC
season; race-to-race noise for the fastest quartile is 1.2–1.9%
(Hopkins & Hewson 2001) and larger for slower runners. So a
championship-only venue absorbs a taper the size of the course prior, and
"XC → indoor −3.2%, XC → outdoor −2.6%, indoor → outdoor −0.8%" not closing
by 1.4% is the signature of a within-year fitness curve, not of a
constant that was mis-measured. The NCAA facility-indexing study (2012)
is the methodological precedent: same-athlete pairs, then a refit
excluding early and late season to show the venue factor is not fitness.
Regression to the mean: band on the pair average (Oldham 1962;
Bland & Altman 1995), and Hayes 1988 warns that selection by a qualifying
cut-off — exactly a championship — defeats even that.

### 5. Indoor and altitude

NCAA facility factors (2012): flat 200 m → banked, men 800 0.9859, mile
0.9874, 3000 0.9885, 5000 0.9894; women 0.9886 / 0.9902 / 0.9915 /
0.9924; banked and oversized equivalent. WA's 2025 short-track tables
imply 0.75–1.8% at 1000 points. Physics (Taboga & Kram 2019): the curve
cost is `m·v²/r`, negligible on a 400 m oval, 0.3–1.8% on 200 m. So an
indoor effect of ~+1% is real, larger for the fast and the short, and
larger than `tau[TF]` — a shared parameter, not 1,574 cells each
rediscovering it.

Altitude: NCAA tables at 1,511 m are purely multiplicative (800 −0.56%,
mile −2.18%, 3000 −2.46%, 5000 −2.64%); Hamlin et al. 2015 (132k
performances): 2–4% above 1,000 m for ≥ 800 m. The engine's 0.035 /km
above 600 m held at the prior is in range; the tables say the coefficient
should be smaller for the 800 (~1%/km) than for 3000–10000 (~4%/km).
Not changed today; noted for the altitude term.

---

## Part II — what changed, and why

### II.1 The variance E-step: the nested block, not the diagonal

`joint_solve.nestedPosteriorVar` computes, per cell, the exact inverse of
the `(d, u_1..u_m)` arrowhead with the abilities held:

```
s        = P_c + Σ_j n_j P_u / (n_j + P_u)
Var(d)   = sigma² / s
Var(u_j) = sigma² [ 1/(n_j + P_u) + (n_j/(n_j + P_u))² / s ]
```

and the `tau²` / `sigma_u²` updates use it (`nested_var=True`, default).
With the probes off, `cell_var` is this block rather than the information
diagonal. Measured against the **exact** dense inverse on corpus-shaped
worlds (24–36 rows per race, 3–5 races per athlete): exact, profiled and
nested all land on the planted race-day sd; the diagonal sits 8–10%
under it. On a world with 4 rows per race and 3 races per athlete *not
even the exact E-step* recovers `sigma_u` (0.006 against 0.030): that
regime is unidentified, and the recorded "sevenfold under-recovery" was
partly that world, not only the estimator. `tests/test_nested_variance.py`.

What this does on the box: `sigma_u` per sport should come out a little
higher than run22's, the one-race share `tau²/(tau² + sigma_u²)` a little
lower, and the sd of published difficulty across `n_results` buckets
(`scripts/difficulty_spread.py` §2) should stop *falling* with evidence.

### II.2 The season-end taper term (issue #22)

**Second cut, same day.** The first cut read a championship class off
the meet's name (three classes, each with its own coefficient, an
invitational guard, a season window, a one-race gate). The owner's two
objections were to the label itself: "no one is tapering for their
league championship, but they are for their state meet; I'm not sure
you can just blanket these things", and "it's so easy for it to go bad;
some other system would be best". Both are right about any label, and
no guard fixes a label. So the covariate is now measured, per race,
from the athletes' own calendars:

    share(race) = # voters in the race whose own season ends within
                  SEASON_END_DAYS (14) of this race
                  / # voters in the race

where a voter is an athlete-season with `SEASON_END_MIN_RACES` (3) or
more races whose last race is more than `SEASON_CLOSED_DAYS` (21) before
the pack date (a season still running has no last race yet, and without
that rule this week's invitational would look like a season end). A
state final's share is near 1, a September invitational's near 0, a
league championship's is whatever its field says, which for most leagues
is low because most of the field goes on. `run_joint.seasonEndShare`.

Model: `row += imp[pool, sport] · share` (untilted), prior mean zero,
prior sd `IMP_PRIOR_SD = 0.02`. Identified off venues that host races
with different shares and off athletes who run both. **Not in a
rating**: a tapered race is a real performance, exactly as the race-day
term is left in; the term exists so the course stays honest. A healthy
fit reads about −2% per unit share (`IMP_EXPECTED`; Bosquet 2007,
Mujika & Padilla), which is recorded as an expectation, never applied.
On the planted world the coefficient is recovered from a continuous
share (`tests/test_shared_terms.py`) and the calendars of a fake pack
give the shares by hand (`tests/test_meet_class.py`).

Why this is safer than a label, in the owner's terms. Within a race
every row carries the same share, so the term cannot move one athlete
against another; across races it moves a race's rows together against
the course, and the course is pinned by the venue's other races or, at
a one-race venue, shrunk to the prior as it always was. A wrong share
needs a wrong calendar, and a calendar is a fact about the athlete, not
a reading of a name. The regression-to-the-mean warning (Hayes 1988:
selection by a qualifying cut-off) still applies to the race-day term,
which is why a qualifier's own deviation stays in `u`.

Design note kept from the first cut: a prior stated in pseudo-rows (a
first cut used 50) competes with `sigma_u²` summed over all the races
that carry the covariate and halved the estimate on a small world. The
prior is a stated SD, like `DIST_PRIOR_SD`.

The name classes (`engine/meet_class.py`, pack column `meet_class`) are
kept as a **diagnostic**: the solve prints the mean season-end share by
name class, and the finals must show the highest share. If they do not,
the calendars are wrong somewhere and that line says so.
`scripts/meet_class_census.py` prints the names behind each class.

### II.3 The sport level: asserted, out of theta

`--sport-level G` (`XCP_SPORT_LEVEL`) puts `mu = [0, −G]` on the Design
(`mu_fixed`): the block is empty, `h·mu` comes off `y` every pass, and
the per-sport means of `d` and `u` are dropped, exactly `--merge-sports`'s
semantics with a number instead of zero. `--merge-sports` used to leave an
*unpenalised* `mu` in theta and merely refuse to bank the cell means into
it, so CG parked whatever it liked there along the near-null direction
against the curve (its test starts from `mu = 0` and never solves).
`XC_TRACK_GAP = ln 1.06` was named "the free scalar, written down once"
and was applied nowhere; now it can be. **Off by default** in the
pipeline (the estimated level is what shipped); Part III says how to run
it. Under it the go-live's XC mean is the definition, not a measurement,
and the log says so.

### II.4 Indoor as a shared term

`row += h · ind[pool] · is_indoor[cell]`, prior `IND_PRIOR_MEAN = +0.012`,
sd `0.01`, from the cell key (`TF:loc:<id>:in`), no pack change. Each
cell's mean indoor term is folded into `delta`, so the go-live and the
board see one course number, and the display zero is now the average
**outdoor** track. On the planted world (40 thin indoor cells sharing
+1.2% under `tau[TF]`'s cap) the coefficient is recovered to 0.4% and the
indoor cells' rmse falls.

### II.5 The distance chain

- **A fourth rating band** (`DIST_BANDS = (105, 120, 135)`,
  `DIST_BAND_ANCHORS = (90, 112, 127, 145)`), so a 1:48 half-miler rated
  150 is no longer handed the 800/1600 relation of a 4:10 miler. The
  winter-gain machinery keeps its own three bands (`SPORT_GAIN_BANDS`).
  `distance_offset` gains a fourth band row per class; the page's
  constants match (pinned by test).
- **The published tables as the prior mean** (`engine/distance_tables.py`):
  for a class the season-best pairs cannot calibrate, the prior mean is
  the table's log-time relation between the event and the pool's
  reference event *minus what the potential already applied*, zero where
  the curve already agrees. Level- and sex-dependent exponents from
  Part I.3. `--no-dist-table` restores the zero prior.

### II.5b Altitude per event, and the rungs (second commit)

- `ALT_DIST_KNOTS` / `altDistanceFactor`: the row's altitude exposure in
  the solve and its credit in the rating are scaled by the event's share
  of the 5000's cost (NCAA Albuquerque tables: 800 0.21, mile 0.83, 3000
  0.93, 5000 1.00; 10k 1.05 from Hamlin). A row without a distance and
  every XC row read 1.0, so the old designs are exact. The acclimatisation
  logic is untouched: the field's home altitude comes off first, then the
  share applies.
- `--diag-var` restores the old E-step for the ladder, and the ladder gains
  `diag-var`, `no-importance`, `no-indoor`, `no-dist-table` and
  `stated-level` rungs so every 2026-09-11 term is scored on held-out
  races by `08b`.
- The XC pack reads `meets_tfrrs.is_championship` as class 2 before the
  name regex, for the `meet_class` cross-check column only.
- The tilt runs on past 140 instead of clamping (`TILT_RATING_LO/HI` are
  40/200, safety rails), and `run_joint.reportTiltByBand` prints the
  applied against the implied `h` per rating band every run.

### II.6 The conversion round trip (#21) — three defects, all consumer-side

1. `conversions._norm_from_time` ran an undamped two-pass fixed point and
   returned an adjusted time built from the effect at the *previous*
   iterate, while the rating the page reports and the inverse evaluates
   at is `100·pm/adjusted`. The round trip was `t·exp(eff(r₃) − eff(r₂))`.
   It is now iterated to 1e-10 with damping and closed with one plain
   step, on both the engine-scale and the legacy path.
2. `conversions.distance_offset` fell back to a **hard band step**
   whenever any band was missing; with `r₂` and `r₃` on opposite sides of
   120 the two legs took different steps: one inter-band step of the 3200
   offset, −3.34%, reproduced exactly. It now borrows the nearest present
   band and interpolates, continuous everywhere.
3. `joint_golive` wrote `engine_scale.anchor_shift` as the results-weighted
   mean over both sports while the display anchor had moved to the
   unweighted mean over track cells, so every *named-venue* conversion
   carried the corpus-mix XC/TF gap as a phantom venue effect. One
   variable now serves both.

Also: `_norm_from_result` routed **every** stored row through its raw
time (the `'|'` test that told solved from filled rows became true of all
rows when the go-live started writing bare pools) — it inverts the stored
rating again; `panels._tilted` tilted an already-tilted rating with the
display-anchored difficulty for the home-page boards — removed; the
athlete header's "about a m:ss 5K" was `100·pm/rating` at a zero-effect
venue, labelled 5K for college men (8000 m) and middle schoolers (3200 m)
— it now goes through `normalized_to_time` at a typical venue and labels
the pool's distance.

`tests/test_conversions_engine_scale.py` closes the trip at 800 / 1600 /
3200 / 5000 / 10000, with an incomplete table, at a straddling rating, and
across events; the residual is the forward leg's 0.01 s rounding.

---

## Part III — what remains, and how to verify

### The next run

```
cd /srv/xc-predictor && git pull
set -a; . /etc/xc-predictor.env; set +a
XCP_ALTITUDE=1 bash deploy/run_pipeline.sh --from 7 2>&1 | tee logs/run19.out
```

`--from 7` because the pack gains the `meet_class` cross-check column
(the taper term itself reads the calendars every pack has). Then, if the
owner wants
the stated scale rather than the estimated one:

```
XCP_SPORT_LEVEL=0.0583 XCP_ALTITUDE=1 bash deploy/run_pipeline.sh --from 8 ...
```

### What to read in `08_golive.log`

```
[joint] season-end taper: N of M rows carry a share ...                    # by name class: share must rise with class
[joint] indoor: 1,574 of 74,366 cells are indoor tracks ...
[joint] XC: race-day sd ... course prior ... a one-race course keeps 0.xx   # compare with run22
[joint] season-end taper, log-time per unit share ...                      # expect near -2%
[joint] tilt by rating band ...                                            # 140+: implied h close to applied
[joint] indoor, log-time per pool ...                                      # expect +0.5..+1.5%
[joint/live] track zero: mean 0.000 ... over N track cells                 # outdoor cells now
[joint/live] difficulty zero = the average TRACK. Cross country lands at   # the estimate, or the definition
```

Then `scripts/difficulty_spread.py` (the sd must **rise** with
`n_results`), `scripts/venue_check.py --venue 'Foot Locker'` /
`'Balboa'` / `'Glendoveer'` (championship venues should read harder than
before), and `scripts/convert_probe.py --tf --person 29603086 --seconds
541.1` (the round trip must close).

### Open, in priority order

1. **Read the ladder.** `08b` now scores `diag-var`, `no-importance`,
   `no-indoor`, `no-dist-table` and `stated-level` against base on
   held-out races. A term that does not beat base on that number is
   decoration and should come out; the level rung cannot be scored on the
   level itself (sport is season) and is read for what the free curve does
   to everything else.
2. **Era drift** (`--era-years 2`) is built and unscored.
3. **Publish `n_results` and the posterior sd beside every difficulty**
   (Timeform's `p`, PY's data classes). `cell_var` is in the npz; the
   `course_difficulties` table has no column for it yet.
4. **Rebuild the track curve on equal-quality pairs** with the level
   dependence of Part I.3 inside it, retiring the per-event offsets — the
   principled end state; today's offsets and table prior are the bridge.
5. **A per-course tilt with a tight prior** (golf's slope) only if the
   data ask for it after the importance term is in: Ultimook-type mud
   courses cost slow runners more than the global tilt says, elite-only
   fields cannot estimate one at all, and a free per-cell slope is how the
   `(d = +c, u = −c)` exploit of issue 156 happened.

Not worth doing, on reflection: a hyperprior on `tau` / `sigma_u`. With
44k identified cells and 540k races voting, any half-normal or PC prior is
inert on the estimate; the observed-sd cap is a display choice (§4 of
`ENGINE.md`) and stays one.

### Sources

Systems: Meylan/Tully (`tullyrunners.com/articles/refrunner.htm`,
`art_mey_sr.htm`, `Track-vs-SpeedRatings.htm`); MileSplit course-rating
(articles 48614, 325752); Slaney (`github.com/MalcolmSlaney/CrossCountryStats`);
Beyer (DRF, Paulick Report); FIS points rules 2025/26; IOF world-ranking
rules 2021; RYA Portsmouth Yardstick 2025; USGA Course Rating System;
runbritain SSS; arXiv 2405.09865 (Harrier League), 2002.06105 (Vaporfly).
Statistics: Searle, Casella & McCulloch 1992; Pinheiro & Bates 2000;
Gelman & Hill 2007; Gelman 2006 (*Bayesian Analysis*); Simpson et al. 2017;
Laird & Ware 1982; Wood & Fasiolo 2017; Coulom 2008 (WHR); Dangauthier et
al. 2007 (TTT); Glickman 1999/2001; Ford 1957.
Performance: Blythe & Király 2016 (*PLoS ONE*); Emig & Peltonen 2020
(*Nat. Commun.*); Vickers & Vertosick 2016; Riegel 1981; Péronnet &
Thibault 1989; Bundle, Hoyt & Weyand 2003; World Athletics scoring tables
2025 (`github.com/GlaivePro/IaafPoints`); Purdy 1970/1974; Cameron 1998;
Daniels–Gilbert.
Season and surface: Bosquet et al. 2007; Mujika & Padilla 2003/2011;
Hopkins & Hewson 2001; Malcata & Hopkins 2014; Sassi et al. 2011; Minetti
et al. 2002; NCAA Facility Indexing Conversion Summary 2012; Taboga & Kram
2019; Hamlin, Hopkins & Hollings 2015; Wehrlin & Hallén 2006; NCAA
altitude adjustment tables (UNM booklet); Oldham 1962; Bland & Altman
1995; Hayes 1988.
