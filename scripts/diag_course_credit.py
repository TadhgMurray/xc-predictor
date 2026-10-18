#!/usr/bin/env python3
"""
diag_course_credit.py -- within one runner's season, do harder courses and
"slow days" rate higher than easy ones? READ ONLY, on the published ratings.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_course_credit.py
    /srv/venv/bin/python scripts/diag_course_credit.py --since 2023 --pool hs_m
    /srv/venv/bin/python scripts/diag_course_credit.py --sample 50   # 1 runner in 50

! A SAMPLE OF RUNNERS BY DEFAULT (1 in 20, whole seasons kept). The full
  join -- every XC race since 2021 through results, meets, the canonical
  course and the day table -- hung for the owner (2026-10-18); the slopes
  need thousands of runner-seasons, not millions.

★ WHY (owner, 2026-10-18). The go-live's "tilt by rating band" report read
  implied h 0.2-0.7 against applied 0.9-1.1 in every band -- courses charged
  three times too hard, if taken at face value. But that report regresses
  the JOINT solve's residuals on out["delta"], which under
  XCP_DIFFICULTY=bracket is the BRACKET engine's course numbers (run_joint
  replaces it, theta stays the joint's), and its x carries both sports'
  level. So it measures the two engines against each other, not the tilt.

  This asks the published ratings directly. A rating is the runner's time
  with the course and the day taken out, so within one athlete-season the
  rating should not depend on how hard the course or the day was:

      ln(rating) = runner-season level + b_course * course + b_day * day
                   + b_date * days into the season + noise

  b = 0: charged right.  b > 0: hard courses / slow days rate HIGH (over-
  credited).  b < 0: under-credited. Per ability band it is the published
  tilt: if fast runners show b > 0 and slow runners b < 0, the course is
  scaled by ability the wrong way. The date term keeps "championships are
  late and on hard courses" from posing as course credit.
"""
import argparse
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# ! FETCHED IN STEPS, JOINED IN PYTHON (2026-10-18: the one-statement join
#   hung twice on the server, sampled or not). Each step is an indexed read
#   or a small table; the planner never sees the five-way join.
_RR_SQL = """
    SELECT result_id, person_id, split_part(pool, '|', 1), year,
           ln(speed_rating), race_date::date, race_date::date - make_date(year, 8, 1)
    FROM   ranking_results
    WHERE  sport = 'XC' AND speed_rating > 0 AND year >= %(since)s
      AND  result_id > 0 AND person_id %% %(sample)s = 0
      AND  (%(pool)s IS NULL OR split_part(pool, '|', 1) = %(pool)s)
"""
_RES_SQL = """SELECT result_id, meet_id, div_id FROM results
              WHERE result_id = ANY(%s) AND source = 'anet'"""
_MEET_SQL = """SELECT meet_id, div_id, course_name, round(gps_lat::numeric, 5),
                      round(gps_long::numeric, 5), distance
               FROM meets WHERE source = 'anet' AND meet_id = ANY(%s)"""
_CC_SQL = """SELECT course_name, round(gps_lat::numeric, 5), round(gps_long::numeric, 5),
                    canonical_id FROM course_canonical"""
_RDE_SQL = """SELECT canonical_id, distance_m, race_date, course_effect, day_effect
              FROM race_day_effect
              WHERE canonical_id = ANY(%s) AND course_effect IS NOT NULL"""


def _chunks(xs, n=20000):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def load(cur, since, sample, pool, say=print):
    """[(person, pool, year, ln rating, course, day, days into season)]."""
    cur.execute(_RR_SQL, {"since": since, "pool": pool, "sample": max(1, sample)})
    rr = cur.fetchall()
    say(f"  {len(rr):,} XC races in the sample")
    res = {}
    for ch in _chunks([r[0] for r in rr]):
        cur.execute(_RES_SQL, (ch,))
        res.update({rid: (m, d) for rid, m, d in cur.fetchall()})
    say(f"  {len(res):,} of them athletic.net rows with a meet")
    meets = {}
    for ch in _chunks({m for m, _d in res.values()}):
        cur.execute(_MEET_SQL, (ch,))
        for m, d, name, lat, lon, dist in cur.fetchall():
            meets[(m, d)] = (name, lat, lon, dist)
    cur.execute(_CC_SQL)
    cc = {(n, lat, lon): cid for n, lat, lon, cid in cur.fetchall()}
    cell = {}
    for key, (name, lat, lon, dist) in meets.items():
        cid = cc.get((name, lat, lon))
        if cid is not None and dist:
            cell[key] = (cid, int(round(float(dist) / 100.0) * 100))
    rde = {}
    for ch in _chunks({c for c, _d in cell.values()}):
        cur.execute(_RDE_SQL, (ch,))
        for cid, dm, day, ce, de in cur.fetchall():
            rde[(cid, dm, day)] = (float(ce), float(de))
    say(f"  {len(cell):,} divisions on a known course, {len(rde):,} course-days")
    out = []
    for rid, pid, pl, yr, lny, day, din in rr:
        md = res.get(rid)
        c = cell.get(md) if md else None
        e = rde.get((c[0], c[1], day)) if c else None
        if e:
            out.append((pid, pl, yr, float(lny), e[0], e[1], float(din)))
    return out


