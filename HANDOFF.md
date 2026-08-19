# Racecast handoff — pooling, difficulty, and the rankings page

Written at the end of a long session. Read §1 and §6 first; everything else is
reference.

---

## 1. STATE

The pipeline is mid-run or just finished. Pools are close to right; the
remaining bad boards are a **course-difficulty** problem, not a pooling one.

**The single biggest fix of the session, and the one to verify first:**
`shrink()` in `linkage_check.py` computed `delta_shrunk` and **nothing read
it**. It fed exactly one thing — `D["difficulty"]`, which `golive` writes to
`course_difficulties` — while all four rating paths read the raw `D["delta"]`.

So the site displayed the shrunk value and the engine rated against the
unshrunk one. Measured at La Feria (`XC:20609:d1600`), a 37-runner middle
school mile where every entrant had 2–3 races: `shrinkByLinkage` correctly
ruled the cell unidentified and replaced `delta = +0.2849` with the sport
default; `course_difficulties` duly showed `+0.022`; the ratings used
`+0.2849` anyway. `exp(0.2849) = 1.3296`, and a 5:37 seventh-grade mile came
out at **162.8**, second on the national middle school board.

★ **Every inflated board entry we chased — Woods Trail Run holding 13 of the
top 25 HS(F) performances, Bengal Invite supplying College(F) #1 and #2,
Burkchioner at 163.7 — is a small, weakly-linked cell.** That is exactly the
population `shrinkByLinkage` flags, and the fix routes it into the ratings for
the first time.

**Verify after the run:**

```sql
-- Woods Trail Run should no longer own the HS(F) performance board.
-- La Feria's Luca Manzo (29578044) should drop from 162.8 to ~125.
SELECT season, pool, races, round(rating_seasonal::numeric,1)
FROM   pair_athlete_season WHERE person_id::text = '29578044' ORDER BY season;
```

And in the `--golive` log:

```
[link] ratings now use the shrunk deltas: median move X, max Y, N cells moved > 0.05
```

---

## 2. THE ONE CLOCK

`season_year.py` was rewritten. **The season is the academic year, August
through July, named for the year it opens in.** One constant,
`ACADEMIC_START_MONTH = 8`, and the SQL forms are generated from it.

★ It **subsumes** both rules it replaced, verified against the module's own 13
test cases: Paterna's Dec 27 and Jan 3 land in one season (what the TF
rollover was for); NXN Dec 6 stays with autumn (what the XC guard protected);
Jan 17 XC closes the autumn campaign (what the XC rollback was for). Two
sport-specific rules and four constants replaced by one seam with no sport to
be wrong about.

**August, not July** — July holds outdoor championships and USATF JO finals,
the *climax* of a season that began the previous autumn. A July seam files
those with the season ahead and halves every summer campaign.

⚠ **The stored year is therefore a year behind for TF.** The 2026 indoor and
outdoor seasons run Dec 2025 → Jul 2026, all academic 2025. The website shows
`year + 1` for TF and filters on the label, but **a direct query against
`ranking_results` or `athlete_season` returns the stored value** and will look
one year off. This bit us twice.

Everything is on this clock now: `grade_fix`, `athlete_season_level`,
`pair_athlete_season`, the packer's grouping key, `pro_athlete_season`,
`panels`, `build_ranking_results`.

---

## 3. WHAT DECIDES A POOL

Exhaustively, in `pool_resolve.resolvePool`:

1. `pro_flag` → `is_pro`
2. a grade that never advanced → `grade_fix` level `pro`
3. a corroborated grade
4. the level of the field raced
5. a bare class word, resolved by the athlete's own grades or by the field's
6. `no_evidence` / `contradicted` / `thin_field` / `lone_word` → **no pool**

**The promotion gates are gone and must not come back.**
`upperclass_first_season` promoted ms/elem → hs from one date per person,
seeded by a single qualifying race, covering 5,854,481 athletes. Person
23950279 is corroborated grade 6, 7, 8 across three years — a middle schooler,
unambiguously — and the gate promoted 2024 and 2025 to `hs_m`. His ability
(~675) is a 3200-anchored number and `hs_m`'s pool mean (~1240) is a
5000-anchored one, so the rating came out at **187 instead of ~111**.

★ **With per-pool anchors, a wrong pool is no longer a wrong label — it
divides a 3200-scale ability into a 5000-scale mean, and the arithmetic is
incoherent rather than merely off.** That is why mispooling suddenly produced
190s when it had always produced plausible-looking numbers before.

