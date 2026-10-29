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

★ --rebuild: HOW MUCH A RE-RUN FLATTERS (owner, 2026-10-29). Each frozen
  race is predicted again for the same runners, by the same basis, with the
  history cut at the day it was frozen -- but on TODAY's ratings and course
  numbers, as every backtest does. Both are scored on the runners they
  share, teams left out. frozen - rebuilt is the leak, plus whatever the
  engine and the models have changed since (so at most the leak's size).

★ --against pred_meet_forecast: THE CLEAN HEAD-TO-HEAD. Two frozen archives
  of the same races (the site's, and the pre-race recorder's race model,
  build_meet_forecasts with XCP_FORECAST_TABLE), both made before the gun,
  scored on the runners both listed. Nothing in either could know the
  result or a later solve.
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


def rebuildRace(cur, fc, ids, predict=None):
    """The frozen race predicted again now: a meet_forecast-shaped dict with
    the same runners' {person_id, seconds, lo, hi}, by the frozen basis,
    history cut at made_on. predict(cur, ids, target) defaults to
    predict._servedTimes."""
    if predict is None:
        import predict as P
        predict = P._servedTimes
    target = {"mode": "rerun_exact", "sport": "XC", "meet_id": int(fc["meet_id"]),
              "div_id": int(fc["div_id"]), "source": fc["source"],
              "history_before": str(fc["made_on"])[:10]}
    old = os.environ.get("XCP_PREDICT_BASIS")
    if fc.get("model_basis"):
        os.environ["XCP_PREDICT_BASIS"] = str(fc["model_basis"])
    try:
        got = predict(cur, list(ids), target)
    finally:
        if old is None:
            os.environ.pop("XCP_PREDICT_BASIS", None)
        else:
            os.environ["XCP_PREDICT_BASIS"] = old
    runners = [{"person_id": p, "seconds": e.get("seconds"), "lo": e.get("lo"),
                "hi": e.get("hi")} for p, e in zip(ids, got) if e and e.get("seconds")]
    return dict(fc, runners=runners, teams=[])


def sharedRunners(a, b):
    """Two forecasts cut to the runners both have times for. Pure."""
    ka = {r["person_id"] for r in a.get("runners") or [] if r.get("seconds")}
    kb = {r["person_id"] for r in b.get("runners") or [] if r.get("seconds")}
    both = ka & kb
    return tuple(dict(x, runners=[r for r in x.get("runners") or []
                                  if r.get("person_id") in both], teams=[])
                 for x in (a, b))


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
    ap.add_argument("--rebuild", action="store_true",
                    help="also predict each frozen race again on today's ratings and score "
                         "both on the runners they share: how much a re-run flatters")
    ap.add_argument("--against", default=None, metavar="TABLE",
                    help="a second frozen archive (pred_meet_forecast: the pre-race "
                         "recorder): both scored on the races and runners they share")
    ap.add_argument("--max-races", type=int, default=None,
                    help="with --rebuild: stop after this many races (about 2 s each)")
    a = ap.parse_args()

    import psycopg2.extras
    from database import getConn
    import meet_forecast as MF
    from meet_recap import actualByDiv, actualTeams

    if a.against and not (a.against == "meet_forecast" or (
            a.against.startswith("pred_") and a.against.replace("_", "").isalnum())):
        raise SystemExit(f"--against {a.against!r}: meet_forecast or a pred_ name only")
    groups = {}
    rebuilt = {}          # --rebuild: key -> [(frozen, rebuilt)] on shared runners
    versus = {}           # --against: key -> [(this table, that table)] on shared runners
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
        other = {}
        if a.against:
            cur.execute(f"""SELECT source, meet_id, div_id, meet_date, made_on, state,
                                   runners, teams, model_basis, model_version
                            FROM   {a.against}
                            WHERE  meet_date >= %s AND meet_date <= %s
                              AND  meet_date < CURRENT_DATE""", (a.lo, a.hi))
            for r in cur.fetchall():
                o = MF._rowOut(r)
                other[(o["source"], int(o["meet_id"]), int(o["div_id"]))] = o
            print(f"{len(other)} frozen races in {a.against}")
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
                pair = None
                if a.rebuild and (a.max_races is None
                                  or sum(len(v) for v in rebuilt.values()) < a.max_races):
                    ids = [r["person_id"] for r in fc.get("runners") or []
                           if r.get("person_id") is not None]
                    try:
                        cur.execute("SAVEPOINT sf_rebuild")
                        now = rebuildRace(cur, fc, ids)
                        cur.execute("RELEASE SAVEPOINT sf_rebuild")
                    except Exception as exc:         # noqa: BLE001
                        cur.execute("ROLLBACK TO SAVEPOINT sf_rebuild")
                        print(f"  rebuild {src}:{mid}/{fc['div_id']}: {type(exc).__name__}: {exc}")
                        now = None
                    if now is not None:
                        f_, r_ = sharedRunners(fc, now)
                        mf, mr = frozenMetrics(f_, fin, []), frozenMetrics(r_, fin, [])
                        if mf is not None and mr is not None:
                            pair = (mf, mr)
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
                if pair is not None:
                    rebuilt.setdefault(key, []).append(pair)
                o = other.get((fc["source"], int(fc["meet_id"]), int(fc["div_id"])))
                if o is not None:
                    f_, o_ = sharedRunners(fc, o)
                    mf, mo = frozenMetrics(f_, fin, []), frozenMetrics(o_, fin, [])
                    if mf is not None and mo is not None:
                        versus.setdefault(key, []).append((mf, mo))
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
    if a.against:
        print(f"\n{MF.TABLE} AGAINST {a.against}, the same races and runners, both frozen "
              f"before the race (teams left out)")
        keys = sorted(versus) + (["ALL"] if len(versus) > 1 else [])
        for key in keys:
            pairs = (versus[key] if key != "ALL" else [p for v in versus.values() for p in v])
            sf, so = summarize([p[0] for p in pairs]), summarize([p[1] for p in pairs])
            print(_row(f"{key} {MF.TABLE}"[:22], sf))
            print(_row(f"{key} {a.against}"[:22], so))
            print(f"  {'':20} {a.against} better by: median |err| {sf['mae'] - so['mae']:+.2f} "
                  f"points, within {sf['within'] - so['within']:+.2f}, rho "
                  f"{_fmt(so['rho'] - sf['rho'] if so['rho'] is not None and sf['rho'] is not None else None, '{:+.3f}')}")
    if a.rebuild:
        print("\nREBUILT ON TODAY'S RATINGS, the same runners (teams left out); "
              "frozen - rebuilt = how much a re-run flatters, at most")
        keys = sorted(rebuilt) + (["ALL"] if len(rebuilt) > 1 else [])
        for key in keys:
            pairs = (rebuilt[key] if key != "ALL"
                     else [p for v in rebuilt.values() for p in v])
            sf, sr = summarize([p[0] for p in pairs]), summarize([p[1] for p in pairs])
            print(_row(f"{key} frozen"[:22], sf))
            print(_row(f"{key} rebuilt"[:22], sr))
            print(f"  {'':20} flattered by: median |err| {sf['mae'] - sr['mae']:+.2f} points, "
                  f"within {sf['within'] - sr['within']:+.2f}, rho "
                  f"{_fmt(sr['rho'] - sf['rho'] if sr['rho'] is not None and sf['rho'] is not None else None, '{:+.3f}')}")


if __name__ == "__main__":
    main()