BANDS = (0, 90, 100, 110, 120, 130, 140, 999)


def withinFit(group, y, X):
    """Least squares of y on X after removing each group's mean from both
    (the runner-season level). Returns (b, se, n). Pure."""
    group = np.asarray(group)
    _, inv, cnt = np.unique(group, return_inverse=True, return_counts=True)
    keep = cnt[inv] >= 3
    inv, y, X = inv[keep], np.asarray(y, float)[keep], np.asarray(X, float)[keep]
    if y.size < X.shape[1] + 10:
        return None, None, int(y.size)
    _, inv = np.unique(inv, return_inverse=True)
    k = int(inv.max()) + 1
    n_g = np.bincount(inv, minlength=k).astype(float)

    def demean(v):
        return v - (np.bincount(inv, weights=v, minlength=k) / n_g)[inv]
    yd = demean(y)
    Xd = np.column_stack([demean(X[:, j]) for j in range(X.shape[1])])
    b, *_ = np.linalg.lstsq(Xd, yd, rcond=None)
    resid = yd - Xd @ b
    dof = max(y.size - k - X.shape[1], 1)
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.pinv(Xd.T @ Xd)
    return b, np.sqrt(np.diag(cov)), int(y.size)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2023)
    ap.add_argument("--sample", type=int, default=20, help="1 runner in N (1 = everyone)")
    ap.add_argument("--pool")
    a = ap.parse_args()
    from database import getConn
    import time
    t0 = time.time()
    print(f"reading XC races since {a.since}, 1 runner in {a.sample} ...", flush=True)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10min'")
        rows = load(cur, a.since, a.sample, a.pool,
                    say=lambda m: print(f"{m}  ({time.time() - t0:.0f}s)", flush=True))
        conn.rollback()
    print(f"  {len(rows):,} rows in {time.time() - t0:.0f}s", flush=True)
    if not rows:
        print("no rows (race_day_effect empty, or no anet XC rows matched)")
        return
    pid = np.array([r[0] for r in rows], dtype=np.int64)
    pool = np.array([r[1] for r in rows])
    yr = np.array([r[2] for r in rows], dtype=np.int64)
    y = np.array([r[3] for r in rows], dtype=float)
    course = np.array([r[4] for r in rows], dtype=float)
    day = np.array([r[5] for r in rows], dtype=float)
    days_in = np.array([r[6] for r in rows], dtype=float) / 100.0
    _, grp = np.unique(np.char.add(np.char.add(pid.astype(str), pool), yr.astype(str)),
                       return_inverse=True)
    X = np.column_stack([course, day, days_in])
    # the runner-season's level, for the bands: its mean rating
    k = int(grp.max()) + 1
    lvl = np.exp(np.bincount(grp, weights=y, minlength=k) / np.bincount(grp, minlength=k))[grp]
    print(f"{len(rows):,} XC races since {a.since} with a published course and day number\n")
    print("b = change in ln(rating) per unit of course / day effect (log-time) inside one "
          "runner's season.\n0 = charged right, + = hard courses / slow days rate HIGH, "
          "- = they rate LOW\n")
    print(f"  {'':<14}{'races':>11}{'b course':>10}{'±':>7}{'b day':>9}{'±':>7}"
          f"{'per 100 days':>14}")

    def line(label, m):
        b, se, n = withinFit(grp[m], y[m], X[m])
        if b is None:
            return
        print(f"  {label:<14}{n:>11,}{b[0]:>+10.3f}{se[0]:>7.3f}{b[1]:>+9.3f}{se[1]:>7.3f}"
              f"{100 * b[2]:>+13.2f}%")
    line("all", np.ones(len(rows), bool))
    for p in sorted(set(pool.tolist())):
        line(p, pool == p)
    print("\n  by the runner-season's mean rating (the published tilt):")
    for lo, hi in zip(BANDS, BANDS[1:]):
        lab = f"<{hi}" if lo == 0 else (f"{lo}+" if hi == 999 else f"{lo}-{hi}")
        line(lab, (lvl >= lo) & (lvl < hi))
    print("\n  reading: b course 0.3 means a +10% course rates 3% higher than the same "
          "runner on a flat one.")


if __name__ == "__main__":
    main()
