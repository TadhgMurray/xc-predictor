# Handoff — 2026-09-11 (engine research and fixes)

Everything below is on `claude/youthful-gates-o25yq9`, unmerged. The
previous state of the server and the site is `docs/HANDOFF-2026-09-08.md`;
the engine's own description is `docs/ENGINE.md`; the survey of how other
systems solve this engine's problems, and what changed today because of
it, is `docs/RESEARCH-ENGINE-2026-09-11.md`. Read that one first.

**Nothing today touched the database or ran on the box.** The sandbox has
no Postgres and no pack. Every change is covered by a synthetic test that
runs here; the first live log decides.

---

## 1. WHAT CHANGED, IN ONE TABLE

| where | what | why |
|---|---|---|
| `joint_solve.nestedPosteriorVar` | the EM E-step's `Var(d)`, `Var(u)` are the exact (cell + its races) block, not `sigma²/A_ii` | the diagonal goes to zero with more rows of ONE race; the truth does not. `sigma_u` was under-stated and every thin course kept too much of one day |
| `joint_solve` + `run_joint` + `speed_ratings_db`/`speed_ratings` | meet-importance term `imp[pool, sport, class]` from a new pack column `meet_class` (meet name → 0/1/2) | a championship-only venue booked the taper as an easy course (Foot Locker, state meets). Beyer's class pars, FIS's zero penalty: a class effect, never a venue's |
| `joint_solve` + `run_joint` | indoor as one coefficient per pool, folded into `delta` | 1,574 indoor cells each rediscovered a ~1% effect under a 1.6% prior; no location hosts both |
| `joint_solve.Design.mu_fixed`, `run_joint --sport-level G` | the XC/TF level ASSERTED and taken out of theta | sport is season; nobody identifies this from results (Tully, NCAA, WMA all state it). `--merge-sports` left `mu` free and unpenalised |
| `joint_solve.DIST_BANDS` (4 bands), `engine/distance_tables.py` | a top band from 135; the World Athletics / Purdy relation as the prior mean of an event offset the pairs cannot calibrate | the exponent rises as ability falls; the tangent extension beyond 3200 is unfounded (college 10k rode four pairs) |
| `racecast/conversions.py` | the forward map solved as a fixed point; the offset continuous in the rating; stored rows invert their own rating | #21: −3.34% reproduced as one inter-band step taken on one leg only |
| `engine/joint_golive.py` | `engine_scale.anchor_shift` = the anchor applied; zero = the average OUTDOOR track | the shift had not followed the display anchor; every named-venue conversion carried the XC/TF gap |
| `racecast/panels.py`, `racecast/app.py`, `athlete.html` | no second tilt on the home boards; the athlete header's equivalence goes through the page's inverse and names the pool's distance | tilting twice; "about a 12:40 5K" for a college man was an 8000 m time |
| `deploy/run_pipeline.sh` | `XCP_SPORT_LEVEL`, `XCP_NO_IMPORTANCE`, `XCP_NO_INDOOR`, `XCP_NO_DIST_TABLE`, on 08 and 08a | the holdout must score the shipped model |
| `joint_solve.altDistanceFactor`, `run_joint` | altitude exposure and credit scaled by the event's share of the 5000's cost (800 a fifth, 10k a bit more) | NCAA tables: one coefficient per sport over-credited the 800 and under-credited the 10k |
| `scripts/ablation_ladder.py`, `run_joint --diag-var` | rungs `diag-var`, `no-importance`, `no-indoor`, `no-dist-table`, `stated-level` | every new term is scored on held-out races by 08b |
| `speed_ratings_db` | `meets_tfrrs.is_championship` marks class 2 for tfrrs XC meets, before the name regex | the feed's own flag beats a name |

Tests added: `tests/test_nested_variance.py`, `tests/test_shared_terms.py`,
round-trip cases in `tests/test_conversions_engine_scale.py`. Updated for
the new behaviour: `tests/test_joint_dist.py` (four bands),
`tests/test_conversions_distance_offset.py`, `tests/test_split_ability.py`
(two stale string matches that failed on master).

## 2. THE NEXT RUN

The pack needs `meet_class`, so from 7:

```
cd /srv/xc-predictor && git pull
set -a; . /etc/xc-predictor.env; set +a
XCP_ALTITUDE=1 bash deploy/run_pipeline.sh --from 7 2>&1 | tee logs/run19.out
```

That is today's shipped model plus the E-step, the importance term, the
indoor term, the table prior and the fourth band, with the level still
**estimated**. To apply the stated scale instead (the owner's definition:
track is the zero, an average XC course is +6%), add `XCP_SPORT_LEVEL=0.0583`.
Both are the owner's call; the research doc (Part I.3, I.4) says why the
level cannot be measured and what every other system does.

## 3. WHAT TO READ FIRST IN `08_golive.log`

```
[joint] meet importance: N of M rows at a championship-class meet        # pack carries it (else: repack)
[joint] indoor: 1,574 of 74,366 cells are indoor tracks
[joint] XC: race-day sd ... course prior ... a one-race course keeps 0.xx  # vs run22: sigma_u a little up, share a little down
[joint] meet importance, log-time per (pool, sport, class)               # c2 near -2%, c1 near -1%
[joint] indoor, log-time per pool                                        # +0.5 .. +1.5%
[joint/live] track zero: mean 0.000 ... over N track cells               # outdoor cells
[joint/live] difficulty zero = the average TRACK. Cross country lands at # the estimate, or "ASSERTED"
```

