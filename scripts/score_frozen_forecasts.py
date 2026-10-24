#!/usr/bin/env python3
"""
score_frozen_forecasts.py -- the forecasts as they were POSTED before each
meet (meet_forecast, pipeline step 13h), scored against what happened.
READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/score_frozen_forecasts.py --from 2026-10-01 --to 2026-10-23
    $P scripts/score_frozen_forecasts.py --from 2026-09-01 --to 2026-10-23 --by basis
    $P scripts/score_frozen_forecasts.py --from 2026-09-01 --to 2026-10-23 --by lead

★ WHY (review 2026-10, docs/REVIEW-PREDICTIONS-2026-10.md). Every other
  accuracy number we print re-predicts a finished race on TODAY's ratings
  and course numbers, which have seen that race (sweep A9) -- including the
  What's new line (16d_backtest). meet_forecast holds what the page showed
  the night before, frozen (meet_forecast.forecastAction: never rewritten on
  or after the meet's day), so nothing in it could know the result. It is
  the one clean test of the whole chain: who we guessed would start, their
  times, their places and the team scores.

  Per group (all, or --by week | basis | lead | level):
    median |err|   on the runners we listed who finished, %
    bias           median signed miss, + = we said slower than they ran
    within         median |err| once each race's own median miss is out
    rho            mean Spearman of our times against theirs, per race
    place|err|     median |our place - their place|, both re-ranked among
                   the runners matched (a place is only a place among them)
    team|err|      mean |our team score - their team score| over the teams
                   in both, points (entries guessed: the real-world number)
    team rho       mean Spearman of the team order
    winner         share of races whose winning team we picked
    cov68          share of finishers inside our posted band (lo..hi, which
                   is +-1 sigma, so honest = 68%)
    listed         share of finishers we had listed at all
"""
import argparse
import datetime
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_MATCHED = 8


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0] * len(xs)
    for r, i in enumerate(order, 1):
        out[i] = r
    return out


def frozenMetrics(fc, finishers, actual_teams):
    """One stored race against its result. Pure.

    fc          the meet_forecast row (runners with person_id, seconds, lo,
                hi, place; teams with team, state, score)
    finishers   placed finishers in finish order {person_id, school,
                time_seconds}
    actual_teams {team, state, points}, best first (meet_recap.actualTeams)
    Returns None when too few of our runners finished to say anything."""
    from backtest_predictions import _spearman
    from meet_recap import _sameTeam
    act = {}
    for f in finishers:
        if f.get("person_id") is not None and f["person_id"] not in act:
            act[f["person_id"]] = float(f["time_seconds"])
    got = [(r, act[r["person_id"]]) for r in fc.get("runners") or []
           if r.get("person_id") in act and r.get("seconds")]
    if len(got) < MIN_MATCHED:
        return None
    e = [math.log(float(r["seconds"]) / a) for r, a in got]
    m = statistics.median(e)
    band = [(r, a) for r, a in got if r.get("lo") and r.get("hi")]
    pr = _ranks([float(r["seconds"]) for r, _a in got])
    ar = _ranks([a for _r, a in got])
    pteams = sorted((t for t in fc.get("teams") or [] if t.get("score") is not None),
                    key=lambda t: t["score"])
    ateams = [t for t in actual_teams or [] if t.get("points") is not None]
    pairs = []
    for t in pteams:
        a = next((x for x in ateams if _sameTeam(t, x)), None)
        if a is not None:
            pairs.append((float(t["score"]), float(a["points"])))
    return {"errs": [100.0 * x for x in e],
            "within": [100.0 * (x - m) for x in e],
            "rho": _spearman([float(r["seconds"]) for r, _a in got], [a for _r, a in got]),
            "place_err": [abs(p - q) for p, q in zip(pr, ar)],
            "team_err": [abs(p - q) for p, q in pairs],
            "team_rho": (_spearman([p for p, _q in pairs], [q for _p, q in pairs])
                         if len(pairs) >= 3 else None),
            "winner": (_sameTeam(pteams[0], ateams[0]) if pteams and ateams else None),
            "in_band": sum(float(r["lo"]) <= a <= float(r["hi"]) for r, a in band),
            "n_band": len(band),
            "listed": len(got) / max(len(act), 1)}


