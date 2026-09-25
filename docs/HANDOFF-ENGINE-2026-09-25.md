# Handoff — the engine and the model (2026-09-25)

For whoever picks up the **rating engine** (normalisation → pack → joint
solve → go-live → boards) or the **prediction model** (feature extraction →
training → the predictions page). Site/UI work is out of scope here; see
`HANDOFF.md` and `docs/ISSUES-RUNNING.md` for that.

The owner runs every server command; you cannot reach the box. Give exact,
copy-pasteable commands, prefer scripts over raw SQL (the owner cannot run raw
SQL easily), and say what output to paste back.

---

## 0. Where things stand right now

| # | State | What to do |
|---|---|---|
| 1 | The last pipeline run **failed in `05_backfill_tf`** at the table swap (`relation "results_tf_new" does not exist`). Cause fixed in `710081c` (see §6.1). The drain's staging table survived. | `scripts/resume_merge.py --table results_tf` (report) → `--finish`, then `. deploy/solve_env.sh; XCP_SPORT_LEVEL_POOLS='college=0.0037,hs=0.0092,ms=0.0191,elem=0.0186' bash deploy/run_pipeline.sh --from 6 --skip 08b_ladder,08a_holdout` |
| 2 | **~181k anet athletes have no name/gender** (XC saver wrote blank placeholders; fixed `a5a9009`). 2,838 of the ~4,000 affected meets are 2026 XC. | After #1: `scripts/requeue_blank_athletes.py --apply`, run the scraper, then a pipeline run. |
| 3 | **tfrrs identity was never linked for 2026** (0 of 79,291 XC rows had a person). Linked by hand today (723,799 fan-out rows, 11,624 freshmen, 24,834 minted); now automatic as step `04a_link_tfrrs`. | Nothing — verify after the next run with `scripts/diag_tfrrs_identity.py` (2026 should show persons and ratings). |
| 4 | **Pro ratings inflated ~1.7×** (Nuguse ~208 vs 147.5 college). Fix in the pack, `d944871` (§3.3). | Verify after the next solve: `scripts/athlete_dump.py 30277189`; pro seasons should sit a few points above his college ones. |
| 5 | **The prediction model is stale and trained on mixed scales.** One-scale extraction landed (`260f0f9`); the page serves rating-based times meanwhile (`XCP_PREDICT_BASIS=rating`). | Re-extract + retrain (§5.4). Then consider `XCP_PREDICT_BASIS=guard`. |

The owner's standard solve command (the chain; `--from 5` inside):

```
cd /srv/xc-predictor && git pull
XCP_SPORT_LEVEL_POOLS='college=0.0037,hs=0.0092,ms=0.0191,elem=0.0186' \
SKIP_WAIT=1 SKIP_CURVE=1 SOLVE_SKIP=08b_ladder,08a_holdout \
bash scripts/overnight_fit_pool_solve.sh
```

Run it in `tmux`/`nohup` — SSH drops have killed runs. **Nothing else may
touch `results`/`results_tf` while it runs** (no scraper, no ad-hoc scripts):
the backfill rebuilds those tables and anything written meanwhile is lost, and
long readers block the swap.

---

## 1. The pipeline, end to end

`scripts/overnight_fit_pool_solve.sh` is the chain the owner runs:

```
curve (SKIP_CURVE=1 skips) → ability_deciles → school_levels → level_graph
  → team_identity → team_pool → solve = deploy/run_pipeline.sh --from 5 --skip $SOLVE_SKIP
```

It sources `deploy/solve_env.sh` (the solve's constants: `XCP_DIFFICULTY=bracket`,
`XCP_SPORT_LEVEL`, `XCP_ERA_YEARS`, `XCP_TEAM_POOL=1`, the bracket/gauge knobs).
Logs: the chain's own in `engine/data/overnight/compute/` (`solve.log`,
`00-progress.log`); `run_pipeline.sh`'s per-step logs in `logs/<timestamp>/`
(`summary.log` + one `<step>.log` each).

`deploy/run_pipeline.sh` steps (numeric prefix is what `--from N` compares):

