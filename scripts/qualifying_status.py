#!/usr/bin/env python3
"""
qualifying_status.py -- which coming meets the site fields from QUALIFIERS
(racecast/qualifying.py), and how: per race, the rule or learned route, how
many runners, and how many of its rounds have run or are predicted. READ
ONLY; the same code the meet previews run (upcoming_preview.qualifiedField).

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/qualifying_status.py                  # the next 30 days
    $P scripts/qualifying_status.py --days 60 --meet 999999

★ WHY (owner, 2026-11-03: "is the qualification thing in the site? how can
  I tell"). The previews do not say where a field came from; this does:
    rule      NCAA DI/DII/DIII or NXN, by its published rules
    route     a learned route (model/data/qualifying_routes.json)
    field     runners listed from qualifiers, and the teams among them
    rounds    run / predicted (our forecast of a round not yet run)
    -         no rule or route: the last edition's teams, as before
"""
import argparse
import collections
import datetime
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
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--meet", type=int, action="append", help="only this meet id")
    a = ap.parse_args()

    import psycopg2.extras
    import last_edition as LE
    import qualifying as Q
    import upcoming_preview as U
    from database import getConn

    today = datetime.date.today()
    hi = (today + datetime.timedelta(days=a.days)).isoformat()
    routes = Q._loadRoutes()
    print(f"routes file: {Q.ROUTES_PATH} ({sum(len(v) for v in routes.values())} routes); "
          f"XCP_FIELD_RULES={os.environ.get('XCP_FIELD_RULES', '1')} "
          f"XCP_QUALIFY_FORECAST={os.environ.get('XCP_QUALIFY_FORECAST', '1')}")
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        cur.execute("""SELECT 'tfrrs' AS src, meet_id, meet_name, date AS d FROM meets_tfrrs
                       WHERE sport = 'XC' AND date ~ '^(19|20)[0-9]{2}-' AND date >= %s AND date <= %s
                       UNION
                       SELECT DISTINCT 'anet', meet_id, meet_name, meet_date FROM meets
                       WHERE meet_date ~ '^(19|20)[0-9]{2}-' AND meet_date >= %s AND meet_date <= %s""",
                    (today.isoformat(), hi, today.isoformat(), hi))
        meets = [dict(r) for r in cur.fetchall()]
        shown = 0
        for m in sorted(meets, key=lambda m: str(m["d"])):
            if a.meet and int(m["meet_id"]) not in a.meet:
                continue
            rule = Q.ruleFor(m["meet_name"], m["src"])
            has_route = (m["src"], LE.editionName(m["meet_name"])) in routes
            if rule is None and not has_route and not a.meet:
                continue
            plan = U.meetPlan(cur, int(m["meet_id"]), m["src"])
            conn.rollback()
            print(f"\n{str(m['d'])[:10]}  {m['meet_name']}  ({m['src']} {m['meet_id']}): "
                  + (f"rule {rule['key']}" if rule else "learned routes" if has_route else "none"))
            if not plan:
                print("    no meet plan (no races posted, or no last edition)")
                continue
            for race in plan.get("races") or []:
                route = None if rule else Q.learnedRoute(m["meet_name"], m["src"],
                                                         race.get("label"), race.get("gender"))
                how = f"rule {rule['key']}" if rule else ("route" if route else "-")
                field, n_pred, n_round = None, 0, 0
                try:
                    if rule:
                        field, n_pred = Q.fieldFromRounds(
                            cur, rule, str(plan["date"])[:10], race.get("gender"),
                            predict=lambda mid, g: U.predictedRound(cur, mid, g, rule["source"]))
                        n_round = len(Q._roundMeets(cur, rule, str(plan["date"])[:10],
                                                    rule.get("window", 21)))
                    elif route:
                        field, n_pred = Q.learnedField(
                            cur, route, str(plan["date"])[:10],
                            predict_div=lambda mid, div: U.predictedDiv(cur, mid, div, m["src"]))
                        n_round = len(route.get("rounds") or [])
                except Exception as exc:                 # noqa: BLE001
                    conn.rollback()
                    print(f"    {race['label'][:28]:28} {race.get('gender') or '?'}  {how}: "
                          f"{type(exc).__name__}: {exc}")
                    continue
                conn.rollback()
                if field:
                    teams = sum(1 for _s, n in collections.Counter(field.values()).items() if n >= 5)
                    print(f"    {race['label'][:28]:28} {race.get('gender') or '?'}  {how}: "
                          f"{len(field)} runners, {teams} teams, from {n_round} rounds "
                          f"({n_pred} predicted)")
                else:
                    print(f"    {race['label'][:28]:28} {race.get('gender') or '?'}  {how}: "
                          f"not enough rounds yet -- last edition's teams")
            shown += 1
        print(f"\n{shown} meet(s) in the next {a.days} days field from qualifiers or would")
        conn.rollback()


if __name__ == "__main__":
    main()
