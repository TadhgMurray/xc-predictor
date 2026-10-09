#!/usr/bin/env python3
"""
diag_team_board.py -- why a team's board number is what it is. READ ONLY.

    /srv/venv/bin/python scripts/diag_team_board.py --school Wartburg
    /srv/venv/bin/python scripts/diag_team_board.py --school Wartburg --pool college_m --year 2026

★ WHY (owner, 2026-10-09, the DIII men's XC team board: Wartburg 130.4
  top-5, "more like 136"). The board races each team's top seven SEASON
  numbers (athlete_season.mean_rating, which is each runner's 80th-
  percentile race, team_season.ratings); a runner the board leaves out, or
  a season number under the races it summarises, moves it. Prints:
    1. the team_season rows: rank, top-5 mean, the seven ratings that raced
    2. every athlete_season row filed under the school (this pool, sport,
       year): season number, decayed, best, races -- and whether the board
       could use it (MIN_RACES)
    3. runners whose rows this season name the school but whose season is
       filed elsewhere (another school string, another pool) -- a top runner
       missing from the board shows up here
    4. for the top runners, each race this season: date, rating, pool, and
       whether ranking_results kept it (a row the boards drop does not
       count toward the season number)
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("scripts", "racecast"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--school", required=True, help="part of the school name")
    ap.add_argument("--pool", default="college_m")
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--year", type=int, default=None,
                    help="athlete_season.year (default: the latest on file)")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    import psycopg2.extras
    from database import getConn
    from build_team_season import MIN_RACES
    like = "%" + a.school + "%"
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        year = a.year
        if year is None:
            cur.execute("SELECT max(year) AS y FROM athlete_season WHERE pool = %s AND sport = %s",
                        (a.pool, a.sport))
            year = cur.fetchone()["y"]
        print(f"school ~ {a.school!r}  pool {a.pool}  sport {a.sport}  year {year}  "
              f"(board MIN_RACES {MIN_RACES})\n")

        print("1. team_season")
        cur.execute("""
            SELECT span, scope, school, state, rank, points, n_athletes,
                   round(top5_mean::numeric, 1) AS top5, round(fifth_rating::numeric, 1) AS fifth,
                   round(best_rating::numeric, 1) AS best, ratings
            FROM   team_season
            WHERE  school ILIKE %s AND pool = %s AND sport = %s AND year = %s
            ORDER  BY span, scope""", (like, a.pool, a.sport, year))
        for r in cur.fetchall():
            rs = [round(x, 1) for x in (r["ratings"] or [])]
            print(f"   {r['span']:7} {r['scope']:9} {r['school']} ({r['state']}) rank {r['rank']} "
                  f"pts {r['points']} n {r['n_athletes']} top5 {r['top5']} 5th {r['fifth']} "
                  f"best {r['best']}  raced {rs}")

        print("\n2. athlete_season under the school")
        cur.execute("""
            SELECT s.person_id, s.school, s.state, s.n_races,
                   round(s.mean_rating::numeric, 1) AS season,
                   round(s.decayed_rating::numeric, 1) AS decayed,
                   round(s.best_rating::numeric, 1) AS best,
                   (SELECT concat_ws(' ', btrim(x.first_name), btrim(x.last_name))
                    FROM athletes x WHERE x.person_id = s.person_id
                    ORDER BY (x.athlete_id = s.person_id) DESC LIMIT 1) AS name
            FROM   athlete_season s
            WHERE  s.school ILIKE %s AND s.pool = %s AND s.sport = %s AND s.year = %s
            ORDER  BY s.mean_rating DESC NULLS LAST""", (like, a.pool, a.sport, year))
        filed = cur.fetchall()
        for r in filed:
            ok = "board" if (r["n_races"] or 0) >= MIN_RACES and r["season"] is not None \
                else f"OFF (races {r['n_races']})"
            print(f"   {r['person_id']:>11} {r['name'] or '?':28} season {r['season']} "
                  f"decayed {r['decayed']} best {r['best']} races {r['n_races']:>2}  "
                  f"{r['school']} ({r['state']})  {ok}")

        print("\n3. raced for the school this season, season filed elsewhere")
        cur.execute("""
            SELECT DISTINCT ON (rr.person_id) rr.person_id, rr.pool, s.school AS filed_school,
                   s.pool AS filed_pool, round(s.mean_rating::numeric, 1) AS season, s.n_races
            FROM   ranking_results rr
            LEFT   JOIN athlete_season s ON s.person_id = rr.person_id AND s.sport = rr.sport
                                        AND s.year = rr.year AND s.pool = rr.pool
            WHERE  rr.school ILIKE %s AND rr.sport = %s AND rr.year = %s
              AND  NOT (rr.person_id = ANY(%s))
            ORDER  BY rr.person_id, s.mean_rating DESC NULLS LAST""",
                    (like, a.sport, year, [r["person_id"] for r in filed] or [0]))
        other = cur.fetchall()
        for r in other:
            print(f"   {r['person_id']:>11} row pool {r['pool']}  filed as {r['filed_school']!r} "
                  f"{r['filed_pool']} season {r['season']} races {r['n_races']}")
        if not other:
            print("   none")

        print(f"\n4. the top {a.top}: every race this season (results vs ranking_results)")
        for r in filed[:a.top]:
            cur.execute("""
                SELECT left(x.date, 10) AS d, x.source, x.meet_id, x.div_id,
                       round(x.speed_rating::numeric, 1) AS rating, x.rating_pool,
                       x.school,
                       EXISTS (SELECT 1 FROM ranking_results k
                               WHERE k.result_id = x.result_id AND k.sport = %s) AS kept
                FROM   results x
                WHERE  x.person_id = %s
                  AND  x.date >= (SELECT min(first_race)::text FROM athlete_season
                                  WHERE person_id = %s AND sport = %s AND year = %s)
                ORDER  BY x.date""", (a.sport, r["person_id"], r["person_id"], a.sport, year))
            races = cur.fetchall()
            print(f"   {r['name']} ({r['person_id']}): season {r['season']}")
            for x in races:
                print(f"      {x['d']} {x['source']:5} meet {x['meet_id']} div {x['div_id']} "
                      f"{x['rating']} {x['rating_pool']} {x['school']!r} "
                      f"{'kept' if x['kept'] else 'NOT ON BOARDS'}")
        conn.rollback()


if __name__ == "__main__":
    main()