def summarize(races):
    """Many frozenMetrics into one row of the table. Pure."""
    if not races:
        return None
    errs = [x for r in races for x in r["errs"]]
    rhos = [r["rho"] for r in races if r["rho"] is not None]
    trho = [r["team_rho"] for r in races if r["team_rho"] is not None]
    win = [r["winner"] for r in races if r["winner"] is not None]
    nb = sum(r["n_band"] for r in races)
    terr = [x for r in races for x in r["team_err"]]
    return {"races": len(races), "runners": len(errs),
            "mae": statistics.median(abs(x) for x in errs),
            "bias": statistics.median(errs),
            "within": statistics.median(abs(x) for r in races for x in r["within"]),
            "rho": statistics.fmean(rhos) if rhos else None,
            "place_err": statistics.median(x for r in races for x in r["place_err"]),
            "team_err": statistics.fmean(terr) if terr else None,
            "team_rho": statistics.fmean(trho) if trho else None,
            "winner": sum(win) / len(win) if win else None,
            "cov68": sum(r["in_band"] for r in races) / nb if nb else None,
            "listed": statistics.fmean(r["listed"] for r in races)}


def _fmt(v, f):
    return "-" if v is None else f.format(v)


def _row(label, s):
    return (f"{label:22} {s['races']:>5} {s['runners']:>7} {s['mae']:>10.2f}% "
            f"{s['bias']:>+6.2f}% {s['within']:>6.2f}% {_fmt(s['rho'], '{:.3f}'):>6} "
            f"{s['place_err']:>8.1f} {_fmt(s['team_err'], '{:.1f}'):>9} "
            f"{_fmt(s['team_rho'], '{:.2f}'):>6} "
            f"{_fmt(s['winner'] and 100 * s['winner'], '{:.0f}%'):>6} "
            f"{_fmt(s['cov68'] and 100 * s['cov68'], '{:.0f}%'):>6} "
            f"{100 * s['listed']:>6.0f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--by", choices=("all", "week", "basis", "lead", "level"),
                    default="all")
    a = ap.parse_args()

    import psycopg2.extras
    from database import getConn
    import meet_forecast as MF
    from meet_recap import actualByDiv, actualTeams

    groups = {}
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        cur.execute(f"""SELECT source, meet_id, div_id, meet_date, made_on, state,
                               runners, teams, model_basis, model_version
                        FROM   {MF.TABLE}
                        WHERE  meet_date >= %s AND meet_date <= %s
                          AND  meet_date < CURRENT_DATE
                        ORDER  BY meet_date, meet_id, div_id""", (a.lo, a.hi))
        stored = [MF._rowOut(r) for r in cur.fetchall()]
        by_meet = {}
        for fc in stored:
            by_meet.setdefault((fc["source"], int(fc["meet_id"])), []).append(fc)
        print(f"{len(stored)} frozen races in {len(by_meet)} meets, {a.lo}..{a.hi}")
        for (src, mid), fcs in by_meet.items():
            try:
                actual = actualByDiv(cur, mid, src)
            except Exception as exc:                 # noqa: BLE001
                conn.rollback()
                print(f"  {src}:{mid}: {exc}")
                continue
            for fc in fcs:
                fin = actual.get(int(fc["div_id"]))
                if not fin:
                    continue
                try:
                    cur.execute("SAVEPOINT sf_teams")
                    teams = actualTeams(cur, fin, fc.get("state"), src)
                    cur.execute("RELEASE SAVEPOINT sf_teams")
                except Exception:                    # noqa: BLE001
                    cur.execute("ROLLBACK TO SAVEPOINT sf_teams")
                    teams = []
                m = frozenMetrics(fc, fin, teams)
                if m is None:
                    continue
                day = datetime.date.fromisoformat(str(fc["meet_date"])[:10])
                made = datetime.date.fromisoformat(str(fc["made_on"])[:10])
                levels = [r.get("pool") for r in fc.get("runners") or [] if r.get("pool")]
                key = {"all": "all",
                       "week": (day - datetime.timedelta(days=day.weekday())).isoformat(),
                       "basis": f"{fc.get('model_basis') or '?'} "
                                f"{(fc.get('model_version') or '')[9:19]}".strip(),
                       "lead": f"{(day - made).days}d ahead",
                       "level": (statistics.mode(levels).split("|")[0]
                                 if levels else "?")}[a.by]
                groups.setdefault(key, []).append(m)
        conn.rollback()

    print(f"\n{a.by:22} {'races':>5} {'runners':>7} {'median|err|':>11} {'bias':>7} "
          f"{'within':>7} {'rho':>6} {'plc|err|':>8} {'team|err|':>9} {'t.rho':>6} "
          f"{'winner':>6} {'cov68':>6} {'listed':>7}")
    for key in sorted(groups):
        s = summarize(groups[key])
        if s:
            print(_row(str(key), s))
    allr = [m for v in groups.values() for m in v]
    if len(groups) > 1 and allr:
        print(_row("ALL", summarize(allr)))


if __name__ == "__main__":
    main()
