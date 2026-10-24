# Complexity review, October 2026

Reviewed on master at `679cb6a` (2026-10-24), from the code only; no access
to the server or the database. The companion map of the system is
`docs/ARCHITECTURE.md`. The list of every Python file nothing reaches is
`docs/REVIEW-COMPLEXITY-2026-10-unreached.tsv`.

## Findings first

1. **A pipeline bug, not a complexity issue: `--from 7` still skips the
   anchor repair.** `05b_anchor_repair` is on the `_ALWAYS` list because
   `--from 7` is the standard recipe and skipped it (run20). But only
   `step()` checks `_ALWAYS`; `steps2`, `stepsN`, `shards` and `bgstep`
   check `--from` alone, and 05b runs through `steps2`
   (`deploy/run_pipeline.sh:854`). So the repair is still skipped on every
   `--from 6` or later run, which is the failure its own comment describes.
   Related: `steps2`/`stepsN` check `--skip` against the first name only,
   so `--skip 05_backfill_tf` does nothing. The patch is in §3.1. I have
   not applied it, because it changes what a run does.
2. **The dead weight is whole files, not code inside files.** 237 of the
   387 files in `scripts/`, and 41 of the 128 in `engine/`, are reached by
   no pipeline step, route or test. A further 63 are reached only by tests.
   Inside the live modules there is very little dead code: 16 functions in
   all, about 125 lines. No env switch is set without being read, no static
   JS file is unused, and only one template was.
3. **Some duplicated logic gives different answers today.** The track race
   page parses "880" as 880 m while the backfill rated the same rows at
   804.67 m (`app.py` calls `normalize_distance.parseEventShort`; the
   backfill uses `event_parse.distanceFromEventShort`). Five display
   helpers print 959.96 s as "15:60" or "15:59.", one of them on the share
   card images. Three families of person-name keys disagree on accents and
   hyphens.
4. **`app.py` is the cost engineers feel most, and the split is cheap
   except for the tests.** Most of its 85 routes fall into clean groups,
   and 19 modules are already blueprints. What makes moving code expensive
   is that 57 test files assert on `app.py`'s source text, more than 50
   import it, and `_topbar.html`, which every page includes, calls
   `url_for` on six `app.py` endpoint names.
5. **The pipeline's 9h22m is not recorded anywhere in the repo.** The best
   breakdown, from the 2026-10-08 notes, puts about 70% of the time in four
   steps: 08_golive (~2.5 h), 10_rankings (~1.5 h), 04_grade_sanity
   (~1-1.4 h) and 07_pack (~40 min). Merging and reordering the small steps
   would save roughly 30-90 minutes. Beyond that the time is inside those
   four steps. Paste-ready commands to measure it are in §7.

What I changed on the branch `claude/upbeat-goldberg-catrkt` (two commits,
no behaviour change; the test suite gives the same 7 failures as master,
listed in §8):

- The 12 share-card routes moved from `app.py` into a blueprint,
  `racecast/card_routes.py`. The code and comments are verbatim. The URL map
  is identical rule for rule; I compared both and requested every card URL
  on master and the branch.
- Removed what nothing references: 12 functions (listed in §1.3), the
  template `_share.html`, and `overrides/override_distances.py`, a
  half-written stub that is a syntax error (`SANE_RATING_BAND =`).

---

## Ranked recommendations

Benefit: how much easier the code gets to understand, or how much time
or correctness it buys. Risk: what goes wrong if the change is subtly off.