| step | what |
|---|---|
| `02_drop_old` | drops a stale `<table>_old` (always runs) |
| `03_pro_flag`, `03b_age_bands` | pro seasons, age bands |
| **`04a_link_tfrrs`** | new today — every tfrrs row gets a person (always runs, §4) |
| `04_grade_sanity` | grade verdicts → `grade_fix` |
| `04b_wheelchair` | `wheelchair_person` (always runs) |
| `04c_twins`, `04d_gender` | `result_twin`, `person_gender` |
| `04f_weather_fit_*` | weather correction fits |
| `05_backfill_xc` / `_tf` | **normalisation** → `normalized_time` (parallel; the run aborts if either fails) |
| `05b_anchor_repair_*` | anchor repair (always runs) |
| `06_tfrrs_meets`, `06c_impossible` | tfrrs meet names, impossible-race flags |
| `07_pack` | `engine/speed_ratings.py --sport merged --cache --pack-only` |
| `08_golive` | `engine/run_joint.py --golive` — the joint solve + writing ratings |
| `08a_holdout`, `08b_ladder` | diagnostics (the owner skips both) |
| `09_tilt`, `09b_fill` | tilt; `fill_ratings` prices rows the solve did not |
| `10_*` | `build_ranking_results` → `ranking_results`, `athlete_season`, boards |
| `10f_recruits`, `10f2_projection` | recruiting; recruit projections (background, model) |
| `11_teams` … `13*` | teams, courses, panels, **`13b_pool_consts`** (HS-equivalent constants), search, sitemap |
| `15/16_rowguard*`, `17_checklist`, `18_vacuum` | row guards, checklist, vacuum |

