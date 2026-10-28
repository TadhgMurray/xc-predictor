# Plan: making the prediction model better

*2026-10-24, revised the same day after review. Follows
`docs/REVIEW-PREDICTIONS-2026-10.md`, which found what is wrong today.
This file says where we are going, what is likely to help, how each idea
gets built, and the experiments that decide between them. Nothing past
E0 is built until its experiment is approved.*

---

## 0. The goal

**Before every meet we post, every listed runner gets a time, a place
and a team score, each with an honest range. Those predictions are
measurably better than anyone else's, judged only on predictions saved
before race day.**

### Splitting a race in two

```
ln(time of runner i in race r)  =  L_r  +  x_ir  +  noise
                                   race     runner
                                   level    vs field
```

- **L_r, the race's level.** Course, distance, footing, weather, and how
  fast the whole field ran that day. It moves every runner together.
- **x_ir, the runner's position in the field.** Their form, and where
  they sit in this field.
- **Places and team scores depend only on x.** Times depend on both.

Today's model learns both terms in one number. Each runner is trained
alone, so the race's level, which no single runner's history can predict,
is the largest part of its target. That is the leading explanation for
why it orders a field worse than the ratings, which take the race level
out on purpose. It is a hypothesis, and E3 tests it.

### The three numbers we chase

Every one is measured on predictions frozen before race day
(`meet_forecast`, Kailo "recorded") and on the held-out season (§2).

