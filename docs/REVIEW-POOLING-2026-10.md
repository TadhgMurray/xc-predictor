# Pooling review — 2026-10-25

How every rated row gets its pool (`hs_m`, `ms_f`, `college_m`, `pro_f`, …),
what is wrong with it, and a plan you can approve item by item. Nothing in
the plan (section 5) is built yet. Section 3 lists what this pass did fix.

**Why it matters.** `speed_rating` is relative to the pool's mean, and the
pool also picks the distance curve. So a wrong pool doesn't shift a rating,
it bends it differently at every distance. Guillaume Tremblay read 166 at
800m and 109 at 8000m as `ms_m`. The standing rule still applies: when in
doubt, separate.

---

## 1. The short version

- **The decision is in one place, but the facts it needs are not.**
  - `resolvePool` takes 14 facts, not the 6 its header listed. The header is
    now fixed.
  - Only the engine's pack passes all 14.
  - The fill step, the board build's fallback, panels and feature_extraction
    pass only facts that come from tables, some of them fewer. None of them
    passes the 5 that come from the team: no team id, team_pool pro, the
    anet team level, the club rules, and the unattached race ceiling.
  - So any row the engine did not stamp is pooled by a different rule. That
    covers every unrated row, and every row the nightly update prices, which
    then ranks.
- **Four of the fact tables are not rebuilt by `run_pipeline.sh`:**
  `level_graph`, `season_level`, `college_flag` and `build_team_pool`. They
  are only as fresh as the last `overnight_fit_pool_solve.sh` or
  `owner_checks.sh`, and they are keyed on person_ids from before that run's
  relinks and merges.
- **One documented safeguard has never worked.** The school-level graph
  that was meant to override `school_levels.pkl` for 198 schools (Cornell,
  Duke, Arkansas and the rest) never matches anything, because of a key
  mismatch (P3).
- **Six clear bugs are fixed in this pass, with tests** (section 3). They
  take effect at the next full run.
- **`scripts/diag_pooling.py`** measures each failure kind and ranks them by
  how many ratings each one moves (section 4). Run it before and after the
  next full run.

## 2. The map: where each fact comes from

Unless a row says otherwise, every fact is keyed on the academic year
(`season_year`, August seam).