Env knobs worth knowing: `XCP_SPORT_LEVEL_POOLS` (per-level TF↔XC gap; the
owner passes it on the command line, not yet default — see §7),
`XCP_JOINT_KERNELS=0` (numpy matvec instead of the fused numba kernel),
`XCP_PACK_PREFETCH=0`, `XCP_SWAP_ATTEMPTS` (backfill swap retries),
`XCP_LINK_TFRRS=0` (skip §4), `XCP_IGNORE_BACKFILL_FAIL=1` (don't abort after
a failed backfill — don't use it casually).

---

## 2. What a rating is

```
normalized_time = raw time × distance factor(pool) × geometry × weather × era   (backfill)
rating          = 100 × pool_mean / adjusted                                    (go-live)
adjusted        = normalized_time / venue & day & distance-offset terms        (joint solve)
```

- **Per-pool anchors.** `normalized_time` is a time at the pool's anchor
  distance (`engine/fit_distance_exponent.POOL_TARGET_METERS` →
  artifact `pool_targets`): elem 2414, **ms 3200, hs 5000, college_m 8000,
  college_f 6000**; everything else (incl. `pro_*`) the global 5000. **The
  anchor is a property of the pool the BACKFILL used**, which is not stored on
  the row. `normalize_distance.normPoolFor` is that pool (shared by the backfill
  and the model's extraction since today).
- **The pack moves each row onto the pool it is RATED in**
  (`speed_ratings.rescaleToPool`): it identifies the stored scale from
  raw/normalized and rescales. Since `d944871`, a `pro_*` row is moved onto its
  **college twin's** scale (`_scalePool`), because…
- **Pros are rated against the college mean** (`pair_write_results._proScaleMap`):
  `pro_m` rows read `college_m`'s `pool_mean`. They keep the pro pool (off every
  board). The backfill never normalises a row as pro (`poolFor` has no pro
  level), so pro rows arrive on whatever anchor their grade/school/season gave
  them — for Nuguse the 5K one, hence the 1.7× before today's fix.
- **Joint solve:** `engine/joint_solve.py`; `_Operator.matvec` uses the fused
  numba kernel `engine/joint_kernels.fusedMatvec` (rows sorted by athlete,
  chunked on athlete boundaries, padded private accumulators — see its header
  for the false-sharing story). ~1,560 CG iterations.
- **Go-live:** `engine/joint_golive.buildLive` → per-row ratings, `rating_pool`
  stamped on every rated row, `engine_scale` per pool. The per-sport gain
  shift is added to track rows' effect.
- **Fill:** `engine/fill_ratings.py` prices unsolved rows with a per-pool
  constant recovered from solved rows (median rating × normalized_time).
- **Boards:** `racecast/build_ranking_results.py` — `isRankablePool` refuses
  `pro_*` and unknown-gender pools; `athlete_season.mean_rating` is the 80th
  percentile of a season's race ratings.
- **HS-equivalent view:** `racecast/pool_view.py` — `hsFactor`/`repFactor`,
  constants in `racecast/pool_constants_cache.json` (warmed by `13b`;
  `scripts/warm_pool_constants.py` / `pool_view.py --fresh`).

**Seasons:** one academic clock, August–July, named for the year it opens
(`engine/season_year.py`). A TF season is **displayed** as year + 1. For pros
this splits the Diamond League season (Aug–Sep races land in the next TF
season) — known, deliberate, open (§7).

---

## 3. Today's engine changes (2026-09-25), in case something regresses

| commit | change |
|---|---|
| `c75bd78`, `9b4d819` | anet TF non-finish sentinel is **SortInt 20,000,000 → stored 20000.002 s**. `scripts/result_status.py` (`TF_SENTINEL`, `isSentinelTime` = ±1 s window, `timeFromSortInt`) is the one rule; savers, TF backfill and pages use it. `scripts/repair_tf_sentinel.py` cleared 43,852 stored rows (log in `tf_sentinel_repair`). |
| `edf6eeb` | **reverted** an attempted pro-anchor alias in `normalize_distance` (it only moved the pro pace band and could have dropped pro rows from the solve). |
| `d944871` | **pro rows rescaled onto the college anchor** in the pack (`_scalePool`), and their pace band read there. Lands with the next solve. |
| `260f0f9` | the model's extraction puts every `normalized_time` on **one 5000 m anchor** (§5). |
| `b3f2a78` + `8e2578a` | `04a_link_tfrrs` / `scripts/link_tfrrs_rows.py` (§4). |
| `710081c` | **backfill swap** can no longer roll back the rebuilt table (§6.1). |
| `a5a9009` | **anet XC saver** writes athletes' names and gender (§4.3). |

---

## 4. Identity (who a row belongs to)

`person_id` on `results`/`results_tf` is the athlete. anet rows are seeded
`person_id = athlete_id` at insert. tfrrs rows arrive with **no** person and
get one only from linking passes.

### 4.1 `04a_link_tfrrs` = `scripts/link_tfrrs_rows.py --apply`

Three passes, every stamped row logged in `person_link_log`
(`rule` = `fanout` / `freshman` / `mint`, `from_person` 0 = had none), each
reversible with `--undo <rule>`:

1. **fan-out** — rows of a tfrrs athlete id (`native_id`, per `id_system`)
   get the one person that id's already-stamped rows agree on.
2. **freshmen** — `scripts/link_freshmen.py`: a tfrrs first-year (college
   freshman grade, no anet row, first race that season) ↔ an anet grade-12
   senior of the previous season, same normalised name, **unique on both sides
   that year**, genders agreeing. Keys are `p:<person>` or
   `n:<id_system>:<native_id>`.
3. **mint** — an id with no person anywhere, first seen this season, gets
   `1,000,000,000 + native_id` (`1,500,000,000 +` for directathletics): above
   every anet id, within int4.

`scripts/diag_tfrrs_identity.py [--school X]` — per season: rows with a person,
tfrrs id only, no id, normalized, rated. **The first thing to run when
anything tfrrs looks unlinked or unrated.**

### 4.2 Cross-feed twins

A race scraped by both feeds is two meets joined by `canon_meet_id`. The
backfill keeps the anet copy and skips a tfrrs row whose `(person_id,
canon_meet_id)` has an anet row (per person per meet, **not per event** —
`scripts/diag_unnormalized.py --school X` measures whether that loses events;
for Tufts it lost none). Race pages borrow person/rating from the twin
(`app._borrowTwins`).

### 4.3 anet athletes

The XC save path writes athletes only through `saveResultsBulk`'s FK
placeholder; until `a5a9009` that placeholder was blank, so new athletes had no
name (**Unknown**) and no gender (**unknown-gender pool → inflated ratings**).
Both athlete writes now fill blanks on conflict. Existing blanks: re-scrape via
`scripts/requeue_blank_athletes.py`.

---

## 5. The prediction model

Files: `model/feature_extraction.py` (corpus → chunks), `model/train.py`,
`model/transformer.py` (`XCPredictor`, `predictInterval`, `baselineSeconds`),
`racecast/predict.py` (serving), `racecast/race_sim.py` (Monte Carlo on the
per-runner bands), `racecast/build_recruit_projection.py` (projections).

### 5.1 How it predicts
A sequence of the athlete's prior races (21 features each, feature 0 =
`normalized_time`) + a context vector for the target → `baseline × exp(mu)`
with a log-normal band. `_predictTimes` converts the normalized prediction to
the target's clock (`_targetClock`: the athlete's own raw/normalized ratio at
that distance, else the conversions curve).

