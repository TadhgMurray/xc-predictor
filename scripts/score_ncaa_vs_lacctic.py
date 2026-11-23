#!/usr/bin/env python3
"""
score_ncaa_vs_lacctic.py -- Racecast on LACCTiC's "Predicting the 2026 NCAA
Championships" table: the same two measures, the same four races.
READ-ONLY (our database; nothing is fetched).

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/score_ncaa_vs_lacctic.py
    ... --day 2026-11-21                 # the championships' date (default)

★ WHY (owner, 2026-11-23, pasting LACCTiC's write-up: LACCTiC 0.857,
  XCRI 0.829, KPACE 0.818 on runners; LACCTiC 0.882 on teams). Their
  measure: how closely a model's order from before the race matched the
  finish order, 1 = perfect -- read here as Spearman's rho per race -- and
  the mean number of places a runner was off. Each model is scored on its
  last list before the race.

Three of our lists, side by side:

  RATING   each finisher's season rating alone (athlete_season), best first:
           what the ratings know, before any course, distance or race model.
  PAGE     (frozen) what the predictions page showed the night before (meet_forecast,
           step 13h; never rewritten on or after race day). Its runners are
           the ones WE guessed would start -- with the site offline and the
           nightly stopped it may be old, or missing; made_on says which.
  MODEL    the actual finishers, predicted with history cut at race day
           (predict._servedTimes, mode rerun_exact), the way LACCTiC's last
           list knew the field. ! If the race's own results are already
           rated (results.speed_rating), the ratings have seen the race and
           this arm is flattered; the script says so.

Runners: Spearman of our predicted times against the finish times, and the
mean |our place - their place|, both re-ranked among the runners scored.
Teams: our team order (each school's predicted top five, scored among the
teams with five) against the real one, scored the same way, so the spelling
and the scoring rule agree on both sides.

! NOT ON LACCTiC's EXACT RUNNERS: they score the 1,088 of 1,101 finishers
  all three of theirs rate; we score every finisher we have a prediction
  for. Close, not identical -- the counts are printed.
"""
import argparse
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# their published numbers (LACCTiC's write-up), for the table
THEIRS_RUNNERS = {"DI men": (0.860, 0.844, 0.841), "DI women": (0.909, 0.895, 0.901),
                  "DIII men": (0.807, 0.764, 0.759), "DIII women": (0.852, 0.815, 0.769)}
THEIRS_RUNNER_HEAD = ("LACCTiC", "XCRI", "KPACE")
THEIRS_PLACES = {"LACCTiC": 30.3, "XCRI": 32.9, "KPACE": 34.5}
THEIRS_TEAMS = {"DI men": (0.880, 0.864, 0.758, 0.741, None),
                "DI women": (0.927, 0.941, 0.955, 0.943, None),
                "DIII men": (0.909, 0.865, 0.875, 0.889, 0.855),
                "DIII women": (0.813, 0.786, 0.750, 0.806, 0.807)}
THEIRS_TEAM_HEAD = ("LACCTiC", "Coaches", "XCRI", "KPACE", "D3GD")

_DIV = {"DI": r"division i(?![iv])|\bd-?i\b", "DIII": r"division iii|\bd-?iii\b"}


def spearman(a, b):
    from backtest_predictions import _spearman
    return _spearman(list(a), list(b))


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0] * len(xs)
    for r, i in enumerate(order, 1):
        out[i] = r
    return out


def runnerScore(pred, actual):
    """pred, actual: {person_id: seconds}. (rho, mean place error, n) on the
    runners in both, places re-ranked among them. Pure."""
    ids = [p for p in actual if p in pred and pred[p]]
    if len(ids) < 10:
        return None, None, len(ids)
    pr = ranks([float(pred[p]) for p in ids])
    ar = ranks([float(actual[p]) for p in ids])
    return (spearman([float(pred[p]) for p in ids], [float(actual[p]) for p in ids]),
            statistics.fmean(abs(x - y) for x, y in zip(pr, ar)), len(ids))


def teamScore(pred, actual, school):
    """Team order from predicted times against the order from finish times,
    the same scoring both ways (qualifying.teamOrder: top five of the teams
    with five). (rho, mean place error, n teams). Pure but for isTeam."""
    import qualifying as Q
    ids = [p for p in actual if p in pred and pred[p]]
    po = Q.teamOrder([(p, school[p], float(pred[p]), None) for p in ids])
    ao = Q.teamOrder([(p, school[p], float(actual[p]), None) for p in ids])
    both = [t for t in ao if t in po]
    if len(both) < 3:
        return None, None, len(both)
    pi = {t: i for i, t in enumerate(t for t in po if t in both)}
    ai = {t: i for i, t in enumerate(both)}
    return (spearman([pi[t] for t in both], [ai[t] for t in both]),
            statistics.fmean(abs(pi[t] - ai[t]) for t in both), len(both))


