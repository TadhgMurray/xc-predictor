#!/usr/bin/env python3
"""
diag_tfrrs_identity.py -- how much of each tfrrs season is tied to a person.

    /srv/venv/bin/python scripts/diag_tfrrs_identity.py
    /srv/venv/bin/python scripts/diag_tfrrs_identity.py --since 2022 --school Tufts

★ WHY (owner, 2026-09-25: "all freshman are unknown", race pages "unlinked",
  "the multiple systems kind of messing things up"). A tfrrs row is tied to a
  person only by the linking passes (merge_links, stamp_fanout,
  link_idless_*), and none of them runs on a schedule. A row with no
  person_id has no athlete page, no name from `athletes`, and -- if the engine
  keys on the person -- no rating. This counts, per academic season and
  sport, how many tfrrs rows carry a person, only a tfrrs athlete id, or
  nothing, and how many are normalized and rated. READ-ONLY.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                  # noqa: E402

SQL = """
    SELECT CASE WHEN substring(date, 6, 2) >= '08'
                THEN substring(date, 1, 4)::int
                ELSE substring(date, 1, 4)::int - 1 END      AS season,
           count(*)                                          AS rows,
           count(person_id)                                  AS with_person,
           count(*) FILTER (WHERE person_id IS NULL
                              AND native_id IS NOT NULL)     AS native_only,
           count(*) FILTER (WHERE person_id IS NULL
                              AND native_id IS NULL)         AS no_id,
           count(normalized_time)                            AS normalized,
           count(speed_rating)                               AS rated
    FROM   {table}
    WHERE  source = 'tfrrs' AND date >= %(since)s {school}
    GROUP  BY 1 ORDER BY 1
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2021)
    ap.add_argument("--school", help="only this school's rows (exact name)")
    a = ap.parse_args()
    school = "AND school = %(school)s" if a.school else ""
    with getConn() as conn:
        with conn.cursor() as cur:
            for sport, table in (("XC", "results"), ("TF", "results_tf")):
                cur.execute(SQL.format(table=table, school=school),
                            {"since": f"{a.since}-08-01", "school": a.school})
                rows = cur.fetchall()
                print(f"\n{sport} ({table}, source tfrrs"
                      f"{', ' + a.school if a.school else ''})")
                print(f"  {'season':<7}{'rows':>11}{'person':>11}{'tfrrs id':>11}"
                      f"{'no id':>9}{'normalized':>12}{'rated':>11}")
                for season, n, wp, nat, none, norm, rated in rows:
                    print(f"  {season:<7}{n:>11,}{wp:>11,}{nat:>11,}{none:>9,}"
                          f"{norm:>12,}{rated:>11,}")


if __name__ == "__main__":
    main()
