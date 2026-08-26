# Project: xc-predictor / scripts
# File:    probe_tf_meet.py
# Purpose: One TF meet, both tables, both feeds -- exactly what ids exist
#          where, so a broken athlete-page link can be diagnosed from facts
#          instead of theories.
#
#     python scripts/probe_tf_meet.py 12345
#     python scripts/probe_tf_meet.py 12345 --rid -1177115244352593205
#     python scripts/probe_tf_meet.py 12345 --person 555
#
# Read-only.

import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meet_id", type=int)
    ap.add_argument("--rid", type=int, default=None,
                    help="one result_id to locate precisely")
    ap.add_argument("--person", type=int, default=None,
                    help="show this person's rows at the meet")
    args = ap.parse_args()

    with getConn() as conn, conn.cursor() as cur:
        print(f"\n  meets_tf rows for meet {args.meet_id} "
              "(source / div / event / name):")
        cur.execute("""
            SELECT source, div_id, event_id, event_short, division,
                   meet_name
            FROM meets_tf WHERE meet_id = %s
            ORDER BY source, div_id, event_id LIMIT 60""", (args.meet_id,))
        rows = cur.fetchall()
        if not rows:
            print("    NONE -- this meet has no meets_tf coverage at all")
        for s, d, e, ev, div, mn in rows:
            print(f"    {str(s):<7} div {str(d):<12} ev {str(e):<12} "
                  f"{str(ev)[:24]:<24} {str(div)[:18]:<18} {str(mn)[:28]}")

        print(f"\n  results_tf (div, event) groups at meet {args.meet_id}:")
        cur.execute("""
            SELECT source, div_id, event_id, count(*),
                   mode() WITHIN GROUP (ORDER BY event_short)
            FROM results_tf WHERE meet_id = %s
            GROUP BY source, div_id, event_id
            ORDER BY source, div_id, event_id LIMIT 60""", (args.meet_id,))
        for s, d, e, n, ev in cur.fetchall():
            print(f"    {str(s):<7} div {str(d):<12} ev {str(e):<12} "
                  f"{n:>4} rows   {str(ev)[:30]}")

        if args.rid is not None:
            print(f"\n  result {args.rid}:")
            cur.execute("""
                SELECT source, meet_id, div_id, event_id, event_short,
                       person_id, athlete_id, date, time_seconds
                FROM results_tf WHERE result_id = %s""", (args.rid,))
            for r in cur.fetchall():
                print(f"    {r}")

        if args.person is not None:
            print(f"\n  person {args.person} at meet {args.meet_id}:")
            cur.execute("""
                SELECT result_id, source, div_id, event_id, event_short,
                       date, time_seconds
                FROM results_tf
                WHERE meet_id = %s
                  AND COALESCE(person_id, athlete_id) = %s""",
                        (args.meet_id, args.person))
            for r in cur.fetchall():
                print(f"    {r}")

    print("\n  READ: the athlete link is /race/tf/<meet>/<event>/<div>?r=. "
          "It works when\n  a results_tf group matches that exact triple; "
          "the meet page lists both\n  meets_tf events AND result-side "
          "groups. Paste this output back.")


if __name__ == "__main__":
    main()
