#!/usr/bin/env python3
"""
freshman_recheck.py -- re-judge every freshman link already made against the
college-namesake guard, and take back the ones that fail.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/freshman_recheck.py                 # DRY RUN: what would be undone
    $P scripts/freshman_recheck.py --show 200
    $P scripts/freshman_recheck.py --write         # undo them (logged)
    $P scripts/freshman_recheck.py --undo          # put every one back

★ WHY (owner, 2026-11-08, /athlete/19764025: "yes" to a sweep). The freshman
  linker joined the college feed's copies of Hayden Buck's East Texas Baptist
  races to the only 2026 senior of that name -- in Guam -- though the main
  feed already had those races under the Tennessee Hayden Buck. It now
  refuses such a pair (link_freshmen.dropCollegeNamesakes); this finds the
  links it made before it did.

★ THE TEST, PER LINK. A freshman link is a college-feed identity (its id
  system and athlete id) put on an anet person A by rule 'freshman' or
  'freshman_time'. It fails when the main feed holds a college race of the
  same name at that college under a person other than A -- the guard's own
  test, the same loader and the same college match.

★ WHAT --write TAKES BACK. Every row of the failing identity that sits on A
  and got there by 'freshman', 'freshman_time' or 'fanout' (the pass that
  spreads an identity's person to its other rows -- left alone, it would put
  the rows straight back on A). Each goes back to what it was before
  (NULLIF(from_person, 0)); the link log entries go, and every move is kept
  in freshman_recheck_undo for --undo. The next pipeline run places them:
  04a_link_tfrrs gives the bare rows their college-feed person, and
  04a2b_feed_twins joins that person to the runner whose races they copy
  (both run on every --from).

Read-only without --write / --undo.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RULES = ("freshman", "freshman_time")
SPREAD = ("freshman", "freshman_time", "fanout")
TABLES = (("XC", "results"), ("TF", "results_tf"))

UNDO_DDL = """
CREATE TABLE IF NOT EXISTS freshman_recheck_undo (
    sport      text   NOT NULL,
    result_id  bigint NOT NULL,
    to_person  bigint NOT NULL,
    rule       text   NOT NULL,
    from_person bigint NOT NULL,
    undone_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sport, result_id, rule))