| Fact | Built by | Table | Pipeline step | Engine | Site / fill |
|---|---|---|---|---|---|
| season_level | `season_level.py` | athlete_season_level (person, ay, sport/ALL) | **not in run_pipeline** | yes | yes (join) |
| grade_untrusted, fixed_grade, fixed_level, grade_verdict | `grade_sanity.py` | grade_fix | 04_grade_sanity | yes | yes |
| is_pro | `pro_flag.py` | pro_athlete_season | 03_pro_flag (runs **before** 04, so it reads last run's grade_fix) | yes | yes |
| pro_ability | `build_pro_ability.py` | pro_ability_season | 05c | yes | yes (`pro_ability.py`) |
| college_first (veto) | `college_flag.py` | college_first_season | **not in run_pipeline** | yes | yes |
| upperclass_first | — | — | — | passed, ignored | passed, ignored |
| team_level | anet_team.level / tfrrs slug | — | per row | yes | **no** |
| no_team | team_id = 0 | — | per row | yes | **no** |
| team_pro | `build_team_pool.py` | team_pool | **not in run_pipeline** | yes | **no** |
| team_has_pros + club gate | `speed_ratings_db.loadClubPros / loadClubMajority` | computed in the pack | per run | yes | **no** |
| race_top_level | `level_graph.py` | race_top_level | **not in run_pipeline** | yes | **no** |
| gender | person_gender + the row's label | person_gender | 04d | pack's own rule | boards' own rule (P6) |

**Who calls `resolvePool`:**

1. **The engine pack** (`speed_ratings.packResults` → `poolOf`) passes all
   14 facts. The go-live writes the result to `results.rating_pool`.
2. **The board build** (`build_ranking_results`) takes `rating_pool` off
   the row. Rows without one (sprints, field events, rows rated before the
   column existed) go through a fallback that has only the table facts.
3. **`fill_ratings`** has only the table facts too. It runs:
   - in the full run (09b), for rows the solve left out of its pace band;
   - in the nightly update (`--venue`), for every new result. Those rows
     **rank**, because the board takes the pool fill wrote into
     `rating_pool`.
4. **`panels._poolOf`** is no longer called by the boards (its own note says
   so). It passes fewer still: no `grade_verdict` and no college veto.
5. **`model/feature_extraction`** passes the old arguments (`race_date`, no
   season). It feeds the ML model only.
6. **The site** (`app.py`, `rankings.py`) reads stored pools and does not
   decide them. One copy to keep in step: `app._NO_POOL_VERDICTS` repeats
   the four "no pool" methods inlined in `resolvePool`.

**Where they disagree:**

- Table facts only, against all 14, above.
- Gender: the boards' gender is not the pack's (P6).
- Season verdicts: the backfill normalises with only *unanimous* season
  verdicts (`backfill_normalize.py:940`), while the engine and the boards
  use any non-null verdict. A mixed season is normalised on one pool's
  curve and rated in another. `rescaleToPool` only repairs it when the two
  scales differ by more than 10% (P7).

## 3. Fixed in this pass (tests pass; takes effect at the next full run)

All of these are in `tests/test_pooling_review_2026_10.py`. The SQL change
was also checked on a scratch Postgres.

1. **The hand list now beats the 14:00 / 15:30 gate.** The gate set
   `is_pro = False` for anyone not fast enough, including people on
   `_PRO_SEASONS`. Tremblay (1:54 for 800m) failed it and went back to the
   middle school pool, which is the exact case the file opens with. The list
   is your word, not an inference, so the gate now skips it.
   (`pool_resolve.py`, the ability gate)
2. **A club runner's corrected school grade is a grade again.**
   - `grade_sanity` writes `grade_untrusted` for *every* season it has a
     verdict on, corroborated ones included.
   - The club rule tested that flag alone, so a youth club's corroborated
     grade 10 was treated as "no grade" and pooled pro. The only thing
     catching it was the speed gate.
   - Now only a distrusted grade with no corrected grade counts as no
     grade. The "club with pros, majority of the season" sweep is unchanged.
3. **tfrrs "Unattached" rows now use the race-ceiling rule.**
   - The engine loads the race ceiling for both kinds of unattached row:
     team_id 0, and an "Unattached" school string, which is the only way a
     tfrrs row can say it.
   - But `resolvePool` only reads the ceiling under `no_team`, and the pack
     set `no_team` from team_id alone. So every tfrrs unattached row ignored
     its race and fell through to "tfrrs means college".
   - New: `pool_resolve.unattachedEntry`, called in the pack. A row with no
     stated ceiling keeps the old path.
4. **The club-majority gate uses the academic year.** It counted calendar
   years (`substr(date,1,4)`) while every other pooling fact counts academic
   years. So one season's fall and spring were judged against two different
   majorities. `loadClubPros`' window had the same mismatch, padded by a
   whole season. (`speed_ratings_db`, `speed_ratings.packResults`)
5. **The club majority counts both sports.**
   - It ran one query per sport, each keeping only the years that had a club
     row, then added them up.
   - So a sport with no club row that season never reached the
     denominator. Three summer club XC races beside ten school track races
     read 3 of 3, a "club season", and the club rules made it pro.
   - It is now one query over both sports. The scratch-Postgres test caught
     this one.
6. **`'6U'` can now match.** `age_band_grades.UNAMBIGUOUS_AGE` spelled it in
   upper case but is matched against `lower(grade)`, so it never matched.

Also: `pool_resolve`'s header now lists all 14 facts and which callers pass
which.

## 4. Diagnostics: `scripts/diag_pooling.py` (read-only)

```
cd /srv/xc-predictor && git pull; set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
tmux new -s pooldiag
$P scripts/diag_pooling.py 2>&1 | tee logs/diag_pooling_$(date +%Y%m%d_%H%M).log
```

To look at one meet's pools by division (seconds; the meet in your brief):

```
$P scripts/diag_pooling.py --meet 270331
```

- **It writes nothing** except session temp tables.
- **The default samples 1 person in 10** (`--sample 1` reads everyone) and
  rows since 2018-08 (`--since`).
- **Counts are the sample times 10.** The examples are real rows.
- **It ends with a table ranking every kind by rows moved**, with how many
  of those rows are on a board.
- **Runtime:** one pass over `results` + `results_tf` to build the sample,
  then each section is quick. The `fill` section runs the board query on
  20,000 rows per sport. Expect tens of minutes, not hours.
- **Timing:** start it well before 06:00 UTC or after 07:00 UTC, so it
  doesn't overlap the apt/Postgres restarts.

| Section | What it counts |
|---|---|
| `flip` | one athlete-season (person, sport, academic year) rated in two or more levels; rows outside the season's majority |
| `xflip` | XC and TF of the same academic year at different levels |
| `ms_in_hs` / `hs_in_ms` | a grade 6–8 rated hs, or a grade 9–12 rated ms/elem (grade_sanity's grade where it has one) |
| `hs_in_college` | grade 9–12 rated college **before** any college season (the college veto's leaks) |
| `college_in_hs` | a college team's row (tfrrs slug or team_pool), or a season after college started, rated hs |
| `pro_in_school` | pro_flag says pro, rated hs/ms/college, split by whether they were fast enough (the veto and the gate working) |
| `pro_pool_slow` | rated pro but not sub-14:00 / 15:30 that season |
| `postcollege` | rated college 5+ seasons after the first college season |
| `gender`, `gender_unknown` | pool sex differs from person_gender; pools of unknown sex |
| `grade_typo`, `grade_back` | a season whose rows disagree on grade; a grade that goes down the next year |
| `unattached` | unattached row not rated at its race's ceiling |
| `club` | row on a team_pool `pro` team rated in a school or college pool |
| `race_level` | ms row in an hs race, hs row in an ms race |
| `merge_wide`, `merge_back` | one season spanning levels 2+ apart (ms and college); a level going back down later (college → hs). Two people under one id, usually |
| `stale` | when each fact table was last analyzed, and how many of its person_ids no longer have a result (built before a relink) |
| `fill` | `fill_ratings`' pool for engine-rated rows vs the engine's stamp: the pool a nightly-priced row gets |

**Please paste me the summary table and the `stale` and `fill` sections.**
Those three decide the order of section 5.

## 5. The plan: approve or refuse each item

Ordered by my estimate of ratings moved. The diagnostics will confirm or
reorder it.

### P1. One fact-gatherer for every caller (the fill / nightly gap)

- **Problem.** Rows the engine did not stamp are pooled without the team
  facts:
  - an unattached runner gets their grade's pool instead of pro or the race
    ceiling;
  - a team_pool pro squad gets its grade's pool;
  - a gradeless club row falls through to the school-name map.
- **Why it matters most.** The nightly update prices every new result this
  way, and those rows rank until the next full run.
- **Fix.**
  - Move the pack's per-row team block (`speed_ratings.py`, around 1500–1565)
    into one function, `rowTeamFacts(...)`.
  - The engine writes the two expensive inputs (the club-majority set and
    the club-with-pros teams) to a small table at go-live.
  - fill, the board fallback and the pack all call that function.
  - The nightly reads the small table instead of rescanning 86M rows.
- **Measure first:** `diag_pooling --only fill`.

### P2. Rebuild the four missing fact tables every full run, in order

- **Problem.**
  - `level_graph` (race_level, race_top_level, school_level_graph),
    `season_level`, `college_flag` and `build_team_pool` run only from
    `overnight_fit_pool_solve.sh` / `owner_checks.sh`.
  - `pro_flag` (03) reads the previous run's `grade_fix` (written at 04).
  - `grade_sanity` reads the previous run's `race_top_level` and
    `result_twin`.
  - `team_pool` and `pro_athlete_season` each read the other's last
    version.
- **Fix.** After the relink and merge steps (04a*):
  1. `level_graph` (about 2.5h)
  2. `season_level`
  3. `college_flag`
  4. `grade_sanity`
  5. `pro_flag`
  6. `build_team_pool`
- **Cost.** About 2.5–3h added to the 9h20m run.
- **Cheaper option.** Rebuild `season_level`, `college_flag` and
  `build_team_pool` every run (minutes), and rebuild `level_graph` weekly.
- **Measure first:** `diag_pooling --only stale` (orphan person_ids).

### P3. Revive the school-level graph lookup, or delete it

- **Problem.**
  - `level_graph` writes node keys `'T:<team_id>'` / `'S:<name>'`.
  - `normalize_distance.loadGraphLevels` normalises those to `'t 123'` /
    `'s cornell'`, but a row's school normalises to `'cornell'`.
  - So nothing ever matches, and the pickle answers alone. By the audit
    written beside the code, that pickle has 128 colleges tagged hs or ms
    (Cornell, Duke, Arkansas) and 2,328 colleges missing.
- **Fix.**
  - Read only `S:` nodes, with the prefix stripped. `T:` nodes are already
    covered by `team_level`.
  - Collision risk: an anet high school named "Cornell" with an
    *ungraded* row would read the tfrrs college node. So apply it only
    where `team_level` is absent.
- **Alternative.** Delete the dead tier and the comment that claims it
  works.

### P4. grade_sanity rule 4: a tie goes to the lower level, and count races

- **Problem.**
  - The field verdict for a thin season ends
    `ORDER BY count(*) DESC, e.level`. A tie is broken alphabetically, so
    college beats hs and hs beats ms. That is a promotion.
  - It counts rows, not races, so a race scraped by both feeds votes
    twice.
  - A `field` verdict also skips the "untrusting may not promote" guard.
- **Fix.**
  - Order ties by level rank, lowest first (`elem < ms < hs < college <
    pro`).
  - Count `DISTINCT (meet_id, div_id, event_key)`.

### P5. grade_sanity: a "contradicted" verdict must not erase a pro verdict

- **Problem.**
  - Rule 5c runs after the pro verdicts and overwrites any season with
    numbers and words as `contradicted`. That includes `stale_grade`,
    `elite_field`, `post_collegiate` and `adult_club`, and `resolvePool`
    then drops the row.
  - The ungraded-event arm of rule 4 (`no_grades` → pro) has no field-size
    floor, so a one-runner ungraded event votes pro.
- **Fix.**
  - 5c skips seasons that already have a level-`pro` verdict.
  - Add the floor that's already written in the comment there
    (`count(*) >= MIN_FIELD_GRADED`).

### P6. One gender for a row, everywhere

- **Problem.** There are three rules:
  - **The pack:** `person_gender`, else the profile majority. A split person
    takes the row's label, and on XC that includes the tfrrs division name.
  - **The boards and fill:** `tmp_person_gender`, which is built from
    `athletes` grouped by **athlete_id**. A person whose person_id isn't
    also an athlete_id gets NULL gender, so fill can't price them.
  - **The backfill:** person level only.
- **Fix.** The boards use the pack's own expression (`packGenderExpr`)
  against `person_gender` directly.
- **Measure first:** `diag_pooling --only gender`.

### P7. One season verdict for normalising and for rating

- **Problem.** The backfill reads only unanimous season verdicts; the
  engine and boards read any. Mixed seasons are normalised on one pool's
  curve and rated in another.
- **Fix.** The backfill reads the same set the engine does.
- **This touches normalisation**, so it needs the distance session's
  agreement.

### P8. Simplify: fewer hand-picked thresholds and special cases

These are proposals, not fixes. Each one removes a number or a path.

- **One clock.** Done for the club gate (section 3). Also delete the dead
  calendar-year notes (`speed_ratings.py:730`, `:1037`; `season_year.py:108`;
  the `grade_sanity` docstrings that say "July" or "calendar").
- **Drop `upperclass_first` from the signature.** It is accepted and
  ignored, and five call sites carry it.
- **Retire `panels._poolOf`.** It is unused and is its own note's
  "delete it once that is certainly not true".
- **One "no pool" list.** `resolvePool` inlines
  `no_evidence/contradicted/thin_field/lone_word`, and `app.py` keeps a copy
  it has to keep in step. Export one constant.
- **`grade_untrusted` should mean what it says.** It currently means "has a
  verdict", which is how bug 2 in section 3 happened. Either rename it, or
  write it only for seasons whose raw grade was overruled.
- **One "club" definition.** Clubs come from three places: anet level 16
  (`loadTeamLevels`, with shares 0.5 / 0.25 and 200 rows), "no grades on
  20+ rows" (`loadClubTeams`, under 5%), and `team_pool` (`kind='club'`).
  `team_pool` should be the one answer, and the other two should feed it.
- **The pro routes.** There are ten:
  1. pro_flag seeds
  2. national-team seed
  3. field propagation
  4. post-collegiate
  5. team_id 0
  6. a race ceiling of pro
  7. team_pool
  8. the HOKA list or the person list
  9. a gradeless club row
  10. a club with pros
  
  The 14:00 / 15:30 gate now cleans up after all of them. Once the
  diagnostics show the gate is doing the work, routes 9 and 10 (the
  majority-gated club rules) could become a single rule: "a gradeless row
  on a non-school team is pro if fast enough".
- **`pro_unknown_gender`** can still be produced (a pro row with no sex).
  The file says such rows should drop.

### P9. Smaller things, for the record

- **"Unattached (TX)" passes the unattached filter**, because
  `normSchoolKey` keeps the parenthesised suffix.
- **`age_band_grades` keys divisions without `source`**, but div_id is only
  unique per source.
- **`pro_ability_season` treats "no usable mark" as "not fast enough"**, so
  a pro whose only marks that season have no distance is demoted.
  `build_pro_ability --sport XC --write` would also write an XC-only table.
- **`trust='low'` is used only by the boards.** The engine still counts
  low-trust seasons in the pool mean, despite `grade_sanity`'s note.
- **The elem pool is rated but has no board** (`rankings.POOLS`). That is
  probably fine; just noting it.

## 6. What I need from you

1. Run section 4's command and send me the summary table plus the `stale`
   and `fill` sections.
2. Approve or refuse P1–P8, one by one. P8's bullets are separate items
   too.
