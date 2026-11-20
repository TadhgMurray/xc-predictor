# Warm-starting the joint solve: the margin of error (2026-11-20)

The owner asked: if 08_golive's joint solve started from last night's answer
instead of from scratch, how far would the published numbers move?

This is a measurement only. The shipped solve is unchanged: the warm start
is a test-only hook that runs only when a caller sets `D.warm_start` and
`XCP_WARM_START` is set, and nothing in run_joint or the pipeline sets either.

## Short answer

There are two kinds of warm start. They give very different answers.

| | What starts from last night | Result vs tonight's cold solve | CG work saved |
|---|---|---|---|
| **theta** | only the unknowns, as pass 1's CG start | **The same answer, up to the CG tolerance.** The difference is no bigger than the cold solve's own CG tolerance. | small: pass 1 only (15–18 fewer CG iterations of 97–271 on the synthetic data, 7–15%) |
| **full** | the unknowns plus the weights, tilt, amplitude, variance components, curve prior and day-guard offset, so `XCP_OUTER` can drop | **A different answer.** It differs from tonight's cold solve by about as much as five outer passes are short of convergence: median ~0.01 pts, p99.9 0.2–0.9 pts, max 1–3.6 pts on an athlete-season rating; 2–7% of athlete-seasons and 6–18% of rows move by ≥ 0.05 pts. | large: 1–2 outers instead of 5 means 55–85% fewer CG iterations, and the per-pass overhead goes too |

A full warm start is **not** within the cold solve's own convergence
tolerance. But the cold answer is not converged either. The cold solve stops
after a fixed five outer passes, with no convergence test. After five passes
the variance components are still moving. On these designs, cold 5 is about
as far from the converged point (cold 10) as a full warm start is from
cold 5. A full warm start lands *closer* to the converged point than tonight's
cold run does (table B). In return, the owner gives up reproducibility: the
board would depend on the history of nights, not only on tonight's data.

## 1. How the solve terminates, and why the path matters

- **The CG solve** (`conjugateGradient`) stops when `||r|| / ||rhs|| < CG_TOL`
  (1e-8, every pass: `CG_TOL_OUTER = CG_TOL`), capped at `CG_MAX_ITER` = 600.
  Passes 2 to 5 already warm-start CG from the previous pass's theta
  (`x0=theta`). Pass 1 starts from zero.
- **The outer loop** runs a fixed `n_outer` (`XCP_OUTER`, 5 in
  `deploy/joint_flags.sh`). There is no convergence check. The only evidence
  for 5 is the run-12 note in run_pipeline.sh: "the sixth outer moved sigma
  by 0.00003 and the level by nothing". Nobody measured how far it moved the
  ratings.
- **The final answer is the exact minimiser of pass 5's linear system, up to
  `CG_TOL`.** That system is built from the previous pass's output:
  - the robust weights `w`, from pass 4's residuals;
  - `sigma2`, `tau2` and `sigma_u2` (the variance components, updated EM-style);
  - the curve prior `lam`;
  - the tilt `h` and the amplitude `amp`, from pass 4's ratings;
  - the distance bands and their calibration (`D.rebandDist` / `D.calibrateDist`);
  - the day-guard offset (`raceDayDivisions`);
  - and, when those features are on, the field-strength weights, the effort
    E-step and the season tie.

  So the published point is "five steps of a fixed-point iteration from a
  fixed start" (tau2 = 0.05, sigma_u2 = 0.01, sigma2 = 1, w = h = amp = 1).
  It is not the fixed point.
- **What this means for a warm start.**
  - A theta-only warm start changes only where pass 1's CG starts. Every pass
    still solves its own system to 1e-8, so the path is the same up to the
    tolerance.
  - A full warm start changes the path. Pass 1 starts from night 1's pass-5
    hyperparameters, so k warm passes are really passes 6 to 5+k of one
    long iteration that runs across nights.

## 2. The synthetic design

- The harness is `scripts/moe_warm_start.py`. It is production-shaped and
  grew from the earlier `synth.py`.
- **Size:** 1M rows, 125k athlete-seasons, 25k races of ~40 rows each,
  3,125 cells, 8 pools.
- **Blocks:** every block the go-live builds. That means the sport offset
  (beta), the year curve fitted per pool, rust, distance classes in rating
  bands with calibration, slope, altitude, meet importance, indoor, the day
  guard on subraces, robust weights, the tilt, and the course priors capped
  at `TAU_MAX_DEFAULT`. The form trend is off, as in production.