Removing the gates also closed the board/engine disagreement for free: all
33,237 mismatched rows were `hs → college`, caused by `speed_ratings` loading
the gate dates from dicts and `build_ranking_results` from `LEFT JOIN`s. Same
rule, two data paths. No gate, no second path.

---

## 4. GRADE RULES AS BUILT (`grade_sanity.py`)

| rule | what it does |
|---|---|
| 2 | a grade recurring ≥2 times in an academic year is corroborated |
| 2b | word fold — see below |
| 2c | a bare class word leans college; overruled by the athlete's own grades, then by the fields they raced |
| 3 | odd values within a season filled from the corroborated one |
| 3b | implied class year must not drift >1 across seasons; three seasons to overrule one |
| 4 | needy seasons take the level of the fields raced |
| 4b | an event where nobody carried a grade → `pro` |
| 5 | a grade held >2 years → `pro` |
| 5b | a school grade after `SR-4`-style collegiate eligibility → `pro` |
| 5c | a season holding both numbers and words where the **word wins** → no verdict |
| 5d | ≥70% of an event's graded entrants nuked → the uncertain remainder goes too |
| 5e | one race, one season, one bare word → no verdict |
| 6 | every examined season gets a row, including `no_evidence` |
| 7 | trust: <2 races → rated but kept off the boards |

★ **The word fold was counting spellings, not races.** An athlete covered by
both feeds has every race stored twice — anet writing `12`, tfrrs writing
`SR`, the same finishing time to the hundredth. Diego Eseverri's academic 2025
is ~20 races held as 40 rows, and the fold read it as 19 numeric votes against
21 word votes. Neither number is evidence about his grade; they are the two
feeds' **coverage**. tfrrs caught a race or two more, so `SR` won, so a Florida
twelfth grader was corroborated as a college senior. Fixed by collapsing to one
row per race (`raceKinds`) *before* counting kinds.

★ **Rule 6 exists because absence and ignorance look identical to a LEFT
JOIN.** Alexa Hernandez has one race, its grade reads `-`, `normGrade`
correctly nulls it, no rule reaches her, no row is written — and `resolvePool`
then falls through to the raw grade and then to the **school name**, putting
her third on the national MS(F) board on the strength of "Stockton Rising
Club". Now she gets an explicit `no_evidence` verdict and no pool.

**Trust is a race count and nothing else.** The first draft also distrusted
field-derived verdicts, but `field/ms` alone was 483,555 seasons and
`bare_field` 2,113,684 — distrusting those would empty the middle school
boards and undo the pass that fixed the College board. A method says where a
verdict came from; it does not say how much of it there is.

**`normGrade` now mirrors `GRADE_TO_LEVEL` exactly and then closes.** Ordinal
tails (`9th`, `7t`), the full `_CLASS_ALIASES` table, `RS` (which was missing
and is collegiate), and the banded keys `7-8`/`9-10`/`11-12` are in; the
`ELSE upper(TRIM(g))` fallback is now `ELSE NULL`. That fallback admitted 1.6M
seasons of `-`, `?`, `NA`, `19+`, `13-14` — every one of which corroborated
like a grade, made the season look decided so rule 4 never ran, was constant so
rule 5 called it stale, and overwrote the row's real grade in `resolvePool`.

---

## 5. HAND-MAINTAINED LISTS IN `pool_resolve.py`

**Para** — `isParaSchool`. ⚠ `para` is a prefix in ordinary place names.
Measured against the corpus, a bare `\bpara\b` takes **Munno Para** (an
Adelaide suburb) and **Para Los Ninos Charter** (a Los Angeles school, where
"para" is Spanish), while Paramus, Paradise Valley, Paragould, Paramount and
Paraclete all have to survive. So `para` only counts inside `paralympic`,
joined to `sport`, qualified by a sporting word, prefixed by a nationality, or
standing alone. ~2,900 rows, ~450 people, no school.

**Pro teams** — `_PRO_TEAMS`, cut to four HOKA squads. ⚠ A single school string
holds both populations: `Puma` is 161 people with grades 1–12 and a fastest
mark of 6.54 s **and** Alex Botterill running 1:45.6. `Asics` is a youth club
**and** Mark English. The string cannot separate them, so none of those names
are used. Sponsored school teams (`HOKA Aggie Running Club`, `Asics Aggies`)
are out for the same reason — grades 9–12.

**Named individuals** — `_PRO_PEOPLE`, four ids. Each defeated a general rule:

- `32703729` Guillaume Tremblay, Université Laval. Grades 5, 6, 7 across
  2024–26, corroborated, cleanly progressing, describing a **Québec programme
  year** rather than a US grade. Runs 1:54 for 800 m. Nothing in the verdict
  machinery can see that a middle schooler cannot.
