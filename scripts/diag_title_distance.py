#!/usr/bin/env python3
"""
diag_title_distance.py -- athletic.net XC divisions whose title names a
distance the stored distance disagrees with.

    /srv/venv/bin/python scripts/diag_title_distance.py
    /srv/venv/bin/python scripts/diag_title_distance.py --show 100
    /srv/venv/bin/python scripts/diag_title_distance.py --div 1014750

★ WHY (owner, 2026-10-08, Soheib Dissa). His college 8k (meet 254166,
  division 1014750, titled "Men 8k") was never rated: the backfill reads an
  anet XC distance from meets.distance alone (_anetXcDistance), and the
  amnesty retrial valued the row at nt 1480.5 -- his raw 8k time, i.e. a
  5000 m reading -- 60% slower than his 5k norm, so the July triage had
  convicted it as a cooked result ("island") and its tfrrs copy died as the
  dedup twin. A division that SAYS 8k and is stored as 5k is a wrong
  distance, not a cooked time, and every runner in it was judged the same
  way.

  This lists every such division: the title's distance (event_parse
  .distanceFromEventShort, the shared parser), the stored one, the results
  in it, and how many of those are in the live _RESULT_DROP set. Two
  distances "disagree" when they differ at the 100 m the titles are written
  in ("5k", "8K", "6000m").

! READ ONLY.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "engine"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--div", type=int)
    a = ap.parse_args()

    from database import getConn
    from event_parse import distanceFromEventShort
    try:
        from corrections import _RESULT_DROP_BY_SPORT
        dropped = _RESULT_DROP_BY_SPORT["XC"]
    except ImportError:
        dropped = frozenset()

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = '600s'")
        cur.execute("""
            SELECT m.div_id, m.meet_id, m.meet_name, m.division, m.distance,
                   COALESCE(m.meet_date, '')
            FROM   meets m
            WHERE  m.division ~* '[0-9]'
              AND  (%(div)s::bigint IS NULL OR m.div_id = %(div)s)
        """, {"div": a.div})
        bad = []
        for div, meet, name, title, stored, date in cur.fetchall():
            said = distanceFromEventShort(title)[0]
            if not said:
                continue
            if stored is None or round(float(stored), -2) != round(said, -2):
                bad.append((div, meet, name, title, stored, said, date))
        print(f"{len(bad):,} anet XC divisions whose title names another distance\n")
        if not bad:
            conn.rollback()
            return
        ids = [b[0] for b in bad]
        cur.execute("""
            SELECT div_id, array_agg(result_id) FROM results
            WHERE  source = 'anet' AND div_id = ANY(%s) GROUP BY div_id
        """, (ids,))
        res = dict(cur.fetchall())
        conn.rollback()

    tot = tot_drop = 0
    rows = []
    for div, meet, name, title, stored, said, date in bad:
        rids = res.get(div) or []
        nd = sum(1 for r in rids if r in dropped)
        tot += len(rids)
        tot_drop += nd
        rows.append((len(rids), nd, div, meet, name, title, stored, said, date))
    print(f"  {tot:,} results in them, {tot_drop:,} of those in the live drop set\n")
    rows.sort(key=lambda r: (-r[1], -r[0]))
    print(f"  {'results':>7} {'dropped':>7}  {'div':>9}  {'stored':>7} {'title':>7}  date        meet / division")
    for n, nd, div, meet, name, title, stored, said, date in rows[:a.show]:
        st = f"{stored:.0f}" if stored is not None else "-"
        print(f"  {n:>7} {nd:>7}  {div:>9}  {st:>7} {said:>7.0f}  {date[:10]:10}  "
              f"{str(name or '')[:40]} / {title}")


if __name__ == "__main__":
    main()
