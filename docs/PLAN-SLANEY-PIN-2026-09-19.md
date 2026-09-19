# The Slaney rebuild: a fixed reference, a race-day term, and shrinkage that runs

2026-09-19. **Plan only. Nothing here is implemented.**

Owner: "copy malcolm slaney's methodolgy exactly, add some shrinkage, maybe a
race-day term, make all flat outdoor 400m tracks 0.0, indoor on avg +0.3%
slower, indoor allowed −0.3% to +2.0%, and courses can change difficulty every
couple years / maybe every year." Then: "Copy it literally word for word just
add these things I've said."

---

## What the 2026-09-19 02:15 run actually did, and what it did NOT

Established from the run's own logs, and it changes the baseline every
measurement below rests on.

**It ran `scripts/pipeline.py`, not `deploy/run_pipeline.sh`.** The log's banner
is `PIPELINE -- TF (results_tf) stages: backfill, engine, suspects`, which is
pipeline.py's; the chain was only switched to the real pipeline at 10:59:40, and
the run started at 02:15. So:

* **`08_golive` never ran. The bracket engine never ran. No XCP_ constant was
  set. `course_difficulties` was never rewritten** — confirmed independently by
  `joint_difficulty_state.npz` still being dated 2026-09-14_21:26.

⚠ **SO THE DIFFICULTY COMPLAINTS PREDATE THIS SESSION.** Non-California courses
  reading negative, and Mt. SAC's 5k at +4.0% against the 3 mile's +10%, are
  both from the September 14 solve. The merged distance curve cannot have caused
  either — it never reached a difficulty. Four failed hypotheses about Foot
  Locker were chasing a real problem that is older than any change here, and
  the leverage arithmetic that cleared the curve was right for the wrong
  reason: it was not that the effect was too small, it is that the path did not
  exist.

★ **WHAT THE RUN DID CHANGE, AND IT IS STILL WRONG.** The backfill rewrote
  `normalized_time` with the merged curve — 29,912,105 TF rows changed — and
  `speed_ratings.py` then ran for both sports. So the live ratings are computed
  on the NEW curve against difficulties computed on the OLD one. That is the
  inconsistent state, and it is the reason a restoration is still needed: not
  to undo bad difficulties, but to put ratings and difficulties back on one
  curve. `deploy/run_pipeline.sh --from 5` is what does that, and it is what the
  chain now calls.

! **AND THE PACK IS UNTOUCHED.** `07_pack` never ran, so `packed_XC_TF.npz` is
  still 2026-09-14_19:35. Every holdout number in this session was measured on
  that pack — which makes them comparable with each other, and means none of
  them reflects the team scrape.

---

## 0. What "word for word" can and cannot mean

I did not have his estimator when I first answered — only `compare_slaney.py`,
which parses his **published output table** (`course_difficulties.html`) and
describes his model in prose. Having now read the repository
(`MalcolmSlaney/CrossCountryStats`), two things must be said before any of this
is scheduled.

**His model equation can be copied literally. His estimator cannot run here.**

    race_time = (average_race_time − race_month*month_slope − student_year*year_slope)
                * runner_ability * course_difficulty

He fits it with **PyMC — Bayesian MCMC, 36 chains × 2,000 samples**, on 70,696
boys' results and ~63,000 girls'. Our corpus is **62,805,298 rows**: about 900×
his. MCMC does not scale there, and no amount of machine buys three orders of
magnitude. Copying his *code* verbatim means copying a sampler that will not
finish.

**And on three terms, copying him verbatim would be a downgrade.** Term by
term:

| his term | ours | verdict |
|---|---|---|
| `race_month * month_slope` (linear) | `curve`: per-pool form curve over day-of-year, amplitude scaled by rating | **ours is richer** — his is a straight line through a shape we already fit |
| `student_year * year_slope` (linear) | ability keyed per (person, pool, **season**) | **ours is richer** — a free level per season assumes no functional form at all |
| — (absent) | `tilt`, the per-course penalty varying with ability | **ours only**, and `ability_slope.py` measures it as real: elite runners rate 7–8 points too high at a hard course, slow runners 8 too low. A single scalar difficulty fits the middle and misses both ends |
| priors: ability σ=0.25, course σ=1.0 | courses shrunk by a fitted k; **athletes not at all** | **his is better**, see §6 |
| Crystal Springs ≡ 1.0 | vote-weighted mean ≡ 0 | **his is better**, see §2 |
| — (absent) | — | race-day term: **neither has one**, §4 |
| — (absent) | `era_years` | course change over time: **ours only**, §5 |

