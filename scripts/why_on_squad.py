#!/usr/bin/env python3
"""
why_on_squad.py -- why is this runner in (or out of) a predicted field?
READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_on_squad.py --person 21745558 \
        --meet 268246 --div 1075516 --date 2026-10-17 --per-team 10

★ WHY (owner, 2026-10-18: Everett Mosher "is no longer running, and it
  shouldn't be by rating"). The field is built from several sources --
  this season's squad, last season's carried forward, this season's race
  entrants -- then ordered by prediction and capped. This runs the real
  field the page asks for (predict.meetField, same parameters) and says,
  for one person: their races this season and last, which school's squad
  they are on and how they got there, whether the stopped-racing rule
  benches them (their team's varsity meets since their last race), and
  where they land -- lineup or bench -- with the predicted time or the
  rating the order used.
"""
import argparse
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine", "model"):
    sys.path.insert(0, os.path.join(_ROOT, _d))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int, required=True)
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int)
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--date")
    ap.add_argument("--per-team", type=int)
    ap.add_argument("--skip-top", type=int, default=0)
    a = ap.parse_args()
    os.environ.setdefault("XCP_DB_QUIET", "1")
    import psycopg2.extras
    from database import getConn
    import predict as P
    from roster import CARRY_RACES
    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        season = P._currentSeason(cur, a.sport)
        print(f"\n  person {a.person}, {a.sport}, the boards' season {season}")
        cur.execute("""SELECT race_date::date AS day, year, school, meet_id,
                              round(speed_rating::numeric, 1) AS rating
                       FROM ranking_results
                       WHERE person_id = %s AND sport = %s AND year >= %s
                       ORDER BY race_date DESC""", (a.person, a.sport, season - 1))
        rows = cur.fetchall()
        print(f"\n  rated {a.sport} races, {season - 1}-{season}: {len(rows)}")
        for r in rows[:15]:
            print(f"    {r['day']}  {r['year']}  {r['school']:<32} meet {r['meet_id']:<9} {r['rating']}")
        # ★ AND HIS RAW RESULTS, RATED OR NOT (owner, 2026-10-18: "none of
        #   his races have ever been rated"). What each row carries that the
        #   engine needs -- a normalized time, a pool, a distance, a course
        #   -- and whether a check set it aside, so the reason shows.
        cur.execute("SELECT to_regclass('public.rating_outlier') IS NOT NULL AS o, "
                    "to_regclass('public.result_twin') IS NOT NULL AS t")
        have = cur.fetchone()
        out_sql = ("EXISTS (SELECT 1 FROM rating_outlier ro WHERE ro.sport = %(sp)s "
                   "AND ro.result_id = r.result_id)" if have["o"] else "false")
        twin_sql = ("EXISTS (SELECT 1 FROM result_twin x WHERE x.sport = %(sp)s "
                    "AND x.result_id = r.result_id)" if have["t"] else "false")
        cur.execute(f"""
            SELECT r.result_id, r.date, r.source, r.meet_id, r.div_id, r.school, r.grade,
                   r.time_seconds, r.normalized_time, r.speed_rating, r.rating_pool,
                   m.distance AS meet_distance, m.course_name,
                   {out_sql} AS outlier, {twin_sql} AS twin
            FROM   results r
            LEFT   JOIN meets m ON r.source = 'anet' AND m.meet_id = r.meet_id
                               AND m.div_id = r.div_id
            WHERE  r.person_id = %(p)s
            ORDER  BY r.date DESC LIMIT 25""", {"p": a.person, "sp": a.sport})
        raw = cur.fetchall()
        print(f"\n  his {a.sport} results rows (rated or not): {len(raw)}")
        for r in raw:
            why = ("RATED" if r["speed_rating"] else
                   "no normalized time" if r["normalized_time"] is None else
                   "no rating pool" if not r["rating_pool"] else
                   "outlier (set aside)" if r["outlier"] else
                   "twin (counted on its copy)" if r["twin"] else "unrated: ?")
            print(f"    {r['date']}  {r['source']:<5} {r['meet_id']}/{r['div_id']}  "
                  f"{(r['school'] or '')[:22]:<22} gr {r['grade'] or '-':<4} "
                  f"t {r['time_seconds']}  norm {r['normalized_time'] and round(float(r['normalized_time']), 1)}  "
                  f"rating {r['speed_rating']}  pool {r['rating_pool']}  "
                  f"dist {r['meet_distance']}  course {(r['course_name'] or '')[:24]}  -> {why}")
        # ★ AND THE GRADE VERDICTS THE ROWS ARE POOLED BY (owner, 2026-10-18:
        #   Everett Mosher, a WPI junior, shows "Grade 10/11/12" from the
        #   2025 season on, every one of those seasons unrated). grade_fix is
        #   grade_sanity's verdict per academic year; `method` names the rule.
        cur.execute("SELECT to_regclass('public.grade_fix') IS NOT NULL AS g")
        if cur.fetchone()["g"]:
            cur.execute("""SELECT season, grade, level, method, trust FROM grade_fix
                           WHERE person_id = %s ORDER BY season""", (a.person,))
            gf = cur.fetchall()
            print(f"\n  grade verdicts (grade_fix): {len(gf) or 'none'}")
            for g in gf:
                print(f"    {g['season']}  grade {g['grade'] or '-':<4} level {g['level'] or '-':<8} "
                      f"method {g['method']:<16} trust {g['trust']}")
        cur.execute("""SELECT sport, year, grade, pool, school, n_races,
                              round(mean_rating::numeric, 1) AS mean_rating
                       FROM athlete_season WHERE person_id = %s ORDER BY year, sport""",
                    (a.person,))
        print("\n  athlete_season rows (the boards'):")
        for g in cur.fetchall():
            print(f"    {g['year']}  {g['sport']:<3} grade {g['grade'] or '-':<5} pool {g['pool'] or '-':<12} "
                  f"{(g['school'] or '')[:24]:<24} races {g['n_races']}  mean {g['mean_rating']}")
        schools = sorted({r["school"] for r in rows if r["year"] == season and r["school"]})
        if schools:
            print(f"\n  schools this season: {', '.join(schools)}")
            for sch in schools:
                cur.execute("""SELECT DISTINCT meet_id, race_date::date AS day
                               FROM ranking_results WHERE school = %s AND sport = %s
                                 AND year = %s ORDER BY 2""", (sch, a.sport, season))
                meets = cur.fetchall()
                last = max(r["day"] for r in rows if r["school"] == sch and r["year"] == season)
                after = [m for m in meets if m["day"] > last]
                print(f"    {sch}: {len(meets)} meets this season; his last race {last}; "
                      f"{len(after)} team meets since ({', '.join(str(m['day']) for m in after[:8])})")
        target = None
        args = {"mode": "rerun", "meet_id": str(a.meet), "sport": a.sport}
        if a.div:
            args["div_id"] = str(a.div)
        if a.date:
            args["date"] = a.date
        target, err = __import__("app")._target(args)
        if err:
            print(f"\n  target: {err}")
            target = None
        src = None
        field = P.meetField(cur, a.meet, a.div, a.sport, when="thisyear", source=src,
                            per_team=a.per_team, pick="predicted", target=target,
                            skip_top=a.skip_top)
        print(f"\n  the field: {len(field['teams'])} teams; pick {field.get('pick')}"
              f"{'; note: ' + field['pick_note'] if field.get('pick_note') else ''}")
        found = False
        for t in field["teams"]:
            for where, lst in (("LINEUP", t["runners"]), ("bench", t["dropped"])):
                for i, r in enumerate(lst):
                    if int(r.get("person_id") or 0) != a.person:
                        continue
                    found = True
                    how = ("carried from last season" if r.get("carried") else
                           "stopped racing (%s varsity meets missed)" % r["stopped"]
                           if r.get("stopped") else
                           "skipped (top N)" if r.get("skipped") else
                           "no race this season (original runner)" if not r.get("bench") and where == "bench"
                           else "this season's squad")
                    pred = r.get("pred_seconds")
                    print(f"    {t['school']}: {where} #{i + 1}  {r.get('name')}  "
                          f"rating {r.get('rating')}  pred {pred and round(float(pred), 1)} "
                          f"({r.get('pred_basis') or '-'})  -- {how}")
                    print(f"      {t['school']} lineup:")
                    for e in t["runners"]:
                        ps = e.get("pred_seconds")
                        print(f"        {(e.get('name') or '')[:24]:<24} "
                              f"{ps and round(float(ps), 1)!s:>8}  rating {e.get('rating')!s:<6} "
                              f"{e.get('pred_basis') or ''}")
        if not found:
            print("    not in the field (no squad row for this race's schools, gender or level)")
        print(f"\n  rule: benched once their team has run {CARRY_RACES} varsity meets since "
              f"their last race (today {datetime.date.today()})")
        conn.rollback()


if __name__ == "__main__":
    main()