"""


def _sys(sport):
    return "'tfrrs'" if sport == "XC" else "COALESCE(r.id_system, 'tfrrs')"


def links(cur):
    """[(sport, sys, native_id, person A, name, college, n rows)]: every
    college-feed identity a freshman rule put on a person, still there."""
    out = []
    for sport, table in TABLES:
        cur.execute(f"""
            SELECT {_sys(sport)}, r.native_id, l.to_person,
                   mode() WITHIN GROUP (ORDER BY btrim(r.athlete_name)),
                   mode() WITHIN GROUP (ORDER BY r.school), count(*)
            FROM   person_link_log l
            JOIN   {table} r ON r.result_id = l.result_id AND r.person_id = l.to_person
            WHERE  l.sport = %s AND l.rule = ANY(%s) AND r.source = 'tfrrs'
              AND  r.native_id IS NOT NULL
            GROUP  BY 1, 2, 3
        """, (sport, list(RULES)))
        out += [(sport, *row) for row in cur.fetchall()]
    return out


def failing(found, namesakes):
    """The links whose college already has a main-feed runner of the name
    under another person: [(link, rivals)]. Pure (link_freshmen's test)."""
    from link_freshmen import normName, sameCollege
    out = []
    for lk in found:
        _sport, _sys_, _nat, a, name, college, _n = lk
        rivals = [(pid, s) for pid, s in namesakes.get(normName(name or ""), []) if pid != a]
        if rivals and sameCollege(college, [s for _p, s in rivals]):
            out.append((lk, sorted({pid for pid, _s in rivals})))
    return out


def write(conn, bad):
    """Take every row of each failing identity off its person (see header)."""
    from psycopg2.extras import execute_values
    moved = Counter()
    with conn.cursor() as cur:
        cur.execute(UNDO_DDL)
        cur.execute("DROP TABLE IF EXISTS fr_bad")
        cur.execute("CREATE TEMP TABLE fr_bad (sys text, native_id bigint, person bigint)")
        execute_values(cur, "INSERT INTO fr_bad VALUES %s",
                       list({(lk[1], lk[2], lk[3]) for lk, _r in bad}))
        for sport, table in TABLES:
            cur.execute(f"""
                INSERT INTO freshman_recheck_undo (sport, result_id, to_person, rule, from_person)
                SELECT l.sport, l.result_id, l.to_person, l.rule, l.from_person
                FROM   person_link_log l
                JOIN   {table} r ON r.result_id = l.result_id AND r.person_id = l.to_person
                JOIN   fr_bad b ON b.native_id = r.native_id AND b.sys = {_sys(sport)}
                                AND b.person = l.to_person
                WHERE  l.sport = %s AND l.rule = ANY(%s) AND r.source = 'tfrrs'
                ON CONFLICT DO NOTHING
            """, (sport, list(SPREAD)))
            cur.execute(f"""
                UPDATE {table} r SET person_id = NULLIF(u.from_person, 0)
                FROM   freshman_recheck_undo u
                WHERE  u.sport = %s AND u.result_id = r.result_id
                  AND  r.person_id = u.to_person
            """, (sport,))
            moved[sport] = cur.rowcount
            cur.execute("""
                DELETE FROM person_link_log l USING freshman_recheck_undo u
                WHERE  l.sport = u.sport AND l.result_id = u.result_id AND l.rule = u.rule
                  AND  l.sport = %s
            """, (sport,))
    conn.commit()
    return dict(moved)


def undo(conn):
    """Put every row --write took back on its person again."""
    back = {}
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('freshman_recheck_undo')")
        if cur.fetchone()[0] is None:
            return back
        for sport, table in TABLES:
            cur.execute(f"""
                UPDATE {table} r SET person_id = u.to_person
                FROM   freshman_recheck_undo u
                WHERE  u.sport = %s AND u.result_id = r.result_id
                  AND  r.person_id IS NOT DISTINCT FROM NULLIF(u.from_person, 0)
            """, (sport,))
            back[sport] = cur.rowcount
            cur.execute("""
                INSERT INTO person_link_log (sport, result_id, from_person, to_person, rule)
                SELECT sport, result_id, from_person, to_person, rule
                FROM   freshman_recheck_undo WHERE sport = %s
                ON CONFLICT DO NOTHING
            """, (sport,))
        cur.execute("DELETE FROM freshman_recheck_undo")
    conn.commit()
    return back


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--show", type=int, default=60)
    a = ap.parse_args(argv)
    from database import getConn
    from link_freshmen import anetCollegeNamesakes, stageCollegeSchools
    with getConn() as conn:
        if a.undo:
            print(f"[recheck] put back: {undo(conn)}")
            return 0
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 0")
            stageCollegeSchools(cur)
            found = links(cur)
            ns = anetCollegeNamesakes(cur, [lk[4] for lk in found if lk[4]])
            bad = failing(found, ns)
        conn.rollback()
        rows = sum(lk[6] for lk, _r in bad)
        print(f"[recheck] {len(found):,} freshman links still in place; {len(bad):,} fail the "
              f"college-namesake test ({rows:,} rows linked by the freshman rules, plus their "
              f"fanout copies)")
        by_college = Counter(lk[5] for lk, _r in bad)
        for lk, rivals in sorted(bad, key=lambda b: (b[0][4] or ""))[:a.show]:
            sport, _s, nat, person, name, college, n = lk
            print(f"  {name!r:<26} {str(college)[:28]:<28} {sport} id {nat}: on /athlete/{person},"
                  f" but the main feed has {name} at this college as "
                  + ", ".join(f"/athlete/{p}" for p in rivals) + f"  ({n} rows)")
        if by_college:
            print("  most at: " + ", ".join(f"{c} x{k}" for c, k in by_college.most_common(8)))
        if not a.write:
            print("DRY RUN -- --write takes them back (--undo puts them back)")
            return 0
        moved = write(conn, bad)
        print(f"[recheck] taken back: {moved}. The next pipeline run places them "
              f"(04a_link_tfrrs, then 04a2b_feed_twins: both run on every --from)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
