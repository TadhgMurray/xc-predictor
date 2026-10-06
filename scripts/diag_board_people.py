#!/usr/bin/env python3
"""
diag_board_people.py -- why is this person on a high school board? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_board_people.py --board TF hs_m 2024 --top 40
    /srv/venv/bin/python scripts/diag_board_people.py --person 2000000023 2000000845
    /srv/venv/bin/python scripts/diag_board_people.py --name "Clive Terrelonge" "Leo Young"

(owner, 2026-10-06: the national and CA track boards carry "Unknown" pros
of ZAP Endurance / Railroad Athletics / Tracksmith graded 12, "Jamaica (CA)"
with no grade, and club names -- Newbury Park Athletic Club, Brentwood Track
Club -- as the school.) The pooling rules disagree with the board in ways
that only the rows can settle: an earlier rule keeps a HIGH-SCHOOL grade on a
pro team ("a sponsor's youth squad and its elite group can wear one name"),
and a pro's "12th year" reads as grade 12. For each person this prints:

  1. every athlete_season row (sport, year, pool, school, state, grade, races,
     rating) -- the whole career, so "college before high school" shows;
  2. the season's result rows grouped by (source, school, team_id, grade):
     how many, the meets, and what team_pool calls the team;
  3. pro_athlete_season seasons, and person_split / person_link_log origins
     for a split id (>= 2e9).

--board picks the people from the top of an ability board (athlete_season,
mean_rating, >= 3 races) so the run covers exactly what the page shows.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402


def _has(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


def boardPeople(cur, sport, pool, year, top, state=None):
    cur.execute("""
        SELECT person_id FROM athlete_season
        WHERE  sport = %s AND pool = %s AND year = %s AND n_races >= 3
          AND  mean_rating IS NOT NULL
          AND  (%s::text IS NULL OR state = %s)
        ORDER  BY mean_rating DESC LIMIT %s""",
                (sport, pool, year, state, state, top))
    return [r[0] for r in cur.fetchall()]


def byName(cur, names):
    out = []
    for n in names:
        cur.execute("""SELECT DISTINCT athlete_id FROM athletes
                       WHERE lower(concat_ws(' ', first_name, last_name)) = lower(%s)
                       LIMIT 5""", (n,))
        out += [r[0] for r in cur.fetchall()]
    return out


def person(cur, pid, tables):
    cur.execute("""SELECT NULLIF(TRIM(concat_ws(' ', first_name, last_name)), '')
                   FROM athletes WHERE athlete_id = %s LIMIT 1""", (pid,))
    r = cur.fetchone()
    name = r[0] if r else None
    if not name:
        for t in ("results", "results_tf"):
            cur.execute(f"SELECT athlete_name FROM {t} WHERE person_id = %s "
                        f"AND athlete_name IS NOT NULL LIMIT 1", (pid,))
            r = cur.fetchone()
            if r:
                name = f"{r[0]} (from result rows)"
                break
    print(f"\n==== person {pid}: {name or '(no name anywhere)'}")
    cur.execute("""SELECT sport, year, pool, school, state, grade, n_races,
                          round(mean_rating::numeric, 1)
                   FROM athlete_season WHERE person_id = %s ORDER BY year, sport""", (pid,))
    print("  seasons:")
    for row in cur.fetchall():
        print("    {} {} {:<10} {:<34} {:<3} grade {:<5} {:>3} races  {}".format(
            *[("-" if v is None else v) for v in row]))
    for t, sport in (("results", "XC"), ("results_tf", "TF")):
        team = "r.team_id" if tables[t]["team_id"] else "NULL::bigint"
        tp = (f"LEFT JOIN team_pool tp ON tp.team_id = {team}"
              if tables["team_pool"] and tables[t]["team_id"] else "")
        tpk = "tp.kind" if tp else "NULL::text"
        cur.execute(f"""
            SELECT substr(r.date, 1, 4) AS yr, r.source, r.school, {team} AS team,
                   r.grade, {tpk} AS kind, count(*),
                   string_agg(DISTINCT m.meet_name, ' | ') AS meets
            FROM   {t} r
            LEFT JOIN (SELECT DISTINCT meet_id, meet_name FROM
                       {'meets' if sport == 'XC' else 'meets_tf'}) m ON m.meet_id = r.meet_id
            {tp}
            WHERE  r.person_id = %s
            GROUP  BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 3""", (pid,))
        rows = cur.fetchall()
        if rows:
            print(f"  {sport} rows (year, feed, school, team_id, grade, team_pool, n, meets):")
            for yr, src, sch, tid, gr, kind, n, meets in rows:
                print(f"    {yr} {src:<5} {str(sch)[:32]:<32} team {tid} grade {gr} "
                      f"[{kind or '-'}] x{n}  {(meets or '')[:90]}")
    if tables["pro_athlete_season"]:
        cur.execute("SELECT season, pro_races FROM pro_athlete_season WHERE person_id = %s "
                    "ORDER BY season", (pid,))
        pro = cur.fetchall()
        print("  pro_athlete_season: " + (", ".join(f"{s} ({n})" for s, n in pro) or "none"))
    if pid >= 2_000_000_000:
        if tables["person_split"]:
            cur.execute("SELECT old_person_id, season, class_year, school FROM person_split "
                        "WHERE new_person_id = %s", (pid,))
            for old, season, cy, sch in cur.fetchall():
                print(f"  split from {old} (season {season}, class {cy}, {sch})")
        if tables["person_link_log"]:
            cur.execute("SELECT rule, from_person, count(*) FROM person_link_log "
                        "WHERE to_person = %s GROUP BY 1, 2", (pid,))
            for rule, frm, n in cur.fetchall():
                print(f"  person_link_log: {n} rows moved from {frm} by '{rule}'")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", nargs=3, metavar=("SPORT", "POOL", "YEAR"))
    ap.add_argument("--state")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--person", nargs="*", type=int, default=[])
    ap.add_argument("--name", nargs="*", default=[])
    a = ap.parse_args()
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        tables = {t: _has(cur, t) for t in ("team_pool", "pro_athlete_season",
                                            "person_split", "person_link_log")}
        for t in ("results", "results_tf"):
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_name = %s AND column_name = 'team_id'""", (t,))
            tables[t] = {"team_id": cur.fetchone() is not None}
        pids = list(a.person) + byName(cur, a.name)
        if a.board:
            sport, pool, year = a.board
            pids += boardPeople(cur, sport.upper(), pool, int(year), a.top, a.state)
        if not pids:
            ap.error("give --board, --person or --name")
        seen = set()
        for pid in pids:
            if pid in seen:
                continue
            seen.add(pid)
            person(cur, int(pid), tables)
        conn.rollback()


if __name__ == "__main__":
    main()
