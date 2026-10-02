#!/usr/bin/env python3
"""
unknown_names.py -- how many results show "Unknown", and why. Read-only.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/unknown_names.py
    /srv/venv/bin/python scripts/unknown_names.py --top 30

★ WHY (owner, 2026-10-02: "unknown athletes are still a really bad and
  really big issue"). A 1993 NCAA D2 race page shows 111 of 147 runners as
  "Unknown", a 2026 Texas invitational 50 of 435, and their athlete pages
  are titled "None". Every one of those rows HAS a person id; what is
  missing is a name anywhere the site looks:

    athletes   first/last name on a row whose person_id OR athlete_id is
               the row's person (the athlete page reads the first, the race
               page the second)
    results    athlete_name on the row itself (tfrrs carries it inline;
               anet rows are ~90% blank and rely on athletes)

  This counts the rows with neither, by sport, source and season, says
  which of the causes each falls under, and lists the meets with the most:
  a meet that is ALL nameless is a scrape that lost its names (re-scrape
  it); a meet that is partly nameless is per-athlete (the athletes those
  ids belong to were never fetched with a name).

  CAUSES, per nameless row:
    no_person       person_id is NULL (unlinked; the page shows the anet id)
    blank_athlete   an athletes row exists for the person, every name blank
                    (the scraper's FK placeholder, never filled)
    no_athlete      no athletes row at all for the person

! TRACK RELAYS ARE NOT COUNTED (2026-10-02). A relay row is a team: the
  saver stores it with no athlete id and no person, on purpose, so every one
  of them read as "no_person" here -- relays are roughly 6% of a meet's rows,
  which is about the whole 6.4% this first reported for track. They are
  counted on their own line; the race page names the squad and its runners
  (relayLegs).
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

# results.date is TEXT ('YYYY-MM-DD'); read it as text so one malformed date
# cannot fail the whole report
SEASON = ("(CASE WHEN r.date ~ '^[0-9]{4}-[0-9]{2}' THEN left(r.date, 4)::int"
          " - CASE WHEN substr(r.date, 6, 2)::int < 8 THEN 1 ELSE 0 END END)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=20, help="meets listed per sport")
    a = ap.parse_args()
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET work_mem = '1GB'")
        # every id the site can put a name to, and the ids with only blank rows
        cur.execute("""
            CREATE TEMP TABLE un_named AS
            SELECT DISTINCT k AS id FROM (
                SELECT person_id AS k FROM athletes
                WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL
                  AND  person_id IS NOT NULL
                UNION ALL
                SELECT athlete_id FROM athletes
                WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL) x;
            CREATE UNIQUE INDEX ON un_named (id);
            ANALYZE un_named;
            CREATE TEMP TABLE un_any AS
            SELECT DISTINCT k AS id FROM (
                SELECT person_id AS k FROM athletes WHERE person_id IS NOT NULL
                UNION ALL SELECT athlete_id FROM athletes) x;
            CREATE UNIQUE INDEX ON un_any (id);
            ANALYZE un_any;
        """)
        cur.execute("SELECT (SELECT count(*) FROM un_named), (SELECT count(*) FROM un_any)")
        named, anyrow = cur.fetchone()
        print(f"[unknown] athletes: {anyrow:,} ids with a row, {named:,} with a name "
              f"({anyrow - named:,} with only blank rows)  ({time.time() - t0:.0f}s)")

        for sport, table in (("XC", "results"), ("TF", "results_tf")):
            t1 = time.time()
            cur.execute(f"""
                CREATE TEMP TABLE un_rows_{sport} AS
                SELECT r.result_id, r.meet_id, r.source, {SEASON} AS season,
                       CASE WHEN r.person_id IS NULL THEN 'no_person'
                            WHEN y.id IS NOT NULL THEN 'blank_athlete'
                            ELSE 'no_athlete' END AS cause,
                       r.person_id, r.athlete_id
                FROM   {table} r
                LEFT JOIN un_named n ON n.id = COALESCE(r.person_id, r.athlete_id)
                LEFT JOIN un_any   y ON y.id = r.person_id
                WHERE  n.id IS NULL
                  AND  NULLIF(btrim(r.athlete_name), '') IS NULL
                  {"AND COALESCE(r.is_relay, 0) = 0" if sport == "TF" else ""}
            """)
            if sport == "TF":
                cur.execute("SELECT count(*) FROM results_tf WHERE COALESCE(is_relay, 0) = 1")
                print(f"\n  (TF relay rows, a team not a person, not counted below: "
                      f"{cur.fetchone()[0]:,})")
            cur.execute(f"SELECT count(*) FROM {table}")
            total = cur.fetchone()[0]
            cur.execute(f"SELECT count(*), count(DISTINCT person_id) FROM un_rows_{sport}")
            n, people = cur.fetchone()
            print(f"\n== {sport}: {n:,} of {total:,} rows nameless "
                  f"({100.0 * n / max(total, 1):.2f}%), {people:,} people  "
                  f"({time.time() - t1:.0f}s)")
            cur.execute(f"""
                SELECT source, cause, count(*), count(DISTINCT person_id)
                FROM un_rows_{sport} GROUP BY 1, 2 ORDER BY 3 DESC""")
            print("  by source and cause:")
            for src, cause, c, p in cur.fetchall():
                print(f"    {str(src):<8} {cause:<14} {c:>11,} rows  {p:>9,} people")
            cur.execute(f"""
                SELECT season, count(*) FROM un_rows_{sport}
                GROUP BY 1 ORDER BY 1 DESC LIMIT 12""")
            print("  by season (latest 12): " + ", ".join(
                f"{s}: {c:,}" for s, c in cur.fetchall()))
            # meets: all nameless (the scrape lost the names) or partly
            cur.execute(f"""
                WITH m AS (
                    SELECT meet_id, source, count(*) AS nameless FROM un_rows_{sport}
                    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %s)
                SELECT m.meet_id, m.source, m.nameless,
                       (SELECT count(*) FROM {table} r
                        WHERE r.meet_id = m.meet_id AND r.source = m.source) AS rows
                FROM m ORDER BY m.nameless DESC""", (a.top,))
            meets = cur.fetchall()
            whole = sum(1 for _m, _s, k, t in meets if k == t)
            print(f"  top {len(meets)} meets ({whole} entirely nameless):")
            for mid, src, k, t in meets:
                print(f"    {src:<6} meet {mid:<9} {k:>6,} of {t:>6,} nameless"
                      + ("   ALL" if k == t else ""))
            # a few anet ids to look up by hand on athletic.net
            cur.execute(f"""
                SELECT DISTINCT person_id, athlete_id FROM un_rows_{sport}
                WHERE source = 'anet' AND cause <> 'no_person' LIMIT 5""")
            ids = cur.fetchall()
            if ids:
                kind = "cross-country" if sport == "XC" else "track-and-field-outdoor"
                print("  look these up on athletic.net (does the site have a name?):")
                for pid, aid in ids:
                    print(f"    https://www.athletic.net/athlete/{aid or pid}/{kind}/  "
                          f"(our person {pid})")
        conn.rollback()
    print(f"\n[unknown] done in {time.time() - t0:.0f}s (read-only; temp tables only)")


if __name__ == "__main__":
    main()
