#!/usr/bin/env python3
"""
qualifying_check.py -- racecast/qualifying.py's rules against the field that
really ran: each rule's championships of a finished season, the field its
rounds give, against who finished. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/qualifying_check.py --season 2025

★ WHY (owner, 2026-11-02: "Sure, but you're checking it"). The rules are
  typed in from the meets' manuals and the at-large picks are approximated;
  this says how close that comes, per championship and gender, before the
  page relies on it:
    found      of the finishers, in the field we built
    precise    of the field we built, finished
    teams      of the teams that ran (five or more finishers), ones we had
    missed     the teams that ran and we did not have, and the ones we had
               that did not run (the at-large guesses, mostly)
"""
import argparse
import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def score(field, finishers):
    """(found, precise, teams found, teams run, missed teams, extra teams).
    field: {pid: school}; finishers: [(pid, school)]. Pure."""
    ran = {p for p, _s in finishers}
    got = set(field)
    t_ran = {s for s, n in collections.Counter(s for _p, s in finishers if s).items() if n >= 5}
    t_had = {s for s, n in collections.Counter(field.values()).items() if s and n >= 5}
    return (len(got & ran) / max(len(ran), 1), len(got & ran) / max(len(got), 1),
            len(t_ran & t_had), len(t_ran), sorted(t_ran - t_had), sorted(t_had - t_ran))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, required=True, help="its August's year")
    a = ap.parse_args()

    import psycopg2.extras
    import last_edition as LE
    import qualifying as Q
    from database import getConn

    lo, hi = f"{a.season}-10-15", f"{a.season}-12-31"
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        # ★ THE DATE FROM THE RESULTS (owner, 2026-11-02: NXN 2025 is
        #   racecast.co/race/xc/253956 but its meets row carries no usable
        #   meet_date, so a date filter on the meta tables missed it)
        cur.execute("""SELECT 'tfrrs' AS src, m.meet_id, min(m.meet_name) AS meet_name,
                              min(r.date) AS d
                       FROM   meets_tfrrs m JOIN results r
                              ON r.source = 'tfrrs' AND r.meet_id = m.meet_id
                       WHERE  m.sport = 'XC' AND m.meet_name ~* 'ncaa|nike|nxn|naia'
                         AND  r.date ~ '^(19|20)[0-9]{2}-' AND r.date BETWEEN %s AND %s
                       GROUP  BY m.meet_id
                       UNION ALL
                       SELECT 'anet', m.meet_id, min(m.meet_name), min(r.date)
                       FROM   meets m JOIN results r
                              ON r.source = 'anet' AND r.meet_id = m.meet_id
                       WHERE  m.meet_name ~* 'ncaa|nike|nxn|naia'
                         AND  r.date ~ '^(19|20)[0-9]{2}-' AND r.date BETWEEN %s AND %s
                       GROUP  BY m.meet_id""", (lo, hi, lo, hi))
        meets = [dict(r) for r in cur.fetchall()]
        print(f"[qualifying] season {a.season}: rules {', '.join(r['key'] for r in Q.RULES)}")
        for m in sorted(meets, key=lambda m: str(m["d"])):
            rule = Q.ruleFor(m["meet_name"], m["src"])
            if rule is None:
                continue
            for race in LE.editionRaces(cur, int(m["meet_id"]), "XC", m["src"]):
                g = race.get("gender")
                if g not in ("M", "F"):
                    continue
                cur.execute("""SELECT DISTINCT person_id, school FROM results
                               WHERE meet_id = %s AND div_id = %s AND source = %s
                                 AND person_id IS NOT NULL AND time_seconds > 0""",
                            (int(m["meet_id"]), race["div_id"], m["src"]))
                fin = [(int(r["person_id"]), r["school"]) for r in cur.fetchall()]
                if len(fin) < 30:
                    continue
                try:
                    field = Q.fieldFor(cur, rule, str(m["d"])[:10], g)
                except Exception as exc:                 # noqa: BLE001
                    conn.rollback()
                    print(f"  {m['meet_name']} {g}: {type(exc).__name__}: {exc}")
                    continue
                if not field:
                    print(f"  {m['meet_name'][:50]} {g}: fewer than {Q.MIN_ROUNDS} rounds found")
                    continue
                f, p, th, tr, miss, extra = score(field, fin)
                print(f"  {rule['key']:8} {m['meet_name'][:44]:44} {g}  {len(fin):>4} ran: "
                      f"found {100 * f:.0f}%, precise {100 * p:.0f}%, teams {th}/{tr}")
                if miss or extra:
                    print(f"      missed {', '.join(miss[:8]) or '-'}"
                          f"{' ...' if len(miss) > 8 else ''}; had but not there "
                          f"{', '.join(extra[:8]) or '-'}{' ...' if len(extra) > 8 else ''}")
        conn.rollback()


if __name__ == "__main__":
    main()
