#!/usr/bin/env python3
"""
diag_swapped_names.py -- people we hold twice with first and last name the
other way round ("Ethan Avila" and "Avila Ethan" at one school). READ ONLY:
temp tables, nothing kept.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_swapped_names.py              # counts + 40 examples
    /srv/venv/bin/python scripts/diag_swapped_names.py --show 200

★ WHY (owner, 2026-10-26, on AthleticLIVE's "Avila Ethan" / our "Ethan
  Avila": "we can prolly fix that right, generally?"). A form filled in
  last-name-first makes a second profile, and no name key joins them: the
  key of "Avila Ethan" is "avila ethan".

A candidate is two different people (person_id) at the same school, of the
same gender, whose names are each other's reversed (suffix kept, so "Smith
Jr" never meets "Jr Smith"), and whose names are not simply the same. Then,
as diag_name_key does:

  safe       exactly two people at the school answer to either order
  crowded    three or more do
  overlap    the two raced one season under different grades: most likely
             two people
  same race  the two ran in one race (XC or track): two people

! "JAMES THOMAS" AND "THOMAS JAMES" CAN BE TWO KIDS. Both are first names
  and both are surnames; the checks above are what keeps them apart, and a
  pair where both names are common first names deserves a look before any
  linker trusts this list. Nothing links on it yet.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from person_name import personKeySql   # noqa: E402

_KEYS = """
CREATE TEMP TABLE sw AS
SELECT DISTINCT a.person_id, upper(a.gender) AS g,
       lower(btrim(a.school)) AS sch,
       {fwd} AS k, {rev} AS kr,
       btrim(concat_ws(' ', a.first_name, a.last_name)) AS shown
FROM   athletes a
WHERE  a.person_id IS NOT NULL AND NULLIF(btrim(a.school), '') IS NOT NULL
  AND  a.gender IN ('M', 'F', 'm', 'f')
  AND  NULLIF(btrim(a.first_name), '') IS NOT NULL
  AND  NULLIF(btrim(a.last_name), '') IS NOT NULL
"""

_PAIRS = """
CREATE TEMP TABLE nk_pair AS
SELECT DISTINCT ON (least(a.person_id, b.person_id), greatest(a.person_id, b.person_id))
       least(a.person_id, b.person_id) AS p1, greatest(a.person_id, b.person_id) AS p2,
       a.sch, a.g, a.k AS k1, b.k AS k2, a.shown AS n1, b.shown AS n2
FROM   sw a
JOIN   sw b ON b.k = a.kr AND b.sch = a.sch AND b.g = a.g
           AND b.person_id <> a.person_id
WHERE  a.k <> a.kr AND a.k <> b.k AND position(' ' in a.k) > 0
"""

_CROWD = """
SELECT p.p1, p.p2, (SELECT count(DISTINCT person_id) FROM sw c
                    WHERE c.k IN (p.k1, p.k2) AND c.sch = p.sch AND c.g = p.g) AS n
FROM   nk_pair p
"""

# the two raced in one race, either sport: two people
_SAME_RACE = """
SELECT p.p1, p.p2 FROM nk_pair p
WHERE  EXISTS (SELECT 1 FROM results r1
               JOIN results r2 ON r2.person_id = p.p2 AND r2.meet_id = r1.meet_id
                              AND r2.div_id = r1.div_id AND r2.source = r1.source
               WHERE r1.person_id = p.p1)
   OR  EXISTS (SELECT 1 FROM results_tf r1
               JOIN results_tf r2 ON r2.person_id = p.p2 AND r2.meet_id = r1.meet_id
                                 AND r2.div_id = r1.div_id AND r2.event_id = r1.event_id
                                 AND r2.source = r1.source
               WHERE r1.person_id = p.p1)
"""

# one academic year, two different grades: listed apart
_OVERLAP = """
WITH rows AS (SELECT person_id, date, grade FROM results
              WHERE person_id IN (SELECT p1 FROM nk_pair UNION SELECT p2 FROM nk_pair)
              UNION ALL
              SELECT person_id, date, grade FROM results_tf
              WHERE person_id IN (SELECT p1 FROM nk_pair UNION SELECT p2 FROM nk_pair)),
s AS (SELECT person_id,
             CASE WHEN extract(month FROM date::date) >= 8
                  THEN extract(year FROM date::date) ELSE extract(year FROM date::date) - 1 END AS ay,
             min(grade) AS gr
      FROM   rows
      WHERE  grade ~ '^[0-9]+$' AND date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
      GROUP  BY 1, 2)
SELECT DISTINCT p.p1, p.p2 FROM nk_pair p
JOIN   s a ON a.person_id = p.p1
JOIN   s b ON b.person_id = p.p2 AND b.ay = a.ay AND b.gr <> a.gr
"""


def classify(pairs, crowd, same, overlap):
    """Pure: -> {kind: [pair rows]} in the four kinds the header names."""
    kinds = {"safe": [], "crowded": [], "overlap": [], "same race": []}
    for row in pairs:
        key = (row[0], row[1])
        if key in same:
            kind = "same race"
        elif crowd.get(key, 2) > 2:
            kind = "crowded"
        elif key in overlap:
            kind = "overlap"
        else:
            kind = "safe"
        kinds[kind].append(row)
    return kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()
    from database import getConn
    t0 = time.time()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '1GB'")
        cur.execute(_KEYS.format(fwd=personKeySql("a.first_name", "a.last_name", keep_suffix=True),
                                 rev=personKeySql("a.last_name", "a.first_name", keep_suffix=True)))
        cur.execute("CREATE INDEX ON sw (k, sch, g)")
        cur.execute("CREATE INDEX ON sw (kr, sch, g)")
        cur.execute("ANALYZE sw")
        cur.execute(_PAIRS)
        cur.execute("ANALYZE nk_pair")
        cur.execute("SELECT count(*) FROM nk_pair")
        n_pairs = cur.fetchone()[0]
        print(f"[swapped] {n_pairs:,} pairs of people whose names are each other's "
              f"reversed ({time.time() - t0:.0f}s)")
        cur.execute(_CROWD)
        crowd = {(p1, p2): n for p1, p2, n in cur.fetchall()}
        cur.execute(_SAME_RACE)
        same = set(cur.fetchall())
        cur.execute(_OVERLAP)
        overlap = set(cur.fetchall())
        cur.execute("SELECT p1, p2, sch, g, n1, n2 FROM nk_pair ORDER BY sch, k1")
        pairs = cur.fetchall()
        conn.rollback()
    kinds = classify(pairs, crowd, same, overlap)
    for kind, rows in kinds.items():
        print(f"    {kind:<10} {len(rows):>8,}")
    print(f"\n  {min(args.show, len(kinds['safe']))} safe examples:")
    for p1, p2, sch, g, n1, n2 in kinds["safe"][:args.show]:
        print(f"    {n1!r:<30} {n2!r:<30} {g}  {sch[:34]:<34}  persons {p1} / {p2}")
    print(f"\n[swapped] done in {time.time() - t0:.0f}s; nothing written")


if __name__ == "__main__":
    main()
