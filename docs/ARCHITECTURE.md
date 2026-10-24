# racecast.co: how it fits together

A guide for an engineer new to the repo, meant to be read in about 30
minutes. It describes the code as it stands on `master` in late October 2026.
The files named here are the place to go next; the HANDOFF, PLAN and
RUNBOOK files in `docs/` are dated logs, not reference.

The table map was built from reading the code, not from the database, so a
table that exists only on the server (built by hand once) may be missing.
Line numbers drift; search for the function name.

---

## 1. The system in one picture

```
 athletic.net  TFRRS             ERA5 (weather)           owner / users
     │           │                     │                        │
 scripts/launcher.py   tfrrs/driver/   backfill/atmost_era5_zarr.py   racecast/uploads.py, fixes.py
 scripts/scrape_results.py  run_tfrrs.py    │                        │ (on the owner's Approve)
     │           │                     │                        │
     ▼           ▼                     ▼                        ▼
 ┌────────── RAW TABLES (Postgres, ~200 GB) ───────────────────────────────┐
 │ meets, results (XC)   meets_tf, meets_tf_meta, results_tf (track)       │
 │ meets_tfrrs, athletes, meet_extras, meet_queue, weather_grid            │
 └──────────────────────────────────────────────────────────────────────────┘
     │  steps 00-04: fix-ups in place (dates, venues, who-is-who linking)
     │               and "verdict" tables (grade_fix, pro, twins, gender...)
     ▼
  step 05  backfill/backfill_normalize.py  → results*.normalized_time
           (distance curve, era, weather, geometry, engine/corrections.py)
     │
  step 07  engine/speed_ratings.py --pack-only → engine/data/packed_XC_TF.npz (a file)
     │
  step 08  engine/run_joint.py --golive  (joint solve + bracket engine)
           → course_difficulties, athlete_ratings, results*.speed_rating,
             race_day_effect, engine_scale, ...
     │
  step 09b engine/fill_ratings.py → prices the rows the solve left out
     │
  steps 10-13  racecast/build_*.py → board and page tables
           ranking_results, athlete_season, season_rank, team_season,
           course_boards, homepage_panels, search_index, ...
     │
     ▼
  racecast/app.py (Flask, gunicorn ×8) → nginx → Cloudflare → racecast.co
```

Two ways the data moves:

- **The full pipeline** (`deploy/run_pipeline.sh`, about 90 named steps,
  roughly 9 to 10 hours; run by hand under tmux) re-normalises every row,
  re-solves every course and athlete, and rebuilds every board.
- **The nightly** (`deploy/nightly_update.sh`, Mon-Sat by a systemd timer)
  scrapes both feeds, links and flags the new rows, normalises only the rows
  nobody has normalised (`--new-only`), prices them at their course with the
  last full run's numbers (`fill_ratings --venue`), and rebuilds the boards
  and search. **No solve runs nightly**, so course difficulties and athlete
  levels move only on a full run, and the next full run overwrites every
  nightly price.

---

## 2. The repo

| Directory | What is in it | Size |
|---|---|---|
| `racecast/` | The website: `app.py` (Flask, ~12,800 lines), ~120 helper modules, the `build_*.py` board builders the pipeline runs, `templates/` (87), `static/` (32 JS files plus images) | 123 .py |
| `engine/` | The rating engine: pack, solve, go-live, the distance/era/weather corrections, the verdict steps (pro, twins, wheelchair, gender, grade sanity), and many fitters and diagnostics | 128 .py |
| `backfill/` | Normalisation (`backfill_normalize.py`), the ERA5 weather fetcher, elevation | 8 .py |
| `scripts/` | Everything else: the anet scraper, the DB layer (`database.py`, `config.py`), the linkers, the pipeline's smaller steps, reports, and ~240 hand-run diagnostics and one-offs | 387 .py, plus 55 data files |
| `tfrrs/` | The TFRRS scraper: `driver/` (queue walker), `scraper/` (fetch and save), `parser/`, `sweep/` | 18 .py |
| `sources/` | The start of a multi-source scraper (timing-company sites). Caches pages in `raw_doc`; nothing downstream reads it yet | 5 .py |
| `model/` | The transformer that predicts times (`transformer.py`, `train.py`, `feature_extraction.py`) | 5 .py |
| `deploy/` | The two pipelines, the model's settings (`solve_env.sh`), server setup, timers, nginx, backups | 21 files |
| `tests/` | ~500 pytest files and a few node tests; `python -m pytest -q tests/` or `bash scripts/run_tests.sh` | |
| `docs/` | Dated handoffs, plans, runbooks and the running issue log; `ENGINE.md` describes the engine | |

