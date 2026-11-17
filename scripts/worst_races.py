#!/usr/bin/env python3
"""
worst_races.py -- the races whose WHOLE FIELD our honest predictions missed
the most, with what the database says about them. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/worst_races.py /srv/pred/val_asof.npz                 # college, worst 40
    $P scripts/worst_races.py /srv/pred/val_asof.npz --source anet --n 60

★ WHY (owner, 2026-11-16, the college report: races on a course with no
  difficulty number averaged a 22% miss of their whole field -- the median
  was 3.9%, so a set of races is off by 20%+ as a whole). A field all off
  by the same 20% is not a runner's bad day; it is the race's distance, a
  course, a mixed or mislabelled field. This lists them, worst first:

    level     the field's median ln(predicted / actual), in % (+ = we said
              slower than they ran: the race was shorter than its distance
              says, or faster)
    implied   the distance at which our predicted times would fit what they
              ran (Riegel), beside the distance on file
    runners, the meet and race names, the course, and whether the course
    has a difficulty number.

Reads race_trees --dump-val's file (race-morning validation predictions).
"""
import argparse
import ast
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

RIEGEL = 1.06


def raceLevels(keys, pred, actual, source=None, lead0=True):
    """{race key tuple: (median ln(pred/actual), runners)} from the dump's
    rows; only `source`'s races when given. Pure."""
    by = {}
    for k, p, a in zip(keys, pred, actual):
        t = ast.literal_eval(str(k))
        if lead0 and len(t) > 4 and t[4]:
            continue
        if source and str(t[2]) != source:
            continue
        if p > 0 and a > 0:
            by.setdefault(tuple(t[:4]), []).append(math.log(p / a))
    return {k: (statistics.median(v), len(v)) for k, v in by.items() if len(v) >= 5}


def impliedDistance(distance, level):
    """The distance our times would fit, by Riegel, when the field ran
    exp(level) faster than predicted at `distance`. Pure."""
    if not distance:
        return None
    return float(distance) * math.exp(-level / RIEGEL)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="race_trees --dump-val's npz")
    ap.add_argument("--source", default="tfrrs", help="tfrrs (college, default) or anet")
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()

    import numpy as np
    z = np.load(a.dump, allow_pickle=False)
    levels = raceLevels(z["key"], z["model_s"], z["actual_s"], a.source)
    if not levels:
        sys.exit("no races of that source in the dump")
    worst = sorted(levels.items(), key=lambda kv: -abs(kv[1][0]))[:a.n]
    big = [lv for lv, _n in levels.values()]
    print(f"{len(levels):,} {a.source} races; whole-field level miss: median "
          f"{100 * statistics.median(abs(x) for x in big):.2f}%, over 10%: "
          f"{sum(abs(x) > 0.10 for x in big):,}, over 20%: {sum(abs(x) > 0.20 for x in big):,}")

    import psycopg2.extras
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        print(f"\n{'level':>7} {'n':>4} {'on file':>8} {'implied':>8}  cell  date        meet -- race  (course)")
        for (mid, div, src, day), (lv, n) in worst:
            if src == "tfrrs":
                cur.execute("""SELECT mt.meet_name, mt.venue_name AS course, mt.division_distances,
                                      mt.distance
                               FROM meets_tfrrs mt WHERE mt.meet_id = %s AND mt.sport = 'XC'
                               LIMIT 1""", (mid,))
            else:
                cur.execute("""SELECT m.meet_name, m.course_name AS course, NULL AS division_distances,
                                      m.distance
                               FROM meets m WHERE m.meet_id = %s AND m.div_id = %s LIMIT 1""",
                            (mid, div))
            m = cur.fetchone() or {}
            import predict as P
            label = P._divisionLabel(cur, mid, div, "XC", source=src)
            dist = None
            dd = m.get("division_distances")
            if isinstance(dd, dict):
                one = dd.get(str(div)) or {}
                dist = one.get("distance") if isinstance(one, dict) else None
            dist = dist or m.get("distance")
            try:
                dist = float(dist) if dist else None
            except (TypeError, ValueError):
                dist = None
            imp = impliedDistance(dist, lv)
            cur.execute("""SELECT 1 AS x FROM course_canonical cc
                           JOIN course_difficulties cd ON cd.canonical_id = cc.canonical_id
                           WHERE cc.course_name = %s LIMIT 1""", (m.get("course"),))
            cell = "yes" if cur.fetchone() else "no"
            print(f"{100 * lv:>+6.1f}% {n:>4} {dist or 0:>8.0f} {imp or 0:>8.0f}  {cell:4}  {day}  "
                  f"{(m.get('meet_name') or '?')[:44]} -- {(label or '?')[:28]}  "
                  f"({(m.get('course') or '?')[:30]})")
        conn.rollback()


if __name__ == "__main__":
    main()
