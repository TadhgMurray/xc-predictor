# Plan — model corrections and pooling  (2026-09-18, spec only, nothing built)

Owner's list, verbatim:

> **Model corrections:**
> - If a race result normalizes to a 5k (or whatever anchor the pool
>   normalizes to) that is a wr in whatever pool (ms/hs/such) is diff, nuke
>   entire race ratings.
> - If a result is more than 5-15 sigma away (from their season's median or
>   mean or whatever) do not rate or rank.
> - Overturn every other current override (and also cull in corrections, back
>   it up, but cull so import isn't so long, so back it up, then empty it,
>   then add these).
>
> **pool:**
> - pool by anet pool, update grades in races/seasons accordingly (college
>   pool but rows say 11 -> JR-3). For clubs if anybody we've marked as pro is
>   in a club, make entire club pro. Anything without a team id in anet is
>   pro. Anything with like 3 athletes is pro (dawgsmenesch).

Read the standing rule in `HANDOFF-2026-09-18.md` first: **when in doubt,
separate.** It applies here too — a wrongly *pooled* athlete is compared
against the wrong field, which is the same class of harm as a wrong merge.

---

## Correction 1 — the WR bar per pool. **THIS ALREADY EXISTS.**

`engine/record_pace.py` + `engine/impossible_race.py` implement exactly this
rule, from the owner's own instruction on 2026-09-14 ("impossible times: do
for every but college pool. And if this happens for a race, make the entire
race unrated / unranked"):

- `_WR_PACE` — open world-record pace per distance and sex, 55m to 10,000m,
  interpolated in log-distance. Not monotonic on purpose: 100m is the fastest
  pace ever run.
- `PACE_FLOOR_SLACK` — 2% inside the record is timing and rounding, not a
  record.
- `POOL_PACE_FACTOR = {"hs": 1.01, "ms": 1.12, "elem": 1.35}` — the per-pool
  anchor the owner is asking for.
- `impossible_race.py` writes the offending races down; the pack, the fill and
  the boards read that table and drop the whole race.

**So the work here is verification, not construction.** Three questions to
answer with measurements before changing anything:

1. Is it actually running in the current pipeline, and how many races does it
   currently nuke? If the answer is ~0, something upstream stopped feeding it.
2. `EXEMPT_POOL_PREFIXES = ("college", "pro")` — college and pro are exempt
   entirely. That was right for *pool-scaled* bars, because a college runner in
   an HS-labelled race trips an HS bar legitimately. But **no race of any pool
   can beat the true open WR**, so the unscaled bar should apply universally
   and would catch genuine garbage in college/pro races that nothing catches
   today. Proposed change: keep the pool-scaled bar exempt for college/pro,
   apply the unscaled WR bar to everything.
3. Are the pool factors right at the anchor distance the pool normalizes to,
   which is what the owner actually asked about? `ms` at 1.12 says a middle
   schooler may run within 12% of the open world record. That is worth
   measuring against the fastest legitimate ms marks in the corpus.

## Correction 2 — sigma outliers. **NEW.**

No equivalent exists. `engine/grade_sanity.py`, `pair_validate.py` and
`speed_ratings_kernels.py` use sigma-like quantities but none of them is "drop
a result that is N sigma from that athlete's own season".

Design questions that change the answer, and which I want measured before
picking:

- **Spread, not sigma.** A season has few races and one huge outlier inflates
  its own standard deviation, hiding itself. Use a robust spread — median and
  MAD (`1.4826 * MAD` approximates sigma) — or the outlier defeats the test it
  is the subject of. This matters more than the threshold.
- **Which side.** A 10-sigma *slow* result is a jog, an injury or a workout,
  and is ordinary. A 10-sigma *fast* result is a data error. These should not
  share a bar; the fast side should be tight and the slow side loose or absent.
- **How few races is too few.** With 2 races a MAD is meaningless. Below some
  count, fall back to the pool-wide spread rather than the athlete's own.
- **Do not rate vs do not rank.** The owner said both. Dropping from ratings
  changes the model's input; dropping from ranks is cosmetic. Cheapest safe
  first step is rank-only, then ratings once the count is known.
- **5 to 15 sigma is a wide range** — with MAD-based spread I would expect the
  useful bar near the low end. Land it as a config constant and report the
  count at 5, 8, 10, 15 before choosing.

## Correction 3 — cull the overrides. **ALREADY BUILT, and safe.**

An earlier draft of this doc called it dangerous on the grounds that
`engine/corrections.py` is a ~52MB source file holding decisions git has never
seen. That is true of the file and wrong about the risk, because it ignores
which parts are regenerable. `scripts/wipe_overrides.py` already encodes the
real distinction:

    pass 1 -> _DISTANCE_OVERRIDES     (regenerated)
    pass 2 -> _RESULT_OVERRIDE        (regenerated)
    pass 3 -> _RESULT_DROP            (ADDS to it, never rebuilds it)

- **The overrides are regenerable.** Culling them costs a rebuild, not a loss,
  which is exactly the owner's ask ("cull so import isn't so long").