**How imports work.** There are no packages. `racecast/`, `scripts/` and
`engine/` are each put on `sys.path` (gunicorn's `--pythonpath` in
`deploy/server_setup.sh`; `sys.path.insert` at the top of most scripts), and
modules import each other by bare name: `from database import getConn`,
`import normalize_distance`. Module names must therefore be unique across
those three directories (today they are). Several racecast modules import
from `app` inside a function (`cards.py`, `pages3.py`, `upcoming_preview.py`)
to avoid a circular import.

**Files that are not in git.** `engine/corrections.py` (~165 MB, generated:
the distance overrides, result drops and pins) travels by scp; the pipeline
refuses to start without it, because a missing file silently un-applies every
correction. `/etc/xc-predictor.env` holds the database credentials and
production switches. `scripts/config_local.py` is a developer's local DB
config.

---

## 3. The pipeline, step by step

The steps are shell functions in `deploy/run_pipeline.sh`:

- `step NAME cmd...` runs one step, logs it to `logs/<run>/<NAME>.log`
  (each line time-stamped) and appends `NAME ok (Ns)` or `FAILED` to
  `logs/<run>/summary.log`. A failure is recorded and the run carries on,
  except at the hard gates below.
- `steps2` / `stepsN` run two or N independent steps at once; `shards`
  splits one builder across N processes; `bgstep` starts a step in the
  background, collected later by `bgjoin` / `bgwait`.
- `--from N` skips steps whose number is below N, except the steps on the
  `_ALWAYS` list (linkers, wheelchair, anchor repair, integrity), which must
  run on every run. `--skip a,b` leaves out named steps. `--dry-run` prints
  the plan.
- One run at a time: a `flock` on `.pipeline.lock`, shared with the nightly.
- Every step runs under `nice`/`ionice` and `XCP_DB_QUIET=1`, so the site
  keeps priority.

The model's settings (`XCP_DIFFICULTY=bracket`, the sport level, two-year
eras, altitude, the indoor level...) are in `deploy/solve_env.sh`, which the
pipeline sources on every run. A value given on the command line wins.
`deploy/solve_env_v2.sh` is the "simpler model" set, kept for a comparison
run.

