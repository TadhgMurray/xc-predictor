#!/usr/bin/env python3
"""
drift_audit.py -- how many rated results still carry a normalisation the
current weather/distance model no longer agrees with ("Since normalised" on
the "How this was rated" breakdown), and which meets to re-normalise.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/drift_audit.py                         # TF and XC: the boards' top rows + a sample
    $P scripts/drift_audit.py --sport TF --top 500 --sample 1000
    $P scripts/drift_audit.py --out /tmp/stale_meets.txt
    $P backfill/backfill_normalize.py --sport TF --apply --meets-file /tmp/stale_meets.txt

★ WHY (owner, 2026-11-04): Luis Martinez, 4:11.66 at the 2013 New Mexico
  3A-5A state meet, rated 147.2 -- "the best hs result OAT". Its stored time
  carries a +4.67% weather credit where today's model gives the race +0.18%;
  the runner-up in the same race, 4:17.58, carries +0.18% and rates 137.0.
  Six of Martinez's points are a normalisation nobody redid. This finds the
  rows like it, among the highest-rated (where a stale credit does the most
  harm: it puts them on top) and in a random sample (how common it is).

★ THE SAME ARITHMETIC AS THE PAGE (explain_rating.explainResult, with app's
  own SQL fragments), so a row flagged here is a row whose breakdown shows
  the step. READ-ONLY; --out writes a meets file for backfill_normalize.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

POOLS = ("hs_m", "hs_f", "college_m", "college_f")
_TABLE = {"XC": "results", "TF": "results_tf"}


def driftOf(body):
    """The "Since normalised" step's percent, or 0.0. Pure."""
    for s in (body or {}).get("steps", ()):
        if s.get("key") == "refit" and s.get("pct") is not None:
            return float(s["pct"])
    return 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=("TF", "XC", "both"), default="both")
    ap.add_argument("--top", type=int, default=300, help="highest-rated rows per pool")
    ap.add_argument("--sample", type=int, default=600, help="a spread sample per sport")
    ap.add_argument("--min", type=float, default=1.0, help="percent that counts as stale")
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--out", help="write SRC:MEET_ID lines for backfill_normalize --meets-file")
    a = ap.parse_args(argv)
    import psycopg2.extras
    import app
    import explain_rating as EX
    from database import getConn
    sports = ("TF", "XC") if a.sport == "both" else (a.sport,)
    stale_meets = set()
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            has_day = app._hasRaceDayEffect(cur)
            ce = app._raceDayCourseSql(cur).replace("rde.", "") if has_day else "NULL::real"
            for sport in sports:
                frag = app._explainFrag(cur, sport)
                ids = []
                for pool in POOLS:
                    cur.execute("""SELECT result_id FROM ranking_results
                                   WHERE sport = %s AND split_part(pool, '|', 1) = %s
                                     AND speed_rating IS NOT NULL
                                   ORDER BY speed_rating DESC LIMIT %s""", (sport, pool, a.top))
                    ids += [("top " + pool, r["result_id"]) for r in cur.fetchall()]
                cur.execute("""SELECT result_id FROM ranking_results
                               WHERE sport = %s AND speed_rating IS NOT NULL
                                 AND mod(abs(result_id), 9973) = 17
                               LIMIT %s""", (sport, a.sample))
                ids += [("sample", r["result_id"]) for r in cur.fetchall()]
                conn.rollback()
                stats, worst = {}, []
                for i, (group, rid) in enumerate(ids):
                    try:
                        body = EX.explainResult(cur, sport, rid, frag, ce_col=ce, has_day_table=has_day)
                    except Exception as exc:                  # noqa: BLE001 -- a row, not the audit
                        conn.rollback()
                        print(f"  {sport} {rid}: {type(exc).__name__}: {exc}", flush=True)
                        continue
                    d = driftOf(body)
                    st = stats.setdefault(group.split()[0], [0, 0])
                    st[0] += 1
                    if abs(d) >= a.min:
                        st[1] += 1
                        worst.append((abs(d), d, rid, body))
                    if i % 200 == 199:
                        print(f"  {sport}: {i + 1:,}/{len(ids):,} read", flush=True)
                    conn.rollback()
                for g, (n, k) in stats.items():
                    print(f"[drift] {sport} {g}: {k:,} of {n:,} rows carry a stale normalisation "
                          f"of {a.min:g}%+ ({100 * k / max(n, 1):.1f}%)")
                worst.sort(key=lambda x: -x[0])
                for _ad, d, rid, body in worst[:a.show]:
                    race = body.get("race") or {}
                    print(f"    {d:+.2f}%  {body.get('rating')}  {race.get('date')}  "
                          f"{(race.get('meet') or '')[:44]}  {race.get('event')}  {race.get('time')}")
                if worst:
                    cur.execute(f"SELECT DISTINCT source, meet_id FROM {_TABLE[sport]} "
                                f"WHERE result_id = ANY(%s)", ([w[2] for w in worst],))
                    stale_meets |= {(sport, r["source"], r["meet_id"]) for r in cur.fetchall()}
                    conn.rollback()
    if a.out and stale_meets:
        for sport in sports:
            lines = sorted(f"{src}:{m}" for sp, src, m in stale_meets if sp == sport)
            path = a.out if len(sports) == 1 else a.out.replace(".txt", f"_{sport.lower()}.txt")
            with open(path, "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"[drift] {len(lines):,} {sport} meets -> {path}  "
                  f"(backfill_normalize.py --sport {sport} --apply --meets-file {path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
