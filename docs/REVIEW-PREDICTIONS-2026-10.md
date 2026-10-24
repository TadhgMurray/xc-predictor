# Review: the race predictions, October 2026

*2026-10-24. Branch `claude/lucid-turing-tyd1b9`. Nothing in this review
ran against the database. The numbers quoted come from the owner's own
runs, logged in `docs/ISSUES-RUNNING.md` (cited by date). §8 has the
commands that produce the numbers still missing.*

---

## 1. Findings

1. **The transformer barely touches what the site serves.** Under
   `XCP_PREDICT_BASIS=blend`, every runner with a rating gets their
   rating's time, multiplied by one factor for the whole field. That
   factor is the median of model ÷ rating, capped at ±12%
   (`predict.blendFactor`). Multiplying every time by one number cannot
   change an order, so **places and team scores come from the ratings
   alone**. The network does two things: it sets that one number per
   race, and it gives a time to runners who have no rating (only when
   its band is under 8%).
2. **Wherever the network's own times have been scored, they order a
   race worse than the ratings do:**
   - Spearman 0.775 against 0.874 (40 races in September, ISSUES
     2026-10-08);
   - within-race miss 2.42% against 2.32% (80 races after the model's
     date, ISSUES 2026-10-22);
   - right order in 79.6% of college pairs against 85.5% (Kailo's races).

   It only wins on **level**. The model's bias is −0.2%. The ratings
   run +2.1% slow on high-school races and −6.4% fast on college courses
   that have no course cell.
3. **Both level errors have plain causes and plain fixes. Neither needs
   a network.**
   - **College:** our tfrrs courses had no course cell, so they were
     converted as an average course. That fix is already in flight
     (`docs/MIGRATE-2026-10.md` §5c).
   - **High school:** a race's rating is the runner's form *that day*,
     because the form curve is left out of ratings (`engine/joint_golive.py`).
     The rating basis averages the last six races and does not project
     forward, so it trails a season's improvement and comes out slow
     in-season.
4. **Against Kailo, all of our loss is level.** On 9 college races
   (1,945 runners):
   - order: 85.5% against their 85.9%, nearly a tie;
   - spread with each race's own level taken out: 1.95% against 2.09%.
     We are already *better* there;
   - average miss: 6.50% against 2.30%, because our bias is −6.4%.

   If we fix the level, the spread figures suggest we land at or under
   their 2.30%. That is not proven: it is 9 races, and some of ours
   were rebuilt rather than recorded.
5. **Almost none of our accuracy numbers are clean.** The What's new
   line (16d), `backtest_predictions.py` and Kailo's "rebuilt" races
   all re-predict a finished race using *today's* ratings and course
   numbers, and today's solve has seen that race (sweep A9). The only
   clean records are the forecasts frozen before race day (step 13h's
   `meet_forecast` table) and Kailo's "recorded" races (step 16c).
6. **The network's validation is not a test of forecasting.**
   - The split is by athlete, not by date. The same races (and the
     venue embedding learned from them) sit on both sides of it.
   - The target race's course difficulty was solved *with* the target
     race.
   - The baseline it reports against is "same as their last race", the
     weakest one there is, not the rating form the site actually serves.
7. **The bands are probably too narrow, and nothing measures them.**
   - A rated runner's time band is the spread of their own recent
     ratings, clamped to 2.5–6%. It has no race-day term, yet the
     engine's own race-day spread in cross country is 3.05%.
   - `race_sim` draws teammates independently (`team_rho = 0`), so team
     win probabilities are overconfident.
8. **Two bugs, both fixed on the branch:**
   - `scripts/diag_calibration.py` grouped results by *day of year*, not
     by days since the last race (it read context slot 3; the gap is in
     slot 4). Every horizon table it printed is void.
   - `kailo_compare.py --basis X` on a race recorded before X existed
     quietly scored the served basis instead and labelled it "recorded".

**Verdict:** the transformer does not earn its place today. It costs:

- a 125 GB extraction that takes over an hour;
- GPU training runs;
- about 6,700 lines of model code;
- 2–3 s of model time per 195-runner field.

In return, the site uses one number per race from it. Replace `blend`
with the `trend` basis (§6) once the §8 backtest agrees. Keep the network
only as the fallback for runners with no rating, and stop retraining it
until a test held out by date shows it beating `trend`.

---

## 2. How it works, end to end

**Inputs** (`model/feature_extraction.py`). The rows are every rated
result in `results` and `results_tf`, streamed per athlete: about 54M
rows. Each result after an athlete's first becomes one example.

- **History:** every earlier race (strictly earlier days) as a
  23-number row:
  - normalized time, course difficulty, days before the target,
    distance, grade, XC/indoor flags;
  - nine weather fields;
  - altitude and altitude change, place, GPS;
  - the race's **rating as a time** and a has-a-rating flag.
- **Target race:** a 25-number row:
  - is-forecast flag, course difficulty, distance, day of year, days
    since the last race, level, gender, school (as a label number);
  - weather, altitude, GPS, grade, year;
  - hidden days.
- **Venue:** an embedding index, for courses with at least 50 races.
- **Twins:** each example also gets copies with part of the history
  hidden: forecast (2–40 weeks), horizon (44–208 weeks), long horizon,
  "silent" gaps and dropout. These teach the network forecasts and
  recruiting projections.
- **Target:** the target race's `normalized_time`. Per `docs/ENGINE.md`
  that already corrects for distance, era and weather, but *not for the
  course*.

**Network** (`model/transformer.py`, about 4M parameters):

- The history rows are projected to 256 numbers and run through 4
  pre-norm encoder layers (8 heads).
- The target race goes in as a token at position 0. It also asks one
  cross-attention question of the history.
- Mean-pooled history, the context, the venue embedding and three
  "break" features feed a small head with two outputs:
  - μ, a z-scored ln(target ÷ baseline);
  - its log-variance.
- **The baseline is the anchor, and the network learns a correction to
  it.** The baseline is `BASELINE_RATING`: a recency-weighted (60-day
  half-life) geometric mean of the prior races' rated times. The
  correction is small: the interquartile range of exp(μ) was 0.97–1.02
  (2026-09-17).

**Training** (`model/train.py`):

- AdamW at 3e-4, batch 64, up to 20 epochs with early stopping
  (patience 3);
- Student-t loss with a learned tail, β-NLL weighting at 0.5, and
  break-band balancing;
- targets clamped at ±8σ.

**Serving** (`racecast/predict.py`):

- `_servedTimes` resolves the target, then calls `_ratingTimes`: each
  runner's last six rated races in the pool they last raced in, weighted
  0.8^k, with outliers more than 20 points low dropped. The band is
  their weighted spread, clamped to 2.5–6%.
- `_ratingToSeconds` turns that into a time on the target course. With
  `XCP_PREDICT_VENUE_DIFFICULTY=xc`, a course with no cell converts as
  the median race.
- Under `blend`, `_predictTimes` also runs the network for the whole
  field:
  - same features, "normal" weather, hidden days = race day − today;
  - the network's normalized time goes onto the race clock through the
    runner's own raw ÷ normalized ratio at that distance, or the pool's
    curve if they have none.
- The median model ÷ rating ratio then moves every rated time. Runners
  with no rating keep the model's time if its band is under 8%;
  otherwise they are listed without a score.

**Simulation** (`racecast/race_sim.py`): each runner's time is drawn
from a log-normal on `sigma_pct` (floor 1.5%), 2,000 draws, and each draw
is scored under the real rules. `team_rho` is a shared team component and
defaults to 0.

---

## 3. What it is trained on, and how it is validated

- **Corpus:** every rated XC and TF result up to the extraction date.
  About 51% of examples are real next races; the rest are twins (the
  2026-09-16 mix: 18% forecast, 30% horizon, with more kinds since).
- **Validation:** 10% of *athletes*, chosen by crc32 of the person id
  (`feature_extraction._isValAthlete`).
- **Reported each epoch:** the mean |error| in log units against
  "same as last race", banded by gap; one-sigma coverage; the order of
  close pairs.
- **Not reported:**
  - error on races after a date;
  - error against the rating form;
  - error inside a race;
  - team scores.

The backtest (`scripts/backtest_predictions.py`) is the only
forecast-shaped test of the network. Its header says it: races before
`model.pt` was written may have been training targets.

---

## 4. Leakage

| # | Where | What leaks | How bad | Fix |
|---|---|---|---|---|
| L1 | Training context `course_difficulty` (`_XC_SQL` joins today's `course_difficulties`) | Target-course difficulty is solved with the target race. A one-race course keeps 59% of what its race showed (ENGINE §2). | **High on thin courses.** The network learns to trust a number that is cleaner in training than it will ever be at serving. | Freeze cells as of a date for training, or target the *rated* time (course and day already out) instead of `normalized_time`. |
| L2 | History `rated_time` (`_ratedTime`) | Each past race's rating carries a day term fitted on the field's season abilities, which include later races. | Low–medium | Same as-of snapshot. The file's own comment admits it. |
| L3 | Athlete-only split (`train.splitTrainVal`) | Validation athletes ran in the same races as training athletes. The venue embedding and the course term learn each day's conditions from the training side. | **Medium.** It flatters validation loss, and save-on-best then picks the more flattered model. | Split by **meet date**: train before T, validate on races at T or later. |
| L4 | No date holdout | Any backtest race before `model.pt` is in-sample for about 90% of runners. | **High for backtests of the model** | Store the training cutoff in `model.pt`; backtests refuse races before it. |
| L5 | Training weather is *observed*; serving uses "normal" | This is a train/serve skew rather than a leak: the model learns weather effects it is never given. | Low–medium | Train on normal-for-the-date, or drop target weather. |
| B1 | Every re-predicting backtest: 16d, `backtest_predictions`, Kailo "rebuilt" | Ratings and course cells come from a solve that saw the race (A9). | **High for level metrics**, mild for order | Score the frozen archives (§7), and snapshot ratings and cells weekly. |
| B2 | `rerun_exact` uses the actual finishers | Who starts is given, so team-score error comes out too low. | Medium, for team scores | The frozen archive guesses the entries, like the real page. |
| B3 | 40 randomly sampled races, one seed, no interval | Not leakage, but ±0.2 points of noise reads as a result. | Medium | Paired per-race differences with a resampled interval (built into `backtest_baseline.py`). |

---

## 5. Calibration

| Output | What is claimed | What is measured | Expected |
|---|---|---|---|
| **Times, rated runner** | lo/hi = ±1σ = 68%. σ is the spread of the last six ratings, clamped to 2.5–6%. | Nothing | **Under 68%.** The race-day effect is missing (ENGINE.md prints its XC sd as 3.05%), and it moves the whole field. |
| **Times, model** | The head's σ from a Student-t loss | Per epoch on athlete-held-out examples. The horizon table (`diag_calibration`) was cut on the wrong slot, so it is void. | Re-run `diag_calibration` (fixed) for the recruiting horizon. |
| **Places** | The same σ, inside a race | Nothing. The common day cancels for places, so σ ought to be the right size here. | Roughly honest, with too few races to estimate a spread from. |
| **Team scores / win %** | The race_sim distribution with `team_rho = 0` | Nothing. `meet_forecast` keeps team scores but not the win probabilities. | **Overconfident** if teammates' days are correlated, which `teamRho` now measures. |
| **Point accuracy** | — | Spearman on 40–80 sampled races; Kailo, 9 races | §7 |

The `trend` basis (§6) splits the band in two:

- **Place spread:** the form's own σ, scaled by `place_k` so the races
  before the cut date cover 68%.
- **Time band:** that place spread plus the measured race-day spread
  (`day_sd`).

`race_sim` reads the place spread (`sigma_pct`). The page's lo/hi read
the time band.

---

## 6. The strong simple baseline: `trend`

`trend` takes the recent rating, puts it on the target course, adds a
fitness trend, and sets the bands from the runner's own spread. It is
built on the branch, and serves only under `XCP_PREDICT_BASIS=trend`.

- **Form:** exactly the `rating` basis: `_ratingTimes` through
  `_ratingToSeconds`, with the venue's cell, or the median race when the
  course has none.
- **Level and trend:** a table fitted only on races *before* the test
  window (`scripts/backtest_baseline.py`). It has:
  - one median correction per (level, course has a cell, form's age:
    0–14 / 15–35 / 36–70 / 71–180 / 181+ days), shrunk toward the
    level's value by race count;
  - the runner's own ln-rating slope this season (3+ races over 21+
    days), carried over the form's age at a fitted share (`slope_beta`).
- **Noise:** as in §5.
- **Code:** `predict.trendShift` and `trendApply` are the single
  definition. The backtest scores exactly what the page would serve.
- **Cost:** no model run for rated runners (the network runs only for
  runners with no rating, as under `rating`), plus one small JSON file.

**What we already know, against the bases it is up against:**

| | median \|err\| | bias | order / within | source |
|---|---|---|---|---|
| rating (no trend) | 4.02% | −0.03% | ρ 0.872 | 40 races, Sept, day before (ISSUES 2026-10-08) |
| model, old checkpoint | 6.27% | +4.56% | ρ 0.768 | same |
| rating | 3.44% | +2.06% | within 2.32% | 80 races after model date (ISSUES 2026-10-22) |
| model | 2.98% | −0.2% | within 2.42% | same |
| blend | the rating's within-race 2.32% at the model's level | | | by construction |
| trend | **to measure (§8 D)** | | | |

`blend` and `trend` both keep the rating's order (apart from the
per-runner slope). The test is therefore whether a table fitted on last
month's races moves the level as well as the network's median does. The
network's level comes from the same history, so a table should do about
as well, and it cannot shift every time on the site when a checkpoint is
retrained.