| | Measure | Target, first milestone | Where we are |
|---|---|---|---|
| **G1 order** | Share of runner pairs in the right order, per race, averaged over races. Spearman beside it. | Beat our own ratings by **+1 point**; beat Kailo on their races | On Kailo's 9 races: ratings 85.5%, Kailo 85.9%, model 79.6% |
| **G2 time** | Mean \|% miss\| (Kailo's statistic), plus the median | Under Kailo's **2.84%** (their 2026 record; 2.86% in their 2021–25 backtest) on 20+ recorded races | 6.50% on their races, all of it level |
| **G3 honesty** | Coverage of the 68% and 90% time ranges; log loss and reliability of team-win odds | Coverage between 63–73% and 86–94%; win odds better than "the lowest predicted score wins" | Not measured |

- **Two tracks run side by side.** Track T (times, §4 T) starts now. It
  is the biggest gap and probably the cheapest to close, and it needs no
  network. Track O (order, the rest of §4) is where the network has to
  earn its place.
- **G3 gates shipping** a probability on any page.
- **Against Kailo, only on shared runners.** Every comparison uses the
  races both of us predicted, the runners both of us priced, and only
  those who finished. `kailo_compare.scores` already works that way: the
  85.5% against 85.9% above is that shared set. Every report keeps this
  rule, and says how many races are recorded and how many rebuilt.

---

## 1. How much room there is: the error budget

| Piece | Size | Can a model reduce it? |
|---|---|---|
| A runner's own day-to-day swing | 1.2–1.9% for the fastest quartile of men in cross country and road races; larger for slower and younger runners (Hopkins & Hewson 2001) | No. This is the floor. |
| Error in our estimate of a runner's form | What is left. Our within-race median miss of 2.32% is about a 3.4% sd. With a 2–2.5% day swing, that leaves **about 2.4–2.8% sd of form error, as big as the noise itself.** | **Yes: this is the model's job** |
| The race's level on the day | ENGINE.md puts the race-day sd at 3.05%, part of it predictable (course, weather) | Partly: Track T |

These are estimates from published figures, not our data. **E0
measures it on our own races, as a ceiling.** Its "hindsight form" uses
the runner's races *after* the one being predicted, which no forecast can
see, so it bounds what better form estimation could ever buy. It is not
a result we can reach.

Research to note: a 2025 study of college club cross country (arXiv
2509.10600) found pooled in-season improvement of about 5.5 s/week, but
predicting *which* runners improve from their first race alone was near
chance (held-out R² ≈ 0). So individual trends need heavy shrinkage
toward group curves.

---

## 2. The test bench, built before any network work

The bench follows the engine's own forward holdout
(`engine/forward_holdout.py`), so the model and the engine are scored on
the same held-out races.

### Windows (`forward_holdout.defaultWindows`)

| Window | Season | Use |
|---|---|---|
| Train | Everything before 2024-08-01 | Fitting |
| Validation | 2024-25 | Every experiment |
| Sealed test | 2025-26 | Looked at once, at the end |
| Live | 2026 | Frozen forecasts and Kailo recorded |

### Two tiers

- **Tier 1, every experiment (cheap).** Split by target date, with
  features from today's solve. Today's solve has seen the validation
  races, but that flatters every arm equally, so comparisons hold.
- **Tier 2, milestones only (one solve).** Features as of the cutoff,
  from a forward fit (`run_joint --holdout-kind forward`). Every
  validation race is predicted from data before the cutoff, with the
  engine's race bootstrap.

### What the bench needs

- **A race id on every example** (meet, division, source, day).
- **One scoring script for any predictor.** It prints G1–G3 by level,
  gap band, championship or not, and early or late season, with a
  paired race bootstrap between any two predictors.

---

## 3. Yardsticks, and the gates the network must pass

| | Predictor | Why it is here |
|---|---|---|
| Y0 | Last race | The floor |
| **Y1** | **Rating form (served now)** | **The first gate.** The network orders worse than this today (79.6% against 85.5%). |
| Y2 | `trend`: rating plus a level table (on the branch) | The simple rule for times |
| Y3 | The engine's carry-forward (forward holdout) | The engine's own forecast |
| Y4 | Gradient-boosted trees on hand-made features, predicting the residual over Y1 | **An instrument, not a product.** It trains in minutes on CPU and says which ideas carry signal before any GPU time. |

**Gates, in order:**

1. A retrained network must beat **Y1 on G1** on validation. Until it
   does, nothing more expensive is built on it.
2. It must beat Y4 on G1, without losing G2.
3. One check on the sealed season.
4. Live: frozen forecasts and Kailo recorded.

**The field encoder (B2)** is the most expensive and riskiest step. It
is built only if E0 shows real room **and** E2 shows that field and race
inputs carry signal.

---

## 4. Ideas

### Two rules that apply to every idea

- **One fitness model, the engine's.** Form, form over the season and
  each runner's trend are the ratings' job (`engine/joint_solve.py`: the
  form curve, opener rust, and the per-runner trend `XCP_FORM_TREND`
  under review there). Prediction uses the engine's ratings and fitness
  numbers as inputs. It adds only what is specific to predicting *one
  race*: the race's level, race importance, teammates, tactics, and the
  field. The `trend` basis's form-age and slope terms are a stopgap;
  they go once the engine can project a runner's form to a race date.
- **No future data in any input.** Every new feature reads only races
  dated before the predicted race: margins, team improvement, meet type
  counts, field strength. Field strength is built from runners' *prior*
  ratings, never from the race's own results. A test per feature builds
  one runner's history, appends a later race, and asserts the feature
  does not move.

### T. Times: the race's level (starts now)

**T1. A race-level model, replacing blend's "the network sets the
level".**
- **Target:** for each past race, the median over its runners of
  ln(actual ÷ rating-predicted time), i.e. how far the ratings missed
  that race as a whole.
- **Features, all known before the gun:**
  - course has a cell, and the cell's value;
  - level and distance;
  - meet type (championship, invitational, dual);
  - weather (forecast at race hour, else normal);
  - week of season;
  - field size, and the field's median *prior* rating;
  - source (anet or tfrrs).
- **How:** first a shrunk table (`backtest_baseline.fitTable` is that,
  at runner level), then trees if the table leaves signal.
- **Judged on G2,** by date, and on Kailo's shared races.

**T2. Finish the tfrrs course cells** (MIGRATE §5c). This is most of the
−6.4% on college courses.

**T3. A no-cell prior:** the median cell at the same level and distance,
instead of the sport-wide median race.

**T4. Day noise as a shared random effect.** It widens the time range
and leaves places alone.

**Order of work:** T2 (in flight), then T1 as a table on the branch's
backtest, then T1 as trees. Each is scored on Kailo's shared races and
the frozen archive as they come in.

### A. The target and the anchor (Track O, highest value)

**A1. Train on position in the field, not raw time.**
- **Target:** x_ir = ln(normalized time) minus the race's median over its
  runners. This removes the race level, about 3% sd of noise nobody can
  predict, from the target. It is leak-free (no engine output needed)
  and is exactly what places depend on.
- **Alternative arm:** the race's rated time (course and day already
  out; a mild leak through the day term).
- **How:** an SQL pass before extraction builds `pred_race_stats`.
  Extraction joins it.
- **Expected:** the biggest single gain in G1. Tested in E3.

**A2. Anchor on the engine's form.** Today's anchor is BASELINE_RATING,
an EWMA of prior rated times. When the engine exposes projected form at
a date, anchor on that instead. The network then learns only what is
race-specific.

### B. Train on whole races

**B1. Batches are races, and a loss scores order.**
- Sample races, and up to about 32 runners from each.
- **Loss:** a likelihood on the centred x, plus a pairwise logistic (or
  Plackett-Luce) loss on finishing order.
- This optimizes G1 directly; the current one-runner loss never does.
- Tested in E4.

**B2. Runners attend to each other (the field encoder).**
- A second small transformer runs across the encoded runners of one
  race.
