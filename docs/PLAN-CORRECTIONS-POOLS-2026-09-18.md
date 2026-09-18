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

## Correction 3 — cull the overrides. **DANGEROUS. READ THIS FIRST.**

Corrections are **`engine/corrections.py`, a ~52MB Python source file**, not a
table. `scripts/backup_corrections.py` already exists and is the right tool:
it verifies the copy by hash, has `--list` and `--restore`.

⚠ **Its own header is the warning:** on this machine the working file held
**5,248 distance and 2,065 result overrides against HEAD's 4,114 and 623**.
That is ~1.6MB of hand-made decisions *that git has never seen*, and `*.bak`
is gitignored, so a backup lives on **one disk with nothing replicating it**.

So before emptying anything:

1. `python scripts/backup_corrections.py` — the hash-verified local copy.
2. **Commit `engine/corrections.py` to git, or copy it off the box.** Step 1
   alone is a seatbelt for ten minutes, not an archive. Emptying the file
   after only step 1 means one disk failure destroys every override ever made.
3. Only then empty it, and add the rule-derived corrections.

"Overturn every other current override" is the owner's call and the reason is
sound — the rules above should supersede hand-patches, and a 52MB import is
slow. But it is irreversible in a way nothing else in this plan is, so it goes
last, after the rules that replace it are in and measured.

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
3. **No anet team id ⇒ pro.** ⚠ This one needs a number before it ships.
   Every tfrrs row has no anet team id — 37.1M rows, 0% coverage (see the
   census). Read literally, this makes the entire tfrrs corpus pro. It must
   mean "no team id *after* the tfrrs bridge has run", which makes it Phase 2
   dependent, and even then the residue should be measured before the rule is
   applied rather than after.
4. **A team with ~3 athletes is pro** ("dawgsmenesch"). Plausible — a real
   school has a roster — but it is a size heuristic and will catch tiny real
   schools. Per the separate-over-merge rule, being wrong here is a *pooling*
   error, which is harmful, so measure the distribution of team sizes and pick
   the floor from it rather than from the example.

## Suggested order

1. Verify Correction 1 is running and count what it nukes (no code).
2. Pooling 2 (pro clubs) — self-contained, cheap, safe.
3. Correction 2, rank-only first, with counts at several thresholds.
4. Pooling 1 with Phase 4 of the team rekey; pooling 3 after Phase 2.
5. Pooling 4 once the team-size distribution is known.
6. Correction 3 last, and only after `corrections.py` is in git or off the box.