- **The drops are not.** Pass 3 only finds rows that are still rated, so a row
  dropped earlier can never be re-found. Clearing the drops deletes filtering
  with nothing to replace it. `wipe_overrides` deliberately leaves them.
- **It appends a `.clear()` block rather than deleting lines**, so the last
  word wins, `--undo` is deleting five lines, and the 1.45M-line record stays
  in git. Better than backup-and-empty, which trades the record for the
  working set.

So this is `python scripts/wipe_overrides.py` to report, then `--write`. The
only genuine caution left: do it *after* the rules meant to supersede the
hand-patches are in, or the rebuild regenerates the same overrides from the
same unfixed inputs and nothing is gained.

## Pooling — `pool by anet pool`

The strongest part of the list, and it is the same principle as the team-id
rekey: **anet states the pool; stop inferring it.**

1. **Pool from the anet team, and fix the grades to match.** `college pool but
   rows say 11 -> JR-3`. `anet_team.level` is already stored (4 = hs, 8 =
   college, 16 = club — measured, see `link_tfrrs_to_anet`), and
   `engine/college_flag.py` currently keys pools on the **school string**.
   Keying on `team_id` instead is Phase 4 of the team rekey, so **this should
   land with that, not before it** — otherwise it is built twice.
2. **A club with a known pro in it is a pro club.** Transitive, cheap,
   one query. Needs a floor: one mis-tagged pro should not promote a youth
   club, so require the pro to have real rows for that club.
3. **No anet team id ⇒ pro.** RECOMMEND AGAINST, and the owner's own
   qualification ("that aren't linked ig") is the reason it does not work.
   After the bridge runs, what is left unlinked is mostly small COLLEGE
   programmes that missed the vote bars — and those bars were just raised, so
   more of them will. A college is not a pro. Unlinked should keep whatever
   `college_flag` makes of its school string, exactly as today, and `pro`
   should require POSITIVE evidence: a `pro_athlete_season` row, an anet club
   level, or a roster too small to be a school. Absence of a link is absence
   of evidence, not evidence of pro.

4. **A team with fewer than 15 distinct athletes is pro.** Owner's decision,
   2026-09-18, after I recommended using `anet_team.level` instead: *"if
   they're that small I'd prefer to make them pro. also ~3 athletes should
   actually be 15 and I mean it."* Taken as given.

   `PRO_MAX_ATHLETES = 15`.

   ⚠ **The window is the whole question, and it is LIFETIME, not per season.**
   Per season, a rural high school fielding eight runners is under the bar
   every year of its existence, and the rule would pro half the countryside.
   Over the team's whole history almost any real school clears 15 easily, so
   the same number catches only genuinely tiny entities — which is what the
   owner is describing. Distinct `person_id` across `results` + `results_tf`
   for that `team_id`, all years.

   ! It is a floor, not an override. `anet_team.level` is still the primary
     signal where it exists (level 16 = club, confirmed by the census: Oregon
     Track Club, Nike Oregon Track Club, Georgetown Running Club); this rule
     catches what has no level, plus the small entities the owner wants caught
     regardless.

   Before it ships, print the count it would move and the largest twenty
   teams it catches. If real schools are in that list, they are the price the
   owner has accepted — but they should be seen, not discovered later on a
   rankings page.

## Built so far (2026-09-18)

`engine/build_team_pool.py` — `team_pool (team_id PK, kind, reason,
n_athletes, n_rows, level, n_pros)`. One row per anet team saying which pool
it is and why. `kind` ∈ pro, club, college, hs, ms, elem, unknown.

Precedence, pinned in `tests/test_team_pool.py`:

1. `n_athletes < 15` (all time, both feeds) → **pro**. Smallness beats anet's
   level, on the owner's instruction.
2. any professional athlete-season on its rows → **pro**.
3. anet level club → **club**.
4. any other anet level → that level.
5. no level → **unknown**, never pro.

Two of the owner's four rules turned out to be already implemented:
`loadClubPros(min_pros=1)` is exactly "any pro in a club makes the club pro",
and `loadTeamLevels` already names `club` from the anet code. So this defers to
both rather than reimplementing them, and the *athlete* pool question stays in
`loadClubMajority`, which holds the 2026-09-14 rule that a collegian racing
the Euros is not a professional — a team-level flag must not override that.

Nothing reads `team_pool` yet. Wiring the readers is deliberately separate, so
the classification can be inspected before it can move a rating.

    python engine/build_team_pool.py --dry-run --show 40

The dry run prints the per-kind counts and the teams the 15-athlete rule
catches, ordered by ROW count — a team with fourteen athletes and five
thousand rows is the suspicious shape, either a real programme whose people
are mis-merged or a relay squad raced to death.

## Suggested order

1. Verify Correction 1 is running and count what it nukes (no code).
2. Pooling 2 (pro clubs) — self-contained, cheap, safe.
3. Correction 2, rank-only first, with counts at several thresholds.
4. Pooling 1 with Phase 4 of the team rekey; pooling 3 after Phase 2.
5. Pooling 4 (PRO_MAX_ATHLETES = 15, lifetime distinct athletes) — print what it moves first.
6. Correction 3 last, and only after `corrections.py` is in git or off the box.
