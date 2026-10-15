#!/usr/bin/env python3
"""
diag_roster_diff.py -- one school's roster as the school page builds it
beside the predictions page's squad for it, and why each runner is on only
one of them. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_roster_diff.py "Arkansas"
    /srv/venv/bin/python scripts/diag_roster_diff.py "Arkansas" --gender M --level college
    /srv/venv/bin/python scripts/diag_roster_diff.py "Amherst" --state MA --sport TF
    /srv/venv/bin/python scripts/diag_roster_diff.py "Arkansas" --meet 158396 --div 659083 [--asran]

  --meet/--div: the predictions side is the page's OWN fetch for that race
  (app._squadsServed: the race's gender, levels, state and, with --asran,
  its season) -- exactly the list the card and its expand button show.

★ WHY (owner, 2026-10-15: "On the predictions page the roster on the page
  isn't the same as the roster on the school page", Arkansas). The two
  pages share roster.py's carry-forward rule and nothing else:
    school page   school.schoolRoster  -- athlete_season, the state chip
    predictions   predict._currentSquads -- athlete_season RATED rows only,
                  one gender and level, + runners raced since the last
                  rebuild (_raceEntrants), same names merged
                  (_dedupeByName), namesakes by _keepIdentity
  Each runner on one side only is printed with the rule that explains it.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))


def _gender(pool):
    p = (pool or "").split("|")[0]
    return p[-1].upper() if p[-2:-1] == "_" else None


def _level(pool):
    return (pool or "").split("|")[0].split("_")[0] or None


def _nameKey(name):
    return " ".join(str(name or "").lower().split())


def explain(school_rows, pred_rows, gender=None, level=None):
    """[(side, row, reason)] for every runner on one side only. Pure.

    side 'school' = on the school page only, 'pred' = in predictions only."""
    s_ids = {r["person_id"] for r in school_rows}
    p_ids = {r["person_id"] for r in pred_rows}
    p_names = {_nameKey(r.get("name")) for r in pred_rows}
    out = []
    for r in school_rows:
        if r["person_id"] in p_ids:
            continue
        if r.get("mean_rating") is None:
            why = "unrated: predictions keeps rated runners only"
        elif gender and _gender(r.get("pool")) not in (None, gender):
            why = f"other gender ({r.get('pool')})"
        elif level and _level(r.get("pool")) not in (None, level):
            why = f"other level ({r.get('pool')})"
        elif _nameKey(r.get("name")) in p_names:
            why = "same name as a predictions runner: merged (_dedupeByName)"
        else:
            why = "dropped by predictions' namesake/state rule (_keepIdentity) or its season"
        out.append(("school", r, why))
    for r in pred_rows:
        if r["person_id"] in s_ids:
            continue
        if r.get("carried"):
            why = "carried from last season (aged forward) -- the school page is past its window or on another year"
        else:
            why = "raced since the last rebuild (_raceEntrants) or another season than the school page"
        out.append(("pred", r, why))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("school")
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--gender", choices=("M", "F"))
    ap.add_argument("--level", help="hs, ms, college, pro")
    ap.add_argument("--state", help="the state chip, e.g. AR")
    ap.add_argument("--meet", type=int, help="the predicted race's meet id")
    ap.add_argument("--div", help="the predicted race's division id")
    ap.add_argument("--asran", action="store_true",
                    help="the page's 'as it ran' mode")
    a = ap.parse_args()
    import psycopg2.extras
    from database import getConn
    import predict
    import school as school_mod
    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        y_pred = predict._currentSeason(cur, a.sport)
        y_school = school_mod.currentSeason(cur, a.school, a.sport, state=a.state)
        s_rows = school_mod.schoolRoster(cur, a.school, y_school, a.sport, state=a.state)
        squads = predict._currentSquads(
            cur, [a.school], a.sport, y_pred, gender=a.gender,
            levels={a.level} if a.level else None,
            states={a.school: a.state} if a.state else None, isolated=True)
        p_rows = squads.get(a.school, [])
        served = None
        if a.meet:
            os.environ.setdefault("XCP_DB_QUIET", "1")
            import app as app_mod
            div_n, ev_n = app_mod.splitRaceKey(a.div or "")
            params = {"sport": a.sport, "gender": a.gender,
                      "levels": {a.level} if a.level else None,
                      "levels_given": bool(a.level), "as_ran": a.asran,
                      "meet_id": a.meet, "div_id": div_n, "event_id": ev_n,
                      "alt": None, "src": None}
            served = app_mod._squadsServed(cur, [(a.school, a.state)], params)[0]
            p_rows = [dict(r, school=a.school) for r in served.get("runners") or []]
            y_pred = served.get("season_year")
        conn.rollback()
    if served is not None:
        print(f"page fetch for meet {a.meet} div {a.div}: season {served.get('season_year')}, "
              f"gender {served.get('gender')}, {len(p_rows)} runners"
              + (f", {served.get('other_gender')} of the other gender" if served.get("other_gender") else ""))
    print(f"{a.school} {a.sport}: school page season {y_school}, {len(s_rows)} runners; "
          f"predictions season {y_pred}, {len(p_rows)} runners"
          + (f" (gender {a.gender})" if a.gender else "")
          + (f" (level {a.level})" if a.level else ""))
    if y_school != y_pred:
        print(f"  ! the two pages are on different seasons ({y_school} vs {y_pred})")
    by_pool = {}
    for r in s_rows:
        by_pool[r.get("pool")] = by_pool.get(r.get("pool"), 0) + 1
    print("  school page pools: " + ", ".join(f"{p}: {n}" for p, n in sorted(by_pool.items(), key=str)))
    gender = (served or {}).get("gender") or a.gender
    diff = explain(s_rows, p_rows, gender, a.level)
    if not diff:
        print("  same runners on both")
        return
    for side, r, why in diff:
        tag = "SCHOOL ONLY" if side == "school" else "PRED ONLY  "
        rating = r.get("mean_rating") if side == "school" else r.get("rating")
        print(f"  {tag} {r['person_id']:>10} {str(r.get('name') or '')[:26]:26} "
              f"{str(r.get('pool') or ''):14} gr {str(r.get('grade') or '-'):5} "
              f"{rating if rating is not None else '-':>6}  {why}")


if __name__ == "__main__":
    main()
