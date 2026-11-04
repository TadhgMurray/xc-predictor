#!/usr/bin/env python3
"""
qualifier_why.py -- why a runner is or is not in a championship's qualifier
field: every round the rule reads, run or predicted, and where they are in
it. READ ONLY; runs the same code as the nightly (build_qualified_fields),
predictions included, so it takes a few minutes.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/qualifier_why.py "Marcelo Mantecon"
    $P scripts/qualifier_why.py "Natasza Dudek" --rule nxn --date 2026-12-05

★ WHY (owner, 2026-11-04: "still no marcelo mantecon" in the NXN field --
  "he would win if in"). The field is the rule applied to its rounds; this
  prints each round with the runner's place and team in it, so a missing
  runner is either a round not found, a race not read, or a place the rule
  does not take.
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
    ap.add_argument("name", help="the runner's first and last name")
    ap.add_argument("--rule", default="nxn", help="ncaa_d1, ncaa_d2, ncaa_d3, nxn or brooks")
    ap.add_argument("--date", default=None, help="the championship's date (default: NXN's, "
                                                 "the first Saturday of December)")
    a = ap.parse_args()

    import datetime
    import psycopg2.extras
    import qualifying as Q
    import upcoming_preview as U
    from database import getConn

    rule = next((r for r in Q.RULES if r["key"] == a.rule), None)
    if rule is None:
        sys.exit(f"no rule {a.rule!r}: {', '.join(r['key'] for r in Q.RULES)}")
    day = a.date
    if not day:
        d = datetime.date(datetime.date.today().year, 12, 1)
        day = (d + datetime.timedelta(days=(5 - d.weekday()) % 7)).isoformat()
    src = rule["source"]

    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("""SELECT DISTINCT person_id, gender FROM athletes
                       WHERE lower(btrim(first_name) || ' ' || btrim(last_name)) = lower(%s)
                         AND person_id IS NOT NULL""", (a.name.strip(),))
        people = cur.fetchall()
        if not people:
            sys.exit(f"no athlete named {a.name!r}")
        ids = {int(p["person_id"]) for p in people}
        gender = next((p["gender"] for p in people if p["gender"] in ("M", "F")), None)
        print(f"{a.name}: person {', '.join(map(str, sorted(ids)))}, gender {gender or '?'}; "
              f"{rule['key']} on {day}")
        if gender not in ("M", "F"):
            sys.exit("no gender on the athlete rows: cannot pick the race")

        # where they raced: the round they should have come through
        cur.execute("""SELECT r.date, r.school, r.meet_id, r.source,
                              COALESCE(mt.meet_name, m.meet_name) AS meet
                       FROM results r
                       LEFT JOIN LATERAL (SELECT meet_name FROM meets_tfrrs x
                                          WHERE r.source = 'tfrrs' AND x.meet_id = r.meet_id
                                          LIMIT 1) mt ON TRUE
                       LEFT JOIN LATERAL (SELECT meet_name FROM meets x
                                          WHERE r.source = 'anet' AND x.meet_id = r.meet_id
                                          LIMIT 1) m ON TRUE
                       WHERE r.person_id = ANY(%s) AND r.date ~ '^(19|20)[0-9]{2}-'
                         AND r.date >= %s
                       ORDER BY r.date DESC LIMIT 12""",
                    (sorted(ids), (datetime.date.fromisoformat(day)
                                   - datetime.timedelta(days=430)).isoformat()))
        print("\ntheir races (newest first):")
        for r in cur.fetchall():
            print(f"  {str(r['date'])[:10]}  {r['school']!s:28.28} {r['meet']}  "
                  f"({r['source']} {r['meet_id']})")

        # every meet in the two windows that looks like a round, and the rule's verdict
        print("\nmeets in the round windows that look like rounds (this season, last):")
        for back in (0, 364):
            hi = (datetime.date.fromisoformat(day) - datetime.timedelta(days=back)).isoformat()
            lo = (datetime.date.fromisoformat(hi)
                  - datetime.timedelta(days=rule.get("window", 21) + 14)).isoformat()
            if src == "tfrrs":
                cur.execute("""SELECT DISTINCT meet_id, meet_name, date AS d FROM meets_tfrrs
                               WHERE sport = 'XC' AND date >= %s AND date < %s
                                 AND meet_name ~* 'region|ncaa'""", (lo, hi))
            else:
                # a meet with no date on its row (NXN 2025 had none) is dated by
                # its results
                cur.execute("""SELECT DISTINCT m.meet_id, m.meet_name,
                                      COALESCE(NULLIF(m.meet_date, ''),
                                               (SELECT min(r.date) FROM results r
                                                WHERE r.source = 'anet' AND r.meet_id = m.meet_id)) AS d
                               FROM meets m
                               WHERE m.meet_name ~* %s
                                 AND (m.meet_date IS NULL OR m.meet_date = ''
                                      OR (m.meet_date >= %s AND m.meet_date < %s))""",
                            ("brooks" if rule["key"] == "brooks" else "nxr|nike cross|federation|cif",
                             lo, hi))
            for r in sorted(cur.fetchall(), key=lambda r: str(r["d"])):
                if not r["d"] or not (lo <= str(r["d"])[:10] < hi):
                    continue
                ok = Q.isRound(rule, r["meet_name"])
                print(f"  {str(r['d'] or '(no date)')[:10]}  {'ROUND' if ok else 'not a round':11} "
                      f"{r['meet_name']}  ({r['meet_id']})")
        conn.rollback()

        seen = []
        orig = Q.roundResults

        def ran(cur_, rule_, mid, g, season=None, every=False):
            rows = orig(cur_, rule_, mid, g, season, every)
            if not every:
                seen.append(("ran", mid, None, rows))
            return rows

        def predicted(mid, g, every=False):
            rows = U.predictedRound(cur, mid, g, src, live=True, every=every)
            seen.append(("predicted (this season, posted)", mid, None, rows))
            return rows

        def past(mid, div, on):
            rows = U.predictedPast(cur, mid, div, src, on, region=True)
            seen.append((f"last season's run again on {on}", mid, div, rows))
            return rows

        Q.roundResults = ran
        info = {}
        try:
            field, n_pred = Q.fieldFromRounds(cur, rule, day, gender, predict=predicted,
                                              predict_past=past, info=info)
        finally:
            Q.roundResults = orig
        names = {}
        mids = sorted({m for _k, m, _d, _r in seen})
        if mids:
            table = "meets_tfrrs" if src == "tfrrs" else "meets"
            cur.execute(f"SELECT DISTINCT meet_id, meet_name FROM {table} WHERE meet_id = ANY(%s)",
                        (mids,))
            names = {int(r["meet_id"]): r["meet_name"] for r in cur.fetchall()}
        conn.rollback()

    print(f"rounds counted {info.get('rounds', 0)} (this season found {info.get('this_season', 0)}, "
          f"last season's {info.get('last_season', 0)}); field {len(field or {})}\n")
    for kind, mid, div, rows in seen:
        order = sorted(rows or [], key=lambda r: r[2])
        at = next(((i, r) for i, r in enumerate(order, 1) if r[0] in ids), None)
        teams = Q.teamOrder(order) if order else []
        where = "not in it"
        if at:
            i, r = at
            team = (f", team {teams.index(r[1]) + 1} of {len(teams)}" if r[1] in teams else "")
            where = f"place {i} of {len(order)} for {r[1]!r}{team}"
        print(f"  {names.get(mid, mid)!s:50.50} {kind}: {len(order)} runners; {a.name} {where}")
    inn = sorted(ids & set(field or {}))
    print(f"\n{a.name} {'IS' if inn else 'is NOT'} in the field"
          + (f" (for {field[inn[0]]!r})" if inn else ""))


if __name__ == "__main__":
    main()