| Phase | Steps | What happens | Main code |
|---|---|---|---|
| Preflight | 00_integrity, 00b_run_snapshot | amcheck every page; snapshot last run's numbers for the "what changed" report | `scripts/integrity_check.py`, `run_changes.py` |
| Dates and seasons | 00_meet_dates, 01_season_year | fix impossible meet dates; self-check of the August season seam | `engine/meet_date_fix.py`, `season_year.py` |
| Weather (background) | 04e_weather_grid | fill `weather_grid` from ERA5 for every venue-day the meets need | `backfill/atmost_era5_zarr.py` (own venv `/srv/wxvenv`) |
| Who is who | 01a, 02_drop_old, 02b, 04a, 04a2, 04a2b, 04a4, 04a3, 04a3b | venue names; link TFRRS rows, teamless rows and feed twins to people; undo splits; the owner's pins and merges. Every person-id move is logged in `person_link_log` | `scripts/link_*.py`, `person_pins.py`, `engine/unlink.py` |
| Verdicts | 03_pro_flag, 03b_age_bands, 04_grade_sanity, 04b_wheelchair, 04c_twins, 04d_gender | who is a pro, which grade is wrong, wheelchair athletes, duplicate rows across feeds, gender. **Gate:** if any fails, the run stops before the backfill | `engine/pro_flag.py`, `grade_sanity.py`, `wheelchair_flag.py`, `twin_flag.py`, `person_gender.py` |
| Overrides | 04g0, 04g1, 04g, 04g2 | pin men's races filed at the women's distance; data checks; re-judge every distance override; dump `dist_override` from `corrections.py` | `engine/gender_pace_pin.py`, `data_checks.py`, `audit_overrides.py`, `dump_overrides.py` |
| Normalise | 05_backfill_xc/tf (parallel), 05b_anchor_repair, 05c_pro_ability | write `normalized_time` for every row; fix rows written on the wrong pool's scale. **Gate:** a failed backfill stops the run | `backfill/backfill_normalize.py`, `engine/normalize_distance.py`, `engine/anchor_repair.py` |
| Courses | 06_tfrrs_meets, 06a, 06b, 06c | TFRRS meet names and GPS; canonical course ids; races faster than the world record | `scripts/land_tfrrs_meet_names.py`, `backfill_tfrrs_location.py`, `build_course_canonical.py`, `engine/impossible_race.py` |
| Pack | 07_pack | read every rated row into `packed_XC_TF.npz` | `engine/speed_ratings.py`, `speed_ratings_db.py` |
| Solve and go-live | 08_golive (+ optional 08a holdouts, 08b ladder, 08c/08d diagnostics, run in the background) | the joint solve (abilities, season curve, tilt, sport level, race-day terms) and the bracket engine (course difficulties); writes the live rating tables. **Gate:** a failed go-live stops the run. ~2.5 h | `engine/run_joint.py`, `joint_solve.py`, `bracket_engine.py`, `joint_golive.py` |
| After the solve | 09b_fill, 09c_school_names, 09d_outliers | price excluded rows; school aliases; flag outlier ratings | `engine/fill_ratings.py`, `scripts/merge_school_names.py`, `engine/rating_outliers.py` |
| Boards | 10_rankings (prepare, 4 shards, finish), 10a-10g2, 11_teams, 11b_indexes, 12, 12b | the board tables every page reads (see §4) | `racecast/build_*.py` |
| Site extras | 13_panels, 13f, 13g, 13b, 13c0, 13c, 13d, 13e, 13h... | home page panels, breakouts, social posts, search, sitemap, IndexNow, follow alerts, meet forecasts | `racecast/panels.py`, `build_breakouts.py`, `search_index.py`, `build_sitemap.py`, `follow_alerts.py` |
| Row guard | 15_rowguard_*, 16_rowguard_apply | find suspect rows and write drops into `corrections.py` **for the next run** | `scripts/diag_suspects.py`, `triage_suspects.py`, `apply_triage.py` |
| Reports | 17_checklist, 17b_status, 16b/c/d, 17a, 17c0, 17c, 17d, 17e | checklists, external comparisons (LACCTIC, Kailo), the run report, the watchdog mail | `scripts/run_checklist.py`, `run_report.py`, `racecast/watchdog.py` |
| Tidy | 18_vacuum, 18b_integrity_boards | vacuum; amcheck the new boards | `scripts/gentle_vacuum.py`, `integrity_check.py` |

**Things that are run by hand, not by either pipeline**, and that the
pipeline depends on: the fitters that write `engine/data/*.pkl` (distance
spline, era curve, geometry, weather when `XCP_WEATHER_FIT=0`, school levels:
`engine/fit_*.py`, `scripts/build_school_levels.py`); `athlete_season_level`
(`engine/season_level.py`, listed in `scripts/owner_checks.sh`);
`venue_elevation`; `tfrrs_meet_geometry`; `college_directory`; the `weather`
table that race pages show. Model training (`model/train.py`) runs on a
rented GPU.

---

## 4. The main tables

S = rebuilt as `<table>_new` and swapped in atomically (`racecast/dbfast.py:
swapTable` or a hand-rolled rename). I = written in place.

### Raw data (written by the scrapers)

| Table | A row is | Written by | Main readers |
|---|---|---|---|
| `meet_queue` | one scrape job (meet, sport, source), state 0-4 | `scripts/queue_meets.py`, the launchers, `tfrrs/sweep/`; the site's fix form (`racecast/fixes.py`) | `scripts/launcher.py`, `tfrrs/driver/run_tfrrs.py` |
| `athletes` | one athletic.net profile | `scripts/database.py: saveAthletesBulk` | athlete page, search, pack |
| `meets` | one XC **division** (`div_id` is the key) | scraper `saveMeet`; uploads; 00_meet_dates | almost everything |
| `results` | one XC performance, both feeds (`source`), carrying `person_id`, `normalized_time`, `speed_rating`, `rating_pool` | scraper; `tfrrs/scraper/save_tfrrs.py`; uploads; linkers (person_id); 05 (normalized_time); 08/09b (speed_rating) | race and athlete pages, backfill, pack, boards |
| `meets_tf`, `meets_tf_meta`, `results_tf` | the track equivalents: one (div, event), one meet's venue/GPS/track, one performance | the same writers as XC; 02b and 06 fill venue and names | the same readers as XC |
| `meets_tfrrs` | one TFRRS meet per sport | `save_tfrrs.py`; 06a (GPS) | race pages, backfill |
| `meet_extras` | JSON teams and event types per meet | both scrapers | meet pages |
| `weather_grid` | one ERA5 hour per grid cell | 04e | 05, the weather fit, `racecast/forecast.py` |

