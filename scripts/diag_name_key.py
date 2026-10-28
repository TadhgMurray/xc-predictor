#!/usr/bin/env python3
"""
diag_name_key.py -- what the one name key (scripts/person_name.py) would
link that today's linker key does not. READ ONLY: temp tables, nothing kept.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_name_key.py              # counts + 40 examples
    /srv/venv/bin/python scripts/diag_name_key.py --show 200

A candidate is two different people (person_id) on athletes profiles whose
names differ under today's key (link_teamless.nameKeySql: accents deleted,
no Jr) but are the SAME under the new one, at the same school, of the same
gender, who never ran in the same race (two runners in one race are two
people). That is the shape of a missed link: "José Peña" and "Jose Pena"
at one high school, or "John Smith" and "John Smith Jr".

  safe      exactly two people share the new key at the school
  crowded   three or more do: a linker's "exactly one match" refuses these
  overlap   the two raced the same season under different grades: listed
            apart, most likely two people (a father and son, cousins)

Each pair is tagged by what made the difference: accents, a suffix, a
hyphen or apostrophe, or a comma.
"""
import argparse
import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from person_name import personKeySql, FOLD_FROM, SUFFIXES   # noqa: E402

_KEYS = """
CREATE TEMP TABLE nk AS
SELECT DISTINCT a.person_id, upper(a.gender) AS g,
       lower(btrim(a.school)) AS sch,
       {old} AS oldk, {new} AS newk,
       btrim(concat_ws(' ', a.first_name, a.last_name)) AS shown
FROM   athletes a
WHERE  a.person_id IS NOT NULL AND NULLIF(btrim(a.school), '') IS NOT NULL
  AND  a.gender IN ('M', 'F', 'm', 'f')
"""

_PAIRS = """
CREATE TEMP TABLE nk_pair AS
SELECT DISTINCT ON (least(a.person_id, b.person_id), greatest(a.person_id, b.person_id))
       a.person_id AS p1, b.person_id AS p2, a.sch, a.g, a.newk,
       a.shown AS n1, b.shown AS n2, a.oldk AS o1, b.oldk AS o2
FROM   nk a
JOIN   nk b ON b.newk = a.newk AND b.sch = a.sch AND b.g = a.g
           AND b.person_id > a.person_id AND b.oldk <> a.oldk
WHERE  position(' ' in a.newk) > 0
"""

_CROWD = """
SELECT p.p1, p.p2, (SELECT count(DISTINCT person_id) FROM nk c
                    WHERE c.newk = p.newk AND c.sch = p.sch AND c.g = p.g) AS n
FROM   nk_pair p
"""

# the two raced in one race: two people
_SAME_RACE = """
SELECT p.p1, p.p2 FROM nk_pair p
WHERE  EXISTS (SELECT 1 FROM results r1
               JOIN results r2 ON r2.person_id = p.p2 AND r2.meet_id = r1.meet_id
                              AND r2.div_id = r1.div_id AND r2.source = r1.source
               WHERE r1.person_id = p.p1)
"""

# one academic year, two different grades: listed apart
_OVERLAP = """
WITH s AS (SELECT person_id,
                  CASE WHEN extract(month FROM date::date) >= 8
                       THEN extract(year FROM date::date) ELSE extract(year FROM date::date) - 1 END AS ay,
                  min(grade) AS gr
           FROM   results
           WHERE  person_id IN (SELECT p1 FROM nk_pair UNION SELECT p2 FROM nk_pair)
             AND  grade ~ '^[0-9]+$' AND date::text ~ '^[0-9]{4}-'
           GROUP  BY 1, 2)
SELECT DISTINCT p.p1, p.p2 FROM nk_pair p
JOIN   s a ON a.person_id = p.p1
JOIN   s b ON b.person_id = p.p2 AND b.ay = a.ay AND b.gr <> a.gr
"""


def legacyKeySql(first, last):
    """The linkers' key before 2026-10-26 (accents DELETED, no suffix rule),
    frozen here so this report still says what the switch changed."""
    return (f"btrim(regexp_replace(regexp_replace(lower(replace("
            f"concat_ws(' ', btrim({first}), btrim({last})), '-', ' ')), "
            f"'[^a-z ]+', '', 'g'), '\\s+', ' ', 'g'))")


def why(n1, n2):
    """What made two names different under the old key."""
    a, b = str(n1 or ""), str(n2 or "")
    tags = []
    if any(c in FOLD_FROM for c in a + b):
        tags.append("accents")
    words = re.sub(r"[^a-z ]+", " ", (a + " " + b).lower()).split()
    if any(w in SUFFIXES for w in words):
        tags.append("suffix")
    if "," in a + b:
        tags.append("comma")
    if "-" in a + b or "'" in a + b:
        tags.append("hyphen/apostrophe")
    return "+".join(tags) or "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()
    from database import getConn
    t0 = time.time()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '1GB'")
        cur.execute(_KEYS.format(old=legacyKeySql("a.first_name", "a.last_name"),
                                 new=personKeySql("a.first_name", "a.last_name")))
        cur.execute("CREATE INDEX ON nk (newk, sch, g)")
        cur.execute("ANALYZE nk")
        cur.execute("SELECT count(*), count(DISTINCT person_id), "
                    "count(*) FILTER (WHERE oldk <> newk) FROM nk")
        n_rows, n_people, n_changed = cur.fetchone()
        print(f"[names] {n_people:,} people on {n_rows:,} (person, school, name) rows; "
              f"{n_changed:,} rows whose key changes ({time.time() - t0:.0f}s)")
        cur.execute(_PAIRS)
        cur.execute("ANALYZE nk_pair")
        cur.execute("SELECT count(*) FROM nk_pair")
        n_pairs = cur.fetchone()[0]
        cur.execute(_CROWD)
        crowd = {(p1, p2): n for p1, p2, n in cur.fetchall()}
        cur.execute(_SAME_RACE)
        same = set(cur.fetchall())
        cur.execute(_OVERLAP)
        overlap = set(cur.fetchall())
        cur.execute("SELECT p1, p2, sch, g, newk, n1, n2 FROM nk_pair ORDER BY sch, newk")
        pairs = cur.fetchall()
        conn.rollback()
    kinds = {"safe": [], "crowded": [], "overlap": [], "same race": []}
    tags = {}
    for p1, p2, sch, g, k, n1, n2 in pairs:
        if (p1, p2) in same:
            kind = "same race"
        elif crowd.get((p1, p2), 2) > 2:
            kind = "crowded"
        elif (p1, p2) in overlap:
            kind = "overlap"
        else:
            kind = "safe"
        kinds[kind].append((p1, p2, sch, g, k, n1, n2))
        if kind == "safe":
            t = why(n1, n2)
            tags[t] = tags.get(t, 0) + 1
    print(f"\n[names] {n_pairs:,} pairs of people the new key would bring together "
          f"(same school, same gender, different old key):")
    for kind, rows in kinds.items():
        print(f"    {kind:<10} {len(rows):>8,}")
    print("\n  safe pairs by what differed:")
    for t, n in sorted(tags.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>8,}  {t}")
    print(f"\n  {min(args.show, len(kinds['safe']))} safe examples:")
    for p1, p2, sch, g, k, n1, n2 in kinds["safe"][:args.show]:
        print(f"    {n1!r:<30} {n2!r:<30} {g}  {sch[:34]:<34}  persons {p1} / {p2}")
    print(f"\n[names] done in {time.time() - t0:.0f}s; nothing written")


if __name__ == "__main__":
    main()
