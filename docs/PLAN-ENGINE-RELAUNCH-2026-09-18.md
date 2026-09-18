# Engine relaunch — the owner's list, checked against the code

Nothing here is built yet. Each item says what already exists, because three
of the six turn out to be partly done, and one of them is a knob rather than a
rewrite.

---

## 1. "Indoor is rated difficulty-wise as if it is 4% easy. Remove track difficulty (at least for now)?"

**This was diagnosed and fixed on 2026-09-11, and the symptom you are seeing is
most likely a switch set the wrong way, not a missing fix.**
`engine/joint_solve.py:564` says it in full:

> ★★ INDOOR IS SEASON (owner, 2026-09-11: "I think indoor might be off"; the
> page read every indoor oval 2.4-3.8% EASIER than outdoors, which is
> backwards).

The cause is collinearity, not a bad estimator: no venue hosts both an indoor
and an outdoor track and nobody races indoors in May, so the form curve's
Dec–Mar level and the indoor cells' mean are **one free direction**. The fit
can put the winter gain in either, and it chose "indoor is easy".

The fix was to stop fitting it: `IND_LEVEL_DEFAULT = 0.012` — indoor asserted
as **1.2% slower**, from the NCAA facility factors and the WA short-track
tables — taken off `y` like `mu_fixed`, with the indoor cells recentred each
pass so they keep only their own deviation (a banked BU below it, a flat 200m
oval above). `--indoor-level fit` still exists and **restores the broken
behaviour**.

**So the first question is which `--indoor-level` the last ratings run used.**
If it was `fit`, that is the whole bug and the fix is a flag. Check the run log
for the `[joint] indoor level ASSERTED|fitted` line — it prints which.

⚠ **And I would not remove track difficulty to fix this.** The collinearity
argument applies to the indoor *level* only. Individual facility deviations
(banked 200m vs flat 300m) are identified fine, because the same athletes run
several tracks in one winter. Removing all track difficulty throws away signal
that is measurable in order to fix a level that is already asserted.

**Proposed:** confirm the flag; if it is already ASSERTED and the boards still
read indoor as easy, then something downstream ignores the assertion and that
is the bug to find. Removing track difficulty stays available but as a last
resort, behind a flag, so the two can be compared.

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

**Proposed:** build that table and print it. It needs no decisions, it either
confirms the owner's instinct or refutes it, and every other tuning question
here depends on the answer. **I would do this before items 1, 3 and 5**, since
it measures whether the difficulty machinery is trustworthy at all.

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
  judged by the boards looking better. Keep a set of results out of the fit and
  report prediction error before and after each change; a change that improves
  the story and not the error is a change that has learnt the story.
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