def main(argv=None):
    import re
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", default="2026-11-21", help="the championships' date (ISO)")
    ap.add_argument("--no-entrants", action="store_true", help="skip the entrants arm (slow part)")
    a = ap.parse_args(argv)

    import psycopg2.extras
    from database import getConn
    import meet_forecast as MF
    import last_edition as LE
    from meet_recap import actualByDiv
    from score_frozen_forecasts import rebuildRace

    out = {}
    import qualifying as Q
    season = int(Q._seasonOf(a.day))
    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET statement_timeout = '900s'")
        cur.execute("""SELECT meet_id, meet_name, date FROM meets_tfrrs
                       WHERE sport = 'XC' AND date = %s
                         AND meet_name ~* 'ncaa' AND meet_name ~* 'champ'
                         AND meet_name !~* 'region'""", (a.day,))
        meets = cur.fetchall()
        if not meets:
            print(f"no NCAA championship meet on {a.day} in meets_tfrrs -- are this "
                  f"week's college results scraped yet?")
            return 1
        for m in meets:
            name = (m["meet_name"] or "").lower()
            div = next((d for d, rx in (("DIII", _DIV["DIII"]), ("DI", _DIV["DI"]))
                        if re.search(rx, name)), None)
            if div is None:
                continue
            mid = int(m["meet_id"])
            actual_by = actualByDiv(cur, mid, "tfrrs")
            races = {int(r["div_id"]): r for r in LE.editionRaces(cur, mid, "XC", "tfrrs")}
            cur.execute(f"""SELECT source, meet_id, div_id, meet_date, made_on, state,
                                   runners, teams, model_basis, model_version
                            FROM {MF.TABLE} WHERE source = 'tfrrs' AND meet_id = %s""", (mid,))
            frozen = {int(r["div_id"]): MF._rowOut(r) for r in cur.fetchall()}
            for d, fin in actual_by.items():
                if len(fin) < 50:
                    continue
                g = (races.get(d) or {}).get("gender")
                if g not in ("M", "F"):
                    lab = ((races.get(d) or {}).get("label") or "").lower()
                    g = "F" if re.search(r"women|girl", lab) else "M" if re.search(r"\bmen\b|boy", lab) else None
                key = f"{div} {'men' if g == 'M' else 'women' if g == 'F' else '?'}"
                actual = {}
                school = {}
                for f in fin:
                    if f.get("person_id") is not None and f["person_id"] not in actual:
                        actual[f["person_id"]] = float(f["time_seconds"])
                        school[f["person_id"]] = f.get("school")
                row = {"meet": m["meet_name"], "finishers": len(actual)}
                fc = frozen.get(d)
                if fc:
                    pred = {r["person_id"]: r.get("seconds") for r in fc.get("runners") or []
                            if r.get("person_id") is not None}
                    row["frozen_made"] = str(fc.get("made_on"))[:10]
                    row["frozen"] = runnerScore(pred, actual)
                    row["frozen_teams"] = teamScore(pred, actual, school)
                # ★ RATINGS ALONE: each finisher's season rating, best first,
                #   as if it were a time -- what the ratings know, without the
                #   course, the distance or the race model
                cur.execute("""
                    SELECT DISTINCT ON (person_id) person_id, mean_rating
                    FROM   athlete_season
                    WHERE  person_id = ANY(%s) AND sport = 'XC' AND year = %s
                      AND  mean_rating IS NOT NULL
                    ORDER  BY person_id, n_races DESC NULLS LAST, mean_rating DESC""",
                            (list(actual), season))
                pred = {int(x["person_id"]): -float(x["mean_rating"]) for x in cur.fetchall()}
                row["rating"] = runnerScore(pred, actual)
                row["rating_teams"] = teamScore(pred, actual, school)
                if not a.no_entrants:
                    cur.execute("SELECT count(*) AS n, count(speed_rating) AS rated FROM results "
                                "WHERE source = 'tfrrs' AND meet_id = %s AND div_id = %s", (mid, d))
                    c = cur.fetchone()
                    row["leak"] = bool(c and c["rated"])
                    shell = {"meet_id": mid, "div_id": d, "source": "tfrrs", "made_on": a.day,
                             "model_basis": None}
                    try:
                        cur.execute("SAVEPOINT nv")
                        now = rebuildRace(cur, shell, list(actual))
                        cur.execute("RELEASE SAVEPOINT nv")
                        pred = {r["person_id"]: r["seconds"] for r in now["runners"]}
                        row["entrants"] = runnerScore(pred, actual)
                        row["entrants_teams"] = teamScore(pred, actual, school)
                    except Exception as exc:                # noqa: BLE001
                        cur.execute("ROLLBACK TO SAVEPOINT nv")
                        row["entrants_error"] = f"{type(exc).__name__}: {exc}"
                out[key] = row
        conn.rollback()

    report(out)
    return 0


