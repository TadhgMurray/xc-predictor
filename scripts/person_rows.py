#!/usr/bin/env python3
"""
person_rows.py -- every cross country row on one profile, with what joined it
there. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/person_rows.py 26140046

★ WHY (owner, 2026-11-07, Katrina Coogan's profile: "there must be something
  else"). A boy's results on a girl's profile got there by a LINK -- the
  feed's own athlete id, a merge across the two feeds, a rule that joined
  two ids. The rows keep the name and id the feed printed on them, and the
  link log says which rule moved a row. A brother with the same surname at
  the same school and the same college would show here as a second first
  name under one profile: the class to catch is the rule that joined them.
"""
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ROWS = """
    WITH mine AS (
        SELECT r.result_id, r.source, r.athlete_id, r.athlete_name, r.school,
               left(r.date, 10) AS d, r.meet_id, r.div_id, r.place, r.time_seconds,
               r.speed_rating, split_part(COALESCE(r.rating_pool, ''), '|', 1) AS pool
        FROM   results r
        WHERE  r.person_id = %(p)s),
    fld AS (
        SELECT r.source, r.meet_id, r.div_id, count(*) AS n,
               count(*) FILTER (WHERE right(split_part(r.rating_pool, '|', 1), 2) = '_m') AS m
        FROM   results r
        JOIN   (SELECT DISTINCT source, meet_id, div_id FROM mine) k
               USING (source, meet_id, div_id)
        GROUP  BY 1, 2, 3)
    SELECT m.d, m.result_id, m.source, m.athlete_id, m.athlete_name, m.school, m.pool,
           m.place, f.n, f.m, m.time_seconds, m.speed_rating,
           (SELECT string_agg(l.rule || ' from ' || l.from_person, ', ')
            FROM person_link_log l
            WHERE l.sport = 'XC' AND l.result_id = m.result_id) AS linked
    FROM   mine m
    LEFT   JOIN fld f USING (source, meet_id, div_id)
    ORDER  BY m.d
"""


def _t(s):
    if not s:
        return "-"
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: person_rows.py PERSON_ID")
        return 2
    pid = int(args[0])
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute("SELECT to_regclass('person_link_log')")
        sql = _ROWS
        if cur.fetchone()[0] is None:
            sql = sql.replace("""(SELECT string_agg(l.rule || ' from ' || l.from_person, ', ')
            FROM person_link_log l
            WHERE l.sport = 'XC' AND l.result_id = m.result_id)""", "NULL::text")
        cur.execute(sql, {"p": pid})
        rows = cur.fetchall()
        conn.rollback()
    names = Counter((r[4] or "?") for r in rows)
    ids = Counter((r[2], r[3]) for r in rows)
    print(f"/athlete/{pid}: {len(rows)} cross country rows")
    print("  names the feeds printed: " + ", ".join(f"{n!r} x{k}" for n, k in names.most_common()))
    print("  feed athlete ids: " + ", ".join(f"feed {'2' if s == 'tfrrs' else '1'} #{a} x{k}"
                                           for (s, a), k in ids.most_common()))
    for d, rid, src, aid, name, school, pool, place, n, m, t, s, linked in rows:
        field = "-"
        if n:
            share = (m or 0) / n
            field = "men's" if share >= 0.8 else ("women's" if share <= 0.2 else "mixed")
        print(f"  {d} {rid:>20} feed {'2' if src == 'tfrrs' else '1'} #{aid} {name!r:<26} "
              f"{str(school)[:22]:<22} {pool:<9} {field:<7} {place or '-'}/{n or '-'} "
              f"{_t(t)} {s and round(float(s), 1)}" + (f"  LINKED: {linked}" if linked else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
