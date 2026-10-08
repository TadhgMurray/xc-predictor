#!/usr/bin/env python3
"""
diag_predict_gender.py -- why a runner of the other gender is in a predicted
race's squads. READ ONLY.

    /srv/venv/bin/python scripts/diag_predict_gender.py --meet 28662 --div 1 --source tfrrs
    /srv/venv/bin/python scripts/diag_predict_gender.py --meet 28662 --div 1 --source tfrrs --all

★ WHY (owner, 2026-10-08): predicting last year's NCAA women's race, "it
  seems to be only certain guys that get through, and it's consistently the
  same guys if I change the predicted race". The same men in every women's
  race means their gender evidence, not the race's, is wrong. This runs the
  predictor's own meetField ("run it this year") and prints, for every
  runner it lists:
    - the race's gender (predict._raceGender: the title, else the runners);
    - athlete_season's pool this season (the squad filter's first source);
    - person_gender's verdict (gender, n_m, n_f, split);
    - the scraped profiles' genders;
    - this season's rows: date, feed, division label, rating_pool, rating.
  Only runners whose evidence names the other gender are printed, unless
  --all. The last section counts them by where the wrong letter comes from.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--source", choices=("anet", "tfrrs"))
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    import psycopg2.extras
    import predict as P
    from database import getConn

    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '300s'")
        label = P._divisionLabel(cur, a.meet, a.div, a.sport, source=a.source)
        originals, _ = P.thisYearOriginals(cur, a.meet, a.div, a.sport, source=a.source)
        gender = P._raceGender(cur, a.meet, a.div, a.sport, a.source,
                               [r["person_id"] for r in originals])
        year = P._currentSeason(cur, a.sport)
        print(f"race: meet {a.meet} div {a.div} ({a.source}) {label!r}  "
              f"gender {gender}  season {year}  {len(originals)} original runners")
        field = P.meetField(cur, a.meet, a.div, a.sport, when="thisyear", source=a.source)
        listed = []
        for t in field.get("teams", []):
            for key in ("runners", "dropped"):
                for e in t.get(key) or []:
                    if e.get("person_id") is not None:
                        listed.append((t["school"], key, e))
        ids = sorted({e["person_id"] for _, _, e in listed})
        print(f"meetField lists {len(listed)} runners ({len(ids)} people)\n")
        if not ids:
            conn.rollback()
            return

        cur.execute("SELECT to_regclass('person_gender') IS NOT NULL AS ok")
        has_pg = cur.fetchone()["ok"]
        pg = {}
        if has_pg:
            cur.execute("""SELECT person_id, gender, n_m, n_f, split FROM person_gender
                           WHERE person_id = ANY(%s)""", (ids,))
            pg = {r["person_id"]: r for r in cur.fetchall()}
        cur.execute("""SELECT person_id, string_agg(DISTINCT COALESCE(gender, '?'), '') g
                       FROM athletes WHERE person_id = ANY(%s) GROUP BY 1""", (ids,))
        prof = {r["person_id"]: r["g"] for r in cur.fetchall()}
        cur.execute("""SELECT person_id, pool FROM athlete_season
                       WHERE person_id = ANY(%s) AND year = %s AND sport = %s""",
                    (ids, year, a.sport))
        season = {}
        for r in cur.fetchall():
            season.setdefault(r["person_id"], []).append(r["pool"])

        other = {"M": "F", "F": "M"}.get(gender)
        why = {}
        for school, key, e in listed:
            pid = e["person_id"]
            pools = season.get(pid) or []
            letters = {p.split("|")[0][-1].upper() for p in pools
                       if p and p.split("|")[0][-2:] in ("_m", "_f")}
            v = pg.get(pid) or {}
            bad = []
            if other and other in letters:
                bad.append("season pool")
            if other and v.get("gender") == other:
                bad.append("person_gender")
            if other and other in (prof.get(pid) or ""):
                bad.append("profile")
            if not bad and not a.all:
                continue
            for b in bad or ["(none)"]:
                why[b] = why.get(b, 0) + 1
            print(f"{school} [{key}] {e.get('name')} person {pid}")
            print(f"    season pools {pools}  person_gender {v.get('gender')} "
                  f"(m {v.get('n_m')}, f {v.get('n_f')}, split {v.get('split')})  "
                  f"profiles {prof.get(pid)!r}  -> {', '.join(bad) or 'ok'}")
            cur.execute("""
                SELECT r.date, r.source, r.meet_id, r.div_id, r.rating_pool,
                       round(r.speed_rating::numeric, 1) AS rating,
                       COALESCE(m.division,
                                mt.division_distances -> r.div_id::text ->> 'div_name') AS label
                FROM   results r
                LEFT   JOIN meets m ON m.div_id = r.div_id AND r.source = 'anet'
                LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND r.source = 'tfrrs'
                                          AND mt.sport = 'XC'
                WHERE  r.person_id = %s ORDER BY r.date DESC LIMIT 8""", (pid,))
            for r in cur.fetchall():
                print(f"      {r['date']} {r['source']:5} meet {r['meet_id']} div {r['div_id']} "
                      f"{r['label']!r} pool={r['rating_pool']} rating={r['rating']}")
        print(f"\nother-gender evidence by source: {why or 'none'}")
        conn.rollback()


if __name__ == "__main__":
    main()