### Verdicts and overrides (steps 03-06)

| Table | Written by | Read by |
|---|---|---|
| `grade_fix`, `pro_athlete_season`, `age_band_result`, `wheelchair_person`, `result_twin` (S), `person_gender` (S), `impossible_result` (S) | 04, 03, 03b, 04b, 04c, 04d, 06c | backfill, pack, boards, pages (they anti-join these) |
| `course_canonical` | 06b (`--incremental` keeps ids) | pack, fill, course pages |
| `dist_override`, `dist_drop` | 04g2, dumped from `engine/corrections.py` | pack, boards, race pages (the backfill imports `corrections.py` directly) |
| `person_link_log`, `person_redirect`, `person_probe` | linkers; 01a/13c0 | athlete page 301s, sitemap |

### The solve's output (step 08, `engine/joint_golive.py: writeLive`)

| Table | A row is | Read by |
|---|---|---|
| `course_difficulties` (I) | one solved course cell (`XC:`/`TF:` name, canonical id, distance, difficulty) | course and race pages, boards, fill |
| `athlete_ratings` (I) | one season rating per (person, pool) | athlete page, cards, conversions |
| `results*.speed_rating`, `rating_pool` | per-row rating, rewritten through a heap rebuild and swap (`speed_ratings_db.mergeColumn`) | every page |
| `race_day_effect` (S), `engine_scale`, `distance_offset`, `sport_gain` | race-day credit, scale constants | race pages, `racecast/conversions.py` |

### Boards and pages (steps 10-13, all S unless noted)

| Table | Built by | Read by |
|---|---|---|
| `ranking_results`, `athlete_season` | 10_rankings (`racecast/build_ranking_results.py`, swapped together) | `/rankings` and `/api/rankings` (`racecast/rankings.py`), athlete, school, teams, predictions, most later builders |
| `season_rank` (S), `season_rank_weekly` (I) | 10g, 10g2 | athlete page ranks, movers, my page |
| `school_identity` and four sibling tables | 10b (five renamed in one transaction) | schools directory, search, boards |
| `school_unit`, `meet_unit` | 10d, 10e | board filters, meets list |
| `team_season` | 11_teams | team boards, school pages |
| `course_rank`, `course_boards` | 12, 12b (3 shards) | course list, course page |
| `homepage_panels` / `_meta` / `_recent` (I) | 13_panels | home page |
| `search_index` + `athlete_named` / `_agg` / `_school`, `meet_agg_xc` / `_tf` | 13c0, 13c | search |
| `breakout_rows`, `college_recruit`, `recruit_projection`, `season_comps`, `meet_forecast`, ... | 13f, 10f, 10f2, 10f3, 13h | the feature pages |

### Written by the site

Accounts and auth (`account`, `login_token`, `account_session`, ...:
`racecast/accounts.py`), follows and alerts (`account_follow`, `alert_item`,
...: `follows.py`, `shortlist.py`, and 13g), user submissions and telemetry
(`fix_request`, `result_upload`, `shared_prediction`, `prediction_snapshot`,
`issue_reports`, `usage_daily`, `app_error`). An approved upload writes into
`meets` and `results` (`racecast/uploads.py: applyUpload`).

### Things to know about the tables

- **Some builders read the previous run's tables.**
  `build_ranking_results` reads `school_identity` and `school_unit`, which
  are rebuilt later in the same run (10b, 10d); `grade_sanity` reads last
  run's `result_twin`. The code says so at each place.