| # | Recommendation | Benefit | Risk | Section |
|---|---|---|---|---|
| 1 | Fix `--from`/`--skip` in `steps2`, `stepsN`, `shards`, `bgstep` | high (a correctness fix) | low | 3.1 |
| 2 | Move the ~240 unreached `scripts/` and `engine/` files out of the main directories into `scripts/attic/`, `scripts/diag/` and `scripts/oneoff/` | high | low (nothing imports them by construction) | 1.1, 5.2 |
| 3 | Fix the parser and formatter disagreements in §2.1 and §2.2 | medium (wrong numbers on pages) | low (display only) | 2 |
| 4 | Take the reports and evidence off the run's critical path (§3.2 A-D) | ~30-90 min a run | low | 3.2 |
| 5 | Archive the dated docs into `docs/archive/` and keep ARCHITECTURE, ENGINE and the topic docs at the top | high for a newcomer | none | 5.4 |
| 6 | Split `app.py` into blueprints, one group per change, converting the source-text tests as each group moves | high | medium (endpoint names, tests) | 4 |
| 7 | One settings module: a registry of every `XCP_*` switch with its default and meaning, plus a test that fails on an unregistered one | medium-high | low if defaults are pinned by a test first | 5.3 |
| 8 | Retire the old engine path (`XCP_JOINT_LIVE=0`): 5 files and 2,664 lines, plus its pipeline branch | medium | low, if the owner agrees it will not be used again | 1.4 |
| 9 | Delete the 11 renamed copies (`fuck2-9.py`, `_drop_old.py`, `_sizes.py`); decide on the four functions in §1.3 that carry decisions | low-medium | none | 1.2, 1.3 |
| 10 | Shared helpers for table swaps (multi-table, `keep_old`) and table-exists checks (~39 copies) | medium | medium (the swaps guard live tables) | 2.4 |
| 11 | Move the step-history ★ narratives from `run_pipeline.sh` (53% comments) into a per-step doc, leaving a one-line ★ and a pointer in the code | medium | none to behaviour; the owner's call | 5.5 |
| 12 | Unify the person-name keys | medium | **high** (changes which person ids the linkers merge) | 2.3 |
| 13 | Speed up the four heavy steps | the most time | medium-high | 3.3 |

---

## 1. Dead code

How it was measured: a graph over every tracked file. The edges are Python
imports at any depth (including inside functions), `importlib` strings,
and any `<name>.py` or `-m module` mention in a `.py`, `.sh` or `.ps1`
file. The roots are `deploy/run_pipeline.sh`, `deploy/nightly_update.sh`,
the systemd timer installers, `deploy/server_setup.sh` (gunicorn), and
`racecast/app.py`; tests are a separate set of roots. File names built at
run time (such as `f"result_drop_slow_{s}.py"`) are invisible to this, so
"unreached" means "nothing in the repo starts it". It does not mean nobody
runs it by hand. §7 has a command that checks the server's shell history
against the list.

### 1.1 Files

| Directory | Files | Pipeline or site | Tests only | Nothing |
|---|---|---|---|---|
| `scripts/` | 387 | 103 | 47 | **237** |
| `engine/` | 128 | 78 | 9 | **41** |
| `racecast/` | 123 | 120 | 2 | 1 |
| `backfill/` | 8 | 3 | 1 | 4 |
| `tfrrs/`, `sources/`, `tools/` | 25 | 17 | 4 | 4 |

The unreached files, sorted by their names (the TSV has every one):

| Category | Files | Examples |
|---|---|---|
| Diagnostics | ~145 | `diag_*`, `why_*`, `explain_*`, `probe_*`, `check_*`, `census_*`, `peek_*` |
| One-off fixes and migrations | ~51 | `fix_*`, `migrate_*`, `refix_gps.py`, `geocode_*`, `recover_nulls.py`, `restore_schema_guards.py` |
| Experiments and fitters | ~42 | `sweep_*`, `curve_*`, `pair_*` (the old engine), `season_estimator_bakeoff.py` |
| Renamed copies | 11 | `scripts/fuck2.py`-`fuck9.py`: each says in its header that it is a different file (`drop_dupe_indexes.py`, `verify_merge.py`, `cleanup_xc_from_results_tf.py`, ...), none of which exists. `_drop_old.py` duplicates `engine/drop_old.py`; `engine/_sizes.py` is three lines |
| **Builds something the pipeline reads** | 5+ | `engine/fit_era_corrections.py` (`era_curve.pkl`), `scripts/build_school_levels.py` (`school_levels.pkl`), `engine/propagate_tfrrs_geometry.py` (`tfrrs_meet_geometry`), `scripts/weather_from_grid.py` (`weather`), `backfill/elevation_backfill.py`; also `engine/season_level.py` (`athlete_season_level`, listed only in `scripts/owner_checks.sh`) |

The last row is **not** dead: these are the hand-run builders the pipeline
depends on. They should be listed somewhere a newcomer finds them (a
`scripts/build/` folder, or a section of `ARCHITECTURE.md`, which now has
one).