---

## 7. Backtest plan, held out by meet date

**Unit:** a race, meaning one (meet, division, day) with at least 20
timed runners and no twin copy (`backtest_predictions.sampleRaces`).

**Folds:** rolling origin, by week.

- For each test week W (Monday to Sunday), anything fitted (the trend
  table, and any future model) uses only races dated before W.
- Every runner's history is cut the day before their race.
- Results are reported per week and pooled.

**Three sources, from cleanest to cheapest:**

1. **Frozen archive** (`meet_forecast`, step 13h). This is what the page
   showed the night before. Nothing in it saw the result, the entries
   were guessed, and the score covers the whole chain. Scorer:
   `scripts/score_frozen_forecasts.py`.
2. **Kailo, recorded** (`engine/data/kailo/*.json`, step 16c). Ours,
   saved before the gun for every basis (now including `trend`), beside
   Kailo's final pre-race version on the same runners. Scorer:
   `scripts/kailo_compare.py --basis B`.
3. **Re-predicted** (`scripts/backtest_baseline.py`). Many races, every
   basis on the same runners. It is flattered by B1 *equally across
   bases*, so read the **differences**, not the levels.

**Metrics** (all three scripts print them the same way):

| Metric | Definition |
|---|---|
| median \|time error\| | median over runners of \|ln(pred ÷ actual)\| (as %); plus the bias (median signed) and the within-race version (the race's own median miss taken out) |
| place rank correlation | mean over races of Spearman(predicted time, actual time) on the runners matched; the frozen scorer adds the median \|place error\| after re-ranking |
| team score error | mean over teams in both of \|predicted − actual team score\| (5 score, 2 displace, `meet_compile.scoreRows`); plus the winner picked % and the team-order Spearman |
| interval coverage | share of actual times inside ±1σ (68%) and ±1.645σ (90%) of the time band; share of within-race misses inside ±1 place σ |
| against Kailo | on the same runners: mean miss (Kailo's own statistic), median, within 2%, bias, pair order |

**Decision rules:**

- **Switch `blend` → `trend`** when, on at least 60 re-predicted races
  over 3+ weeks, the paired interval for trend − blend on the per-race
  median |err| is under +0.10 points and ρ is no lower than blend's
  minus 0.005. After that, check one week of the frozen archive.
- **Claim "better than Kailo"** only on 20+ *recorded* races, when the
  paired per-race interval for ours − theirs on mean miss sits wholly
  below 0.
- **Retrain the network** only with the split by date, and only if it
  beats `trend` on the re-predicted folds.

---

## 8. Commands for the owner

These use a separate checkout of this branch, so the live site and the
nightly run are untouched. Outputs are trimmed.

**A. Set up the review checkout** (seconds). Gitignored server files are
linked from the live tree.

```
cd /srv/xc-predictor && git fetch -q origin claude/lucid-turing-tyd1b9 && \
git worktree add -q --detach /srv/xcp-review FETCH_HEAD && \
git ls-files -o --directory engine racecast model scripts | grep -v __pycache__ | \
while read f; do f=${f%/}; [ -e "/srv/xcp-review/$f" ] || ln -s "/srv/xc-predictor/$f" "/srv/xcp-review/$f" 2>/dev/null; done; \
ls /srv/xcp-review/model/data/model.pt /srv/xcp-review/engine/data/kailo | head -3
```

**B. The clean numbers: frozen forecasts and Kailo recorded.** Read only,
about 5–10 min (the Kailo API is called about once a second).

```
cd /srv/xcp-review && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
grep -E '^XCP_(FORECASTS|KAILO|PREDICT_BASIS)=' /etc/xc-predictor.env
$P scripts/score_frozen_forecasts.py --from 2026-09-01 --to 2026-10-23 --by week 2>&1 | tail -10
$P scripts/score_frozen_forecasts.py --from 2026-09-01 --to 2026-10-23 --by basis 2>&1 | tail -5
for b in blend rating model; do echo "== $b"; $P scripts/kailo_compare.py --basis $b 2>&1 | grep -A6 "^  ALL "; done
```

If the first script finds 0 frozen races, step 13h is off
(`XCP_FORECASTS=1` turns it on). From then on it is the scorecard to
trust.

**C. How much A9 flatters the backtest** (read only, about 5 min). Run
the same Kailo races recorded and rebuilt:

```
cd /srv/xcp-review && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
mv engine/data/kailo /srv/kailo.hold && mkdir engine/data/kailo && \
$P scripts/kailo_compare.py 2>&1 | grep -A6 "^  ALL "; rmdir engine/data/kailo; mv /srv/kailo.hold engine/data/kailo
```

Here `engine/data/kailo` is the symlink, so the recordings move aside for
one run and come straight back. Compare its "ALL" with B's blend line.

**D. The baseline against blend and the model, held out by date.** This
is the long one: **about 20–40 min of database reads.** Postgres restarts
between 06:00 and 07:00 UTC, so start it only if `date -u` says before
05:15 or after 07:00.

```
cd /srv/xcp-review && set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python; date -u +%H:%M
nohup $P -u scripts/backtest_baseline.py --train-from 2026-08-20 --from 2026-10-04 --to 2026-10-23 \
  --train-divs 200 --divs 80 --with-model --write-table engine/data/rating_trend.json \
  > /srv/xcp-review/baseline.log 2>&1 &
# when it has finished:
sed -n '/^table:/p; /^basis/,/^$/p; /^paired/,/^$/p; /^by level/,$p' /srv/xcp-review/baseline.log
```

The table lands in the review checkout only. Production has no
`rating_trend.json` and does not serve `trend`.

Once this branch is merged, copying that file to
`/srv/xc-predictor/engine/data/` (only with your go-ahead) does two
things: 16c starts recording a real `trend` for Kailo's races, and 16d
prints a `trend` row. It changes nothing that is served while
`XCP_PREDICT_BASIS` is `blend`. Without the file, `trend` simply equals
`rating`.

**E. Clean up when done:** `cd /srv/xc-predictor && git worktree remove --force /srv/xcp-review`

Send back the outputs of B, C and D.

---

## 9. Recommendations

| # | Change | Expected gain | Cost | Risk |
|---|---|---|---|---|
| 1 | **Finish the tfrrs course cells** (MIGRATE §5c: backfill gps, `build_course_canonical`, solve) | College level −6.4% → near 0; against Kailo, mean miss 6.5% → roughly 2–2.5% (estimate from the level-free spread) | Already scripted; one solve | Low |
| 2 | **Serve `trend` instead of `blend`** after §8 D, then refit the table weekly (a pipeline step after 16d) | Same order as blend. Level from an explicit table instead of a checkpoint. Honest time bands. **2–3 s less model time per field** (the network only for runners with no rating). | Done on the branch; one env change and a restart, with your go-ahead | Low; `rating` is one env change away |
| 3 | **Stop retraining the network** until a date-split version beats `trend` | Saves the extraction (over 1 h, 125 GB), GPU runs and most model upkeep | None | Runners with no rating keep today's fallback |
| 4 | **Use the frozen archive as the accuracy number** on What's new (`score_frozen_forecasts.py`), not 16d | An honest public number, with team scores and coverage | Small (`run_report` reads one more log) | None |
| 5 | **Keep `p_win` and σ in `meet_forecast` teams** (`upcoming_preview._TEAM_KEYS`) | Lets win probabilities be scored (Brier) | Small | None |
| 6 | **Set `team_rho` from the data** (`teamRho`, printed by §8 D) | Team win % no longer overconfident | Trivial once measured | Low |
| 7 | **Weekly as-of snapshot** of course cells and this season's ratings | Clean re-predicted backtests for any past week; also fixes L1/L2 if the network is ever retrained | Small tables, a few minutes a week | Low |
| 8 | **College runners still rated from high school** (ISSUES 2026-10-22, about 1,950 runners, spread 5.0% against Kailo's 1.6%) | The biggest order gain left against Kailo early in the college season | Medium (identity / first-college-season rows) | Medium |
| 9 | **If the network is kept:** split by date, freeze course difficulty as of the cut (or target rated time), validate against `trend` not the last race, drop the school label number, store the training cutoff in `model.pt` | Makes its validation mean something | Medium; one extraction and a retrain | — |
| 10 | **Replace the no-rating fallback** with a prior: the last rating at the previous level through the HS→college map, or the pool median with a wide band | Removes the last runtime need for `model.pt` | Medium; needs a freshman backtest | Medium |

**Ways to be clearly better than Kailo, in order:**

- Fix the level (#1, #2): their whole lead is level, and our level-free
  spread is already better.
- Fix the college runners still on high-school form (#8): that is where
  their order edge comes from.
- Publish calibrated bands and win odds that we score in public (#4–#6).
- We cover high school and middle school too; Kailo covers college only.

---

## 10. What is on the branch

All of it is opt-in, or a report:

- `racecast/predict.py`:
  - the `trend` basis (`trendTable`, `trendKey`, `trendShift`,
    `trendApply`), served only under `XCP_PREDICT_BASIS=trend`;
  - `_formShape`, which adds `form_age_days`, `last_days` and
    `season_slope` to every rating entry (new keys only, no value
    changes).
- `scripts/backtest_baseline.py`: §6 and §7 on re-predicted races, held
  out by date, every basis on the same runners, with paired intervals,
  `team_rho` and `--write-table`.
- `scripts/score_frozen_forecasts.py`: the frozen archive scored on every
  §7 metric.
- `scripts/backtest_predictions.py`:
  - race sampling moved into `sampleRaces`, the same SQL, shared with
    the new script;
  - a `trend` row when a table exists. The `blend` line that
    `run_report` reads is unchanged.
- `scripts/kailo_compare.py`: records `trend` beside the other bases; a
  missing basis is now rebuilt rather than read off the served one.
- `scripts/diag_calibration.py`: the gap slot fixed (3 → 4), and its test
  pinned to `transformer.CONTEXT_GAP_INDEX` and to the slot the builder
  actually fills.
- Tests:
  - new: `tests/test_predict_trend.py`, `tests/test_backtest_baseline.py`,
    `tests/test_score_frozen_forecasts.py`;
  - updated: `tests/test_calibration_diag.py`.