- **Linkage:** clustered. 95% of an athlete's races are in their own region,
  and 70% of athletes run one sport only. An unclustered random draw is an
  expander graph, and CG converges in a few steps, which says nothing about
  production.
- **Hard variant (`MOE_HARD=1`):** 99% of races in region, 10% two-sport
  athletes, and season links between consecutive athlete-seasons of one person.
- **Night 1** is the corpus minus one meet weekend. 0.05% of its rows carry a
  wrong time (an error of 3–25%).
- **Night 2** is night 1 plus the weekend (whole races on one weekend, 0.70%
  of rows, with some new athletes and cells), with those 0.05% rows
  corrected. Dense ids are rebuilt each night. Night 1's state is mapped onto
  night 2 by global key, the way production would have to do it.
- **What is compared** is what the site shows:
  - the athlete-season rating `100 · pool_mean / exp(a)`, in points;
  - the per-row rating `rating · exp(a − (y − h·(δ + u)))`, in points;
  - course difficulty `100 · expm1(δ)`, in percentage points.

  Points are on each pool's own scale. The HS-equivalent number a page shows
  is this times the pool's factor, so the counts below hold to within that
  factor.

### Cold run-to-run noise

None. Two cold solves of the same night are **bit-identical** (theta,
ratings, deltas and weights; numba with 4 threads, and with 1 thread).

One trap for anyone running a harness: `solveJoint` mutates its `Design`.
It re-points `e_idx` and re-calibrates `e_mean` every pass, and the field
mode writes `imp_w`. So a second solve on the same `Design` object is not
cold. In a first draft of this harness that showed up as a 0.69-point
"repeat" difference. Build a fresh `Design` for every solve. run_joint
already does this: one solve per design.

## 3. Measurements

Notation: max / p99.9 / median of |difference|, then the count ≥ 0.05 pts.
There are 124,957 athlete-seasons, 1,000,000 rows and 3,125 cells.

### A. Each variant vs tonight's cold solve (5 outers, CG 1e-8): standard design

