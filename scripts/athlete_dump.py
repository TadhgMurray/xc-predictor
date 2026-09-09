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


# ! ASK THE CATALOG, DO NOT GUESS (2026-09-09). The first cut named
#   athletes.state, which does not exist, and the whole dump died on
#   section 1 -- a diagnostic that cannot survive a missing column is
#   useless exactly when you need it. Every hand-written column list here
#   is now filtered to what the table really has.
def _have(cur, table, wanted):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    got = set()
    for r in cur.fetchall():
        got.add(r[0] if not isinstance(r, dict) else next(iter(r.values())))
    return [c for c in wanted if c in got]


def _section(name, fn):
    """Run one section; a failure prints and the dump carries on. One bad
    column must not cost the other four sections."""
    print(f"\n-- {name} " + "-" * max(0, 62 - len(name)))
    try:
        fn()
    except Exception as exc:                                # noqa: BLE001
        print(f"    !! {type(exc).__name__}: "
              f"{str(exc).strip().splitlines()[0]}")


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
    p = {"p": person_id}

    def identity():
        cols = _have(cur, "athletes",
                     ["athlete_id", "person_id", "first_name", "last_name",
                      "school", "state", "gender", "grade", "source"])
        _table(*_rows(cur, f"SELECT {', '.join(cols)} FROM athletes "
                           f"WHERE athlete_id = %(p)s", p))
    _section("1. identity", identity)

    def seasons():
        cols = _have(cur, "athlete_season",
                     ["year", "sport", "pool", "grade", "school", "state",
                      "n_races", "first_race", "last_race"])
        rate = [c for c in _have(cur, "athlete_season",
                                 ["mean_rating", "best_rating",
                                  "decayed_rating"])]
        sel = ", ".join(cols + [f"round({c}::numeric, 1) AS {c}"
                                for c in rate])
        _table(*_rows(cur, f"SELECT {sel} FROM athlete_season "
                           f"WHERE person_id = %(p)s ORDER BY year, sport", p))
    _section("2. seasons (athlete_season -- what the boards rank)", seasons)

    # ★ THE TERMS, NOT JUST THE RATING. rating_pool says which pool the
    #   ENGINE rated the row in (blank = priced by the fill, or written by
    #   the old sequential engine, which stamped no pool); normalized_time
    #   is what the rating was computed from; course_pct is the venue term
    #   the rating already carries.
    for sport, table, meets, ev in (("XC", "results", "meets", "division"),
                                    ("TF", "results_tf", "meets_tf",
                                     "event_short")):
        def races(sport=sport, table=table, meets=meets, ev=ev):
            rcols = _have(cur, table,
                          ["date", "time_seconds", "normalized_time",
                           "speed_rating", "rating_pool", "event_short",
                           "is_field", "mark"])
            has_ev_on_row = ev in rcols
            mcols = _have(cur, meets, ["meet_name", "course_name", "division"])
            # narrow the lateral by division when both sides carry one
            div_join = bool(_have(cur, meets, ["div_id"])
                            and _have(cur, table, ["div_id"]))
            sel = [f"r.{c}" for c in rcols if c != "speed_rating"]
            if "speed_rating" in rcols:
                sel.append("round(r.speed_rating::numeric, 1) AS rating")
            if "meet_name" in mcols:
                sel.insert(1, "left(m.meet_name, 30) AS meet")
            if not has_ev_on_row and ev in mcols:
                sel.insert(2, f"left(m.{ev}, 24) AS {ev}")
            # ⚠ AND THE DIVISION EVEN WHEN THE ROW HAS AN EVENT. Track has
            #   BOTH: anet puts the class in meets_tf.division ("Wheelchair")
            #   and leaves event_short a bare "800m". Showing only the event
            #   is what made this dump report "no chair signal" for an
            #   athlete whose race page says "800m · Boys · Wheelchair"
            #   (2026-09-09).
            if has_ev_on_row and "division" in mcols:
                sel.insert(2, "left(m.division, 20) AS division")
            diff = ""
            if "course_name" in mcols and _exists(cur, "course_difficulties"):
                sel.append("round((cd.difficulty * 100)::numeric, 1) "
                           "AS course_pct")
                diff = ("LEFT JOIN course_difficulties cd ON cd.course_name = "
                        f"'{sport}:' || m.course_name")
            # ⚠ A LATERAL, NOT A JOIN, AND THIS COST A WHOLE DUMP.
            #   meets holds one row per DIVISION and meets_tf one row per
            #   EVENT (14.17M rows over 658K (meet_id, div_id) pairs, ~21.5
            #   each), so `JOIN ... ON meet_id AND source` fans every result
            #   out across every division or event of its meet. Kohen
            #   Grantom's THREE track rows came back as 405 identical lines,
            #   which reads as "this athlete has 405 races" and is the
            #   opposite of what a diagnostic is for. LIMIT 1 is exactly one
            #   meet row per result, whatever the meet's shape.
            _table(*_rows(cur, f"""
                SELECT {', '.join(sel)}
                FROM   {table} r
                LEFT   JOIN LATERAL (
                    SELECT * FROM {meets} mm
                    WHERE  mm.meet_id = r.meet_id AND mm.source = r.source
                    {"AND mm.div_id = r.div_id" if div_join else ""}
                    LIMIT  1
                ) m ON TRUE
                {diff}
                WHERE  r.person_id = %(p)s
                ORDER  BY r.date
            """, p), limit=60)
        _section(f"3{'a' if sport == 'XC' else 'b'}. {sport} races", races)

    def exclusions():
        if _exists(cur, "wheelchair_person"):
            print("  wheelchair_person row:")
            _table(*_rows(cur, "SELECT * FROM wheelchair_person "
                               "WHERE person_id = %(p)s", p))
        else:
            print("  wheelchair_person: TABLE DOES NOT EXIST")

        # ⚠ THE RULE, SEPARATELY FROM THE LIST, and this is the whole chair
        #   diagnosis. In the list -> fine. Not in the list but the rule
        #   matches -> the list went stale. Not in the list and the rule
        #   matches nothing -> the rule never saw them, which is a different
        #   bug needing a different fix.
        from wheelchair_flag import WHEELCHAIR_RX
        print("\n  rows the LIVE chair rule matches "
              "(regardless of the list):")
        _table(*_rows(cur, """
            SELECT 'XC' AS sport, r.date, left(m.division, 44) AS label
            FROM   results r
            LEFT   JOIN meets m ON m.div_id = r.div_id AND r.source = 'anet'
            WHERE  r.person_id = %(p)s
              AND  COALESCE(m.division, '') ~* %(rx)s
            UNION ALL
            SELECT 'TF', r.date,
                   left(concat_ws(' | ', NULLIF(mt.division, ''),
                                  r.event_short), 44)
            FROM   results_tf r
            LEFT   JOIN meets_tf mt ON mt.meet_id = r.meet_id
                                   AND mt.div_id  = r.div_id
                                   AND mt.source  = r.source
            WHERE  r.person_id = %(p)s
              AND (COALESCE(r.event_short, '') ~* %(rx)s
                OR COALESCE(mt.division, '')  ~* %(rx)s)
            ORDER  BY 2
        """, {"p": person_id, "rx": WHEELCHAIR_RX}), limit=20)

        # ! AND EVERY DISTINCT LABEL THEY EVER RACED UNDER, matched or not.
        #   If the rule finds nothing, this is what it would have had to
        #   match -- i.e. the answer to "why doesn't it fire".
        print("\n  every distinct division / event they raced (rule or no):")
        _table(*_rows(cur, """
            SELECT sport, label, n FROM (
                SELECT 'XC' AS sport, left(m.division, 44) AS label,
                       count(*) AS n
                FROM   results r
                LEFT   JOIN meets m ON m.div_id = r.div_id
                                   AND r.source = 'anet'
                WHERE  r.person_id = %(p)s GROUP BY 2
                UNION ALL
                -- BOTH track columns: the class can be in either one
                SELECT 'TF', left(concat_ws(' | ', NULLIF(mt.division, ''),
                                            r.event_short), 44), count(*)
                FROM   results_tf r
                LEFT   JOIN meets_tf mt ON mt.meet_id = r.meet_id
                                       AND mt.div_id  = r.div_id
                                       AND mt.source  = r.source
                WHERE  r.person_id = %(p)s GROUP BY 2
            ) x ORDER BY sport, n DESC
        """, p), limit=40)

        for tbl, what in (("result_twin", "flagged duplicate races"),
                          ("pro_athlete_season", "pro seasons")):
            if not _exists(cur, tbl):
                continue
            if not _have(cur, tbl, ["person_id"]):
                continue
            cur.execute(f"SELECT count(*) AS n FROM {tbl} "
                        f"WHERE person_id = %(p)s", p)
            row = cur.fetchone()
            n = (row[0] if not isinstance(row, dict)
                 else next(iter(row.values())))
            print(f"  {tbl}: {n} ({what})")
    _section("4. exclusions", exclusions)

    def dropped():
        # ! THE FEEDBACK-LOOP CHECK. A chair athlete's absurd row gets
        #   dropped, which removes the evidence they race a chair. A dropped
        #   row beside a chair-shaped time is that loop.
        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            if not _have(cur, table, ["normalized_time"]):
                continue
            cur.execute(f"""SELECT count(*) AS n FROM {table}
                            WHERE person_id = %(p)s
                              AND normalized_time IS NULL""", p)
            row = cur.fetchone()
            n = (row[0] if not isinstance(row, dict)
                 else next(iter(row.values())))
            print(f"  {sport}: {n} row(s) with no normalized_time "
                  f"(dropped, or never priced)")
    _section("5. rows with no normalized_time (the rowguard)", dropped)


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