Two large files in the list are data, not code. `scripts/result_drop_tf.py`
(268,859 lines) and `scripts/result_drop_xc.py` (47,862) are generated sets
of result ids, outputs of the triage meant to be merged into
`corrections.py`. They are 80% of the unreached lines. The same goes for the
55 `.tsv`/`.csv`/`.json` files in `scripts/` (about 2.4 MB: suspect lists,
geocode caches, adjudication verdicts).

`racecast/build_clock_calibration.py` describes itself as running "every
run", but no pipeline step runs it any more (it is reached by its test
alone). The last commit on master took the 5K clock correction back out,
so it is probably meant to be dormant; worth a line in its header.

### 1.2 Templates, JS, static files, env switches

- **Templates:** `_share.html` was the only one nothing renders, extends,
  includes or imports. Removed.
- **JS:** all 32 files are loaded by a rendered template.
- **Images:** `static/m1`-`m9` look unreferenced, but the home page builds
  their names (`slot ~ '.webp'`), so they are live. `m9.png` is missing,
  and the template falls back to it only when the `.webp` is gone.
- **Env switches:** 263 `XCP_*` names. Every one set in a shell file or
  `/etc` is read by some code; `XCP_ORIGIN` is an iptables chain name, not
  a switch. The real problem is spread, not dead switches (§5.3). The
  switches that guard code nobody runs are the old engine's (§1.4).

### 1.3 Functions

16 top-level functions in the live modules are referenced nowhere but their
own definition. I removed the 12 with no decision recorded in them:

- `app.py`: `season_bests_by_year` and its helpers `_better_rating`,
  `_track_event_best`, `_track_rating_best`