- **What it could learn:** tactical championship races, a 150 in a 100
  field running easy, teammates packing, head-to-head history.
- **Built only past both gates in §3.**

### C. Race-specific inputs (all prior-only)

| Idea | Why | How | Signal check |
|---|---|---|---|
| **C1. Race importance and effort** | Top runners jog dual meets and peak at championships | Meet type from names and divisions (conference, district, sectional, regional, state, NXN, NCAA, invitational, dual, JV/open), in the history and in the target race; the runner's prior-rating rank in the field | E2; G1 on championships |
| **C2. Margin, not just place** | A 40-second winner was not pushed | Per history race: gaps to the runners ahead and behind, and to the winner. All from races already run. | E2 |
| **C3. Teammates** | Teammates train, taper and travel together | This season's median change in teammates' ratings up to the day before; the share of teammates racing | E2; G1 late season |
| **C4. HS → college** | About 1,950 college runners are still served off high-school form | Mostly a data fix (identity and first-season rows), for the engine. Here: check the HS → college examples cover the right grades. | G1 college, September |
| **C5. Bad days are not form** | Results skew slow | A skewed likelihood for the x target | Validation NLL and G1 |

### E. Honest ranges (the gate for G3)

- A separate sd for x (runner) and for L (race), calibrated on the
  validation season, with one scale per level and gap band.
- `team_rho` measured (`backtest_baseline.teamRho`) and used by
  `race_sim`.
- `meet_forecast` keeps p_win, so frozen odds can be scored.

### F. Data quality (cheap, ongoing)

Report the share of validation error from |err| > 10% rows, by cause:
wrong distance, merged identity, JV mixed into varsity.

### Other architectures

- **Trees (Y4)** are the challenger on order. If they match the network,
  the network has to justify itself.
- **A per-athlete fitness state model** belongs in the engine (the one
  fitness model), not here.

---

## 5. Experiments, in order

| # | Track | Question | Setup | Decides | Cost |
|---|---|---|---|---|---|
| **E0** | O | How much room is there in order? **A ceiling.** | `scripts/diag_order_headroom.py`: on 2024-25 and 2025-26 races, order from last race, from served prior form, and from two **hindsight** forms (season excluding the day; the nearest races either side). Paired by race; by level and month; a split of the within-race spread into form error and day. | How hard to push G1; whether B2 is ever worth it | SQL only, ~20–30 min per season |
| **T1a** | T | How much of the time miss does a race-level table remove? | `scripts/backtest_baseline.py` (on the branch), trained on races before the window and tested after | Whether trees are needed for T1 | ~20–40 min |
| **E1** | O | Where does each yardstick stand on the bench? | Tier 1: Y0–Y2 and the current model.pt on the validation season | The baseline table | One extraction with race ids (~1 h+) |
| **E2** | O | Which race-specific inputs carry signal? | Y4: trees on the residual over Y1 with C1–C3 features | Which go into the network; gate for B2 | Hours, CPU |
| **E3** | O | Does training on position in the field help? (A1) | Same network, x target, against today's target | The target; **gate 1 (beat Y1)** | GPU: subset, then full |
| **E4** | O | Do race batches and an order loss help? (B1) | E3 with and without them | Gate 2 | GPU, one retrain |
| **E5** | O | Do E2's winners help the network? | E4 plus those features | The feature set | One extraction and a retrain |
| **E6** | O | The field encoder (B2), only if E0 and E2 say so | E5 plus the encoder | The architecture | More GPU |
| **E7** | both | Full product | T1 + network + calibration; Tier 2; sealed season once; live | Ship or not | One forward solve |

Arms are compared on a 10% subset of chunks over 2 epochs first; only
winners get a full run.

---

## 6. How it gets built (outline)

- **Side tables, all prefixed `pred_`**, each built by one script and
  named here. None exists yet; each is added with its experiment.

  | Table | What it holds | Built by | For |
  |---|---|---|---|
  | `pred_race_stats` | Per race: runners, median ln normalized time, field median prior rating, meet type | An SQL pass before extraction | A1, C1, T1 |
  | `pred_course_cells_asof` | Course cells from a forward fit, keyed by cutoff date | The Tier 2 solve | Tier 2, T1 |
  | `pred_ratings_asof` | Per-row ratings from the same fit, keyed by cutoff date | The Tier 2 solve | Tier 2 |

- **Extraction (one re-extraction covers E1–E5):**
  - add race id and target date;
  - join `pred_race_stats`;
  - add meet type (C1), margins (C2), teammates (C3);
  - every feature is prior-only and has its test.

  New inputs are appended at the end of the vectors.
