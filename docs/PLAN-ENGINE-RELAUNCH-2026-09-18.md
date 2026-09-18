# Engine relaunch — the owner's list, checked against the code

Nothing here is built yet. Each item says what already exists, because three
of the six turn out to be partly done, and one of them is a knob rather than a
rewrite.

---

## 1. Indoor reads easy. **THE BRACKET ENGINE HAS NO INDOOR ANCHOR AT ALL.**

⚠ An earlier draft of this doc answered this from `engine/joint_solve.py` and
was wrong to: the owner runs the **bracketed engine**. joint_solve DID fix this
on 2026-09-11 by asserting `IND_LEVEL_DEFAULT = 0.012` (indoor 1.2% SLOWER,
from the NCAA facility factors), because the indoor level and the winter form
curve are one free direction the fit resolves backwards. `bracket_engine.py`
has no equivalent, which is why the owner says it is not fixed. It is not.

**MEASURED, 2026-09-18** (`scripts/diag_indoor_level.py`, from the published
`course_difficulties` cells — seconds, no row scan):

    surface      cells    median      mean       p25       p75
    indoor       1,575   -0.0168   -0.0152   -0.0259   -0.0044
    outdoor     25,629   -0.0020   -0.0004   -0.0074    0.0050

    indoor median MINUS outdoor median: -0.0148

So **indoor is published 1.5% EASIER than outdoor**, and the quartiles say the
worst cells reach ~2.6% easier. The owner said 4%; the median is 1.5% and the
tail approaches his figure. Either way the SIGN is wrong — an indoor oval is
slower than an average outdoor track, not faster — and the size is material:
1.5% of log-time is several rating points at every distance.

**The mechanism, from `bracket_engine.py:430`:**

> the vote-weighted mean of D per **(sport, era)** is held at zero each pass

and `cell_sport` is one bit — XC vs TF:

    cell_sport = [1 if k.startswith("TF:") else 0 for k in cell_keys]
    cell_group = cell_sport * 100_000 + cell_era

So indoor and outdoor track are pinned **together**. Their *combined* mean is
anchored; the *split between them* is free, and nothing asserts what it should
be. That is exactly the collinearity joint_solve documents — no venue hosts
both surfaces and nobody races indoors in May, so the winter form gain can sit
in the form curve or in the indoor cells, and the fit is free to choose.

**And the prior amplifies it.** `PRIOR_GROUP_BY = {"XC": 1.0, "TF:out": 2.5,
"TF:in": 1.0}` gives indoor the *weakest* pull toward its group mean, for a
good reason stated in the code (banked vs flat, 160m vs 300m, and no weather).
But a weak pull toward an *unanchored* level means indoor keeps more of a
number that was never pinned to anything.

