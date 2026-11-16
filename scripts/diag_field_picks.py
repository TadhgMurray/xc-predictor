#!/usr/bin/env python3
"""
diag_field_picks.py -- rebuild a championship's projected field and say how
each team got in (owner, 2026-11-16: North Central (Ill.) projected 2nd in
its D3 regional, yet not in the nationals field).

READ-ONLY, and slow: like the nightly (build_qualified_fields), it predicts
every round that has not run (upcoming_preview.qualifiedFieldInfo, live).

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_field_picks.py --meet 27292 --day 2026-11-21 --div 1
    ... --school "North Central (Ill.)"      # where one team stood

Prints, per round: its automatic team(s), then the at-large picks in the
order they were made, with the round each came from.
"""
import argparse
import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meet", type=int, required=True, help="last season's championship meet id")
    ap.add_argument("--source", default="tfrrs")
    ap.add_argument("--day", required=True, help="this year's date of the championship (ISO)")
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--school", action="append", default=[])
    a = ap.parse_args(argv)

    import psycopg2.extras
    from database import getConn
    import upcoming_preview as U

    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET statement_timeout = '900s'")
        plan = U.pastPlan(cur, a.meet, a.source, a.day)
        if not plan:
            print("no plan for that meet and day")
            return 1
        names = {}
        cur.execute("SELECT meet_id, meet_name FROM meets_tfrrs WHERE sport = 'XC'")
        for r in cur.fetchall():
            names[int(r["meet_id"])] = r["meet_name"]
        why = {}
        field, info = U.qualifiedFieldInfo(cur, plan, a.div, live=True, why=why)
        conn.rollback()

    def rname(k):
        mid = k[1] if isinstance(k, tuple) else k
        return names.get(int(mid), str(mid)) if str(mid).lstrip("-").isdigit() else str(k)

    picks = why.get("picks") or {}
    print(f"{plan.get('name')} on {a.day}, race {a.div}: "
          f"{len(field or {})} runners, info {info}")
    print("\nautomatic:")
    for k, schools in (picks.get("auto") or {}).items():
        print(f"  {rname(k):<70} {', '.join(schools)}")
    print("\nat-large, in pick order:")
    for i, (k, school) in enumerate(picks.get("at_large") or [], 1):
        print(f"  {i:>2}. {school:<34} from {rname(k)}")
    if a.school and field:
        by = collections.Counter(field.values())
        print()
        for s in a.school:
            hit = {k: v for k, v in by.items() if s.lower() in (k or "").lower()}
            print(f"{s!r} in the field: {hit or 'NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
