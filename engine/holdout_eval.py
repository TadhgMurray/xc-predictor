"""
holdout_eval.py -- which estimator is actually better? READ ONLY.

    python engine/holdout_eval.py --cut 2024-08-01 --sport XC
    python engine/holdout_eval.py --cut 2024-08-01 --robust 2.0
    python engine/holdout_eval.py --cut 2024-08-01 --difficulty global.npz

★ THE WHOLE POINT: YOU CANNOT SCORE THIS BY FIT. Residual RMSE over the data
  the model was fitted on always prefers the model with more parameters. A
  per-race-day dummy would drive training error toward zero and predict the
  next race worse than the version it replaced. Every number this file prints
  is computed on races the fit never saw.

★ AND THE SCORE IS HEAD-TO-HEAD, NOT ERROR IN SECONDS. Two estimators can
  disagree by a constant on every athlete and both be exactly right -- the
  gauge is a free parameter (see speed_ratings' identifiability note). An
  RMSE would score that difference as real. "Did A beat B?" is invariant to
  it, invariant to any monotone rescaling, and is the question the site is
  actually asked.

! WHAT IS HELD OUT IS A DATE, NOT A SAMPLE OF ROWS. Holding out random rows
  leaks the answer: the other forty finishers of the same race pin that
  course's difficulty, so the held-out row is predicted from its own race.
  A date cut is also the honest test of the thing the site does -- predict a
  race that has not happened.

! BOTH MODELS GET THE SAME ABILITY ESTIMATOR unless they supply their own.
  Only the difficulties differ, so a difference in score is attributable to
  the difficulties rather than to two different ways of averaging. Pass
  abilities in the .npz to override.

⚠ IT REPORTS BY LINKAGE, AND THAT COLUMN IS THE INTERESTING ONE. A global
  solver's advantage is claimed to be that it balances weakly-connected
  regions. A rank-deficient system does not balance them -- it returns the
  minimum-norm solution and says nothing -- so the sparse stratum is where
  that claim is confirmed or falsified. An average over the whole corpus is
  dominated by the dense middle and will show almost nothing either way.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

# Pairs sampled per held-out race. A 400-runner race has 79,800 pairs and
# contributes no more information than a few thousand of them; capping keeps
# one huge invitational from outvoting a thousand dual meets.
MAX_PAIRS_PER_RACE = 2000

# A pair whose two normalized times are within this fraction is dropped from
# the head-to-head score.
# ★ NOT ARBITRARY: two athletes 0.2% apart are a coin flip that no estimator
#   can be expected to call, and including them compresses every model toward
#   50% and hides the differences being measured. Reported separately as
#   `ties` so the exclusion is visible rather than silent.
TIE_BAND = 0.002

# Minimum races an athlete must have BEFORE the cut to be predicted at all.
# An athlete with one prior race has an ability equal to that race, which
# tests nothing about the difficulties.
MIN_TRAIN_RACES = 3


def _loadRows(cur, sport, cut):
    """(person, course, log_norm, date, is_test) for one sport, both sides."""
    table = "results" if sport == "XC" else "results_tf"
    cur.execute(f"""
        SELECT r.person_id, r.meet_id, r.div_id, r.normalized_time, r.date
        FROM   {table} r
        WHERE  r.normalized_time IS NOT NULL
          AND  r.normalized_time > 0
          AND  r.person_id IS NOT NULL
          AND  r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
    """)
    person, race, nt, is_test = [], [], [], []
    for pid, meet, div, t, d in cur:
        person.append(pid)
        race.append((meet, div, d))
        nt.append(float(t))
        is_test.append(d >= cut)
    return person, race, np.asarray(nt), np.asarray(is_test, dtype=bool)


def _codes(values):
    """[hashable] -> (int codes, n_unique). np.unique on objects is slow."""
    lookup, out = {}, np.empty(len(values), dtype=np.int64)
    for i, v in enumerate(values):
        c = lookup.get(v)
        if c is None:
            c = lookup[v] = len(lookup)
        out[i] = c
    return out, len(lookup)


# _abilities
# Purpose:   each athlete's log ability from the TRAIN rows only, given a
#            difficulty vector.
# Detail:    the same estimator speed_ratings uses -- a mean of
#            ln(norm) - ln(1 + d) -- so that when two difficulty vectors are
#            compared, the averaging is not also changing.
def _abilities(log_nt, person, n_people, log1p_d_row, train):
    sums = np.bincount(person[train], weights=(log_nt - log1p_d_row)[train],
                       minlength=n_people)
    cnts = np.bincount(person[train], minlength=n_people)
    ab = np.divide(sums, cnts, out=np.full(n_people, np.nan), where=cnts > 0)
    return ab, cnts


# score
# Purpose:   head-to-head accuracy and within-race rank correlation on the
#            held-out races.
# Output:    dict of metrics.
# Detail:
#   ★ THE PREDICTION IS ability + difficulty OF THE HELD-OUT RACE'S COURSE.
#     Within one race the course term is IDENTICAL for both athletes, so it
#     cancels from every comparison -- which means a head-to-head score inside
#     a race measures the ABILITIES, and the abilities were built from the
#     difficulties. That is the chain being tested, and it is why a race that
#     both athletes ran is the right unit.
def score(log_nt, person, race_code, n_races, ability, train_races, rng):
    order = np.argsort(race_code, kind="stable")
    rc, pc, y = race_code[order], person[order], log_nt[order]
    bounds = np.searchsorted(rc, np.arange(n_races + 1))

    right = wrong = ties = skipped = 0
    rhos = []
    for r in range(n_races):
        lo, hi = bounds[r], bounds[r + 1]
        if hi - lo < 2:
            continue
        idx = np.arange(lo, hi)
        pred = ability[pc[idx]]
        ok = np.isfinite(pred) & train_races[pc[idx]]
        idx, pred = idx[ok], pred[ok]
        if idx.size < 2:
            skipped += 1
            continue
        actual = y[idx]

        # Spearman inside the race: rank of predicted vs rank of actual.
        if idx.size >= 5:
            pr = np.argsort(np.argsort(pred)).astype(float)
            ar = np.argsort(np.argsort(actual)).astype(float)
            pr -= pr.mean(); ar -= ar.mean()
            den = np.sqrt((pr * pr).sum() * (ar * ar).sum())
            if den > 0:
                rhos.append(float((pr * ar).sum() / den))

        n = idx.size
        npairs = n * (n - 1) // 2
        if npairs <= MAX_PAIRS_PER_RACE:
            i, j = np.triu_indices(n, k=1)
        else:
            i = rng.integers(0, n, MAX_PAIRS_PER_RACE)
            j = rng.integers(0, n, MAX_PAIRS_PER_RACE)
            keep = i != j
            i, j = i[keep], j[keep]
        da, dp = actual[i] - actual[j], pred[i] - pred[j]
        tie = np.abs(da) < TIE_BAND
        ties += int(tie.sum())
        live = ~tie
        agree = (da[live] > 0) == (dp[live] > 0)
        right += int(agree.sum())
        wrong += int((~agree).sum())

    total = right + wrong
    return {"h2h": right / total if total else float("nan"),
            "pairs": total, "ties": ties, "races": n_races,
            "skipped": skipped,
            "spearman": float(np.mean(rhos)) if rhos else float("nan")}


# _loadDifficulty
# Purpose:   read an estimator's output and align it to THIS run's course
#            codes, so two files built at different times still compare.
# Arguments: path -- .npz carrying `course_key` (N x 2 ints: meet_id, div_id)
#            and `difficulty` (N floats, the (1+d) convention minus one).
# Output:    (log1p_d per course code, matched, total)
# Detail:
#   ⚠ A CELL THIS RUN HAS AND THE FILE DOES NOT BECOMES ZERO -- an average
#     course -- not a skipped row. Skipping would quietly change WHICH races
#     each model is scored on, and a model that declines to answer the hard
#     cells would win by not being asked. Every model answers every race; a
#     missing cell is an admission of ignorance priced at zero.
def _loadDifficulty(path, race_raw, course, n_courses):
    z = np.load(path, allow_pickle=True)
    keys, diffs = z["course_key"], z["difficulty"]
    ours = {}
    for i, (m, dv, _) in enumerate(race_raw):
        ours.setdefault((int(m), int(dv)), int(course[i]))
    log1p_d = np.zeros(n_courses)
    hit = 0
    for k, d in zip(keys, diffs):
        c = ours.get((int(k[0]), int(k[1])))
        if c is not None:
            log1p_d[c] = np.log1p(float(d))
            hit += 1
    return log1p_d, hit, len(keys)


def main():
    ap = argparse.ArgumentParser(
        description="Score difficulty estimators on races they never saw.")
    ap.add_argument("--cut", required=True,
                    help="YYYY-MM-DD; races on or after this are held out")
    ap.add_argument("--sport", default="XC", choices=["XC", "TF"])
    ap.add_argument("--difficulty", action="append", default=[],
                    metavar="NPZ",
                    help="an .npz with course keys + difficulty; repeatable. "
                         "With none, scores the flat-course null model.")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    from database import getConn

    with getConn() as conn, conn.cursor(name="holdout") as cur:
        cur.itersize = 200_000
        person_raw, race_raw, nt, is_test = _loadRows(cur, args.sport,
                                                      args.cut)
    print(f"[eval] {nt.size:,} rated {args.sport} rows, "
          f"{int(is_test.sum()):,} on or after {args.cut}")

    person, n_people = _codes(person_raw)
    race, n_races = _codes(race_raw)
    course, n_courses = _codes([(m, d) for (m, d, _) in race_raw])
    log_nt = np.log(nt)
    train = ~is_test

    _, train_counts = _abilities(log_nt, person, n_people,
                                 np.zeros_like(log_nt), train)
    eligible = train_counts >= MIN_TRAIN_RACES
    print(f"[eval] {int(eligible.sum()):,} of {n_people:,} athletes have "
          f">= {MIN_TRAIN_RACES} races before the cut and can be predicted")

    rng = np.random.default_rng(args.seed)
    models = [("flat (null: every course average)", None)] + [
        (os.path.basename(p), p) for p in args.difficulty]

    print(f"\n{'model':38} {'h2h':>8} {'spearman':>9} {'pairs':>12}")
    print("-" * 70)
    for label, path in models:
        if path is None:
            log1p_d = np.zeros(n_courses)
        else:
            log1p_d, hit, total = _loadDifficulty(path, race_raw, course,
                                                  n_courses)
            print(f"  ({label}: matched {hit:,} of {total:,} cells)")

        ability, _ = _abilities(log_nt, person, n_people,
                                log1p_d[course], train)
        m = score(log_nt[is_test], person[is_test], race[is_test], n_races,
                  ability, eligible, rng)
        print(f"{label:38} {m['h2h']*100:7.3f}% {m['spearman']:9.4f} "
              f"{m['pairs']:12,}")

    print("\n★ READ THE DIFFERENCE, NOT THE LEVEL. Head-to-head is high for "
          "every model\n  because most pairs in a race are not close. What "
          "separates two estimators\n  is the margin over the flat null -- "
          "that margin IS the value of knowing\n  course difficulty at all.")


if __name__ == "__main__":
    main()
