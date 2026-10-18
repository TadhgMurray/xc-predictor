#!/usr/bin/env python3
"""
collapse_race_days.py -- race_day_effect back to ONE row per course-day.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/collapse_race_days.py           # count only
    /srv/venv/bin/python scripts/collapse_race_days.py --write   # rebuild, swap

★ WHY (owner, 2026-10-18, /athlete/29751562: one race listed 16 times in a
  season table). A go-live under XCP_RACE_KEY=division wrote a row per
  DIVISION of a day, and every reader joins on (course, distance, date), so
  each race came back once per division. engine/joint_golive.writeRaceDays
  now writes one row per course-day; this collapses a table written before
  that, the same way (a day's divisions weighted by their rows), without
  waiting for the next go-live. Safe to run again: a collapsed table is
  left as it is.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))

_DDL = """
    CREATE TABLE race_day_effect_new AS
    SELECT course_name, canonical_id, distance_m, race_date,
           (sum(day_effect * n_rows) / NULLIF(sum(n_rows), 0))::real      AS day_effect,
           sum(n_rows)::integer                                            AS n_rows,
           (sum(course_effect * n_rows) FILTER (WHERE course_effect IS NOT NULL)
            / NULLIF(sum(n_rows) FILTER (WHERE course_effect IS NOT NULL), 0))::real
                                                                            AS course_effect
    FROM   race_day_effect
    GROUP  BY course_name, canonical_id, distance_m, race_date
"""


def main():
    from database import getConn, swapTable
    write = "--write" in sys.argv
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*),
                              count(DISTINCT (course_name, canonical_id, distance_m, race_date))
                       FROM race_day_effect""")
        rows, days = cur.fetchone()
        print(f"[race days] {rows:,} rows for {days:,} course-days")
        if rows == days:
            print("[race days] already one row per course-day; nothing to do")
            conn.rollback()
            return
        if not write:
            conn.rollback()
            print(f"[race days] DRY RUN: {rows - days:,} duplicate rows would go; --write")
            return
        cur.execute("DROP TABLE IF EXISTS race_day_effect_new")
        cur.execute(_DDL)
        cur.execute("ALTER TABLE race_day_effect_new ALTER COLUMN course_name SET NOT NULL, "
                    "ALTER COLUMN race_date SET NOT NULL, ALTER COLUMN day_effect SET NOT NULL, "
                    "ALTER COLUMN n_rows SET NOT NULL")
        cur.execute("CREATE INDEX ON race_day_effect_new (canonical_id, distance_m, race_date)")
        cur.execute("CREATE INDEX ON race_day_effect_new (course_name, race_date)")
        conn.commit()
        swapTable(conn, "race_day_effect", "race_day_effect_new")
        print(f"[race days] rebuilt: {days:,} rows, one per course-day")


if __name__ == "__main__":
    main()
