"""
build_search_helpers.py -- rebuild the three tables the search index reads.

    python racecast/build_search_helpers.py

Pipeline step 13c0, just before 13c_search_index.

★ WHY (owner, 2026-10-11: /athlete/1009167329 "doesn't show up on search
  results ... I have to find through the school"). search_index.py's athlete
  query joins three precomputed tables --

      athlete_named  (person_id, name)
      athlete_agg    (person_id, last_year, n_races)
      athlete_school (person_id, school)

  -- and they were "built once in DBeaver" (search_index.py's own note) and
  never again. Every person made since was missing from search: the tfrrs
  people link_tfrrs_rows mints from 1,000,000,000 up (college-only pages),
  the splits person_collision makes from 2,000,000,000, and every athlete
  scraped since. Their pages worked; nothing could find them.

★ ONE SCAN OF THE RESULTS. Both results tables are read once, grouped by
  person: the count, the newest year, the newest real school, and a name
  off the result rows. No sort over 180M rows: the latest school is
  max(date || '|' || school) -- dates are ISO text, so the max IS the
  latest -- which a hash aggregate answers.

★ THE NAME: the athletes table's (the profile name, most-used spelling
  across a person's profiles), else the name printed on their result rows
  (tfrrs people have no athletes row at all; rankings.nameLateral reads the
  same fallback).

Swap discipline: each table is built as <name>_new and swapped in
(database.swapTable), so search keeps working on the old tables meanwhile.
"""

import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")

from database import getConn, swapTable                        # noqa: E402

# a school string that names no school: unattached in any spelling, blank
_REAL_SCHOOL = ("school IS NOT NULL AND btrim(school) <> '' "
                "AND lower(school) NOT LIKE '%unattached%'")

_SCAN = f"""
CREATE UNLOGGED TABLE tmp_person_scan AS
SELECT person_id,
       count(*)                                         AS n_races,
       max(substr(date, 1, 4))                          AS last_year,
       max(CASE WHEN {_REAL_SCHOOL} AND date ~ '^[0-9]{{4}}-'
                THEN substr(date, 1, 10) || '|' || btrim(school) END) AS latest,
       max(NULLIF(btrim(athlete_name), ''))             AS row_name
FROM (
    SELECT person_id, date, school, athlete_name FROM results
    WHERE  person_id IS NOT NULL
    UNION ALL
    SELECT person_id, date, school, athlete_name FROM results_tf
    WHERE  person_id IS NOT NULL
) r
GROUP BY person_id
"""

_PROFILE_NAMES = """
CREATE UNLOGGED TABLE tmp_profile_name AS
SELECT DISTINCT ON (person_id) person_id, name
FROM (
    SELECT person_id,
           NULLIF(btrim(concat_ws(' ', btrim(first_name), btrim(last_name))), '') AS name,
           count(*) AS n
    FROM   athletes
    WHERE  person_id IS NOT NULL
    GROUP  BY 1, 2
) a
WHERE  name IS NOT NULL
ORDER  BY person_id, n DESC, name
"""

_TABLES = {
    "athlete_named": """
        CREATE TABLE athlete_named_new AS
        SELECT s.person_id, COALESCE(p.name, s.row_name) AS name
        FROM   tmp_person_scan s
        LEFT   JOIN tmp_profile_name p USING (person_id)
        WHERE  COALESCE(p.name, s.row_name) IS NOT NULL;
        ALTER TABLE athlete_named_new ADD PRIMARY KEY (person_id)""",
    "athlete_agg": """
        CREATE TABLE athlete_agg_new AS
        SELECT person_id,
               CASE WHEN last_year ~ '^[0-9]{4}$' THEN last_year::int END AS last_year,
               n_races::int AS n_races
        FROM   tmp_person_scan;
        ALTER TABLE athlete_agg_new ADD PRIMARY KEY (person_id)""",
    "athlete_school": """
        CREATE TABLE athlete_school_new AS
        SELECT person_id, substr(latest, 12) AS school
        FROM   tmp_person_scan
        WHERE  latest IS NOT NULL;
        ALTER TABLE athlete_school_new ADD PRIMARY KEY (person_id)""",
}


def main():
    t0 = time.time()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '1GB'")
            for t in ("tmp_person_scan", "tmp_profile_name",
                      *(f"{n}_new" for n in _TABLES)):
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            print("scanning results and results_tf, one pass ...", flush=True)
            cur.execute(_SCAN)
            cur.execute("SELECT count(*) FROM tmp_person_scan")
            print(f"  {cur.fetchone()[0]:,} people with results "
                  f"({time.time() - t0:.0f}s)", flush=True)
            cur.execute(_PROFILE_NAMES)
            for name, sql in _TABLES.items():
                cur.execute(sql)
                cur.execute(f"SELECT count(*) FROM {name}_new")
                print(f"  {name}_new: {cur.fetchone()[0]:,} rows", flush=True)
        conn.commit()
        for name in _TABLES:
            swapTable(conn, name, f"{name}_new")
            print(f"  {name}: swapped in", flush=True)
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS tmp_person_scan")
            cur.execute("DROP TABLE IF EXISTS tmp_profile_name")
        conn.commit()
    print(f"done in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