- **Training:** date split from target dates, the race-grouped sampler,
  a multi-task loss, per-race G1 every epoch, and the training cutoff
  written into `model.pt`.
- **Serving:** the network gives x, T1 gives L, a field's x is centred,
  then L is added. `blend` goes away.
- **Evaluation:** one bench script for every predictor, plus
  `score_frozen_forecasts.py` and `kailo_compare.py` for live.

---

## 7. Compute

The server has no GPU. Training so far has run on a rented RunPod RTX
3090 (`docs/HANDOFF-2026-09-01.md`), or on your PC (`train_overnight.ps1`
points at a ROCm environment). Approximate cost of a rented 3090, at
public prices (check the live page):

| | Rate | One run |
|---|---|---|
| Community cloud (can be reclaimed; checkpoint each epoch) | ~$0.22/h | — |
| Secure cloud | ~$0.43/h | — |
| A 10% subset arm over 2 epochs | a few hours | ~$1–3 |
| A full run (HANDOFF §13.3: ~80M examples, days of GPU) | ~2 days | ~$10–25 |
| Storage for the 125 GB chunks (network volume) | monthly | a few dollars a month |

**During E3–E6:** a handful of subset arms and one or two full runs a
week, so about **$10–40 a week**. Nothing is rented before E0, T1a and
E2 say there is something to train for. E0, T1a and E2 need no GPU.

---

## 8. Results so far

### E0, run 2026-10-24 (owner): a ceiling, not a result

Same runners, same races; pairs in the right order, mean over races.
"Gap" is nearest minus prior, with a 90% interval from resampling races.

| Season | Races | Runners | Last race | **Prior (served)** | Season (hindsight) | **Nearest (hindsight)** | Gap |
|---|---|---|---|---|---|---|---|
| 2024-25 | 294 | 21,677 | 87.0% | **87.7%** | 89.9% | **90.1%** | +2.42 [+2.12, +2.74] |
| 2025-26 | 293 | 20,905 | 87.1% | **88.1%** | 90.4% | **90.6%** | +2.52 [+2.22, +2.82] |

**By month (prior → nearest):**

| Month | 2024-25 | 2025-26 |
|---|---|---|
| August | 84.0 → 89.7 (**+5.7**) | 83.6 → 89.8 (**+6.2**) |
| September | 86.7 → 90.1 (+3.4) | 88.0 → 91.2 (+3.2) |
| October | 89.1 → 90.2 (+1.1) | 89.3 → 90.4 (+1.1) |
| November | 89.2 → 89.5 (+0.3) | 89.8 → 90.0 (+0.2) |

**Within-race spread** (rms of race medians): prior 3.12% → nearest
2.49% (2024-25), and 2.97% → 2.25% (2025-26). The form error the ratings
carry is at most about 1.9% in those units; the rest is the day.

### What it means

1. **There is room on order, and it is not huge.**
   - The most better form estimation could buy is about +2.5 points.
     Even perfect hindsight gets about 9.5% of pairs wrong: that part is
     the day.
   - The first milestone (+1 point) is about 40% of the ceiling.
     Plausible, not free.
2. **Almost all of the room is at the start of the season.**
   - August: about +6 points. September: about +3. October: about +1.
     November: about 0.
   - By championship season, the ratings are already within a third of a
     point of hindsight form.
   - So better form estimation will not move late-season or championship
     order. Anything left there is the day, plus race-specific effects
     (tactics, effort) that E0 cannot separate from the day.
3. **So the order problem is mostly the season opener:** predicting
   autumn form from last season, a summer, or a spring of track, by grade
   and level. That is a career and sequence problem, which the network's
   history encoder and its gap twins were built for. **This is the
   clearest case for keeping the network.** It is also where the field
   encoder (B2) and championship effects (C1) matter least, so both drop
   in priority for order. C1 stays in Track T, for the race's level.
4. **Averaging recent races buys little over the last race alone:**
   +0.7 and +1.0 points.
5. **College is barely sampled** (7–10 races per season). Kailo's world
   needs its own run (E0b).
6. ⚠ **2025-26 is the sealed test season** (`forward_holdout`). E0 fits
   nothing and only describes the ratings, and the two seasons agree, so
   the seal mostly holds. Every experiment from here uses 2024-25 for
   validation and leaves 2025-26 alone until the end.

### Changes to the plan

- **E0b** (next, SQL only): the per-runner split on 2024-25 (in season
  against season opener, by days since the last race), and college only
  (`--source tfrrs`) on 2023-24 and 2024-25.
- **E1 focuses on openers.** The network, the ratings and the engine's
  carry-forward across the summer (Y3, the season tie) are compared on
  the same opener races.
  - Carrying form across a gap is fitness, so under the one-fitness-model
    rule it belongs to whichever of the engine and the network does it
    better. **The owner decides after E1.**
  - If the engine wins, the network does not do it twice.