- **`results*.speed_rating` has several writers**: the go-live, the fill
  (full and nightly), and the legacy engine (`XCP_JOINT_LIVE=0`). Two
  different heap-rebuild swaps act on the same 35M-row table, which is why
  `02_drop_old` clears `results_old` first.
- **Schemas not in the repo**: `ranking_results`/`athlete_season` (the
  shadow table is cloned from the live one; `migrate_rankings.sql` is not
  in git) and `weather_grid` (no CREATE TABLE anywhere). The core raw
  tables are created in `scripts/database.py`.

---

## 5. The request path for the main pages

All pages render through Jinja templates that include `_topbar.html`, which
loads `layout.js`, `link-flash.js`, `rating-explain.js`, `topbar-search.js`
and `usage.js`. `app._headers` sends `public, max-age=300, s-maxage=900` on
every public 200 GET, so Cloudflare serves most traffic; `/api/*` is
`no-store` unless a route says otherwise. Each request gets one pooled
connection, parked on `flask.g` (`racecast/db_timing.py: getConn`).

| Page | URL → view | What it reads | Template and JS |
|---|---|---|---|
| Athlete | `/athlete/<id>` → `app.athlete` (~720 lines) | `get_races` (every result across both sports and feeds), `athlete_season`, `athlete_ratings`, `season_rank`, `ranking_results`, the verdict tables; helpers in `athlete_bests`, `athlete_chart_data`, `pool_view`, `season_floor`, `cuts` | `athlete.html`; `athlete-charts.js`, `comps-fan.js`, `follow.js`, `race-page.js`, `scale-view.js`, `share.js` |
| Race (XC) | `/race/xc/<meet>/<div>` → `app.race_xc` | `get_race_header`, `get_race_results`: `results`, `meets`, `course_difficulties`, `race_day_effect`, `dist_override`, `result_twin`, `weather` | `race.html`; `race-page.js`, `equiv-line.js`, `above-level.js` |
| Race (TF) | `/race/tf/<meet>/<event>/<div>` → `app.race_tf` | the TF equivalents plus `meets_tf_meta`, `meet_extras` | `race_tf.html` |
| Meet | `/meet/xc/<meet>`, `/meet/tf/<meet>` → `app.meet_xc` / `meet_tf` | the divisions, `meet_compile.py` for team scores | `meet.html`, `meet_tf.html` |
| Course | `/course/<name>` → `app.course` | `course_boards` (precomputed at 12b); falls back to `buildCourseCtx` live, cached 6 h | `course.html` |
| Boards | `/rankings` → `rankings_page`; `/rankings/<sport>/<pool>[/<st>]` → `rankings_landing` | the page is a shell; `rankings.js` calls `/api/rankings`, `/api/teams`, `/api/courses`, `/api/rankings/rank` (`racecast/rankings.py`, `teams.py`, `courses.py` over `ranking_results`, `athlete_season`, `team_season`); responses cached in `ttlcache` and at the edge | `rankings.html`, `landing.html`; `rankings.js` |
| Predictions | `/predictions` → `predictions_page`; JSON at `/api/predict/*` | `racecast/predict.py` (the default basis is the rating, `XCP_PREDICT_BASIS`; the transformer is opt-in); results cached in `prediction_snapshot` for 6 h | `predictions.html`; `predictions.js` |
| Meet preview | `/meet/preview/xc/<id>` → `pages3` blueprint | `meet_forecast` (13h) via `upcoming_preview.py` | `meet_preview.html` |
| Share cards | `/card/*.png` → `card_routes` blueprint | `racecast/cards.py` draws and caches PNGs on disk | — |

`app.py` holds about 85 routes (88 `@app.route` lines). 19 modules are
already blueprints: accounts, follows, my_page, shortlist, team_meets,
uploads, fixes, social, monitor, pages2, pages3, cuts, state_odds, scale,
goal, h2h, meet_track, ncaa_pages and card_routes. Most are registered in a
block near the end of `app.py`.

---

## 6. Where each concern lives

