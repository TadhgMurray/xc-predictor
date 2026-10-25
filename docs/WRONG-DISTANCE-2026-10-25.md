# Wrong distances: one system (design, 2026-10-25)

Owner's ask: find cross country races stored at the wrong distance, correct
them so the fix sticks, diagnose them afterwards. Never correct a race into
being overrated. Over-find for review, but only act on big errors. Watch for
combined divisions. Fewer correction layers, not one more.

Code: `engine/wrong_distance.py`. Tests: `tests/test_wrong_distance.py`.
Status: **the finder (read only) is built. The writer waits until the owner has
read the finder's list.**

---

## 1. The evidence: raw times, not ratings

For every runner in a division, compare their time here at the **listed**
distance with their own other races that season, all scaled to 5000 m with
`t ~ d^1.06`:

    e   = ln(time) - 1.06 ln(listed / 5000)       this race, as a 5K
    res = e - median(e over the runner's other races that season)

The division's number is the median `res` over its runners (at least 3 other
races each). A 5K stored as 6K reads about -19%: the field ran 19% faster than
it normally does. A men's 8K stored at 5K reads about +50%.

Why this and not the rating gap that `data_checks` race_field uses:

- **It doesn't change when a pin is applied.** It never reads a
  speed_rating, a course number or an override. race_field's evidence
  vanished as soon as its own pin fixed the race, so its pins had to be
  "sticky". This evidence can re-judge every pin on every run.
- **The course and race-day terms can't soak up part of the error**, so a
  10% error reads as 10%, not 6%.
- Runners' other races that are themselves off by 3% or more are left out
  of the reference (a second pass), so one wrong race doesn't spoil the
  references for the next.

### Taking the course out

A hard course reads slow every year at the same label. A wrong label reads
off on **one** day. So the division's number is compared with a course baseline:

1. the venue's **other meets** at the same listed distance (2+ of them), else
2. **the meet's other divisions** (same course, same day), else
3. nothing (0).

A baseline beyond ±12% isn't a course effect. It means every race there
carries the same wrong label, so it's listed as `venue_wide` for review and
not used.

### The independent witnesses (each adds a point)

| witness | point when |
|---|---|
| field | 6+ runners with other races, 70%+ off the same way (**2 points** for 15+ runners at 85%+) |
| the meet's other races | ran true (within 4%) while this one didn't |
| a sibling division | is stored at the proposed distance |
| the venue's other days | at this label ran true (within 5%) |
| the venue's own history | has run the proposed distance on 2+ other days |
| other years here | same venue, label and level: this year's raw median is 8%+ off the same way |
| raw pace | the field median is in the fastest 3% (or slowest 3%) of fields at its level, at the listed distance |

