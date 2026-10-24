# Plan: making the prediction model better

*2026-10-24. Follows `docs/REVIEW-PREDICTIONS-2026-10.md`, which found
what is wrong today. This file says where we are going, what is likely
to help, how each idea gets built, and the experiments that decide
between them. Ideas first, code second: nothing here is built until its
experiment is approved.*

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
is the largest part of its target. That is the main reason it orders a
field worse than the ratings, which take the race level out on purpose.

### The three numbers we chase

Every one is measured on predictions frozen before race day
(`meet_forecast`, Kailo "recorded") and on the held-out season (§2).

| | Measure | Target, first milestone | Where we are |
|---|---|---|---|
| **G1 order** | Share of runner pairs in the right order, per race, averaged over races. Spearman beside it. | Beat our own ratings' order by **+1 point**; beat Kailo on their races | Ratings 85.5%, Kailo 85.9%, model 79.6% (college, 9 races) |
| **G2 time** | Mean \|% miss\| (Kailo's statistic), plus the median | Under Kailo's **2.84%** (their 2026 record; 2.86% in their 2021–25 backtest) on 20+ recorded races | 6.50% on their races, all of it level |
| **G3 honesty** | Coverage of the 68% and 90% time ranges; log loss and reliability of team-win odds | Coverage between 63–73% and 86–94%; win odds better than "the lowest predicted score wins" | Not measured |

**G1 comes first.** Order drives places, team scores and win odds, and
it is the part the network has to earn: the race level (G2) is mostly
the course numbers and the engine. **G3 gates shipping** a probability
on any page.

Whether +1 point is ambitious or timid is unknown until E0 (§5) measures
how much room there is.

---

## 1. How much room there is: the error budget

| Piece | Size | Can a model reduce it? |
|---|---|---|
| A runner's own day-to-day swing | 1.2–1.9% for the fastest quartile of men in cross country and road races; larger for slower and younger runners (Hopkins & Hewson 2001) | No. This is the floor. |
| Error in our estimate of a runner's form | What is left. Our within-race median miss of 2.32% is about a 3.4% sd. With a 2–2.5% day swing, that leaves **about 2.4–2.8% sd of form error, as big as the noise itself.** | **Yes: this is the model's job** |
| The race's level on the day | ENGINE.md puts the race-day sd at 3.05%, part of it predictable (course, weather) | Partly, through better course numbers and weather |

If those figures hold, there is real headroom in form, and that is where
the transformer can win. E0 measures it directly before anything is
built, so we know whether +1 point is a ceiling or a floor.

Research to note: a 2025 study of college club cross country (arXiv
2509.10600) found pooled in-season improvement of about 5.5 s/week, but
predicting *which* runners improve from their first race alone was near
chance (held-out R² ≈ 0). So individual trends need heavy shrinkage
toward group curves. Free-standing per-runner slopes are noise.

---

## 2. The test bench, built before any model work

Without a clean test, no idea in §4 can be judged. The bench follows the
engine's own forward holdout (`engine/forward_holdout.py`), so the model
and the engine are scored on the same held-out races.

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
  from a forward fit (`run_joint --holdout-kind forward` already fits
  only on rows before the date; its per-row ratings and cells go to side
  tables). Every validation race is predicted from data before the
  cutoff, the same question the engine's forward scorecard answers, with
  its race bootstrap.

### What the bench needs

- **A race id on every example** (meet, division, source, day). It is
  needed for per-race metrics and for the race-level training in §4.
- **One scoring script for any predictor:** last race, rating, trend,
  GBM, any `model.pt`. It prints G1–G3 by level, by gap band,
  championship or not, and early or late season, with a paired race
  bootstrap between any two predictors.

---

## 3. Yardsticks to beat

| | Predictor | Why it is here |
|---|---|---|
| Y0 | Last race | The floor |
| Y1 | Rating form (served now) | What the site does |
| Y2 | `trend`: rating plus a level table plus a shrunk slope (on the branch) | The strong simple rule. The network must beat it. |
| Y3 | The engine's carry-forward (forward holdout) | The engine's own forecast, already on this bench |
| Y4 | **Gradient-boosted trees** on hand-made features, predicting the residual over Y2 | **An instrument, not a product.** It trains in minutes on CPU and says which ideas carry signal before any GPU time. If it matches the transformer, it is a candidate itself. |

**Shipping rule:** a new `model.pt` serves only when it beats Y2 and Y4
on validation G1, without losing G2. A one-time check on the sealed
season confirms it.

---

## 4. Ideas, ranked by expected gain for the cost

### A. The target and the anchor (highest value)

**A1. Train the runner part on position in the field, not raw time.**
- **Target:** x_ir = ln(normalized time) minus the race's median over its
  runners (or a trimmed mean). This removes the race level, about 3% sd
  of noise nobody can predict, from the target. It is leak-free (it
  needs no engine output) and is exactly what places depend on.
- **Alternative arm:** the race's rated time. Course and day are already
  out, but the day term was fitted on season abilities that include
  later races, a mild leak.
- **How:** an SQL pass before extraction builds a `race_stats` table
  (race id, runners, median ln normalized time, median rating, top-5
  mean). Extraction joins it.
- **Expected:** the biggest single gain in G1, because capacity now goes
  to form instead of race noise.

**A2. Model the race level separately.**
- L_r = as-of course cell + distance + weather + championship flag +
  field strength.
- It is a small model (linear or GBM) trained on race medians. It
  supplies G2's level; the network supplies x.
- It also replaces blend's "model sets the level" with something that
  can be inspected.

**A3. Keep anchoring on the rating, and hand it the trend inputs.**
Keep BASELINE_RATING as the anchor, and pass Y2's form age, own slope
and level-cell correction as inputs. The network then starts from the
best simple predictor and learns corrections to it.

### B. Train on whole races (high)

**B1. Batches are races, and a loss scores order.**
- Each step samples races, and up to about 32 runners from each.
- **Loss:** Gaussian or Student-t on the centred x, plus a pairwise
  logistic (or Plackett-Luce) loss on finishing order inside the race.
- This optimizes G1 directly, which the current one-runner loss never
  does.
- **How:** a sampler that groups examples by race id. Chunks are written
  race-grouped, or an index maps each race to its examples.

**B2. Let runners attend to each other (the new architecture).**
- After each runner's history is encoded, a second small transformer
  runs across the runners of one race, then outputs x.
- **What it can learn that ratings cannot:**
  - tactical championship races that compress the front;
  - a 150 in a 100 field running easy;
  - teammates packing;
  - head-to-head history between two runners.
- **Order:** only after B1, and only if B1 shows race training helps.
- **Cost:** more GPU per step. Inference runs one race at a time, which
  is what the page already does.

### C. Better estimates of form (medium to high)

| Idea | Why | How | Signal check |
|---|---|---|---|
| **C1. Race importance and effort** | Top runners jog dual meets and peak at championships (taper). Late season is where the meets that matter are. | Label meet type from names and divisions (conference, district, sectional, regional, state, NXN, NCAA, invitational, dual, JV/open). Add the runner's rating rank in the field. Use both in the history and in the target race. | Y4 importance; G1 on championships |
| **C2. Margin, not just place** | A 40-second winner was not pushed, so their time understates them | Per history race: the gap to the next runner ahead and behind, and the gap to the winner | Y4 |
| **C3. Team trend** | Teammates train together; group improvement is real but individual improvement is unpredictable (arXiv 2509.10600) | This season's median slope of the runner's teammates, plus a slope shrunk toward team and class-year curves. The engine's `XCP_FORM_TREND` is a related idea. | Y4; G1 late season |
| **C4. Track as cross country evidence** | Early-season and freshman cross country is thin; spring 1600/3200 says a lot | Already in the history; check the network uses it (an ablation), and give it the runner's sport offset | G1 early season |
| **C5. HS → college transitions** | About 1,950 college runners are still served off high-school form (spread 5.0% against Kailo's 1.6%) | Fix the identity and first-season rows; check the horizon examples cover HS → college at the right grades | G1 college, September |
| **C6. Bad days are not form** | Results skew slow (falls, illness, jogging) | A skewed likelihood (skewed-t), so a slow day costs less than a fast surprise | Validation NLL and G1 |

### D. The race level, for times (medium for G2, nothing for G1)

- **D1. Course cells.** Freeze them as of the cutoff for training. Give
  courses with no cell a prior from the median cell at the same level
  and distance. Finish the tfrrs geocoding (MIGRATE §5c).
- **D2. Forecast weather at race hour** when we have it, instead of
  "normal" (`_weatherVariants` already fetches it). Training uses
  observed weather, which is the closest stand-in for a 1–2 day forecast.
- **D3. Day noise as a shared random effect.** It widens time ranges
  and leaves places alone.

### E. Honest ranges (gates G3)

- A separate sd for x (runner) and for L (race), each calibrated on the
  validation season with one scale per level and gap band.
- `team_rho` measured (`backtest_baseline.teamRho`) and used by
  `race_sim`.
- `meet_forecast` keeps p_win, so frozen odds can be scored.

### F. Data quality (cheap, ongoing)

- Report the share of validation error coming from |err| > 10% rows, by
  cause: wrong distance, merged identity, JV mixed into varsity.
- The Student-t loss already softens them, but fixing the source helps
  the ratings too.

### On other architectures, honestly

- **Keep the transformer** for what only a sequence model does: gaps,
  returns, cross-sport careers, and odd histories.
- **Y4 (trees) is the challenger.** If trees on good features match it
  on G1, the extra machinery has to justify itself.
- **A per-athlete state-space model** (form as a random walk on a
  season curve, a Kalman filter) is the principled middle ground. It
  belongs in the engine (`XCP_FORM_TREND` is close) and is a fair Y5 if
  E2 says trend matters.

---

## 5. Experiments, in order

Each arm is compared on a 10% subset of chunks over 2 epochs first;
only winners get a full run.

| # | Question | Setup | Decides | Cost |
|---|---|---|---|---|
| **E0** | How much room is there? | On 2024-25 and 2025-26 races: order from prior form (Y1) against order from **hindsight form**, the same season's ratings excluding the race itself (earlier and later races). The gap is the most that better form estimation can buy; what remains is day noise. Per-runner race-to-race sd by level beside it. | How far to push G1, and whether B2 is worth it | One script, SQL only, ~20 min |
| **E1** | Where does each yardstick stand? | The bench (§2, Tier 1). Score Y0–Y2 and the current model.pt on the validation season. | The baseline table | Extraction with race ids (~1 h+) |
| **E2** | Which ideas carry signal? | Y4: trees on the residual over Y2 using C1–C5 features, with importance and G1 gain per feature group | Which of C1–C5 go into the network | Hours, CPU |
| **E3** | Does training on position in the field help? (A1) | Same network, target x_ir, against today's target | The target for everything after | One retrain on the subset, then full |
| **E4** | Do race batches and an order loss help? (B1) | Arm E3 with and without the race-grouped sampler and the pairwise loss | Whether B2 is next | One retrain |
| **E5** | Do E2's winning features help the network? | E4 plus those features | The feature set | One extraction and a retrain |
| **E6** | Does looking across the field help? (B2) | E5 plus the field encoder | The architecture | One retrain, more GPU |
| **E7** | Full product: level model, calibration, Tier 2 check, then sealed season once, then live | A2 + D + E | Ship or not | One forward solve plus the sealed check |

**Fastest path to an answer:** E0, then E1, then E2. Together they say
how much there is to win and where. E3 and E4 are the bets most likely
to pay off.

---

## 6. How it gets built (outline, not code)

- **Extraction (one re-extraction covers E1–E5):**
  - add race id and target date per example;
  - join `race_stats` for the x target and field strength;
  - add meet type (C1), margins (C2), team slope (C3);
  - read course cells from an as-of snapshot table for validation and
    test rows.

  The new features are appended at the end of the vectors, as the file
  requires (`_forwardRaw`'s "new inputs go on the end").
- **Training:**
  - date split from target dates (replacing the athlete-hash split);
  - the race-grouped sampler;
  - a multi-task loss (x likelihood + pairwise order);
  - per-race G1 printed each epoch;
  - the training cutoff written into `model.pt`.
- **Serving:** the network gives x; the level model gives L. A field's
  x is centred per race, then L is added. `blend` goes away, because
  the level is now its own model.
- **Evaluation:** the one bench script (§2) for every predictor, plus
  `score_frozen_forecasts.py` and `kailo_compare.py` for live.

---

## 7. What I need from you

1. **Agree the goal** (§0): G1 first, G2 against Kailo, G3 as the gate
   for probabilities.
2. **Run E0.** I will write it next. It is SQL only, about 20 minutes,
   and needs no GPU. It tells us how much room there is before we spend
   any GPU time.
3. **For E1 onward:** how much GPU time per week you are willing to
   spend, and whether you are OK with new side tables (`race_stats`,
   as-of snapshots of course cells and ratings).