Then: `scripts/difficulty_spread.py` (the sd of difficulty must now RISE
with `n_results`, not fall); `scripts/venue_check.py --venue 'Balboa'`
and `'Glendoveer'` (championship venues read harder than before);
`scripts/convert_probe.py --tf --person 29603086 --seconds 541.1` (closes).

## 4. THINGS THAT BIT TODAY

- A prior stated in pseudo-rows on a shared coefficient competes with
  `sigma_u²` summed over every race that carries it. State it as an SD.
- A one-race-per-cell world with three races per athlete is unidentified
  for `sigma_u` **even with the exact posterior**; a test built on it
  would have blamed the estimator. The corpus has ~100 rows per race.
- `--merge-sports` never pinned `mu`; its test starts from zero.
- `engine_scale.anchor_shift` and the display anchor were two variables
  for one number, and drifted apart in one commit (9.5 → e6eaa63).
- The `'|'` in `rating_pool` that told solved rows from filled ones went
  away when the go-live started writing bare pools; every stored result
  then took the "filled" path.
- Seven script-style tests fail on master before today
  (`test_anchor_repair`, `test_capped`, `test_pipeline_failures`,
  `test_returning_teams`, `test_shadow_migration`, `test_sprint_boards`,
  `test_track_anchor`) and six pytest-style ones (`test_hs_factor_per_pool`,
  `test_meet_units`, `test_pager_last`, `test_pipeline_shards`,
  `test_pipeline_step_guards`, `test_sport_gain`) — all verified on a
  pristine checkout of the branch head; left alone. `test_rank_line_units`
  and `test_weather_fixes` need the box's `database` module and do not
  collect in a sandbox. Full pytest run here: 281 passed, 55 skipped.
- `test_thin_course_shrinkage` reproduces the OLD collapse and now pins
  `nested_var=False` to do it; `test_track_is_zero` pins the anchor's new
  variable name.

## 5. OPEN

`docs/ENGINE.md` §11, rewritten today: read the ladder (every new term now
has a rung), era drift, posterior sd and `n_results` on the page, the
curve rebuilt on equal-quality pairs, and a per-course tilt only if the
data ask for it. A hyperprior on `tau`/`sigma_u` is not worth building:
with 44k identified cells it is inert, and the observed-sd cap is a
display choice.

**Is the championship help to a course's difficulty automatic and
always applied? (owner, 2026-09-11: "I could see that going very
wrong.")** It is not automatic, and after the fourth commit it is gated.
Measured on a planted world: a genuinely one-race ordinary venue
mislabelled as a championship had its rows over-rated by 2.2% and its
course read one point too hard, because the cell takes a share of the
class's taper whether or not the label is right. Three guards now stand
between a label and a rating, all in `engine/meet_class.py`:

- the invitational guard: a name that says invitational, preview,
  classic, festival, relays or dual is ordinary whatever else it says,
  unless it also says qualifier, championship, final or prelim;
- the season window: a championship-labelled meet outside the weeks
  championships are run (XC mid-October to mid-December, track February
  to the start of July) is ordinary;
- the one-race gate: the term is not applied to rows at a venue with one
  race in the pack. With two or more races the others pin the course and
  the term is a relabel of the day; with one the course would take a
  share of it either way.

The coefficient itself is fitted per (pool, sport, class) with a ZERO
prior mean; the taper literature's minus one and minus two and a half
percent are what a healthy fit should look like in the log
(`IMP_EXPECTED`), not a number that is applied. `scripts/meet_class_census.py`
prints the biggest meet names behind each class on the real corpus, and
`--find preview` looks one name up; run it before trusting the class.

Answers to the three questions asked on 2026-09-11, so they are on file:

- **Faster runners get less of a course's difficulty.** Yes, and it was
  already so: the tilt `h = 1 − 0.0031·(rating − 100)` multiplies the
  course, the sport level and the day, clamped to ratings 70–140. The new
  indoor term is tilted the same way; the distance offsets, altitude and
  the taper term are not, because those are costs of the event, the air
  and the field, not of the ground. The tilt is one global slope, not one
  per course, on purpose: an elite-only field cannot estimate a slope and a
  free per-cell slope is how the issue-156 exploit happened.
- **Fitness and rust are out of a course's difficulty.** Yes, by
  construction: the form curve and the opener rust are fitted jointly with
  the cells, so a course that hosts only November races is not charged
  with the season's form. The taper was the piece that was still landing
  in the course, and the importance term is its home now.
- **Altitude depends on where the runner comes from.** In the solve, yes:
  a row's exposure is the venue's altitude less half the athlete-season's
  home altitude (`ALT_ACCLIM`), so residents' abilities are their
  sea-level speed and the cell is terrain, not the visitors' penalty. In a
  rating the credit is per race, the venue less half the field's mean home
  altitude, so everyone in one race keeps their finish order. Today added
  the event's share of the cost (an 800 a fifth of a 5000).