So the useful reading of the request is: **take the two things he has that we
do not, keep the three we have that he does not, and add the two neither has.**
Which is what the rest of the instruction already says. The one thing to drop
is "word for word".

**What his code IS good for: §7.** Run it unmodified on a California subsample
as an external check on our solver. That is a real use of "exactly" — same
model, same data, a different estimator family — and it is the only way to tell
a solver bug from a modelling gap.

---

## 1. Geometry plumbing — the gate on everything else

**Nothing else in this plan can start until this lands.** The bracket cell key
is `TF:loc:<id>:in|:out` — location and surface, **no track length, no
banking**. "All flat outdoor 400m tracks" cannot be addressed today, which is
why what shipped on 2026-09-18 was a mean-pin over all outdoor cells rather
than the owner's design. That was mine to state at the time and I did not.

The data exists. `meets_tf_meta` carries `track_type`, `track_length`,
`is_indoor`; `engine/geometry_resolver.py` already resolves them per meet and
`engine/venue_geometry_overrides.py` already corrects known-wrong venues. This
is plumbing, not collection.

1. **Carry geometry to the pack.** Add `track_length` and `track_type` to the
   pack columns beside `course_lat`/`course_lon`, resolved through
   `geometry_resolver` so the overrides apply.
2. **Define the reference class, once, in one function.** `isFlatOutdoor400(...)`
   → `track_length` rounds to 400 **and** `track_type` is not banked **and**
   not indoor. One predicate, one place, so the pin and every diagnostic agree.
3. **Census before anything uses it.** How many outdoor TF cells qualify, how
   many rows, how many are unknown-geometry. A reference class that covers few
   cells is a worse anchor than the mean it replaces, and that has to be known
   *before* the pin is written, not after a solve.

**Test:** the predicate against `venue_geometry_overrides`' hand-verified
venues, including the oversized-flat and banked cases it lists.

---

## 2. The reference pin: flat outdoor 400m ≡ 0.0

This is Slaney's Crystal-Springs-≡-1.0 generalised from one course to a class,
and it is the highest-value item.

**Why it is nearly free, measured.** Last night's fitted priors:
`TF:out` race-day sd **1.60%** against course sd **0.71%**, ratio 5.13 — so a
one-race outdoor track already keeps only **16%** of its own reading. The model
already says outdoor tracks are near-identical and the spread is mostly day
noise. Fixing them at 0.0 is a short step past what the shrinkage does anyway.

**Why it matters more than that sounds.** It converts the gauge from a free
parameter into a fixed reference. Today the zero floats on a vote-weighted
mean, which is exactly how "every course outside California went negative"
becomes possible — a redistribution has nothing absolute to push against. With
flat outdoor 400s fixed, every athlete who has run a track race carries a
calibrated reference and cross-country is measured against that rather than
against itself.

Implementation: replace `gauge_ref` (currently "not indoor") with the §1
predicate, and hold those cells at **0.0 each** rather than holding their mean
at 0. The `use = m_ref if m_ref.any() else m_g` fallback stays — a group with
no reference cell must still be pinned somehow or it drifts without limit.

**Test, and it is the owner's own:** after the pin, non-California courses stop
being uniformly negative. That is a one-line check on the difficulty table and
it should be run before anything is published.

---

## 3. Indoor: a prior at +0.3%, with −0.3%/+2.0% as gates that flag

Agreed as sanity gates, not clamps. A clipped cell stops responding to evidence
and cannot be told apart from a genuinely +2.0% one — the same mistake as the
distance floor, which made curves legal rather than right while the prior
inside the fit did the job properly.

1. Shrink each indoor cell toward **+0.3%** by its group prior (the existing
   `TF:in` machinery, fitted at 1.05–1.14 races last night).
2. Keep −0.3%/+2.0% as a **report**: count and list cells outside it, publish
   them anyway, and treat a growing count as evidence the centre is wrong.

