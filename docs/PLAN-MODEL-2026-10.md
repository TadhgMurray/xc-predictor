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