| variant | athlete-season rating | row rating | course % | CG iters (per pass) |
|---|---|---|---|---|
| cold repeat | 0 / 0 / 0, 0 | 0, 0 | 0, 0 | 97 (37,20,13,16,11) |
| **cold, CG 1e-10** (the CG tolerance's own error) | 0.190 / 0.030 / 0.0037, **17** | 0.038 / 0.030 / 0.0037, **0** | 0.387 / 0.243 / 0.0049, **134** | 293 |
| **warm theta, 5 outers** | 0.189 / 0.029 / 0.0031, **43** | 0.042 / 0.028 / 0.0031, **0** | 0.242 / 0.138 / 0.0024, **12** | 82 (**16**,29,13,11,13) |
| warm theta, CG 1e-10 | equal to cold 1e-10 to 1e-4 | | | 291 |
| cold, 6 outers (one extra pass) | 0.883 / 0.141 / 0.0105, 3,536 | 0.218 / 0.125 / 0.0167, 73,703 | 0.601 / 0.475 / 0.030, 1,184 | 107 |
| cold, 10 outers (≈ the fixed point) | 1.660 / 0.246 / 0.0140, 8,932 | 0.380 / 0.189 / 0.0248, 200,937 | 0.986 / 0.788 / 0.045, 1,486 | 139 |
| warm full, 1 outer | 3.597 / 0.423 / 0.0107, 4,990 | 0.680 / 0.209 / 0.0169, 79,799 | 0.574 / 0.470 / 0.030, 1,170 | **13** |
| warm full, 2 outers | 1.785 / 0.211 / 0.0122, 6,277 | 0.376 / 0.161 / 0.0216, 147,134 | 0.839 / 0.672 / 0.041, 1,419 | 28 |
| warm full, 2 outers, CG 1e-10 | 1.780 / 0.211 / 0.0123, 6,315 | 0.377 / 0.161 / 0.0216, 146,973 | 0.837 / 0.661 / 0.041, 1,415 | 109 |
| warm full, 3 outers | 1.538 / 0.225 / 0.0133, 7,852 | 0.356 / 0.179 / 0.0237, 181,526 | 0.970 / 0.775 / 0.045, 1,476 | 38 |
| warm full, 5 outers | 1.656 / 0.248 / 0.0140, 8,920 | 0.381 / 0.189 / 0.0248, 200,805 | 0.981 / 0.783 / 0.045, 1,482 | 47 |

### B. Against the converged point (cold, 10 outers): standard design

| variant | athlete-season rating | row rating | course % |
|---|---|---|---|
| **cold, 5 outers (what ships)** | 1.660 / 0.246 / 0.0140, 8,932 | 0.380 / 0.189 / 0.0248, 200,937 | 0.986 / 0.788 / 0.045, 1,486 |
| cold, 6 outers | 0.878 / 0.130 / 0.0057, 1,058 | 0.176 / 0.077 / 0.0093, 11,725 | 0.396 / 0.314 / 0.015, 921 |
| warm full, 2 outers | 1.942 / 0.137 / 0.0024, 723 | 0.130 / 0.042 / 0.0037, 540 | 0.149 / 0.116 / 0.0045, 213 |
| warm full, 3 outers | 0.868 / 0.075 / 0.0012, 205 | 0.067 / 0.016 / 0.0014, 23 | 0.027 / 0.017 / 0.0007, 0 |
| warm theta, 5 outers | the same as cold 5 | | |

### C. Hard design (season links, sparse linkage)

| variant vs cold 5 | athlete-season rating | row rating | course % | CG iters |
|---|---|---|---|---|
| cold, CG 1e-10 | 0.851 / 0.0070 / 0.0009, 26 | 0.044 / 0.0086 / 0.0015, 0 | 0.073 / 0.053 / 0.0015, 5 | 391 |
| **warm theta** | 0.854 / 0.0089 / 0.0005, **24** | 0.042 / 0.0086 / 0.0011, **0** | 0.039 / 0.015 / 0.0012, **0** | 253 (**27**,59,53,54,60) vs 271 (35,59,53,64,60) |
| cold, 6 outers | 1.189 / 0.890 / 0.0075, 4,357 | 0.225 / 0.110 / 0.015, 51,270 | 0.488 / 0.427 / 0.028, 1,121 | 330 |
| cold, 10 outers | 1.195 / 0.887 / 0.0112, 7,117 | 0.374 / 0.181 / 0.0229, 177,332 | 0.865 / 0.778 / 0.045, 1,494 | 566 |
| warm full, 1 outer | 2.913 / 0.918 / 0.0082, 6,005 | 0.654 / 0.221 / 0.0157, 64,141 | 0.476 / 0.428 / 0.028, 1,112 | 62 |
| warm full, 2 outers | 1.131 / 0.559 / 0.0093, 2,927 | 0.290 / 0.149 / 0.0198, 122,723 | 0.722 / 0.607 / 0.039, 1,378 | 124 |
| warm full, 3 outers | 1.165 / 0.869 / 0.0108, 6,623 | 0.348 / 0.171 / 0.0219, 159,377 | 0.780 / 0.714 / 0.043, 1,447 | 180 |

Against cold 10, the hard design gives:

- warm full, 3 outers: 1.058 / 0.417 / 0.0026, 300 athlete-seasons; 15 rows; 100 cells.
- warm full, 5 outers: 257 athlete-seasons; 15 rows; 0 cells.
- cold 5: 7,117 athlete-seasons; 177,332 rows; 1,494 cells.

### Wall time

- On 4 shared cores the wall clock was noisy: the same cold solve took
  14.7–23.9 s on the standard design. CG iterations are the honest cost
  measure.
- Typical times on the standard design:
  - cold: about 15–24 s;
  - warm theta: 10.5 s;
  - warm full: 2.0 s for 1 outer, 4.7 s for 2, 6.2 s for 3.
- In production a pass is about 300–425 CG iterations (2026-10-12 server
  bench: 425/300/309), about 1,200–1,560 a run, plus the after-solve work
  of each pass (nested variances, weights, calibration). Estimates:
  - **theta:** saves part of pass 1 only. Pass 1's system is built from the
    cold starting hyperparameters, not last night's, so last night's theta
    is not close to its solution: 37→16 iterations on the standard design,
    35→27 on the hard one. On production that would be some fraction of
    ~425 iterations, roughly **3–20% of the solve's CG time**. Unmeasured.
  - **full, 2 outers:** cuts the solve to about **2/5 of its passes**, and
    the passes are no cheaper. Roughly **50–60% of the joint solve's time**
    (guessed, from the pass count).

## 4. Answers

**Is the warm-start result within the cold solve's own convergence tolerance?**

- **theta: yes.** On every metric it is the same size as the CG tolerance's
  own error (cold 1e-8 vs cold 1e-10):
  - a median of 0.003 pts;
  - no row rating moves by ≥ 0.05;
  - 24–43 of 125k athlete-seasons move by ≥ 0.05 (cold 1e-10 moves 17–26);
  - no more than 12 cells move by ≥ 0.05 %, against 134 for cold 1e-10.

  Run with CG 1e-10, warm theta and cold land on the same point. The shown
  value (1 decimal) changes for a handful of athlete-seasons a night, the
  same ones a tighter CG would change. The saving is small.
- **full: no, measured against the CG tolerance.** It moves 2–7% of
  athlete-seasons and 6–18% of rows by ≥ 0.05 pts, and a few by 1–3.6 pts.
  Against the outer loop's truncation error it is within tolerance: cold 5
  is just as far from the converged answer, and warm full with 3 outers is
  4–40x closer to it than cold 5 (median 4–12x, count ≥ 0.05 24–44x) (table B). An extra outer pass or a tighter
  CG does not bring the full warm start back to the cold answer. Tightening
  CG changes nothing (the 2-outer run at 1e-10 matches the one at 1e-8), and
  extra outers move it further from cold 5, towards the fixed point.

**What the owner gives up with a full warm start**

1. **Reproducibility.** Tonight's board would depend on every earlier night.
   A rebuild from scratch, a replay (deploy/engine_replay.sh) or a holdout
   would no longer reproduce the live numbers. Each of them would need the
   saved state of the night before.
2. **Hysteresis.** Robust weights and the day-guard flags carry over. A row
   that was wrong last night starts with last night's down-weight. A meet that
   is deleted or re-keyed leaves its influence in the variance components
   until it washes out over several nights.
3. **Fixed-point risk.** Cold 5 is always the same deterministic function of
   the data, even if it is unconverged. A warm chain is only as good as the
   fixed-point iteration is stable. In a deliberately misspecified variant
   (season links forcing together abilities that were drawn independently),
   the outer loop fell into an **odd/even 2-cycle**:
   - cold 5 vs cold 6: median 0.83 pts, max 38 pts;
   - cold 6 vs cold 10: close.

   So a warm chain's answer would depend on the parity of the number of
   passes since the last cold start. Separately, a run with 8-row races
   showed the day guard flipping 5↔11 flagged divisions on alternate passes.
4. **Bit-identity with today's output.** The project's speed-up rule
   ("outputs stay EXACTLY the same") rules out the full warm start as a pure
   speed-up. It is a model change, and it needs the owner's decision.

## 5. What to check on production data before adopting it

1. **Measure the ruler first.** Run the go-live solve once with
   `XCP_OUTER=10` on the same pack, and compare the boards with cold 5
   using this harness's metrics. Also read the per-outer lines already in the
   08_golive log (`sigma`, `sigma_u`, `tau` for outers 4 and 5). If cold 5
   is already converged in production, so that cold 10 moves less than
   0.05 pts, a full warm start costs nothing in accuracy. If it is not
   converged, the owner should first decide whether the published number is
   meant to be "5 passes" or "the fixed point".
2. **Check odd/even.** Compare cold 5 with cold 6, and cold 6 with cold 7.
   An alternating difference means the outer loop cycles, and a warm chain
   must not be adopted.
3. **Key the state.** `saveState` stores arrays by dense index, and
   `--from-state` refuses any size change. A warm start needs the athlete
   key, the cell key, the race key and the row key saved with the state,
   then mapped as `scripts/moe_warm_start.py:warmFrom` does. That includes
   rows that disappear, rows that are reassigned to another athlete-season,
   and pool changes.
4. **Shadow it for a week.** Run the warm solve beside the cold one each
   night. Compare the all-time boards (top 25 per pool, which is where the
   owner looks) and the course table. Watch whether the gap between warm and
   cold grows over the nights, and how it behaves after a big correction
   night.
5. **Count the theta-only saving.** It is free in accuracy. Run pass 1 with
   last night's mapped theta and count its CG iterations against today's
   ~425. That number alone says whether the theta warm start is worth the
   key-mapping code.
6. **Check the features this synthetic data left off**, each for its
   carried state: the effort term, the season tie (fitted on the first
   untied pass), field-strength importance and the bracket difficulty mode.

## Reproduce

```
MOE_SCALE=0.25 python -I scripts/moe_warm_start.py            # standard, 1M rows
MOE_HARD=1 MOE_SCALE=0.25 python -I scripts/moe_warm_start.py # links + sparse
```

The hook is in `engine/joint_solve.py`, just before the outer loop. Its test
is `tests/test_joint_solve.py::test_warm_start_hook_is_test_only_and_theta_keeps_the_answer`,
which checks that the hook is inert by default and that a theta warm start
reproduces the cold answer.