Note the cell counts too: 1,575 indoor cells against 25,629 outdoor. The
indoor group is 6% of the track corpus and carries the weakest prior (1.0
against outdoor's 2.5), so it is both the least constrained by data and the
least pulled toward its group mean — while its group mean is itself unanchored.

**Proposed fix, mirroring the engine that got it right:** extend the gauge from
`(sport, era)` to `(sport, surface, era)` and pin the indoor group's mean to an
ASSERTED level rather than leaving it to float inside TF. Reuse
`joint_solve.IND_LEVEL_DEFAULT` so there is one number, not two. Keep
`PRIOR_GROUP_BY` as it is — with the level anchored, a weak prior is then doing
the job it was designed for.

⚠ **I would still not remove track difficulty.** The collinearity is about the
indoor LEVEL only. Per-facility deviations are identified fine, because the
same athletes run several tracks in one winter. Removing them discards
measurable signal to fix a level that should simply be asserted. If the owner
wants the comparison, the right shape is a flag that zeroes the surface term,
so both can be run and scored against `scripts/bracket_holdout.py`.

## 2. XC race duplicated into TF as "Race Results" (AMO)

Partly handled. `scripts/purge_tfrrs_xc_in_tf.py` exists from 2026-09-18 and
removed the tfrrs case on positive track-side evidence with a `MAX_MEETS`
brake. The **~1,590 anet XC/TF pairs are logged and not actioned** (see
`ISSUES-RUNNING.md`) because the owner's own check said a November 1600m can be
a real track race in the XC season.

The new rule is sharper and usable: **same day + same time ⇒ one race, whatever
the sport says; keep the sport the time of year implies.** That is a real
discriminator the earlier pass lacked — it was comparing names and dates only.

**Proposed:** a dry run keyed on `(date, time, athlete set)` rather than
`(date, meet name)`, printing the pairs and which side it would keep, with the
season window as the tie-break. Do not write until that list is read: the
earlier version of this proposed deleting 5.37M real track rows (Penn Relays,
Houston ISD) before it was corrected.

## 3. "Make 5k normalization = track 5k"

Concrete and small. The anchor for a normalized time becomes the **track** 5k
rather than whatever composite is used now. Two things to check first: which
anchor the pools currently normalize to, and whether the XC↔TF offset is
absorbed by the anchor change or double-counted with an existing term. The
second is the failure mode — an offset applied twice looks like a working
change and shifts every XC rating by a constant.

## 4. "Does the median of each percentile runner actually gain with difficulty, or are they overfed?"

This is the most valuable item on the list and it is a **measurement, not a
change.** It asks whether the difficulty term is calibrated or merely
monotone: bin races by fitted difficulty, and within each bin take the median
rating of the 10th, 25th, 50th, 75th, 90th percentile finisher. If difficulty
is honest, those medians are flat across bins — the difficulty has already been
removed. If they rise with difficulty, hard races are being over-credited
("overfed"), and the slope per percentile says by how much and to whom.

**BUILT: `engine/diag_difficulty_calibration.py`** (read-only).

    python engine/diag_difficulty_calibration.py --since 2015

Difficulty bins down the side, within-race percentile bands across, the median
`speed_rating` in each cell, and the slope of each column in rating points per
point of difficulty.

⚠ **The percentile is computed INSIDE each race**, and that is the whole
design. Comparing everyone on hard courses with everyone on easy ones measures
who SHOWS UP — championship courses are hard AND hold better fields, an effect
far larger than the calibration error being looked for. Ranking within each
race and comparing like position with like position removes it.

⚠ **A FLAT column is the PASS**, not a rising one. If difficulty is honest it
has already been removed from the rating. A rising column is the owner's
"overfed", and the slope says by how much and to whom. The output states this
in words, and `tests/test_difficulty_calibration.py` pins it, because the table
is easy to read backwards.

Undefined rather than zero where there is too little evidence: one bin, or
every bin at the same difficulty, reports `--`. Printing 0.0 there would read
as "flat, calibrated", which is the opposite of "we cannot tell".

## 5. "Race importance decides season ability"

The least specified item. `run_joint` already fits a per-pool, per-season
importance (`imp[i]`, printed as a percentage per pool). Needs a conversation
before code: does "decides" mean importance should *weight* a race's
contribution to the season estimate, or that a big race should *cap* or
*anchor* it? Those are different models with different failure modes — the
first lets one championship dominate, the second discards mid-season form.

## 6. "Course difficulty by year?"

Worth it and cheap to measure first: per course, fit a per-year deviation and
report the ones whose spread across years exceeds their within-year noise. A
course that genuinely changed (a re-route, a new surface) will stand out from
one that is merely noisy. The risk is the obvious one — per-year terms on a
course with two races a year will fit weather and field, not the course — so
the report should include the race count per course-year before any term is
added.

---

## Things I would add

- **A hold-out check before any of this ships.** None of these changes can be
  judged by the boards looking better. `scripts/bracket_holdout.py` already
  exists and prints held-out error by the number of races behind a cell —
  every change here should be scored with it, before and after.
- **Rate the same athlete twice, from disjoint halves of their season.** If the
  two ratings disagree by much more than the model's own claimed uncertainty,
  the uncertainty is wrong, and every "5-15 sigma" bar and every board depends
  on it being roughly right.
- **Log which knobs produced each ratings build** — indoor level, anchor,
  difficulty terms — beside the output. Item 1 is only ambiguous today because
  nothing records which `--indoor-level` was used, and that question cost this
  session an hour of reading.
- **Check the 3.39M orphan track results first** (`ISSUES-RUNNING.md`, ~95k a
  year since 2021, cause still live). They are rows whose meet is missing, and
  they are in the ratings input. A relaunch on top of them relaunches on top
  of that.