**And there is a bug to find here first.** `XCP_INDOOR_LEVEL=0.003` already
asserts +0.3% — the owner's exact number — yet indoor measured **−1.68%**, 1.5%
*easier* than outdoor, with the sign wrong for a slower oval. So either the
assertion is not applied or the gauge overrides it. Finding that is probably
most of the indoor work, and it should be found **before** the centre is
changed, or we will be tuning a number that is being ignored.

---

## 4. The race-day term, which the pin is what makes possible

Neither model has one. Today, in a one-race cell the course effect and the day
effect **are the same number** — `tests/test_thin_course_shrinkage.py` measured
σ_u collapsing to 0.0012, the course keeping 100% of one day's noise.

With every flat outdoor 400 fixed at 0.0, any deviation at one of those races
**is** the day. That is a clean, direct estimate of race-day variance, from
cells where the course term cannot absorb it.

    per race r at a reference cell:   u_r = mean over voters of (z − a)/h
    sigma_day  = the robust spread of u_r across reference races

Then use that σ as the **numerator** of the XC shrinkage instead of estimating
it from XC's own confounded cells — which is where §6 gets its number.

Order matters and is the point: **pin → measure day noise → shrink with it.**
Each step makes the next measurable, which is the opposite of how the
2026-09-19 run went.

---

## 5. Courses changing over time — largely already built

`XCP_ERA_YEARS` splits every course into N-year eras tied by a random walk, and
`XCP_ERA_DRIFT` sets the walk's step (log-time sd per era, default 0.01). Last
night ran `era_years=2`. The go-live publishes each venue's latest era under
its bare key.

So the work is not building it, it is **choosing N by holdout** — and the
harness already takes `--era-years`, with `--dump`/`--compare` for the
comparison, because a shorter era changes coverage as well as error:

    for n in 1 2 3; do bracket_holdout --era-years "$n" --compare <baseline>; done

One caution specific to N=1: an annual era with one race day in it is the
degenerate case §4 describes, so N=1 is only safe *after* the race-day term
exists. Sequence it last.

---

## 6. Shrinkage — and his priors point the opposite way to ours

His: **runner ability σ=0.25, course difficulty σ=1.0.** Tighter on runners
than on courses.

Ours is the reverse. Courses get three nested shrinkages and a fitted prior;
`levels()` gives the athlete a **plain mean** of their other rows in the window,
so one row counts as much as thirty — both as a voter, whose noise lands in the
course, and as the published rating. That is independent external support for
the athlete prior already built in `bracket_engine` (`prior_athlete`, shipped
off) and for the owner's "shrinkage should prolly be greater".

1. Score `--prior-athlete fit` against 0 on the athlete-thinness table already
   added to the holdout. Thin buckets should improve, fat buckets not worsen.
2. Feed §4's σ_day into the course prior instead of the within-multi-race-course
   estimate. Note the selection this fixes: the prior is currently fitted on
   the 17,333 XC courses with 2+ races and applied to the 14,147 with one.
3. Leave the course prior's *form* alone. It already de-biases τ correctly
   (`fitPriors` subtracts `mean(s_w2/eff)`), and its 61% weight on a one-race
   course is exactly `τ²/(τ²+σ²)` — right, given those variances. The variances
   are what §4 improves.

---

## 7. His code as an external check, on a subsample

The honest use of "exactly".

1. Take his repository unmodified and a California HS XC subsample sized to
   what PyMC will chew — his own run was ~70k results, so match that.
2. Fit both: his sampler and ours, same rows, same reference course.
3. Compare **course by course**, in his units, which `compare_slaney.py`
   already knows how to do (it converts ours forward, putting distance back,
   rather than correlating raw numbers that would mostly measure distance).

What agreement would prove, and `compare_slaney.py` says this already: our
solver and our normalisation are sound. What it cannot prove is any term
neither model has — both are two-way fixed effects on overlapping populations
and share their blind spots, altitude and championship taper among them.

**And disagreement is the valuable outcome**, because it separates a solver bug
from a modelling gap, which is the thing four failed hypotheses about Foot
Locker could not do.

---

## 6b. Are the pools free-falling against each other?

Owner: "I wonder if the pools are kind of free falling against each other
because they aren't well connected."