| Concern | Where |
|---|---|
| DB connection, pool, swaps | `scripts/config.py` (credentials from env), `scripts/database.py` (pool, `getConn`, core DDL, scraper saves), `racecast/db_timing.py` (per-request connection, query timing), `racecast/dbfast.py` (`swapTable`, fast COPY) |
| athletic.net scraping | `scripts/launcher.py` (the Chrome driver, queue walker), `scrape_results.py`, `scraper.py`, `queue_meets.py` |
| TFRRS scraping | `tfrrs/driver/`, `tfrrs/scraper/`, `tfrrs/parser/` |
| Linking people | `scripts/link_tfrrs_rows.py`, `link_teamless.py`, `link_feed_twins.py`, `link_profile_school.py`, `person_pins.py`, `link_freshmen.py: normName` (the name key) |
| Seasons and grades | `engine/season_year.py` (the August seam), `engine/grade_sanity.py`, `racecast/grade_label.py` |
| Which pool a row is rated in | `engine/pool_resolve.py: resolvePool` (shared by engine and site) |
| Event names → metres | `engine/event_parse.py` (the backfill's parser); `normalize_distance.parseEventShort` also gives the event's kind |
| Normalisation | `backfill/backfill_normalize.py` (the driver) and `engine/normalize_distance.py` (curves, loaded from `engine/data/*.pkl`) |
| Overrides and drops | `engine/corrections.py` (generated, not in git), `engine/audit_overrides.py`, `dump_overrides.py`, `scripts/triage_suspects.py`, `apply_triage.py` |
| The solve | `engine/run_joint.py` (driver, flags), `joint_solve.py`, `bracket_engine.py`, `joint_golive.py`; `docs/ENGINE.md` for the maths |
| Model settings | `deploy/solve_env.sh`; switches are read with `os.environ.get("XCP_...")` where they are used (about 250 of them) |
| Boards | `racecast/build_ranking_results.py` and the other `build_*.py`; served by `rankings.py`, `teams.py`, `courses.py`, `pool_view.py` |
| Rating ↔ time conversions | `racecast/conversions.py`, `scale.py`, `explain_rating.py` |
| Predictions | `racecast/predict.py`, `race_sim.py`, `forecast.py`, `model/` |
| Time display | `racecast/time_format.py: format_time` |
| School names and identity | `racecast/school_name.py`, `school_identity.py`, `build_school_identity.py`, `school_logo.py` |
| Accounts, follows, uploads | `racecast/accounts.py`, `follows.py`, `my_page.py`, `uploads.py`, `fixes.py` |
| Monitoring | `racecast/watchdog.py` (run checks, mail), `monitor.py`, `usage.py`, `app_errors.py`, `site_status.py` |
| Server | `deploy/server_setup.sh` (gunicorn unit, nginx), `install_*_timer.sh`, `backup_db.sh`, `origin_firewall.sh`, `docs/CLOUDFLARE.md` |

---

## 7. Conventions you will meet

- **★ and ⚠ comments.** A `★` block records a decision, usually the
  owner's, with its date and often a quote; `⚠` and `!` mark a trap that
  has already cost a run. They are the project's record of why. When you
  move code, move its comments with it.
- **Swap, don't update.** Big tables are rebuilt as `<t>_new` and renamed
  over the live one, so the site never reads a half-built table. A failed
  swap leaves `_new` behind and the rerun resumes from it.
- **Schema probes.** `app.py` checks once per process whether optional
  tables and columns exist (`_RESULT_TWIN`, `_RACE_DAY`, ...), so the site
  runs against a database a step or two behind the code.
- **Tests read source text.** Many tests assert that a string or an
  ordering appears in `app.py` or `run_pipeline.sh`. Moving code can fail a
  test without any behaviour changing; update the assertion with the move.
- **Two parallel feeds.** athletic.net and TFRRS rows sit in the same
  tables, told apart by `source`, and the same race can appear in both
  (`result_twin`).
- **"Pool".** A rating is relative to a pool (`hs_m`, `college_f`, ...).
  Every public page shows the HS-equivalent scale; `pool_view.py` converts.

---

## 8. Running it locally

```
pip install -r requirements-site.txt -r requirements-engine.txt -r requirements-scrape.txt
python -m pytest -q tests/              # ~4,300 tests, about 6 minutes, no database needed
bash scripts/run_tests.sh               # the same, plus the node tests
```

The site needs a Postgres with the schema and data; point `XCP_DB_*` (or
`scripts/config_local.py`) at it and run `python racecast/app.py`.
`deploy/db_tunnel.ps1` tunnels to the server's database from a workstation.