- `26054637` Andrew Hunter (`Asics`), `29751385` Fouad Messaoudi (`Morocco`),
  `32542330` David Mullarkey (`Great Britain & N.I.`).

⚠ Keep this list small. A per-person list does not generalise and nobody
maintains it.

---

## 6. PIPELINE

```powershell
python engine\dump_overrides.py          # ALWAYS after any corrections.py edit
python engine\drop_old.py
python engine\grade_sanity.py --write
python backfill\backfill_normalize.py --sport both --apply
del engine\data\packed_XC_TF.npz, engine\data\pair_solve_cache.npz
python engine\speed_ratings.py --sport merged --cache --pack-only
python engine\linkage_check.py --golive
python engine\apply_tilt.py --refresh --write
python racecast\build_ranking_results.py
python racecast\panels.py
```

Or `.\run_rest.ps1`, which preflights the files, refuses to start if `app.py`
is running, waits for a running `grade_sanity`, logs per step, and **stops on
the first non-zero exit**.

⚠ **`dump_overrides.py` is the step everyone forgets.** It turns
`corrections.py` into the `dist_override` table. Editing the file changes
nothing until it runs — we lost a full pipeline pass to this.

⚠ **No `--split` on `linkage_check`.** It hardcodes `ridge = 0.0` and stalled
at relative residual 1.000e+00 on the first CG iteration. It does not converge
on this pack.

⚠ **`--sport merged`, not split.** The pair solver already models sport
per-athlete via `beta` while keeping one shared `alpha`, which is what makes a
146 in XC mean the same as a 146 in TF. Splitting the pack would give two
independent abilities and remove that property.

**Ordering that matters:** `pro_flag` before `grade_sanity` (pooling reads
both). `backfill_normalize` after `grade_sanity` (it pools from `grade_fix`,
and with per-pool anchors a disagreement writes `normalized_time` on the wrong
scale — a 64% rating error frozen into the row). `panels` after
`build_ranking_results` (it reads that output now).

---

## 7. DISTANCE TOOLS

**`audit_overrides.py`** — removes bad overrides. Now reads a two-source
`div_distance` table (`meets` ∪ the `meets_tfrrs.division_distances` blob).
⚠ It used to join `meets`, which is anet-only, so every tfrrs division came
back `stored = NULL` and `judgeRemoval` bailed on "no sane stored distance to
fall back to" — the override survived because the fallback could not be *seen*.
Of 449,748 divisions only 3,750 were even eligible. **42 removals written.**

**`propose_distances.py`** — new. Proposes corrections the way removals work:
an outlier for its class, **plus a candidate distance already in use at the
same meet or course**. The implied distance says *which* candidate; it cannot
invent one. Meet siblings rank above course history.

