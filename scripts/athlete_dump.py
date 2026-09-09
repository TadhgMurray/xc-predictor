#!/usr/bin/env python3
"""
athlete_dump.py -- everything about one athlete, on one screen, for diagnosis.

    scripts/athlete_dump.py 23965611
    scripts/athlete_dump.py 26155532 23965611 > /tmp/dump.txt

★ WHY THIS EXISTS. Diagnosing a wrong rating from the WEB PAGE is diagnosing
  the answer, not the working. The page shows a rating; the question is
  always which term produced it -- the pool it was rated in, the course
  difficulty, the normalized time behind it, whether the row was priced by
  the fill or solved by the engine. Those are columns, and none of them is
  on the page.

  It also means the owner can hand over one command's output instead of
  writing SQL, which is the actual bottleneck when a bad row turns up.

! READ-ONLY. Every statement here is a SELECT. Safe on the live database
  with the site running.

Sections, in the order a diagnosis usually needs them:

    1  identity           who, and every school string they have worn
    2  seasons            athlete_season: the rating the boards rank
    3  races              every rated row, both sports, with the terms
    4  exclusions         wheelchair_person / result_twin / pro flag
    5  pool trail         what pool each season resolved to, and why
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                # noqa: E402


def _rows(cur, sql, args=None):
    cur.execute(sql, args or {})
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def _table(cols, rows, limit=None):
    """Fixed-width, because this gets pasted into a chat window and a CSV
    does not survive that."""
    if not rows:
        print("    (none)")
        return
    shown = rows if limit is None else rows[:limit]
    vals = [[("" if v is None else str(v)) for v in r] for r in shown]
    w = [max(len(c), *(len(v[i]) for v in vals)) if vals else len(c)
         for i, c in enumerate(cols)]
    w = [min(x, 34) for x in w]
    def line(cells):
        return "  " + "  ".join(str(c)[:w[i]].ljust(w[i])
                                for i, c in enumerate(cells))
    print(line(cols))
    print("  " + "  ".join("-" * x for x in w))
    for v in vals:
        print(line(v))
    if limit is not None and len(rows) > limit:
        print(f"    ... {len(rows) - limit:,} more rows")


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    row = cur.fetchone()
    return (row[0] if not isinstance(row, dict)
            else next(iter(row.values()))) is not None


def dump(cur, person_id):
    print("=" * 78)
    print(f"  ATHLETE {person_id}")
    print("=" * 78)

    print("\n-- 1. identity ------------------------------------------------")
    _table(*_rows(cur, """
        SELECT athlete_id, first_name, last_name, school, state, gender
        FROM   athletes WHERE athlete_id = %(p)s
    """, {"p": person_id}))

    print("\n-- 2. seasons (athlete_season -- what the boards rank) ---------")
    _table(*_rows(cur, """
        SELECT year, sport, pool, grade, school, state,
               round(mean_rating::numeric, 1)  AS rating,
               round(best_rating::numeric, 1)  AS best,
               n_races, first_race, last_race
        FROM   athlete_season WHERE person_id = %(p)s
        ORDER  BY year, sport
    """, {"p": person_id}))

    # ★ THE TERMS, NOT JUST THE RATING. rating_pool says which pool the
    #   ENGINE rated the row in (blank = the row was priced by the fill, or
    #   written by the old sequential engine, which stamped no pool);
    #   normalized_time is what the rating was computed from; the course
    #   difficulty is the venue term the rating already carries.
    for sport, table, meets, ev in (
            ("XC", "results", "meets", "m.division"),
            ("TF", "results_tf", "meets_tf", "r.event_short")):
        print(f"\n-- 3{'a' if sport == 'XC' else 'b'}. {sport} races "
              f"------------------------------------------")
        _table(*_rows(cur, f"""
            SELECT r.date, left(m.meet_name, 30)          AS meet,
                   left({ev}, 24)                         AS event_or_div,
                   r.time_seconds, r.normalized_time,
                   round(r.speed_rating::numeric, 1)      AS rating,
                   r.rating_pool,
                   round((cd.difficulty * 100)::numeric, 1) AS course_pct
            FROM   {table} r
            LEFT   JOIN {meets} m ON m.meet_id = r.meet_id
                                 AND m.source  = r.source
            LEFT   JOIN course_difficulties cd
                   ON cd.course_name = '{sport}:' || m.course_name
            WHERE  r.person_id = %(p)s
            ORDER  BY r.date
        """, {"p": person_id}), limit=60)

    print("\n-- 4. exclusions ----------------------------------------------")
    if _exists(cur, "wheelchair_person"):
        print("  wheelchair_person:")
        _table(*_rows(cur, "SELECT * FROM wheelchair_person "
                           "WHERE person_id = %(p)s", {"p": person_id}))
    else:
        print("  wheelchair_person: TABLE DOES NOT EXIST")

    # ⚠ AND THE RULE, SEPARATELY FROM THE LIST. If the list does not have
    #   them but the rule matches one of their rows, the list went stale
    #   (issue 1.6). If neither, the rule never saw them and that is a
    #   different bug entirely.
    try:
        from wheelchair_flag import WHEELCHAIR_RX
        print("\n  rows the LIVE chair rule matches (regardless of the list):")
        _table(*_rows(cur, f"""
            SELECT 'XC' AS sport, r.date, left(m.division, 40) AS label
            FROM   results r LEFT JOIN meets m ON m.div_id = r.div_id
                                              AND r.source = 'anet'
            WHERE  r.person_id = %(p)s
              AND  COALESCE(m.division, '') ~* %(rx)s
            UNION ALL
            SELECT 'TF', r.date, left(r.event_short, 40)
            FROM   results_tf r
            WHERE  r.person_id = %(p)s
              AND  COALESCE(r.event_short, '') ~* %(rx)s
            ORDER  BY 2
        """, {"p": person_id, "rx": WHEELCHAIR_RX}), limit=20)
    except Exception as exc:                                # noqa: BLE001
        print(f"  (chair rule check skipped: {exc})")

    for tbl, what in (("result_twin", "flagged duplicate races"),
                      ("pro_athlete_season", "pro seasons")):
        if _exists(cur, tbl):
            cur.execute(f"SELECT count(*) AS n FROM {tbl} "
                        f"WHERE person_id = %(p)s", {"p": person_id})
            row = cur.fetchone()
            n = row[0] if not isinstance(row, dict) else next(iter(row.values()))
            print(f"  {tbl}: {n} ({what})")

    print("\n-- 5. dropped rows (the rowguard) -----------------------------")
    # ! THE FEEDBACK LOOP CHECK. A chair athlete's absurd row gets dropped,
    #   which removes the evidence they race a chair -- see issue 1.6. A
    #   dropped row here beside a chair-shaped time is that loop.
    for sport, table in (("XC", "results"), ("TF", "results_tf")):
        cur.execute(f"""
            SELECT count(*) AS n FROM {table}
            WHERE person_id = %(p)s AND normalized_time IS NULL
        """, {"p": person_id})
        row = cur.fetchone()
        n = row[0] if not isinstance(row, dict) else next(iter(row.values()))
        print(f"  {sport}: {n} row(s) with no normalized_time "
              f"(dropped, or never priced)")


def main():
    ids = [int(x) for x in sys.argv[1:] if x.strip().isdigit()]
    if not ids:
        print(__doc__)
        print("usage: scripts/athlete_dump.py <person_id> [person_id ...]")
        return 2
    with getConn() as conn, conn.cursor() as cur:
        for pid in ids:
            dump(cur, pid)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