### 5.2 One scale (`260f0f9`)
Per-pool anchors made each history a mixture (ms 3200-, hs 5000-, college
8000-equivalents). `feature_extraction.toCommonScale` moves every row onto
5000 m with its own pool's curve (`normalize_distance.anchorShift` ×
`normPoolFor`), keeping the stored value as `normalized_time_pool`.
`metadata.pkl` records `norm_scale`; `train.py` copies it into
`target_stats.pkl`; `predict._normScale()` feeds a loaded model the scale it
was trained on (a model without the key = old `pool` mixture) and
`_denormContext` shifts a common-scale prediction back onto the pool's anchor.
`XCP_MODEL_NORM_SCALE=pool` extracts the old way.

### 5.3 What the page serves (`racecast/predict._servedTimes`)
`XCP_PREDICT_BASIS`:
- **`rating` (default)** — every runner with a rated race gets a time from
  their recent race ratings (`_ratingTimes`: last ≤6 in their latest pool,
  target sport first, falls dropped, recency-weighted; band 2.5–6%, +2%/yr when
  older than a year), converted to the course by `conversions`. The model only
  for runners with no rated race. Reason: the stale model runs slow for
  everyone, and mixing bases gave some runners a head start.
- `guard` — keep the model's time when within 8% of the rating time and its
  band < 8%; else the rating time.
- `model` — raw network.
Only the page is guarded; projections and `scripts/diag_model_quality.py` call
`_predictTimes` directly.

### 5.4 Retraining (owner runs it; HANDOFF.md §15 has the pod details)
```
cd /srv/xc-predictor && git pull
setsid nohup /srv/venv/bin/python -u model/feature_extraction.py > /srv/extract.log 2>&1 </dev/null &
# copy chunks to the pod, smoke, then the full run (HANDOFF.md §15)
```
Go/no-go: the epoch line's `model X%` must beat `last-race Y%`. After a
one-scale model is live, compare `diag_model_quality` against the rating basis
before switching `XCP_PREDICT_BASIS` to `guard`.

---

## 6. Traps that cost runs

1. **The backfill merge is one transaction until the swap.** Build heap +
   indexes + swap run on one connection; `_swapWithRetry` now commits the build
   before its first attempt. If a run still dies after the drain, the staging
   table (`bf_staging_<sport>`) survives: `scripts/resume_merge.py --table
   <t>` → `--finish`. `_assertNoLeftovers` refuses to start with a
   `<table>_old`/`_new` present.
2. **Ctrl-C does not stop a server query.** An interrupted script's query keeps
   running and holds locks. `scripts/slow_queries.py` lists them;
   `--terminate PID` ends them.
3. **Don't scrape during a backfill** (rows land in the old table and are lost).
4. **Anchors:** any code that reasons about the scale of `normalized_time` must
   ask which pool wrote it (`normPoolFor`) or which pool rates it
   (`_scalePool`), never assume 5000.
