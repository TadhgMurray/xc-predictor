#!/usr/bin/env python3
"""
diag_venue_speed.py -- where a track venue page spends its time. READ ONLY.

    /srv/venv/bin/python scripts/diag_venue_speed.py 1234 out
    /srv/venv/bin/python scripts/diag_venue_speed.py 1234 out --explain

★ WHY (owner, 2026-10-09: "venue page still slow" after the one-pass temp
  table). Runs the route's steps in its order, on one transaction as the
  route does, and prints the seconds each took and how many rows it made;
  --explain adds EXPLAIN (ANALYZE, BUFFERS) for the temp-table build, the
  pass every other step reads. The location id and in/out are the two last
  parts of the venue page's URL (/venue/tf/<id>/<in|out>).
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("location_id", type=int)
    ap.add_argument("indoor", choices=("in", "out"))
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args()
    is_indoor = a.indoor == "in"

    import psycopg2.extras
    import app as A
    from database import getConn
    from pool_view import stampRowsHs

    steps = [
        ("label", lambda cur: A.get_tf_venue_label(cur, a.location_id, is_indoor)),
        ("difficulty", lambda cur: A.get_tf_venue_difficulty(cur, a.location_id, is_indoor)),
        ("temp table (venue_rows)", lambda cur: A._ensureVenueRows(cur, a.location_id, is_indoor)),
        ("bests", lambda cur: A.get_tf_venue_bests(cur, a.location_id, is_indoor)),
        ("meets", lambda cur: A.get_tf_venue_meets(cur, a.location_id, is_indoor)),
        ("individual records", lambda cur: A.get_tf_venue_individual_records(cur, a.location_id, is_indoor)),
        ("relay records", lambda cur: A.get_tf_venue_relay_records(cur, a.location_id, is_indoor)),
    ]
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        out = {}
        total = time.time()
        for name, fn in steps:
            t = time.time()
            got = fn(cur)
            n = len(got) if isinstance(got, (list, tuple)) else ("-" if got is None else "1")
            out[name] = got
            print(f"  {time.time() - t:7.2f}s  {name:26} rows {n}", flush=True)
        t = time.time()
        stampRowsHs(cur, "TF", out["bests"], event_key="event_short")
        print(f"  {time.time() - t:7.2f}s  {'stampRowsHs (bests)':26}", flush=True)
        cur.execute("SELECT count(*) AS n FROM venue_rows")
        print(f"  total {time.time() - total:.2f}s; venue_rows holds {cur.fetchone()['n']:,} results")
        if a.explain:
            conn.rollback()
            pool_col = A._ratingPoolCol(cur, 'results_tf')
            cur.execute(f"""
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT r.result_id, r.person_id, r.athlete_id, r.athlete_name,
                       r.school, r.time_seconds, r.mark, r.is_field,
                       COALESCE(r.is_relay, 0) AS is_relay, r.date, r.grade,
                       r.speed_rating, {pool_col}, r.event_short,
                       m.meet_id, m.div_id, m.event_id, m.meet_name,
                       CASE WHEN mm.gender IN ('M', 'F') THEN mm.gender END AS meet_gender
                FROM   meets_tf m
                JOIN   results_tf r
                       ON r.meet_id = m.meet_id AND r.div_id = m.div_id
                      AND r.event_id = m.event_id
                LEFT   JOIN meets_tf_meta mm ON mm.meet_id = m.meet_id
                WHERE  m.location_id = %(loc)s
                  AND  COALESCE(m.is_indoor, 0) = %(indoor)s
            """, {"loc": a.location_id, "indoor": 1 if is_indoor else 0})
            print("\nEXPLAIN of the venue_rows pass:")
            for r in cur.fetchall():
                print("  " + list(r.values())[0])
        conn.rollback()


if __name__ == "__main__":
    main()