- `panels._seasonJoinFor`, `predict.isTrackEvent`,
  `results_parse.escapeForDisplay`, `school_identity.stateFor`,
  `tilt.difficultyFor` (ISSUES-RUNNING #8 already noted it had no caller)
- `season_year.rollsOver` (always returned False)
- `atmost_era5_zarr.fetchCellWeather`, "kept for --debug", which `--debug`
  does not call
- `train.zScore`

**Left for you to decide**, because each carries a ★ or design note:
`alert_email.hsScale` (the 2026-10-10 decision that alerts show the HS
scale; check that the mail still does), `bad_distance.venueContext`,
`season_level.academicYearOf` and `season_level.seasonLevelJoinSql` (the
LEFT-not-INNER note).

### 1.4 Default-off code paths

- **The old engine** (`XCP_JOINT_LIVE=0`, `run_pipeline.sh:1355-1366`):
  `engine/linkage_check.py`, `pair_all.py`, `pair_recenter.py`,
  `distance_fix.py` and `distance_fix_by_pool.py` are reached through that
  branch alone, 2,664 lines in all. The joint solve has been live since
  2026-09-08. Retiring it also removes the `08b_joint_shadow`, `09_tilt` and
  `10c_gap --emit` branches.
- **Off by default, each guarding a feature:** `XCP_WEATHER_FIT`,
  `XCP_NCAA`, `XCP_RECORD_BOOKS`, `XCP_STATE_ODDS`, `XCP_ALERTS`,
  `XCP_FORECASTS`, `XCP_LACCTIC`, `XCP_KAILO`, `XCP_BACKTEST`,
  `XCP_PAGE_TIMING`. Keep them, but list them in one place (§5.3).

---

## 2. Duplicated logic

### 2.1 Distance from an event name (fix soon; low risk)

Four parsers, run on the same inputs:

| Input | `event_parse` (the backfill's) | `normalize_distance.parseEventShort` | `results_parse.distanceOf` | `tf_points.eventDistance` |
|---|---|---|---|---|
| `Mile` | 1609.344 | **None** | 1609.344 | 1609.344 |
| `1 Mile` | 1609.344 | 1609.34 | **1609.3** | 1609.344 |
| `880` | 804.672 | **880.0** | **880.0** | 804.672 |
| `3000mSC` | None | 3000 (steeple) | None | 3000 |

`racecast/app.py` (the TF race page, two places near `get_tf_race_results`)
says it uses "the same parser the backfill used" but calls `parseEventShort`.
So an "880" with no stored distance shows PRs against 880 m. The fix: call
`event_parse.distanceFromEventShort`, and keep `parseEventShort` only for
the event's kind. `results_parse` writes upload distances with 1609.3. Add
one parity test over a table of event names.

The mile constant appears as 1609.344 in 22 files, 1609.34 in 14 and 1609.3
in 3. The "stored distance, else parse the name" step is written five
times (`anchor_repair`, `fill_ratings`, `build_ranking_results._tfDistance`,
`fit_weather_correction`, `explain_rating`); `fill_ratings` uses a
truthiness test, so a distance of 0 counts as missing.

### 2.2 Time formatting (fix soon; display only)

`racecast/time_format.format_time` rounds first, then splits, so 959.996
prints "16:00". These copies split before rounding:

| Function | 959.96 | 959.996 |
|---|---|---|
| `social._clock` | **15:60.0** | **15:60.0** |
| `cards._clock` (the share images) | **15:60** | **15:60** |
| `explain_rating.clock` | 15:59.96 | **15:60.00** |
| `csv_export.fmtTime` | 15:59.96 | **15:60.00** |
| `race_story._clock` | 15:59.96 | **15:59.** |

I confirmed these by running the code. Each should delegate to
`format_time`, and the carry case should be added to `test_time_format.py`.
`projections`, `upcoming_preview` and `season_floor` print whole seconds on
purpose; leave them. Time parsing has three versions:
`results_parse.parseTime` strips a trailing "q", `recruiting.parseTime`
does not, and `tfrrs/parser/parse_time.py` handles "4:05.21 (4:05.203)".

### 2.3 Name keys (unify last; high risk)

Person names are keyed three ways:

- `scripts/link_freshmen.normName`, used by the linkers, `twin_flag` and
  the watchdog, drops accented letters: "Peña, José" becomes `jos pea`.
- `dedup`/`fuzzy_pass` de-accent and keep hyphens.
- `racecast/predict._nameKey` deletes hyphens: "Mary-Kate" becomes
  `marykate`.

`search_index.searchFold` has the most complete fold. The SQL mirror in
`link_feed_twins._NAME_SQL` disagrees with `normName` on "A, B, C", and no
test covers it. The fix is one module with `fold()` and `nameKey()` plus one
SQL twin, but changing the key changes which person ids merge, which is
stored data. Do it last, behind a dry-run diff of the links it would make.

School keys: `engine/normalize_distance.normSchoolKey` and
`scripts/build_school_levels.normSchool` are the same code with copied
tables. One writes `school_levels.pkl` and the other reads it ("must match
EXACTLY"), and there is no parity test. `build_school_levels` should import
the engine's function. There are three "not a team" lists that disagree, in
`panels`, `school_name` and `normalize_distance`: "Unknown" is a team to one
and not to another.

### 2.4 Smaller families

- **Season year:** `engine/season_year.py` (August) is canonical, and about
  16 hand copies agree with it. Five use July and disagree for a July race:
  `bad_distance`, `component_check` ×2, `reference_fit`, and the fallback in
  `racecast/course_history`.
- **Grade keys:** `rankings.gradeKeySql` turns '11-12' into '111';
  `recruiting.gradeNumSql` gives NULL; `grade_label.classYearGrade`
  disagrees with the SQL it says it mirrors.
- **Pools:** already consolidated in `engine/pool_resolve.resolvePool`.
  Exceptions: the era and distance fitters call `poolFor` directly, so the
  curves are fitted on a different pooling than the ratings use.
- **Table swaps:** `racecast/dbfast.swapTable` handles one table. Four
  hand-rolled variants (`build_ranking_results`, `build_school_identity`,
  `backfill_normalize._swapTables`, `merge_column._swap`) keep `_old`,
  rename several tables, or re-point keys. Extend `swapTable` with those
  options, test first, because these guard the live tables.
- **Table-exists checks:** about 39 copies (`_hasTable`, `_tableExists`,
  `_exists`). They differ in whether they qualify `public.`; an unqualified
  one also matches a temp table. Put one in `dbfast`.
- **US states:** about 14 lists. `scripts/geocode_deadend_us.US_STATES` is
  missing ME, MT, NV and DC. `_stateNames()` is copied in `pages2`,
  `pages3`, `state_odds` and `school_identity`, and `_states()` in `cuts`
  and `goal`.
- **Gender from a division or pool name:** five copies with different edge
  cases ('hs_m|XC' gives 'U', None or 'm'); none tested.

---

## 3. The pipeline

### 3.1 The `--from`/`--skip` patch (recommendation 1)

Add one helper next to `step()` and use it in the four batch functions:

```bash
# beforeFrom NAME: true when NAME is before --from and not on _ALWAYS
beforeFrom() {
  case " $_ALWAYS " in *" $1 "*) return 1 ;; esac
  [ "$(echo "$1" | sed 's/^0*\([0-9]*\).*/\1/')" -lt "$FROM" ] 2>/dev/null
}
```

- `steps2`: skip the pair only when both names are before `--from`, and
  honour `--skip` per name.
- `stepsN`: the same, per name.
- `shards` and `bgstep`: `beforeFrom "$name"`. This also removes the dead
  `02_drop_old` exception in `shards`.

Add a test that sources the functions with `DRY=1 FROM=7` and asserts
that `05b_anchor_repair_xc` prints its plan line.

The same reading found these smaller problems:

- `step` always returns 0, so the eleven `step ... || true` are no-ops.
  Their comments say the opposite.
- `--from 17` skips 16b-16d, which run after 17_checklist.
- The header still says "the same 26 steps" and "FOUR HOURS".
- `08_golive`, `10c_gap` and `05_backfill` each appear twice on exclusive
  branches.
- Step numbers are out of order in eight places.

### 3.2 Merging, dropping and running steps in parallel

These estimates use the 2026-10-08 timings (`docs/ISSUES-RUNNING.md`) and
the durations quoted in the script's comments. Most small steps have never
had their time recorded, so measure first (§7).

| | Change | Evidence | Saves |
|---|---|---|---|
| A | Run the holdouts (08a ×3) and the ladder (08b) weekly, not every run | They are background work at nice 19, but they compete with 10_rankings for the disk, and the run waits for them before 17_checklist (`run_pipeline.sh:1610`). Holdouts take 8,717 s; the ladder took 12,305 s on 2026-10-11 | 0 to the length of the wait, plus less contention during the boards |
| B | Start 16b/16d/17a as background steps right after 10_rankings_finish, collected before 17c | They need only the final ratings; `run_report.py` reads their logs | 16d alone is "40 races, a few minutes each" |
| C | Drop 16c (Kailo record) from the pipeline | the `xcp-kailo-record` timer already runs the same command every 6 hours (`install_kailo_record_timer.sh`) | minutes |
| D | Run the row guard (15 → 16) in the background from 09b, joined before 17_checklist | It reads the ratings and writes `corrections.py` for the next run | ≥ 20 min |
| E | Run 04b_wheelchair and 04d_gender beside 04_grade_sanity | grade_sanity does not read their tables. Keep 04c after it, since grade_sanity reads `result_twin` | their run time |
| F | 09c_school_names beside 09b/09d; 00b beside 00_integrity; 05c beside the 06 chain; 10e beside 10b | no shared tables (comments at each step). Nothing may run beside 11b (CREATE INDEX CONCURRENTLY) | a few minutes each |
| G | Drop 10c_gap while the joint solve is live | telemetry only; nothing reads its log | minutes |
| H | Merge the six person linkers (04a → 04a3b) into one ordered driver | each scans `results*`; the order rules are at `run_pipeline.sh:594-641`. Keep each rule's undo log | several full scans; unmeasured |
| I | Move 01_season_year (a self-test) into the tests | it reads and writes nothing (`season_year.py:338`) | seconds; one less step to explain |

My estimate for A-G together is 30-90 minutes of wall clock. It depends
mostly on how long the run now idles waiting on the evidence chain and on
10f2 (2h41m in the background), which the timeline command in §7 will show.

### 3.3 Where the rest of the time is

08_golive (~2.5 h, the joint solve's outer passes), 04_grade_sanity (its
field queries), 10_rankings (four shards plus finish) and 07_pack (~40 min).
Each is its own project; none is a merge or a reorder.

---

## 4. `app.py` into blueprints

The routes, grouped (line numbers on master):

| Group | Routes | Shared app.py symbols it needs | Order |
|---|---|---|---|
| Share cards | 12 | `getConn`, `_tf_meet_sources`, `_loadShare` | **done** (`card_routes.py`) |
| Ops (`/debug/queries`, `/account/status`, `/watchdog`, `/activity`, `/resolve`) | 5 | `_statusAdmin` (also passed to `monitor.install`) | 2 |
| Recruiting (`/recruiting*`, `/coaches*`, `/recruit/<id>`) | 8 | `get_homepage_meta`, `_seasonYears`, `stampCrests` | 3 (one test asserts `@app.route("/recruiting")` in app.py's text) |
| Info (`/about`, `/report`, `/contact`, `/updates`) | 7 | few; endpoints used by `url_for` in `_topbar.html` | 4 |
| SEO (`robots.txt`, manifest, sitemap, verification, IndexNow) | 5 | `_PRIVATE_PREFIXES` | 5 (the catch-all `/<key>.txt` must keep its rule) |
| Conversion tools (`/conversions`, `/api/convert`, ...) | 6 | caches, `explain_rating` | 6 |
| Search | 4 | `_SCHOOL_IDX`, `_SEARCH_CACHE` | 7 |
| School and records | 8 | record helpers | 8 |
| Boards (`/rankings*`, `/api/rankings*`, `/api/teams`, `/api/courses`) | 8 | `_publicBoard`, `_RANKINGS_SPORT_CACHE` | 9 |
| Predictions (`/predictions`, `/api/predict/*`) | 13 | `_predictSource`, `_feedPin`, `splitRaceKey`, ttlcache | 10 |
| Course and venue | 4 | `_COURSE_CACHE`, `buildCourseCtx` (imported by `build_course_boards`) | 11 |
| Race and meet | 8 | 80 symbols, 29 shared | 12 |
| Athlete | 6 | 79 symbols, 21 shared | 13 |

What has to exist first:

- `racecast/web_core.py`: the SQL fragment builders at the top of `app.py`
  (`_athlete_lateral`, `_tfrrs_join`, `_dist_override_join`, ...), the
  cache-header constants, and the schema probes.
- `web_sources.py`: `meet_sources`, `pick_source`, `_ridArg`, the `_tf*`
  source helpers. Between 12 and 18 routes reach each one.
- `web_jinja.py`: every filter and global, registered by one function.
  Today they are spread through the file; `athlete_href` is set halfway
  down. Note that `school_label` is registered twice.

Then `app.py` becomes the factory: hooks, error handlers, start-up loads,
blueprint registration, and re-exports for the modules that import from
`app` inside functions (`cards`, `upcoming_preview`, `pages3`,
`profile_page`, `meet_recap`, `meet_track`, `build_course_boards`).

**The risks, and how to handle each:**

1. **Endpoint names change** (`about_page` becomes `info.about_page`).
   `_topbar.html` is on every page, so one missed `url_for` there is a
   site-wide 500. Either update the templates as each group moves, or keep
   the name with `endpoint=`. Then add a test that renders every template
   once.
2. **Tests read `app.py`'s text** (57 files) and monkeypatch its globals
   (68 calls, 17 on `A.getConn`). Convert each group's tests in the same
   commit as the move. Prefer behaviour tests (a test client request) over
   source-text ones; the source-text ones are what make every move cost
   double.
3. **Circular imports:** blueprints import shared helpers from `web_*`,
   never from `app`.
4. **Import-time side effects** (label, crest and opt-out loads, the
   prewarm thread) stay in `app.py`.

The cards commit is the pattern: verbatim bodies, the URL map diffed before
and after, and every route requested on both trees.

---

## 5. Proposed structure

### 5.1 Target layout

```
racecast/
  app.py             factory, hooks, registration (target: < 1,500 lines)
  web_core.py, web_sources.py, web_jinja.py
  *_routes.py        one blueprint per group (card_routes.py is the first)
  build_*.py         the board builders (unchanged)
  ...                the helper modules (unchanged)
engine/              the live engine only; fitters in engine/fit/, diagnostics in engine/diag/
scripts/
  (root)             what the pipeline and the site import or run: database.py, config.py, the linkers, the step scripts
  build/             hand-run builders the pipeline reads (§1.1, last row)
  diag/              diagnostics run by hand
  oneoff/            one-off fixes and migrations kept for history
  data/              the .tsv/.csv/.json files and the result_drop_*.py sets
deploy/
settings.py          (or scripts/settings.py) the switch registry, §5.3
docs/
  ARCHITECTURE.md, ENGINE.md, ACCOUNTS.md, CLOUDFLARE.md, SEO.md, ...
  archive/           every dated HANDOFF / PLAN / RUNBOOK / CHECK / SWEEP
```

### 5.2 Sorting `scripts/` safely

- Move only the unreached files first. Nothing in the repo imports or
  starts them, so moving them cannot break the pipeline or the site.
- They put `scripts/` on `sys.path` relative to the working directory
  (`sys.path.insert(0, "scripts")`), so `python scripts/diag/x.py` from
  the repo root keeps working. The few that use `os.path.dirname(__file__)`
  need a one-line change; check each with `python -m py_compile` and a
  `--help` run.
- Check first which ones you run by hand (§7, command B). Those go to
  `diag/`, and the rest to `oneoff/`, or out of the repo (git keeps them).
- The 63 tests-only files: each has a test that pins it. Either keep both,
  or retire the script and its test together.

### 5.3 One settings module

Today there are about 260 `XCP_*` switches, read with `os.environ.get` (or
a local `_env` helper) in 103 files. Model defaults live in
`deploy/solve_env.sh`, alternatives in `solve_env_v2.sh`, production
values in `/etc/xc-predictor.env`, and the explanations in ★ comments where
each is read. A newcomer cannot list them.

Proposal:

1. Add `settings.py`, a table of `Setting(name, type, default, where_read,
   meaning)`, with one accessor `setting("XCP_DIFFICULTY")`.
2. Generate it from the code once, so the defaults are exactly today's.
3. Add a test that fails when code reads an `XCP_` name the table doesn't
   have, or when a default changes without the table changing.
4. Move readers over to the accessor gradually; nothing breaks while both
   forms exist.
5. Keep `solve_env.sh` as the run profile. The pipeline can print the
   resolved settings at the start of every run, which it does today for six
   of them.

### 5.4 Docs

There are 40 files and about 26,600 lines. The issue logs (`ISSUES-RUNNING.md`
6,625 lines, `ISSUES-2026-08-24.md` 6,250) and 19 handoffs are history. Root
`HANDOFF.md` is `docs/HANDOFF-2026-09-13.md` with about 490 more lines
appended. Move every dated file into `docs/archive/`, keep the root
`README.md`, and point it at `docs/ARCHITECTURE.md` and `docs/ENGINE.md`.

### 5.5 The ★ comments

There are 4,281 ★ markers. `run_pipeline.sh` is 53% comment lines,
`backfill_normalize.py` 46%, `joint_solve.py` 32%, `app.py` 20%. They are
the record of why, and the rule is to keep their meaning. But in the
pipeline script they bury the 90 step lines. Suggestion: one
`docs/PIPELINE-STEPS.md` with a section per step, holding the full
narratives, dates and quotes moved verbatim. Each step in the script keeps
a one-line ★ summary and the anchor. Do this only if you want it; it is
pure text, but it is your record.

---

## 6. What I would not change

- **The swap-don't-update pattern, the gates, the lock, and the
  corrections.py preflight.** Each has paid for itself.
- **`engine/pool_resolve.py` and `engine/season_year.py`.** These are
  already the single source; only a few stragglers bypass them.
- **The nightly/full split.** It is clear and well commented. The nightly
  leaves out `04a4_unsplit` and `04a3b_merges`, which the full run treats
  as always-run; worth a look (`nightly_update.sh:212-226`).

---

## 7. Commands to run on the server (read-only)

All from `/srv/xc-predictor`. None prints a secret: the DB connection
prints only `user@host:port/db`, and the history command prints file names
only.

**A. Where the last full run's time went** (slowest steps first, then the
start and end of each step's log):

```bash
cd /srv/xc-predictor/logs
R=$(for d in 2*/; do d=${d%/}; grep -q ' 18_vacuum ' "$d/summary.log" 2>/dev/null && echo "$d"; done | tail -1); echo "run $R"
sed -nE 's/^ +([^ ]+) (ok|FAILED after) \(?([0-9]+)s,? ?([a-z ]*)\)?.*/\3 \1 \2 \4/p' "$R/summary.log" \
  | sed 's/FAILED after/FAILED/' | sort -rn \
  | awk '{t+=$1; n=$1; $1=""; printf "%6.1f min %s\n", n/60, $0} END{printf "sum of step times %.1f h (parallel and background steps overlap)\n",t/3600}' | head -45
for f in $(ls -tr "$R"/*.log); do printf '%s %s %s\n' "$(grep -m1 -oE '^[0-9:]{8}' "$f")" "$(date -r "$f" +%H:%M:%S)" "$(basename "$f" .log)"; done | tail -100
```

In the timeline, a long gap between `16_rowguard_apply` ending and
`17_checklist` starting is time spent waiting on the evidence chain. A gap
after `18b_integrity_boards` is time spent waiting on 10f2 or the watchdog.

**B. Which unreached scripts you actually run by hand:**

```bash
cd /srv/xc-predictor
cat ~/.bash_history /root/.bash_history 2>/dev/null | grep -ohE '[A-Za-z0-9_]+\.py' | sort | uniq -c > /tmp/hist_py.txt
cut -f2 docs/REVIEW-COMPLEXITY-2026-10-unreached.tsv | grep '\.py$' | xargs -n1 basename | sort -u > /tmp/unreached.txt
awk 'NR==FNR{u[$1]=1; next} ($2 in u){print $1, $2}' /tmp/unreached.txt /tmp/hist_py.txt | sort -rn | head -60
echo "unreached files in history: $(awk 'NR==FNR{u[$1]=1; next} ($2 in u)' /tmp/unreached.txt /tmp/hist_py.txt | wc -l) of $(wc -l < /tmp/unreached.txt)"
```

**C. Tables the site and pipeline never read** (since the statistics were
last reset):

```bash
cd /srv/xc-predictor && set -a && . /etc/xc-predictor.env && set +a && /srv/venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "scripts")
from database import getConn
with getConn() as c, c.cursor() as cur:
    cur.execute("SELECT stats_reset::date FROM pg_stat_database WHERE datname = current_database()")
    print("stats since", cur.fetchone()[0])
    cur.execute("""SELECT relname, pg_size_pretty(pg_total_relation_size(relid)),
                          n_tup_ins + n_tup_upd + n_tup_del AS writes
                   FROM pg_stat_user_tables
                   WHERE coalesce(seq_scan, 0) + coalesce(idx_scan, 0) = 0
                   ORDER BY pg_total_relation_size(relid) DESC LIMIT 40""")
    for r in cur.fetchall(): print(*r, sep="\t")
    cur.execute("SELECT count(*), pg_size_pretty(sum(pg_total_relation_size(relid))) FROM pg_stat_user_tables")
    print("all tables:", *cur.fetchone())
EOF
```

**D. Timers, local changes and leftovers:**

```bash
cd /srv/xc-predictor
systemctl list-timers --all --no-pager | grep -iE 'xcp|kailo|backup|nightly'
git status --short | head -20
ls engine/data/*.npz logs 2>/dev/null | wc -l; du -sh logs engine/data 2>/dev/null
```

`git status` shows any script that exists only on the server. The timers
show whether the Kailo record (§3.2 C) already runs on its own.

---

## 8. Test status

`python -m pytest -q tests/` on master at `679cb6a` gives 7 failed, 4,153
passed and 143 skipped. The branch fails the same 7 tests. They fail here
for reasons unrelated to this work:

- `test_chair_person_everywhere::test_checklist_reads_the_person_list`
- `test_dict_cursor_columns::test_no_request_path_query_selects_two_bare_aggregates`
- `test_requirements_complete` ×2. `racecast/upload_fetch.py` imports
  `sources` after adding the repo root to `sys.path`, and the test's
  resolver does not see that, so the test, not the code, is behind.
- `test_school_level_units::test_a_school_season_keeps_a_small_clusters_state`
- `test_sweep_20261010_model` ×2 (`stored_pool`)
