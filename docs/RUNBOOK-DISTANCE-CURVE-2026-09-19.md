# The distance curve: what to run, in what order, and what each run decides

2026-09-19. Everything below is **off by default**. A per-module run of the
whole test suite before and after the commit that added these is identical
but for the one new module, so the artifact you have today is unchanged
until you pass a flag.

Each step is a **measurement with a decision attached**. Run it, read the
named lines, then decide. Do not run them all at once — step 2 changes the
curves step 3 would judge.

---

## Step 0 — does one shape fit both sports? (free, no flag)

```
python engine/fit_distance_exponent.py
```

The `SHAPE TEST` block is new and prints on every run. Read **`ms_m`,
`ms_f`, `elem_m`, `elem_f`** and nothing else: their XC and TF supports
overlap by ~0.62 log units, and they are the only pools where the test has
power. `hs_m` (0.13) and `college_m` (0.22) print a verdict refusal on
purpose.

| what it says | what it means | what to do |
|---|---|---|
| `→ ONE LAW` on ms and elem | the shapes agree where both sports measured; the level gap between them is terrain | go to step 2 |
| `→ TWO LAWS` | they disagree in shape, not just level | **check whether XC's column drops below 1.000 anywhere first.** An impossible exponent is not evidence of a second law — if it does, XC's confound is what you are reading, and step 3 comes before this decision |
| `BAND TOO NARROW` everywhere | ms and elem are thin this year | skip step 2; steps 1 and 3 stand alone |

Why the local exponent and not the existing conversion table: a conversion
from a 1000s 5K mixes level with shape. Two sports with identical exponents
and different terrain penalties diverge on every row of that table, and two
sports with different laws can agree at the anchor. Level is exactly the
thing the merge does not need.

## Step 1 — is the TF slope a degree-ladder artifact? (cheap, reversible)

```
python engine/fit_distance_exponent.py --degree-rule locations
```

The claim: the ability deciles measure college TF flat at ~1.12, the curve
says 1.15 → 1.05, and the reason is that `POLY_DEGREE_EDGES` counts distinct
distance *transitions*. Track is raced at 800/1600/3200/5000, so every
doubler in the country collapses into one endpoint bin, while every XC
course is a different measured length. `college_f|TF` has 29 transitions,
misses degree 3 by one, and gets a quadratic across a factor-of-6 span
however many pairs sit inside those bins.

**Read:** the `degree N by edges, M by the other rule` clause on each pool's
health line — it prints only where the rules disagree — and then
`college_m|TF` / `college_f|TF` in `scripts/distance_curve_support.py`.

**Decide:** if the TF exponent goes flat near 1.12 and the XC pools do not
move much, the slope was the ladder. If TF pools start failing the stability
gate, the extra degree is fitting noise and the old rule was right for the
wrong reason.

## Step 2 — merge the sports (only if step 0 said ONE LAW)

```
python engine/fit_distance_exponent.py --merge-sports
```

**Read:**

- `MERGED SHAPE PER POOL` and the `eps per sport` line under each pool.
  The two offsets should differ — if they come out equal, stage 1 borrowed
  or fell back and the merge is running undebiased.
- Then `scripts/distance_curve_support.py`: **the six `in? = NO` rows should
  all become `yes`.** That is the headline. Those six pools — every one of
  them TF — currently normalise *every row* through a 56% extrapolation off
  the boundary slope, which is the 9:00 3200 → 31:00 8k. A merged pool spans
  800..10,000 and contains every anchor.

**Nothing downstream changes.** The merged entry is stored under both
`pool|XC` and `pool|TF` carrying the bare pool's anchor, and
`normalize_distance.targetFor` already reads the bare key — so there is
still one reporting scale per pool and the pace band, the boards and the
merged `(person_id, pool)` ability term never see a second one. That last
point is the whole reason the per-sport anchors in `POOL_TARGET_METERS` have
always been dead: when they disagreed, `poolBandFor` gated HS TF rows at
(192, 1152) and refused a 5:29 1600 a place on every board.

## Step 3 — replace the clamp with the physics

```
python engine/fit_distance_exponent.py --slope-prior 1
```

**Read `floored_segments`. It should go to zero.** That is the entire test.

`MIN_LOCAL_EXP` clamps the sampled values *after* the fit, so the fit still
bends below 1.0 and the clamp flattens the result — which is why the
2026-09-18 re-fit left `college_f|XC` a straight line at 1.040 over 38
floored segments. A straight line at the floor is not a measurement. The
prior pulls `dg/dx` toward 1.06 **only where the endpoint support is
one-sided**, which is what an end segment is.

**Start at 1 and do not go far above it.** The prior enters one global
polynomial fit, so it cannot be perfectly local. Measured: at weight 1 the
largest interior shift on a well-supported corpus is 0.003; at weight 8 the
interior drifts 1.122 → 1.085. Above a few units you are fitting the prior.

If `floored_segments` does *not* reach zero, the pairs are asserting an
impossible exponent loudly enough to outvote the prior at weight 1. Raise it
to 2, and if it still holds, the problem is the sample, not the fit.

---

## What none of this fixes

**The XC confound.** Cross-country pairs carry the course and the calendar
— a November championship 10k against an October 8k — and a same-athlete
pair cancels neither. Step 3 makes XC curves *plausible*; it does not make
them *identified*. If step 0 returns TWO LAWS, this is the first thing to
suspect, and the ability-decile spread (+0.055 on college XC against
−0.017 on college TF) may be this rather than ability.

**TF's sample.** 3.39M orphan track results are unlinked, and they are
exactly the same-athlete pairs the TF side of every curve is starving for.
Linking them would do more for track than steps 1–3 together.

**Curve by ability.** Deliberately not started. It needs step 2's verdict
first (there is no point fitting three ability curves per sport if one shape
per pool is right), it must be a *shrinkage* fit rather than a split or it
just makes three thin curves out of one, and its ability axis has to be
defined without the curve — percentile within (pool, sport, season) computed
from races at or near the pool's anchor distance, where no normalisation is
needed — or it is circular.