**`dump_overrides.py`** — rewritten. One row per division (the table now has a
primary key; it used to insert both values of a conflict, and `dist_override`
had no unique key, so the backfill's LEFT JOIN would fan out). On conflict
`DIST_PROPOSED` wins over the hand-written dict.

★ **That precedence is the opposite of what it looks like it should be, and
the data decided it.** The first two conflicts: `(9640, 2)` and `(9756, 1)`,
both hand-written 6000, both proposed ~5000, both actually 5k races. A
proposal is corroborated by other divisions at the same venue; the hand-written
value is one person's recollection from an unknown date.

★ **And the field shift must be computed from `speed_rating`, not
`normalized_time`.** `normalized_time` has no difficulty applied — difficulty
enters at the rating step — so a hard course and a wrong distance are literally
the same number in it. `speed_rating` has difficulty divided out. Switching
took the removals from 91 to 42 and the proposals from 390 to 72, and the class
baselines tightened from 0.941–1.031 to 0.956–1.011. The dropped ones were hard
courses being condemned for their terrain.

---

## 8. WEBSITE

**Best times board** (`board=pr`) — ranks the clock, not the rating. One row
per athlete (a PR is singular). ⚠ Its candidate set is ordered by **time**, not
rating: ordering by rating would pull the best-*rated* times and then sort by
seconds, quietly dropping a fast time run at an easy venue.

29 distances including the imperial XC ones (1.5, 2.5, 3, 4, 5 miles). Matching
is a **±0.25% band**, not equality — `ranking_results.distance` is whatever the
meet recorded, so a five-mile race appears as both `8047` and `8046.72`, and
one 5000 is stored as `4988.9663`. ⚠ 0.25% is chosen, not rounded to: the
metric/imperial pairs sit 0.56% apart, and at 0.5% the windows for 1600 and
1609 overlap so a race recorded as 1604 appears on both boards.

**Scope** — USA by default, via an explicit 51-code list. ⚠ Not
`state IS NOT NULL`: a Canadian meet reads `QC`/`ON`/`BC`, so a null test keeps
exactly the athletes it is meant to exclude.

**`min_races`** — 20 for TF, 8 otherwise, in both the API and the input box.
⚠ The flat 20 was argued from noise and is right for track; it is *impossible*
for cross country, where a season is 8–12 races. It was silently emptying every
XC board, which is why `sport=both` looked like it only loaded track.

**Performances is uncapped.** `MAX_PER_PERSON` is gone: a board headed "best
races" that omits some of the best races is a different list from the one it
promises. Removing it is also what made the board rankable — with a cap a row's
position depends on a window function over everyone above it.

**Athlete search works on all three boards**, by counting rather than sorting
(`_rankInResults`). ⚠ Default sort only; the route refuses other sorts rather
than returning a plausible wrong number.

---

## 9. OPEN

1. **`build_ranking_results` has not run since `distance` and `event_id` were
   added to `_COLUMNS`.** Until it does, the Best times and Performances boards
   return a 400 naming the missing column. `createShadow` migrates the schema
   itself, so one run fixes it.
2. **Field-events marks board.** `results_tf.mark` is **text**, and its
   commonest values are `NH`, `ND`, `DNS`, `SCR`, `FOUL`. Real marks are
   feet-inches (`4-06.00`), where `5-00.00` outranks `4-10.00` and sorts below
   it as text. Two spellings per event (`shot` and `Women's Shot Put`).
   Unanswered: does `event_type_id` already unify them? That query decides
   whether a normaliser is needed.
3. **`nothing to say` jumped 20,331 → 736,552** between runs, 36×, unexplained.
   May be legitimate (5c/5d nuking upstream) or a bug in rule 6's reachable set.
4. **`bare_class` fell 197,180 → 58,802 and `bare_field` 29,356 → 23,100**
   across runs. Those passes fix the Florida cohort and they are shrinking.
5. **Suspicion marker on the website.** `grade_fix.trust` exists; the athlete
   page should flag untrusted results rather than showing a board-ineligible
   number silently. **Explicitly requested.**
6. **`unlink.py`** was patched for the racing-gap signal but only dry-run once.
   `person_split` exists from an earlier write, so a second `--write` would
   split already-split people. Confirm intended state before running.
7. **Colussi (29812759)** reads `college` from age 13. Trust removes him from
   boards; the verdicts are still wrong. Cosmetic vs actual — owner's call.
8. **Perschon (30193425)** comes out `contradicted` — one anet `12` against
   four tfrrs `SR`, words winning on coverage. Correct under the standard
   (exclude rather than guess), but he is a real Lake Central senior.

---

## 10. GOTCHAS EARNED THIS SESSION

- **A patch script that asserts before writing loses every edit in the file.**
  This cost three separate re-dos, including one that crashed the pack an hour
  in. Apply edits as one transaction or write after each.
- **`CREATE OR REPLACE FUNCTION` cannot change a return type.** `raceIdent`
  went text → bigint and needed an explicit `DROP FUNCTION` by signature.
- **A correlated subquery over a CTE re-runs per row.** Rule 5e's
  "how many seasons does this person have" ran 92 minutes before it was killed;
  `count(*) OVER (PARTITION BY person_id)` answers it in the existing pass.
- **A temp table cannot be scanned in parallel.** `allraces` is `UNLOGGED`
  now, which is what makes `max_parallel_workers_per_gather` do anything.
- **`raceIdent` returning text was a third of the runtime.** It is
  `GROUP BY`'d and `count(DISTINCT)`'d across 225M rows in four places; as an
  integer those are machine words.
- **Normalising ~500 distinct grade spellings beats calling `normGrade` 225M
  times.** Four regexes per call over the whole corpus, to answer a question
  with a few hundred distinct inputs.
- **`$ErrorActionPreference = "Stop"` plus `2>&1` aborts on any stderr write.**
  Python writes ordinary progress there. Judge failure on `$LASTEXITCODE`.
- **`$LASTEXITCODE` persists**, so a step with no native command is judged on
  the previous step's result.
- **A hardcoded error message is worse than a stack trace.** "the distance
  column is not there yet" was wrong — it was `event_id` — and it looked like
  an answer.
- **Hiding an `<option>` does not deselect it.** A hidden option that is still
  current keeps its value and gets sent.
- **A marker that survives the thing it detects is not a marker.** `run_rest`
  polled for `bare_field` rows to know `grade_sanity` had finished; an earlier
  run had already written 2.1M of them, so it never waited.
