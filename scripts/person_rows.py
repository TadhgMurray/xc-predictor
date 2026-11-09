#!/usr/bin/env python3
"""
person_rows.py -- every cross country row on one profile, with what joined it
there. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/person_rows.py 26140046
    $P scripts/person_rows.py 19764025 --sport TF     # track: one line per feed id and team

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


# ★ TRACK (owner, 2026-11-08, /athlete/19764025: a Tennessee runner's profile
#   carrying four years of a Guam namesake's track). Track rows are many, so
#   they print grouped: one line per (feed, feed athlete id, name, team), with
#   its seasons, row count and the rules that moved its rows here.
_TF_GROUPS = """
    SELECT r.source, r.athlete_id, r.athlete_name, r.school,
           min(left(r.date, 10)), max(left(r.date, 10)), count(*),
           string_agg(DISTINCT r.grade, ','),
           (SELECT string_agg(DISTINCT l.rule || ' from ' || l.from_person, ', ')
            FROM person_link_log l
            WHERE l.sport = 'TF' AND l.result_id = ANY(array_agg(r.result_id))) AS linked
    FROM   results_tf r
    WHERE  r.person_id = %(p)s
    GROUP  BY 1, 2, 3, 4
    ORDER  BY 5
"""


def mainTF(pid):
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute("SELECT to_regclass('person_link_log')")
        sql = _TF_GROUPS
        if cur.fetchone()[0] is None:
            sql = sql.replace("""(SELECT string_agg(DISTINCT l.rule || ' from ' || l.from_person, ', ')
            FROM person_link_log l
            WHERE l.sport = 'TF' AND l.result_id = ANY(array_agg(r.result_id)))""", "NULL::text")
        cur.execute(sql, {"p": pid})
        rows = cur.fetchall()
        cur.execute("""SELECT athlete_id, first_name, last_name, school
                       FROM athletes WHERE athlete_id = %s""", (pid,))
        me = cur.fetchall()
        conn.rollback()
    print(f"/athlete/{pid}: track rows grouped by feed id and team; the profile's own row: {me}")
    for src, aid, name, school, d0, d1, n, grades, linked in rows:
        print(f"  {d0} to {d1}  feed {'2' if src == 'tfrrs' else '1'} #{aid} {name!r:<24} "
              f"{str(school)[:26]:<26} grades {grades}  {n} rows"
              + (f"  LINKED: {linked}" if linked else ""))
    return 0


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: person_rows.py PERSON_ID [--sport TF]")
        return 2
    import public_ids as _pi                   # a page's token works too
    pid = _pi.decode(args[0])[1] if _pi.decode(args[0]) else int(args[0])
    if "--sport" in args and args[args.index("--sport") + 1:][:1] == ["TF"]:
        return mainTF(pid)
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