- **E2's features for openers**, all prior-only:
  - grade progression (rising grades, a freshman's first college season);
  - last season's trajectory (early to late);
  - spring track times and how recent they are;
  - the length of the break;
  - the school's typical summer improvement in past years (its returning
    runners, seasons before this one).
- **B2 and C1-for-order wait** until E1 and E2 show room outside the
  season opener.

### E0b, run 2026-10-24 (owner): where the room is, per runner, and college

Median |within-race miss| per runner, prior → nearest (hindsight), with
last race beside it.

**"Share of room"** is each band's share of the squared error that
hindsight removes: runners × (prior² − nearest²). It is a rough split,
not an exact one.

| Band | All levels 2024-25 | College 2024-25 | College 2023-24 |
|---|---|---|---|
| In season, last race ≤21 days ago | 2.30 → 1.89 (last 2.35) · 79% of runners · **42% of room** | 2.25 → 1.80 · 72% · 43% | 2.27 → 1.82 · 72% · 37% |
| In season, >21 days | 3.13 → 2.63 · 8% · 7% | 2.68 → 2.22 · 14% · 10% | 3.19 → 2.42 · 10% · 12% |
| Season opener, ≤120 days (spring track) | 3.15 → 1.97 · 4% · 7% | 2.45 → 1.87 · 4% · 3% | 2.84 → 1.75 · 4% · 6% |
| **Season opener, 121–365 days** (off a summer) | **4.15 → 2.47** · 9% · **30%** | 3.81 → 2.32 · 10% · 30% | 4.19 → 2.85 · 13% · 35% |
| Season opener, >1 year | 8.22 → 3.40 · 1% · 14% | 7.07 → 2.22 · 1% · 14% | 6.47 → 2.71 · 1% · 10% |
| **Openers, total** | 13% of runners, **51% of room** | 15%, 47% | 18%, 51% |

**College, pairs in order** (148 tfrrs races each):

| Season | Prior | Nearest | Gap |
|---|---|---|---|
| 2024-25 | 86.9% | 90.0% | **+3.09** [+2.57, +3.64] |
| 2023-24 | 86.2% | 89.5% | **+3.32** [+2.87, +3.78] |

### What it means

1. **Half of the room sits in one runner in seven: the season opener.**
   - These are mostly runners coming off a summer (121–365 days), plus
     returners after a year or more.
   - Their miss today is 4–8%, against 2.3% for everyone else. Last race
     and the rating average are equally lost on them.
   - This is the network's natural job: careers, grade progression,
     summer improvement, and spring track as evidence.
2. **The other half is spread thin over mid-season runners,** about 0.4
   points each.
   - There, the rating average barely beats the last race (2.30% against
     2.35%).
   - Some of the hindsight gain is just "seeing next week's race", which
     no forecast can, so the reachable part is smaller than it looks.
   - The engine's in-season fitness (form curve, per-runner trend) is the
     natural owner of it.
3. **College has more room than overall** (+3.1 to +3.3 points against
   +2.4), with the same opener pattern. That is the ground on which to
   beat Kailo.

### Next: E1a, the current network on the same test (no GPU, ~1 h)

`diag_order_headroom.py --with-model` adds model.pt as a fifth basis,
split by the same runner bands. model.pt was trained on these seasons, so
this is its best case: athlete-held-out with `--val-only`, and fully
in-sample without it.

- **If it cannot beat prior on openers even here,** the network has to
  be retrained before it can own them (E3 onward).
- **If it beats prior on openers,** a hybrid (the network for openers,
  ratings in season) is the first real use of it, and E1 confirms it on
  the date-split bench.

### E1a, run 2026-10-25 (owner): the current network on the same test

| Run | Races / runners | Prior pairs | Model pairs | Model − prior | Nearest (ceiling) |
|---|---|---|---|---|---|
| Athletes it never trained on (`--val-only`) | 273 / 2,601 | 88.1% | 88.1% | **+0.08** [−0.48, +0.66] | 90.6% |
| Everyone (in-sample: flattered) | 196 / 14,601 | 87.7% | 88.1% | +0.46 [+0.19, +0.74] | 90.2% |
| College, never trained on | 96 / 898 | 88.8% | 88.0% | −0.72 [−1.81, +0.38] | 90.7% |

**Median |within-race miss| by runner** (held-out athletes): prior →
model, with the ceiling.

| Runners | Prior | Model | Ceiling |
|---|---|---|---|
| In season, ≤21 days | 2.12% | 1.92% | 1.73% |
| In season, >21 days | 2.70% | 2.47% | 2.36% |
| Season opener, ≤120 days | 3.13% | 2.25% | 1.17% |
| Season opener, 121–365 days | 3.56% | 3.25% | 2.03% |

