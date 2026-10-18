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
                          f"rating {r.get('rating')}  pred {pred and round(float(pred), 1)}  -- {how}")
                    order = [(e.get("name"), e.get("pred_seconds") and round(float(e["pred_seconds"]), 1),
                              e.get("rating")) for e in t["runners"]]
                    print(f"      {t['school']} lineup (name, predicted s, rating): {order}")
        if not found:
            print("    not in the field (no squad row for this race's schools, gender or level)")
        print(f"\n  rule: benched once their team has run {CARRY_RACES} varsity meets since "
              f"their last race (today {datetime.date.today()})")
        conn.rollback()


if __name__ == "__main__":
    main()
