# Project: xc-predictor / racecast
# File:    build_comps.py
# Purpose: "Runners like you" (owner, 2026-10-05): for every past high-school
#          season, how that runner turned out -- next season, senior season,
#          college -- so a page can ask "the athletes who were where you are,
#          at your grade, going the way you are going: what happened to them?"
#          Pipeline step 10f3, after 10f (college_recruit). Writes
#          season_comps and season_comps_meta; comps.py reads them.
#
#     python racecast/build_comps.py
#     python racecast/build_comps.py --dry-run
#
# ★ ONE ROW PER HIGH-SCHOOL SEASON, ITS FUTURE ATTACHED. The same person and
#   sport a year before (the trajectory), a year after, at grade 12, and
#   their first college season (college_recruit). A page then reads one
#   index range -- sport, pool, grade, rating -- instead of joining a
#   person's whole career per comp per view.
#
# ★ "LIKE YOU" IS YOUR OWN UNCERTAINTY, NOT A CHOSEN BAND. A season rating
#   is the mean of n races that scatter by sigma, so it is known to about
#   sigma/sqrt(n). Two runners whose ratings differ by less than that are
#   the same as far as the data can tell. sigma is measured here, per sport
#   and pool, as the pooled within-season sd of race ratings (the race-to-
#   race spread About quotes), and stored in season_comps_meta.
#
# ★ THE MATCH USES WHAT THE MODEL READS. Rating, grade and the trajectory
#   (last season to this) are the model's own leading inputs for a
#   high-schooler's next season; the projection it makes from them
#   (recruit_projection) is shown beside the comps' outcomes on the page.
#   The comps are measured, the model is fitted: where they disagree is a
#   question for the model.
#
# ! THE BOARDS' OWN FLOOR. A season below season_floor's race minimum is not
#   a season the site ranks, so it is neither a comp nor a subject here.
#
# Swap discipline as build_recruiting: shadow tables, indexed, then
# dbfast.swapTable under a bounded lock; the live table is never empty.

import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

from database import getConn                            # noqa: E402
from dbfast import swapTable                            # noqa: E402
from season_floor import DEFAULT_FLOOR                  # noqa: E402
from recruiting import gradeNumSql                      # noqa: E402

HS_POOLS = ("hs_m", "hs_f")

_DDL = """
DROP TABLE IF EXISTS season_comps_new;
DROP TABLE IF EXISTS season_comps_meta_new;
CREATE TABLE season_comps_meta_new (
    sport      text NOT NULL,
    pool       text NOT NULL,
    sigma      real NOT NULL,       -- race-to-race sd of a rating within a season
    n_seasons  integer NOT NULL,    -- seasons the sd was pooled over
    newest     integer NOT NULL,    -- the newest stored season year in the table
    PRIMARY KEY (sport, pool)
);
"""

# ★ THE SEASONS, ONCE, AS A TEMP TABLE: every join below reads it three times
#   (prev, next, senior), and athlete_season is ~13M rows of every pool.
_SEASONS = f"""
CREATE TEMP TABLE cs_seasons AS
SELECT s.person_id, s.sport, s.pool, s.year, {gradeNumSql('s')} AS g,
       s.mean_rating::real AS rating, s.n_races, s.school, s.state
FROM   athlete_season s
WHERE  s.pool IN %(pools)s
  AND  s.mean_rating IS NOT NULL
  AND  s.n_races >= %(min_races)s
"""

_BUILD = """
CREATE TABLE season_comps_new AS
SELECT s.person_id, s.sport, s.pool, s.year, s.g AS grade, s.rating, s.n_races,
       s.school, s.state,
       p.rating  AS prev_rating, p.n_races AS prev_n,
       nx.rating AS next_rating,
       sr.rating AS senior_rating,
       cr.school AS college, cr.state AS college_state, cr.division AS college_division,
       cr.first_rating AS college_rating, cr.first_year AS college_year
FROM   cs_seasons s
LEFT JOIN cs_seasons p  ON p.person_id = s.person_id AND p.sport = s.sport
                       AND p.year = s.year - 1 AND p.g = s.g - 1
LEFT JOIN cs_seasons nx ON nx.person_id = s.person_id AND nx.sport = s.sport
                       AND nx.year = s.year + 1 AND nx.g = s.g + 1
LEFT JOIN cs_seasons sr ON sr.person_id = s.person_id AND sr.sport = s.sport
                       AND sr.g = 12 AND sr.year = s.year + (12 - s.g)
LEFT JOIN college_recruit cr ON cr.person_id = s.person_id AND cr.sport = s.sport
WHERE  s.g BETWEEN 9 AND 12
"""