**high** = 4+ points, **medium** = 3, **low** = 2 or fewer. Confidence is
reported and orders the list. It does **not** decide whether a downward pin
is written (owner, 2026-10-25: "the main thing isn't confidence but that you
don't overestimate the distance"; see §3).

Runners with too few other races (one-meet middle schoolers) can't testify. A
division with too few referenced runners is judged on raw pace alone, and
only gets listed (`pace_outlier`, review).

## 2. Thresholds and why

| | value | why |
|---|---|---|
| distance exponent | 1.06 | what every distance tool here uses; the 5% snap tolerance absorbs its error |
| looked at | field 8%+ off (raw, or against the course baseline) | field_shift measured normal racing ending around 8-9% |
| same way | 70% of runners 3%+ off the same way | a wrong distance moves the whole field; a few fast runners don't |
| runners | 6+ with other races to be judged, **10+ to be pinned** | under 10 a few odd runners can carry the median; listed for review instead |
| snap | implied distance within 5% of a real one | a real one = standard XC distance (1609, 2000, 2414, 3000, 3218, 4000, 4828, 5000, 6000, 6437, 8000, 10000) or one the venue ran on 2+ other days (Mt. SAC's 4715) |
| **big move** | **proposed differs from listed by 12%+** | the smallest real error, 5K vs 6K, is 18%. Label differences sit under it: 4828/5000 3.5%, 3000/3218 7%, 6000/6437 7%, 4715/5000 6%. These are counted as `small_label` and **never acted on**. |
| combined | two clusters 12%+ apart, the smaller 3+ runners and 15%+ of the field, gap at least twice either cluster's spread | see §4 |

Mt. SAC check: a championship field 3% fast at a true 4828 is not even looked
at. 8% fast implies 4477, which is near no real distance, so it gets
`off_no_standard` (review only). To get proposed down to 4000 a field would
have to run about 20% faster than its own season, which no fast day does.

## 3. Direction: always underestimate

`normalized = raw * (5000/d)^1.06`, so a stored distance LONGER than the truth
**overrates**.

- **Never a distance longer than the times say.** Whatever is written sits
  at most 2% (the estimate's noise) above the distance the field's times
  imply. If the nearest real distance is longer than that, the next real one
  below is used. If there is none within 5% below, nothing is written and the
  race is listed.
- **Down (listed too long, field fast)**: the case this system is for.
  **`pin`, written automatically, at any confidence**, once the bars hold:
  a 12%+ move onto a real distance, 70%+ of the field the same way, and 10+
  runners with other races. A downward pin only lowers ratings, and never
  below what the times say.
  - A venue where **every** day reads 20% fast is pinned too. No course is
    that fast, so the label is wrong every year.
- **Up (listed too short, field slow)**: these runners are already
  **underrated**, which is safe. Raising the distance raises ratings, so it
  is **never written automatically**. The harm a too-short label does to
  others is through the course: the slow times make the course look hard,
  and everyone else there gets too much credit (Al Shirley 2026). So the
  action is **`novote`**: keep the listed distance and stop the division
  voting on the course. `pin-up?` (owner only) needs `high` plus a sibling
  stored at, or venue history of, the proposed distance.
  - Except at a venue where **every** day reads slow. That is a hard course
    far more often than a label that's wrong every year (Steens Mountain
    +41%), and novote on all of them would leave the course no difficulty.
    Listed only.
- **Ambiguous candidates** (two within the 5% snap, e.g. 4828 and 5000):
  the one the meet or venue actually runs. Otherwise, or if it runs both, the
  **lower** one, which gives the lower rating.
- **Backstop**: the backfill already refuses any override that raises a sane
  listed distance (`backfill_normalize._resolveDistanceGender`). That stays.

> Found while reading: that backfill clamp means `gender_pace_pin`'s pins
> (all upward, e.g. Al Shirley 5000 → 8000) **never change normalized_time**.
> But they do reach `dist_override`, so the solve uses the 8000 as the row's
> distance and makes the division venueless. They're half-applied. The new
> system replaces them with `novote`, which is what they were for.

## 4. Combined divisions

One division holding two races (girls' 4K inside the boys' 5K; a JV 5-mile
and a women's 5K under one tfrrs heading). Pinning the whole division to one
distance is wrong for half of it, so that is never done.

- Detection: each runner's own `res` is split into the best two groups. Two
  races show up as two tight clusters 12%+ apart. One smeared field doesn't,
  and neither do three outliers.
- Each cluster's implied distance is snapped separately. Ties go to the listed
  distance.
- Then: is the split explained by **gender** or **level** (ms/hs)? If 85%+
  of each value lands in one cluster, the split is "girls ran 4000, boys
  ran 5000" → `split`, high.
- Writer (proposed): pin **the fast cluster only, and only downward**, keyed
  by the attribute that explains it (division + gender, or division +
  level). The cluster that ran the listed distance keeps it. If no attribute
  explains the split, nothing is pinned per runner automatically: the
  division gets `novote` and goes on the review list.

## 5. One place for pins

A Postgres table, written only by this system (and by the owner through it):

    distance_pin (
      source, meet_id, div_id,
      part        text   -- '' whole division | 'g:F' | 'lvl:ms'  (combined)
      listed      real,
      distance    real,  -- the corrected distance; NULL for novote / veto
      action      text,  -- pin | novote | veto (= use the listed distance, drop an old override)
      status      text,  -- active | retired | held (owner: keep) | refused (owner: never)
      kind, confidence, points, evidence text,
      set_by      text,  -- auto | owner
      first_seen, last_judged, retired_at, retired_why,
      PRIMARY KEY (source, meet_id, div_id, part))
    distance_pin_log (at, source, meet_id, div_id, part, old, new, why)  -- append only

- **Evidence per pin**: the same sentence the finder prints, refreshed on
  every run.
- **Reversible**: nothing is ever deleted. A retired pin keeps its row and
  its reason. One SQL statement, or `--hold`/`--refuse MEET/DIV`, overrides any
  verdict.
- **Sticks across runs**: it lives in the database (backed up nightly), not
  in the 165 MB generated `corrections.py` that `rebuild_overrides` can
  regenerate.
- **Read by**:
  - `backfill_normalize`: loads the active rows once at start (a few
    thousand). Precedence: a pin or veto, then the existing dicts, then the
    listed distance. The upward clamp stays.
  - `dump_overrides`: pins go into `dist_override` at top precedence, vetoes
    remove their key, and a new `novote` column marks novote rows.
  - `speed_ratings_db._xcQuery`: one more line in the venue CASE,
    `WHEN dov.novote THEN NULL`.
  - Everything that already joins `dist_override` (the site, rankings,
    predict) is unchanged.
- **The tfrrs key fix**: rows are keyed by source, so a tfrrs (meet,
  ordinal) can't collide with an anet (meet, div). `_DISTANCE_PIN_FIELD_TFRRS`
  existed only to work around that.

## 6. How a wrong pin comes off by itself

1. **Every run re-judges every active pin** from the raw-time evidence at
   the **listed** distance. Pinning doesn't change that evidence, so a
   correct pin keeps getting confirmed. To stay, a pin needs a field 6%+ off
   (8% to write one) and must still be no longer than the times say, so it
   doesn't flap on a run-to-run wobble.
   When the evidence no longer supports it, the pin is retired with the
   reason logged, and the division goes back to its listed distance on that
   run's backfill.
2. **A second witness after the solve**: a pinned division whose field then
   reads 9%+ **below** its own level was over-corrected (the Catholic League
   2 miles pinned to a mile). It's retired on the next run. This is
   data_checks' existing "rejects" check, moved here.
3. **A circuit breaker**: if a run would add more than 300 pins, or retire
   more than 300, it writes nothing and says why (`--allow-many` to
   override). venue_label wrote 14,802 pins in a week; this rule would have
   stopped it on day one.
4. `held` and `refused` are the owner's word and are never changed
   automatically.

## 7. What it replaces

| today | after |
|---|---|
| `data_checks.py` race_field **pins** + the sticky block (`_DISTANCE_PIN_FIELD`, `_DISTANCE_PIN_FIELD_TFRRS`) | **replaced.** race_field keeps its rating-gap *listing* (it also catches non-distance faults); it no longer pins. The 229 remaining pins are judged once and moved into the table (or vetoed). |
| `data_checks.py` venue_label | **deleted.** Report-only already; `small_label` covers it. |
| `gender_pace_pin.py` (04g0) | **replaced** by `novote` / `pin-up?` (§3). Step removed. |
| `audit_overrides.py` (04g): removes overrides the field rejects; CSV proposals | **replaced.** Every `dist_override` entry is judged by the same evidence (finder: "EXISTING OVERRIDES"). An override **longer** than the times say (it overrates) is replaced automatically, whoever wrote it. One **shorter** than the times say (it underrates by 5%+) is replaced if generated, and **listed** for the owner if hand-typed (`_DISTANCE_OVERRIDES_XC`). It is replaced by this system's pin, or by the listed distance when the times say that is right (a `veto` row). If neither fits, it stays and is listed. |
| `propose_distances.py` (DIST_PROPOSED), `distance_repair.py`, `distance_pin.py`, `kill_bad_overrides.py`, `rebuild_overrides.py` (manual, not in the pipeline) | **retired as writers** (code left in place; a guard stops `--write`). Their past entries stay in corrections.py, frozen, and are judged like any override until each is moved into the table or vetoed. Then those dicts can go. |
| `bad_distance.py`, `impossible_distance.py`, `scripts/why_*` | read-only diagnostics, kept. `wrong_distance.py --explain MEET/DIV` is the distance diagnostic. |
| `dump_overrides.py` (04g2) | **kept**: still the one materialiser of `dist_override`, plus the pins, vetoes and novote. |

Pipeline after: `04g1 wrong_distance --write` → `04g2 dump_overrides`. Steps
04g0 and 04g (audit) are gone, and data_checks still runs but writes no pins.
That's one writer of distance corrections instead of five.

## 8. Order of work

1. ✅ Finder, read only: `engine/wrong_distance.py`. Ranked list, counts by kind,
   verdict on every existing override, `--explain`.
2. **Owner runs it on the server and reads the list.** Thresholds get adjusted
   from what it shows.
3. Writer: the table, `--write` with the re-judge and the circuit breaker, the
   backfill, dump and `_xcQuery` readers, the one-time move of the legacy
   generated pins, and the pipeline change. Tests: the pure parts, plus SQL on
   a scratch Postgres (synthetic corpus with planted faults; already used
   by the finder's test).

## 9. Owner's decisions (2026-10-25)

1. **Confidence doesn't gate a pin. Never a distance longer than the times
   say.** Downward pins are written at any confidence once the bars in §2
   hold, never above the field's implied distance (§3).
2. **Upward: `novote` only.** A distance is never raised automatically.
3. **Overrides follow the same rule.** One longer than the times say is
   replaced, whoever typed it. One shorter than the times say is replaced if
   a script wrote it, and listed for the owner if a person typed it.