**Two facts make that precise.** Ability is keyed `akey = (r[_PID], pool)`, and
the rating is `points = pool_mean / ability * 100`. So:

* **within** a pool the scale is fixed by construction — 100 is that pool's mean;
* **between** pools the ONLY connection in the data is courses both pools race,
  because `D` is keyed per course with no pool in the key;
* and the best bridge available — the same human, spring high-school track then
  autumn college cross-country, weeks apart — is **severed**: a pool change
  makes two independent ability unknowns out of one person.

⚠ **AND A POOL-WIDE DRIFT IS INVISIBLE WHERE ANYONE WOULD LOOK FOR IT.**
`points` divides by the pool mean, so shifting a whole pool moves no athlete's
rating at all. It shows up only in the difficulties of courses that pool races
— which is where the damage was actually noticed. Any check for this has to be
on difficulties or on cross-pool races, never on ratings.

### What to do, in order

**(i) The track pin already fixes most of this, which is a reason to want it
more.** Every pool races flat outdoor 400s. Once those are 0.0 by construction
(§2), high school, college and pro all share one ABSOLUTE reference, and each
pool's ability scale is anchored to it rather than to itself. Track is the one
venue every pool has in common, so it is the natural connector and it is
already item §2. No new term needed.

**(ii) Measure it before modelling it.** Two numbers decide whether this is real
or theoretical, and neither needs a new solve:

* `engine/diag_connectivity.py` already builds the bipartite athlete × course
  graph, and its own docstring says it keys on `person_id` alone while the
  engine keys `(person_id, pool)` — so it reports an **upper bound** and
  splitting by pool "can only ever SUBDIVIDE a component". Run it pool-aware
  and count how many courses are raced by two or more pools, weighted by rows.
* Count the **crossing athletes**: people with rows in two pools within one
  year. That is the sample size any transition term would rest on.

**(iii) A cross-pool error check, from machinery that exists.** A pool-wide
drift mis-predicts mixed races systematically. Take held-out races whose
finishers span two pools and report error by pool pair. If the pools have
drifted apart, that table says so and says by how much — and it is the same
`bracket_holdout` bucketing already used for course thinness and athlete
thinness.

**(iv) Only then, a pool-transition offset.** If the crossers are numerous and
the mixed-race check shows drift:

        ability[i, pool2] = ability[i, pool1] + offset(pool1 -> pool2) + delta[i]

one shared scalar per ordered pool pair, with `delta[i]` shrunk — the offset is
the pool gap, `delta` is how far this person differs from a typical transition.

! **THE PATTERN ALREADY EXISTS IN THIS CODEBASE**, which is the argument for
  doing it this way rather than inventing something. `XCP_SPORT_LEVEL` pins the
  XC/TF gap as a stated scalar instead of estimating what the data cannot
  identify, and `XCP_SPORT_LEVEL_POOLS` already carries it PER POOL
  (`college=0,hs=0.008,ms=0.012,elem=0.015`). A pool-transition offset is the
  same move on a different axis.

⚠ **DO NOT instead let one ability span the transition.** A high-school senior
  becoming a college freshman racing 8k against 22-year-olds undergoes a real
  level change, not noise. The severing is right; what is missing is a term
  that says how big the typical jump is.

---

## 8. Order, and one rule

1. §1 geometry plumbing + census  ← gates everything
2. §3's *bug hunt* only: why is asserted +0.3% indoor reading −1.68%?
3. §2 the pin. **Stop. Check non-CA courses are no longer uniformly negative.**
4. §4 race-day term off the pinned cells
5. §6 shrinkage, using §4's σ; and §6b (ii)+(iii), which are measurements
   and can run any time
6. §3 the indoor prior and gates
7. §5 era length by holdout, N=1 last
8. §6b (iv) a pool-transition offset, only if (ii) and (iii) say it is needed
9. §7 the external check, whenever convenient — it blocks nothing

**One change per solve.** The 2026-09-19 failure was two unvalidated changes
landing together: `--merge-sports` became the default before the shape test
that was built to decide it had ever been read, and it went out in the same run
as a rebuilt `school_levels`. Neither could be blamed or cleared afterwards.
Every step above has a named check, and the check runs before the next step
starts.