5. **Sentinels:** anet XC 999999, anet TF 20000.002, tfrrs NULL + status
   letters. Use `result_status`, never a new magic number.
6. **`person_id` width** is unknown on the server — keep minted ids < 2³¹.
7. **TF season labels are academic year + 1** everywhere a person reads them.

---

## 7. Open, in the order the owner raised them

1. **Verify the pro fix** (§0 #4). If pros are still high, compare the dump's
   `normalized_time / time_seconds` against the college rows.
2. **TF underrated** — `XCP_SPORT_LEVEL_POOLS` is still passed by hand;
   `scripts/sport_level_fit.py` measures it. Decide whether it becomes the
   default in `deploy/solve_env.sh`.
3. **Pro TF season clock** — Aug–Sep Diamond League races fall into the next
   season. The fix is a TF-specific seam (Aug–Sep close the previous season)
   in `engine/season_year.py`; needs a re-pack. Owner's call.
4. **Monte Vista HS 3200 m at +19% difficulty** (2026-08-21, Cal v MV dual) —
   likely a mis-measured course the engine absorbs as difficulty. Check a
   runner's other ratings; if ~10 lower, add a distance override.
5. **Retrain the model** on one scale (§5.4).
6. **The backfill's twin rule is per person per meet, not per event** —
   harmless for Tufts, unmeasured elsewhere (`diag_unnormalized.py`).
7. **Freshman linking quality** — spot-check `person_link_log` rule
   `freshman`; `--undo freshman` if it welded strangers.
8. Championship projections and "fastest races" (owner likes both; not
   started) — rating-based field → `race_sim.simulate`.

---

## 8. Diagnostics toolbox (all read-only unless noted)

| script | answers |
|---|---|
| `scripts/athlete_dump.py <person_id>` | everything about one athlete: seasons, every race's terms (`normalized_time`, `rating_pool`, rating) |
| `scripts/diag_tfrrs_identity.py [--school]` | tfrrs rows linked / rated per season |
| `scripts/diag_unnormalized.py --school X` | why a school's tfrrs track rows have no `normalized_time` |
| `scripts/slow_queries.py [--watch] [--terminate PID]` | what the DB is running now (terminate writes) |
| `scripts/diag_model_quality.py` | model vs baseline vs actual |
| `scripts/sport_level_fit.py` | the TF↔XC level gap per pool |
| `scripts/warm_pool_constants.py` | HS-equivalent constants, old → new |
| `scripts/add_page_indexes.py [--check]` | builds missing indexes CONCURRENTLY (writes) |
| `scripts/resume_merge.py --table T [--finish]` | finish a crashed backfill merge (writes) |
| `scripts/link_freshmen.py`, `scripts/link_tfrrs_rows.py` | identity passes (dry run by default) |
| `scripts/requeue_blank_athletes.py` | blank anet athletes → meets to re-scrape (`--apply` writes) |
| `scripts/repair_tf_sentinel.py` | TF sentinel survey / repair |
| `scripts/page_forensics.py` | a corrupted heap page (the 2026-09 bit flip) |

---

## 9. Conventions

- Comments explain WHY, with the owner's words and the date when a rule came
  from them (`★` design, `⚠` trap, `!` subtlety). Match the density around you.
- Every behaviour change gets a test in `tests/`; many tests assert on source
  text, so renaming a pinned line means updating its test (`test_predict_school_state`,
  `test_race_school_state`, `test_predict_scale_view` did today).
- Known pre-existing failures (not yours): `test_school_logos` (6),
  `test_nonfinish_pool_600` (1), `test_meet_units`, `test_pipeline_shards`,
  `test_pipeline_step_guards`, `test_recruiting_athlete` (2),
  `test_reference_pin`, `test_calibration_diag`, `test_model_contract`,
  `test_train_smoke` (2). Compare before/after with `git stash`.
- Work on `claude/nice-fermi-y7b42a`; the owner asked for every commit to be
  fast-forwarded to `master` too (`git push origin HEAD:master` after checking
  `git merge-base --is-ancestor origin/master HEAD`).