ARMS = (("rating", "RC rate"), ("entrants", "RC model"), ("frozen", "RC page"))


def report(out):
    """Print our rows beside theirs: per race, LACCTiC's published columns,
    then ours -- ratings alone, the model on the entrants, the frozen page."""
    def f3(v):
        return f"{v:.3f}" if isinstance(v, float) else "  -  "

    def mean4(xs):
        return statistics.fmean(xs) if len(xs) == 4 else None
    order = ["DI men", "DI women", "DIII men", "DIII women"]
    print("\nRUNNERS (Spearman rho; 1 = finish order exactly)")
    print(f"  {'race':<11}" + "".join(f"{h:>9}" for h in THEIRS_RUNNER_HEAD)
          + "".join(f"{h:>9}" for _k, h in ARMS) + "   scored / finishers")
    rho = {k: [] for k, _h in ARMS}
    off = {k: [] for k, _h in ARMS}
    for k in order:
        r = out.get(k)
        th = THEIRS_RUNNERS[k]
        if not r:
            print(f"  {k:<11}" + "".join(f"{x:>9.3f}" for x in th) + "   (no results here)")
            continue
        cells, n = "", 0
        for arm, _h in ARMS:
            v = r.get(arm) or (None, None, 0)
            if v[0] is not None:
                rho[arm].append(v[0])
                off[arm].append(v[1])
            n = n or v[2]
            cells += f"{f3(v[0]):>9}"
        print(f"  {k:<11}" + "".join(f"{x:>9.3f}" for x in th) + cells
              + f"   {n} / {r['finishers']}"
              + (f"   page made {r['frozen_made']}" if r.get("frozen_made") else "   no page list")
              + ("   ! ratings have seen this race" if r.get("leak") else "")
              + (f"   model failed: {r['entrants_error']}" if r.get("entrants_error") else ""))
    th_avg = [statistics.fmean(THEIRS_RUNNERS[k][i] for k in order) for i in range(3)]
    print(f"  {'Average':<11}" + "".join(f"{x:>9.3f}" for x in th_avg)
          + "".join(f"{f3(mean4(rho[a])):>9}" for a, _h in ARMS))
    print("  mean places off: " + ", ".join(f"{k} {v}" for k, v in THEIRS_PLACES.items())
          + "".join(f", {h} {statistics.fmean(off[a]):.1f}" for a, h in ARMS if off[a])
          + "  (ours: mean over the races of each race's mean)")

    print("\nTEAMS (Spearman rho of the team order)")
    print(f"  {'race':<11}" + "".join(f"{h:>9}" for h in THEIRS_TEAM_HEAD)
          + "".join(f"{h:>9}" for _k, h in ARMS) + "   teams")
    tav = {k: [] for k, _h in ARMS}
    for k in order:
        r = out.get(k) or {}
        cells, n = "", 0
        for arm, _h in ARMS:
            v = r.get(arm + "_teams") or (None, None, 0)
            if v[0] is not None:
                tav[arm].append(v[0])
            n = n or v[2]
            cells += f"{f3(v[0]):>9}"
        print(f"  {k:<11}" + "".join(f"{f3(x):>9}" for x in THEIRS_TEAMS[k]) + cells + f"   {n}")
    print(f"  {'Average':<11}" + "".join(
        f"{f3(statistics.fmean(THEIRS_TEAMS[k][i] for k in order) if i < 4 else None):>9}"
        for i in range(5))
        + "".join(f"{f3(mean4(tav[a])):>9}" for a, _h in ARMS))

if __name__ == "__main__":
    raise SystemExit(main())