# pooled within-season sd: sum of squared deviations over sum of (n - 1),
# over every HS season with two or more rated races
_SIGMA = """
SELECT sport, pool, sqrt(sum(ss) / nullif(sum(n - 1), 0)) AS sigma, count(*) AS n_seasons
FROM  (SELECT sport, pool, count(*) AS n,
              (count(*) - 1) * var_samp(speed_rating) AS ss
       FROM   ranking_results
       WHERE  pool IN %(pools)s AND speed_rating IS NOT NULL
       GROUP  BY person_id, sport, pool, year
       HAVING count(*) >= 2) g
GROUP BY sport, pool
"""


def main():
    ap = argparse.ArgumentParser(description="Build season_comps (Runners like you).")
    ap.add_argument("--dry-run", action="store_true", help="counts only, write nothing")
    a = ap.parse_args()
    t0 = time.time()
    params = {"pools": HS_POOLS, "min_races": DEFAULT_FLOOR}
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SELECT to_regclass('college_recruit')")
            have_recruit = cur.fetchone()[0] is not None
            if not have_recruit:
                print("  college_recruit missing -- the college outcome will be blank (run 10f).")
            cur.execute(_SIGMA, params)
            sigmas = cur.fetchall()
            for sp, pool, sg, n in sigmas:
                print(f"  sigma {sp}/{pool}: {sg:.2f} rating points over {n:,} seasons")
            cur.execute(_SEASONS, params)
            cur.execute("CREATE INDEX ON cs_seasons (person_id, sport, year)")
            cur.execute("ANALYZE cs_seasons")
            cur.execute("SELECT count(*), max(year) FROM cs_seasons")
            n_seasons, newest = cur.fetchone()
            print(f"  high-school seasons on the floor ({DEFAULT_FLOOR}+ races): "
                  f"{n_seasons:,}, newest {newest}  [{time.time() - t0:.0f}s]")
            if a.dry_run or not n_seasons or not sigmas:
                if not n_seasons or not sigmas:
                    print("  NOTHING TO WRITE -- season_comps left as it was.")
                conn.rollback()
                return
            cur.execute(_DDL)
            build = _BUILD if have_recruit else _BUILD.replace(
                "LEFT JOIN college_recruit cr ON cr.person_id = s.person_id AND cr.sport = s.sport",
                "LEFT JOIN (SELECT NULL::bigint AS person_id, NULL::text AS sport, NULL::text AS school,"
                " NULL::text AS state, NULL::text AS division, NULL::real AS first_rating,"
                " NULL::int AS first_year) cr ON FALSE")
            cur.execute(build)
            cur.execute("CREATE INDEX idx_season_comps_new_match "
                        "ON season_comps_new (sport, pool, grade, rating)")
            cur.execute("CREATE INDEX idx_season_comps_new_person "
                        "ON season_comps_new (person_id)")
            for sp, pool, sg, n in sigmas:
                cur.execute("INSERT INTO season_comps_meta_new VALUES (%s, %s, %s, %s, %s)",
                            (sp, pool, sg, n, newest))
            cur.execute("""SELECT count(*), count(next_rating), count(senior_rating),
                                  count(college) FROM season_comps_new""")
            n, n_next, n_senior, n_college = cur.fetchone()
            conn.commit()
        swapTable(conn, "season_comps", renames=[
            ("idx_season_comps_new_match", "idx_season_comps_match"),
            ("idx_season_comps_new_person", "idx_season_comps_person")])
        swapTable(conn, "season_comps_meta", renames=[
            ("season_comps_meta_new_pkey", "season_comps_meta_pkey")])
    print(f"  season_comps: {n:,} seasons -- {n_next:,} with a next season, "
          f"{n_senior:,} with a senior season, {n_college:,} with a college season "
          f"[{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