In-sample, openers off a summer read 4.06% → 3.68% (ceiling 2.51%), and
after over a year 8.22% → 5.85% (ceiling 3.60%). College, held out: the
model is worse in every band (in season 1.99% → 2.04%; openers 2.12% →
2.63%).

### What it means

1. **On order, today's network ties the ratings even in its best case.**
   It does not beat them on held-out athletes (+0.08, interval ±0.6),
   only slightly in-sample, and it is behind on college. It cannot own
   openers as it stands.
2. **But it misses by less per runner in most bands, with the same
   order.** A predictor that only shrinks the gaps between runners does
   exactly that. So either the ratings' gaps are too wide (a calibration
   any rule can fix), or the network knows something about gap sizes.
   **E1b separates the two:** one factor on the ratings' within-race gaps,
   each race left out of its own fit (`diag_order_headroom.py`, "gap
   size"). If that closes most of the network's lead, the lead was a
   constant. Either way it is a free gain on times (G2).
3. **On openers it closes only a fifth to a third of the way to the
   ceiling,** and none of that shows up as order. That fits the plan's
   diagnosis (a target holding the race level, a one-runner loss) more
   than "sequence models can't do this". **Whether openers can be
   predicted from prior-only data at all is the question for E2.**

### Next

- **E1b:** the same runs with the gap factor (no GPU, ~1 h).
- **E2o, the opener probe (no GPU, build on approval):** trees on
  openers only, predicting the prior's within-race miss from prior-only
  facts:
  - last season's final and best ratings, and its early-to-late
    trajectory;
  - the latest spring track ratings, and how recent they are;
  - grade and grade progression, including the HS → college step;
  - the length of the break, and races last season;
  - the school's typical summer change in past seasons.

  Trained on 2022-23 and 2023-24 openers, tested on 2024-25.

  **Gate:** if trees cannot beat prior on openers, a retrained network
  is unlikely to either, and the GPU work (E3 onward) should not start.
  If they can, the features that won go into the retrain.

### The race model, version 1 (trees), run 2026-10-25 (owner)

`model/race_rows.py` builds the table and `model/race_trees.py` trains and
scores it. Trained on 2018-19 to 2023-24 (a quarter of those races, kept
whole), scored on all of 2024-25.

| 2024-25 (never seen) | Races | Ratings pairs | Model pairs | Gain [90%] |
|---|---|---|---|---|
| All | 35,321 | 84.24% | **85.52%** | **+1.28** [+1.26, +1.30] |
| College | 941 | 81.10% | 82.67% | +1.57 [+1.44, +1.70] |

- Rho went from 0.814 to 0.840, and the within-race miss from 2.91% to
  2.62%.
- It is better in every month, at every level, and in every runner band.
  Openers off a summer went from 5.15% to 4.60%; mid-season runners
  from 2.37% to 2.13%.
- The ratings read lower here than in E0 because this table includes
  every race with 8 or more rated runners (JV, small and middle-school
  fields too); E0 used fields of 20 or more.
- **Times:** median 4.44% → 3.85%, mean 7.64% → 5.89% (college 8.41% →
  7.24%), against a rough (level, distance) table. The gap between
  median and mean says a few very bad races drive the mean.
- **What it leans on most:** the runner's gap to the field's median,
  form level, rank in the field, distance, form, week of season, grade,
  teammates opening their season.
- **Not finished:** the trees stopped at the 1,500-round cap while still
  improving.

**Version 1.1, on the branch:**

- The starting point is the page's own clock: each runner's rating
  through `predict._ratingToSeconds` on the race's distance and course
  cell (`race_rows.pageBase`). "Ratings" in every comparison is then what
  the site prints, and the level part corrects the page.
- Times are printed against the page itself, with the 12 worst races by
  name (to find the wrong distances and mixed fields behind the mean).
- Calibrated ranges: a place spread per runner band and a race-level
  spread, fitted on half the validation races and checked on the other.
  Both are saved with the model.
- Up to 4,000 rounds.
- The switch: `XCP_PREDICT_BASIS=race` (`racecast/race_model.py`)
  re-predicts the rated field with the trees, and leaves the field to the
  ratings when it cannot read it.
- The Kailo recorder records `race` beside the other bases.
- `XCP_KAILO_STORE` keeps review recordings out of the live store.

### Version 1.1, run 2026-10-26 (owner): the page's own clock

| 2024-25 (35,321 races) | Ratings (the page) | Model |
|---|---|---|
| Pairs in order | 87.22% | **88.33%** (+1.11 [+1.09, +1.12]) |
| Rho | 0.885 | 0.901 |
| Within-race miss | 2.75% | 2.41% |
| Time miss, median / mean | 3.96% / 5.27% | **2.93%** / 4.24% |
| College mean | 5.10% | 4.59% |

- **On the page's clock, the ratings themselves order much better**
  (87.2% against 84.2% on the 5K-equivalent). The page's distance and
  course conversion matters for mixed fields.
- **What the model leans on is now form:** this season's best and mean,
  week of the season, the 30-day trend, grade, spring track. Before, it
  leaned mostly on clock fixes.
- **Ranges:** place 65% inside ±1σ (a little narrow); time 68% / 86%
  (the 90% one is short: the tails are heavy).
- **The mean is driven by bad distances.** 2.9% of races miss by more
  than 10% as a whole. The worst twelve were all stored at 1, 2 or 3 m
  ("Manhattan College XC Invitational", "SEC Batch Meet 1"), so they read
  8× fast. **Version 1.2** reads the corrected distance
  (`feature_extraction._XC_DIST`: the hand-verified override, the meet's
  own distance, then a tfrrs division's), takes the median over the race,
  and treats anything outside 1,500–15,000 m as no distance.

### Version 1.2, run 2026-10-26 (owner)

- **Order held:** 88.32% against 87.22% (+1.10 [+1.09, +1.12]).
- **Times barely moved** (mean 4.25%). The 1–3 m races are gone; the
  worst are now races that are not cross country at all: an NJCAA
  half-marathon, "Fast Track 800", "Test Race", practice meets. They have
  no sane distance, and no clock can be right there.
- The server has 125 GB, 97 GB free: room to train on every earlier race.

### Version 1.3, on the branch

- **The day's weather** (temperature, dew point, rain, wind) for the
  race's level, and as a runner input. The page's clock leaves weather
  out, and Kailo says it has none. Training reads the `weather` table at
  the cross country hour; the page passes the forecast, else the normal
  for the place and date.
- **Head-to-head, beyond the ratings** (`race_rows.headToHead`): from
  the past races (400 days, last 15) in which runners of this field met,
  the share of meetings won, and that share less what today's ratings
  order expects. Prior races only; computed by sorting, not by pairs.
- **The level is learned only on races with a real distance**
  (`page_base`).
- **Times are reported three more ways:** on races with a known
  distance, on clean races, and on college fields of 100+ (Kailo's kind
  of race).
- **Trained on every earlier race** (`--train-frac 1`).

### 1.4 and 1.5 (owner's runs, 2026-10-26)

- **1.3/1.4 on every earlier race:** 88.45% against 87.22% (+1.23 [+1.21,
  +1.24]). Time miss: median 2.88%; college fields of 100+ 3.54% against
  the page's 4.20%. Head-to-head became the model's most important input.
- **Without ratings:** raw times alone 86.31%; trees with nothing the
  engine made 87.34% (the ratings 87.22%). The ratings are worth about
  1.1 points of order to the model.

### Version 1.6 (on the branch)

- **Forecast copies** at 7 and 28 days, for a quarter of races each.
- **The team's record** against the ratings.
- **Missing-input practice:** a few training rows with weather, course
  cell or head-to-head blanked.
- **Scoring:** the headline is race morning, forecasts are scored per
  lead, and ranges are saved per lead.
- **`model/race_blend.py`:** six members (trees on the ratings, trees
  without, LightGBM, LightGBM LambdaRank, XGBoost, a neural net),
  blended on the even validation races and scored on the odd ones.
- **The pre-race recorder** is step 13h unchanged, run with
  `XCP_PREDICT_BASIS=race` and `XCP_FORECAST_TABLE=pred_meet_forecast`.
  It records every posted meet, and `score_frozen_forecasts.py` scores it
  with the same table setting.

### Every baseline on the same races (owner's run, 2026-10-27)

1,000 race-morning races of 2024-25, 64,806 runners. The transformer is
run as the page runs it, history cut the day before.

| | Pairs | Rho | Median miss | Mean miss |
|---|---|---|---|---|
| Last race | 86.30% | 0.871 | 3.68% | 5.19% |
| Ratings | 87.17% | 0.887 | 3.84% | 5.14% |
| Transformer alone | 87.73% | 0.895 | 3.09% | 4.40% |
| **Site today (blend)** | 87.17% | 0.887 | 3.31% | 4.69% |
| **New model (1.5)** | **88.56%** | **0.906** | **2.85%** | **4.13%** |

- **Against the site:** +1.39 points on order, and times about 14%
  closer.
- **The transformer was trained on data that includes 2024-25,** so its
  line is flattered; the new model never saw the season. It is still
  ahead of the transformer on everything (+0.83 points on order, 2.85%
  against 3.09% median miss).
- **`--train-frac`** (race_trees, race_blend) fits on a sample of the
  training races, for quicker tests.

### 1.6 results (owner's run, 2026-10-27)

- **Single model:** 88.59%, +1.37 [+1.35, +1.38].
- **Forecasts:** 7 days ahead +1.38 (87.67% against 86.28%); 28 days
  ahead +1.56 (85.06% against 83.50%). Time miss 7 days ahead 2.97%
  median, 28 days ahead 3.40%.
- **Ranges:** 69 / 70 / 90.
- **Blend of six:** every member about 88.60%; the blend 88.68% (+0.07)
  for about 6 hours of compute. The members agree because the inputs are
  the limit, not the model, so new information is where the rest lies.

### Version 1.7 (on the branch): new information

- **Records in context** (owner: "the runner at this course's
  difficulty"): the runner's residual against the ratings at this course,
  on courses of similar difficulty (cell bands at 0.02 / 0.05 / 0.09), at
  similar distances, and in similar heat. From real results before the
  row's cut; vectorised, seconds.
- **`div_type` from the division title:** varsity / JV / open.
- `race_rows` saves each row's course id.
- **The test protocol from here:** reuse a table, `--train-frac 0.35`,
  old table against new, in minutes. One full run only for the final
  model.

### 1.7 results (owner's run, 2026-10-27): the new information buys nothing

Same table (rows7), same races, `--train-frac 0.35`; "without" leaves out
`crs_,dif_,dst_,hot_,div_type` (`race_trees --without`).

| | With | Without |
|---|---|---|
| Order, race morning | 88.56% (+1.34) | 88.55% (+1.33) |
| Order, 28 days ahead | 85.03% | 85.02% |
| Median time miss | 2.90% | 2.90% |
| College 100+ | 3.52% | 3.52% |

- **+0.01 point: nothing.** The ratings are already course-adjusted (the
  joint solve), and each runner's own record already carries how they run
  against them. What is left of a runner at one course or one band of
  difficulty is a handful of races of day-to-day noise.
- **Order looks close to what results alone can tell.** Every model type
  sits at about 88.6% (the blend), and E0's hindsight ceiling is about 90%.
  More inputs of the same kind will not move it; only information the
  results do not hold would (training, injury, entries).
- **So the time is where to push,** and the level of the race more than
  the order inside it.

### Version 1.8 (on the branch): the course's own level record

- **For the level, not the runners.** A course that keeps running slower
  or faster than its cell says (a loop measured long, a cell from few
  races, mud every October) misses the same way race after race. The
  record is that miss: the race's mean (actual − page clock) at this
  course and distance band, from real races on days before the race's cut
  (`CLV_FEATURES`, clipped at 15% so a wrong distance is not a course).
- The level model reads it; the runner trees never see it. The page reads
  it from the pickle (`course_level`).
- **Test:** rows7, with and `--without clv_`. Read the TIMES table and the
  race-level miss line.

### 1.8 results (owner's run, 2026-10-28): the course's level record works

rows7, `--train-frac 0.35`, with and `--without clv_`:

| | With | Without |
|---|---|---|
| Median time miss | **2.83%** | 2.90% |
| Mean miss (Kailo's statistic) | **4.25%** | 4.34% |
| College 100+ | **3.43%** | 3.52% |
| 7 / 28 days ahead, median | 2.94% / 3.40% | 3.01% / 3.44% |
| Race-level miss, median | **1.87%** | 2.00% |
| Races over 5% / over 10% | 13.5% / 2.9% | 14.6% / 3.0% |

Order is untouched (the runner trees never see it). It stays in.

### Next (owner, 2026-10-28: "only 4 and 6")

- **#4, the data:** `race_trees` now prints where the race-level misses
  are, by the first cause the data shows (an odd race name, a distance
  unlike the course's usual one, mixed levels in one field, a field under
  8, a course with no history). What to fix follows from it.
- **#6, a sequence model:** each runner's last results, in order, read by
  a small network trained for this target, as a `race_blend` member beside
  the trees. Expected +0.1–0.3 points, mostly openers.

### #6 on the branch: the `seq` member

- `race_trees.seqTable`: every row's runner's last 16 real results before
  its cut, oldest first, from the full table: the residual against the
  ratings, the place and the rated place in that field, distance, course
  cell, meet type, level, college, field size, and how long before.
- `race_blend --members seq`: the mlp's inputs plus those results, pooled
  by attention (the query comes from who the runner is). Against `mlp` it
  measures what the ORDER of the results is worth beyond the hand-made
  summaries.
- On a synthetic table where each runner has a hidden offset only the
  history shows, with the records off: mlp 82.09% (the ratings), seq
  86.89%. It reads the history.
