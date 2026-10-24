#!/usr/bin/env python3
"""
backtest_baseline.py -- the strong simple baseline against what the site
serves, HELD OUT BY MEET DATE. Cross country. READ ONLY, except the one
JSON table --write-table names.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/backtest_baseline.py --train-from 2026-08-20 --from 2026-10-01 --to 2026-10-22
    $P scripts/backtest_baseline.py ... --with-model      # blend and model too (slower)
    $P scripts/backtest_baseline.py ... --write-table engine/data/rating_trend.json

★ WHY (review 2026-10, docs/REVIEW-PREDICTIONS-2026-10.md). The page serves
  a rating's time moved by one transformer-made factor per field (blend).
  Whether the transformer earns that place is a question about what a
  simple rule does without it, scored on races the rule never saw:

    rating  predict._ratingTimes: the last six rated races in the pool,
            weighted 0.8 ** k, as a time on the target's course
    trend   rating moved by a table FITTED ON THE TRAIN RACES ONLY
            (predict.trendShift): one median correction per (level, course
            has a cell, the form's age), plus the runner's own slope this
            season carried over that age. Bands: the form's own spread for
            places (scaled to what the train races showed), plus the
            race-day spread for times.
    blend   what the site serves (--with-model; predict.blendFactor)
    model   the transformer alone (--with-model)

  1. TRAIN: races dated [--train-from, --from). Each runner's rating time
     with the history cut the day before, against what they ran. The table
     is fitted here and nowhere else.
  2. TEST: races dated [--from, --to]. Every basis on the same runners.

  Per basis, on the test races:
    median |err|     the typical miss on a runner's time, %
    bias             median signed miss, + = predicted too slow
    within           median |err| once each race's own median miss is out
    rho              mean Spearman, predicted vs actual order, per race
    team |err|       mean |predicted - actual| team score, points, over
                     the teams that score among the runners predicted
    winner           share of races whose winning team was picked
    cov68 / cov90    share of actual times inside the +-1 / +-1.645 sigma
                     time band (honest = 68 / 90)
    place68          share of within-race misses inside +-1 place sigma
  then each pair of bases race by race (the per-race median |err|, A - B,
  with a 90% interval from resampling races), and the table's team_rho:
  how much of a runner's miss their teammates share (race_sim's team_rho,
  which the pages leave at 0).

⚠ STILL FLATTERED, AND EQUALLY. Ratings and course cells come from today's
  solve, which saw the test races (sweep A9), and model.pt may have trained
  on them. That flatters every basis alike, so read the DIFFERENCES. The
  clean scores are the pre-race archives: scripts/score_frozen_forecasts.py
  (meet_forecast) and scripts/kailo_compare.py (recorded).
⚠ TEAM SCORES HERE ARE OF THE RUNNERS WHO FINISHED, re-ordered. Who
  starts is given; score_frozen_forecasts.py is the number with entries
  guessed too.
"""
import argparse
import datetime
import json
import math
import os
import random
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# A cell's median is pulled toward its level's by K races: a cell seen in
# three races is mostly those races' weather, one seen in thirty is the
# rule. The level's is pulled toward the overall the same way.
SHRINK_RACES = 8.0
# Residuals past this are a wrong distance or a walked-in DNF, not form;
# the slope fit and the spreads read them clipped.
CLIP_LN = 0.08
# Fewer matched runners than this and a race says nothing about order.
MIN_MATCHED = 8
GAUSS_MAD = 0.6745       # median |z| of a standard normal


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def _median(xs):
    return statistics.median(xs) if xs else None


def _shrink(value, n, prior, k=SHRINK_RACES):
    return prior + (value - prior) * n / (n + k)


def _slopeX(r):
    """The trend term's input: ln-rating slope carried over the form's age."""
    import predict as P
    s = max(-P.TREND_MAX_SLOPE, min(P.TREND_MAX_SLOPE, float(r.get("slope") or 0.0)))
    return s * float(r.get("age") or 0.0) / 30.0


def trainRows(races):
    """[(race index, runner)] with a rating time and a result."""
    return [(i, r) for i, race in enumerate(races) for r in race["runners"]
            if r.get("rating_s") and r.get("actual")]


def _cells(rows, races, offset):
    """(overall, levels, cells, cell_races) of ln(actual / rating time) less
    offset(runner), shrunk by races."""
    import predict as P
    by_cell, by_level = {}, {}
    for i, r in rows:
        e = math.log(r["actual"] / r["rating_s"]) - offset(r)
        k = P.trendKey(r["level"], races[i]["has_cell"], r.get("age"))
        by_cell.setdefault(k, []).append((i, e))
        by_level.setdefault(r["level"], []).append((i, e))
    overall = _median([math.log(r["actual"] / r["rating_s"]) - offset(r)
                       for _i, r in rows]) or 0.0
    levels = {lv: _shrink(_median([e for _i, e in v]), len({i for i, _e in v}), overall)
              for lv, v in by_level.items()}
    cells, n_races = {}, {}
    for k, v in by_cell.items():
        n = len({i for i, _e in v})
        cells[k] = _shrink(_median([e for _i, e in v]), n, levels[k.split("|")[0]])
        n_races[k] = n
    return overall, levels, cells, n_races


def fitTable(races, train=None):
    """The trend table from TRAIN races only. Pure.

    races: [{"has_cell": bool, "runners": [{actual, rating_s, sig, level,
    age, slope}]}]. Returns what predict.trendShift reads, plus counts."""
    import predict as P
    rows = trainRows(races)
    if not rows:
        return {}
    # 1. the level cells with no slope term; 2. the slope on what is left;
    # 3. the cells again on what the slope does not explain
    # ! WITHIN CELLS: x is never negative (an age is), so a fit through the
    #   origin would hand the cell's own level to the slope
    overall, levels, cells, n_races = _cells(rows, races, lambda r: 0.0)
    by = {}
    for i, r in rows:
        k = P.trendKey(r["level"], races[i]["has_cell"], r.get("age"))
        e = max(-CLIP_LN, min(CLIP_LN, math.log(r["actual"] / r["rating_s"]) - cells[k]))
        by.setdefault(k, []).append((_slopeX(r), e))
    num = den = 0.0
    for v in by.values():
        mx = statistics.fmean(x for x, _e in v)
        me = statistics.fmean(e for _x, e in v)
        num += sum((x - mx) * (e - me) for x, e in v)
        den += sum((x - mx) ** 2 for x, _e in v)
    beta = max(0.0, min(1.0, -num / den)) if den > 0 else 0.0
    overall, levels, cells, n_races = _cells(rows, races, lambda r: -beta * _slopeX(r))
    table = {"overall": overall, "levels": levels, "cells": cells,
             "slope_beta": beta}
    # 4. the spreads, after the correction: the race's own median miss is
    # the day; what is left inside a race is the place spread
    shifts, ratios = [], []
    for i, race in enumerate(races):
        es = []
        for r in race["runners"]:
            if not (r.get("rating_s") and r.get("actual")):
                continue
            ent = {"form_pool": r["level"], "form_age_days": r.get("age"),
                   "season_slope": r.get("slope")}
            pred = math.log(r["rating_s"]) + P.trendShift(ent, table, race["has_cell"])
            es.append((math.log(r["actual"]) - pred, r.get("sig")))
        if len(es) < MIN_MATCHED:
            continue
        m = _median([e for e, _s in es])
        shifts.append(m)
        ratios += [abs(max(-CLIP_LN, min(CLIP_LN, e - m))) / s
                   for e, s in es if s and s > 0]
    if shifts:
        c = _median(shifts)
        table["day_sd"] = 1.4826 * _median([abs(x - c) for x in shifts])
    if ratios:
        table["place_k"] = max(0.5, min(3.0, _median(ratios) / GAUSS_MAD))
    table.update({"made": datetime.date.today().isoformat(),
                  "train": list(train) if train else None,
                  "n_races": len(races), "n_runners": len(rows),
                  "cell_races": n_races})
    return table


def teamPoints(schools):
    """{school: points} for runners in finishing order (meet_compile's
    rules: five score, two displace, incomplete teams lifted out)."""
    from meet_compile import scoreRows
    out = scoreRows([{"school": s} for s in schools])
    return {t["school"]: t["points"] for t in out.get("teams") or []}


def scoreBasis(races, pick):
    """The test metrics of one basis. pick(race, runner) -> (seconds,
    time_sigma, place_sigma) or None. Pure but for teamPoints."""
    from backtest_predictions import _spearman
    errs, within, rhos, team_err, per_race = [], [], [], [], {}
    winners = races_n = in68 = in90 = n_band = pin = n_pin = 0
    for race in races:
        got = []
        for r in race["runners"]:
            p = pick(race, r)
            if p and p[0] and r.get("actual"):
                got.append((r, p))
        if len(got) < MIN_MATCHED:
            continue
        races_n += 1
        e = [math.log(p[0] / r["actual"]) for r, p in got]
        m = statistics.median(e)
        per_race[race["key"]] = 100.0 * statistics.median(abs(x) for x in e)
        errs += [100.0 * x for x in e]
        within += [100.0 * (x - m) for x in e]
        for x, (_r, p) in zip(e, got):
            if p[1]:
                n_band += 1
                in68 += abs(x) <= p[1]
                in90 += abs(x) <= 1.645 * p[1]
            if p[2]:
                n_pin += 1
                pin += abs(x - m) <= p[2]
        rho = _spearman([p[0] for _r, p in got], [r["actual"] for r, _p in got])
        if rho is not None:
            rhos.append(rho)
        act = teamPoints([r.get("school") for r, _p in
                          sorted(got, key=lambda g: g[0]["actual"])])
        pred = teamPoints([r.get("school") for r, _p in
                           sorted(got, key=lambda g: g[1][0])])
        both = set(act) & set(pred)
        team_err += [abs(pred[t] - act[t]) for t in both]
        if act and pred:
            winners += min(act, key=act.get) == min(pred, key=pred.get)
    if not errs:
        return None
    return {"races": races_n, "runners": len(errs),
            "mae": statistics.median(abs(x) for x in errs),
            "bias": statistics.median(errs),
            "within": statistics.median(abs(x) for x in within),
            "rho": statistics.fmean(rhos) if rhos else None,
            "team_err": statistics.fmean(team_err) if team_err else None,
            "winner": winners / races_n if races_n else None,
            "cov68": in68 / n_band if n_band else None,
            "cov90": in90 / n_band if n_band else None,
            "place68": pin / n_pin if n_pin else None,
            "per_race": per_race}


def paired(a, b, draws=2000, seed=0):
    """a and b: {race: median |err|} of two bases. (races, mean a - b, 90%
    interval by resampling races, share of races a is better). Pure."""
    keys = sorted(set(a) & set(b))
    if len(keys) < 5:
        return None
    d = [a[k] - b[k] for k in keys]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(d) for _k in d) for _i in range(draws))
    return (len(d), statistics.fmean(d), means[int(0.05 * draws)],
            means[int(0.95 * draws) - 1], sum(x < 0 for x in d) / len(d))


def teamRho(races, table):
    """How much of a runner's miss inside a race their teammates share, from
    the TRAIN races after the table's correction -- what race_sim's team_rho
    is (it defaults to 0: teammates independent). Pure but for isTeam."""
    import predict as P
    from meet_compile import isTeam
    cross = sq = 0.0
    n_pairs = n_sq = 0
    for race in races:
        w = []
        for r in race["runners"]:
            if r.get("rating_s") and r.get("actual"):
                ent = {"form_pool": r["level"], "form_age_days": r.get("age"),
                       "season_slope": r.get("slope")}
                x = (math.log(r["actual"] / r["rating_s"])
                     - P.trendShift(ent, table, race["has_cell"]))
                w.append((r.get("school"), max(-CLIP_LN, min(CLIP_LN, x))))
        if len(w) < MIN_MATCHED:
            continue
        m = statistics.median(x for _s, x in w)
        by = {}
        for school, x in w:
            if isTeam(school):
                by.setdefault(school, []).append(x - m)
        for xs in by.values():
            if len(xs) < 2:
                continue
            tot = sum(xs)
            cross += (tot * tot - sum(x * x for x in xs)) / 2.0
            n_pairs += len(xs) * (len(xs) - 1) // 2
            sq += sum(x * x for x in xs)
            n_sq += len(xs)
    if not n_pairs or not sq:
        return None
    return max(0.0, min(0.5, (cross / n_pairs) / (sq / n_sq)))


def picker(basis, table=None, need_model=False):
    """pick(race, runner) for scoreBasis. need_model: only the runners the
    model also priced, so every basis is scored on the same people."""
    import predict as P

    def rating(race, r):
        s = (r.get("sig") or 0.0)
        return (r["rating_s"], s, s) if r.get("rating_s") else None

    def trend(race, r):
        if not r.get("rating_s"):
            return None
        e = {"seconds": r["rating_s"], "sigma_pct": 100.0 * (r.get("sig") or 0.0),
             "form_pool": r["level"], "form_age_days": r.get("age"),
             "season_slope": r.get("slope")}
        P.trendApply(e, table or {}, race["has_cell"])
        return (e["seconds"], e["time_sigma_pct"] / 100.0, e["sigma_pct"] / 100.0)

    def blend(race, r):
        if not (r.get("rating_s") and race.get("blend")):
            return None
        s = r.get("sig") or 0.0
        return (r["rating_s"] * race["blend"], s, s)

    def model(race, r):
        if not r.get("model_s") or not r.get("rating_s"):
            return None              # the same runners as the others
        s = r.get("model_sig") or 0.0
        return (r["model_s"], s, s)

    pick = {"rating": rating, "trend": trend, "blend": blend, "model": model}[basis]
    if not need_model:
        return pick
    return lambda race, r: pick(race, r) if r.get("model_s") else None


# ------------------------------------------------------------------ #
#  the database side
# ------------------------------------------------------------------ #

def finishers(cur, race):
    """[(person_id, seconds, school)] in finishing order, one row a person."""
    cur.execute("""
        SELECT person_id, time_seconds, school FROM (
            SELECT DISTINCT ON (person_id) person_id, time_seconds, school
            FROM   results r
            WHERE  meet_id = %s AND div_id = %s AND source = %s
              AND  person_id IS NOT NULL
              AND  time_seconds > 0 AND time_seconds < 19999
              AND  NOT EXISTS (SELECT 1 FROM result_twin x
                               WHERE x.sport = 'XC' AND x.result_id = r.result_id)
            ORDER  BY person_id, time_seconds) f
        ORDER BY time_seconds""", (race["meet_id"], race["div_id"], race["source"]))
    return [(r["person_id"], float(r["time_seconds"]), r["school"]) for r in cur.fetchall()]


def collect(cur, races, with_model=False, label=""):
    """Each race's runners with every basis's inputs, history cut the day
    before the race."""
    import predict as P
    out = []
    for n, race in enumerate(races, 1):
        day = datetime.date.fromisoformat(race["day"])
        target = {"mode": "rerun_exact", "sport": "XC", "meet_id": race["meet_id"],
                  "div_id": race["div_id"], "source": race["source"]}
        try:
            fin = finishers(cur, race)
            ids = [f[0] for f in fin]
            spec = P._targetSpec(cur, target)
            rated = P._ratingTimes(cur, ids, spec, day)
            model = (dict(zip(ids, P._predictTimes(cur, ids, target, spec=spec)))
                     if with_model else {})
        except Exception as exc:                     # noqa: BLE001
            cur.connection.rollback()
            print(f"  {label} {race['meet_id']}/{race['div_id']}: {exc}", flush=True)
            continue
        cur.connection.rollback()
        runners = []
        for pid, secs, school in fin:
            e = rated.get(pid) or {}
            m = model.get(pid) or {}
            runners.append({
                "pid": pid, "school": school, "actual": secs,
                "rating_s": e.get("seconds"),
                "sig": (e.get("sigma_pct") or 0.0) / 100.0,
                "level": (e.get("form_pool") or "?").split("|")[0],
                "age": e.get("form_age_days"), "slope": e.get("season_slope"),
                "model_s": m.get("seconds") if m.get("is_race_time") else None,
                "model_sig": (m.get("sigma_pct") or 0.0) / 100.0})
        rec = {"key": f"{race['source']}:{race['meet_id']}:{race['div_id']}",
               "day": race["day"], "has_cell": spec.get("venue_cell") is not None,
               "runners": runners}
        if with_model:
            rec["blend"] = P.blendFactor([(r["model_s"], r["rating_s"])
                                          for r in runners])
        out.append(rec)
        if n % 10 == 0 or n == len(races):
            print(f"  {label} [{n}/{len(races)}] {race['day']}", flush=True)
    return out


def _fmt(v, f, nd=2):
    return "-" if v is None else f.format(round(v, nd))


def report(test, bases, table, need_model=False):
    print(f"\n{'basis':7} {'races':>5} {'runners':>7} {'median|err|':>11} {'bias':>7} "
          f"{'within':>7} {'rho':>6} {'team|err|':>9} {'winner':>7} "
          f"{'cov68':>6} {'cov90':>6} {'place68':>7}")
    for b in bases:
        s = scoreBasis(test, picker(b, table, need_model))
        if not s:
            print(f"{b:7} (nothing scorable)")
            continue
        print(f"{b:7} {s['races']:>5} {s['runners']:>7} {_fmt(s['mae'], '{:.2f}%'):>11} "
              f"{_fmt(s['bias'], '{:+.2f}%'):>7} {_fmt(s['within'], '{:.2f}%'):>7} "
              f"{_fmt(s['rho'], '{:.3f}', 3):>6} {_fmt(s['team_err'], '{:.1f}', 1):>9} "
              f"{_fmt(s['winner'] and 100 * s['winner'], '{:.0f}%', 0):>7} "
              f"{_fmt(s['cov68'] and 100 * s['cov68'], '{:.0f}%', 0):>6} "
              f"{_fmt(s['cov90'] and 100 * s['cov90'], '{:.0f}%', 0):>6} "
              f"{_fmt(s['place68'] and 100 * s['place68'], '{:.0f}%', 0):>7}")
    print("\npaired by race (median |err| per race, A - B; - = A better; 90% interval)")
    per = {b: (scoreBasis(test, picker(b, table, need_model)) or {}).get("per_race") or {}
           for b in bases}
    for x, y in (("trend", "rating"), ("trend", "blend"), ("blend", "rating"),
                 ("trend", "model")):
        if x in per and y in per:
            got = paired(per[x], per[y])
            if got:
                n, mean, lo, hi, share = got
                print(f"  {x:>6} - {y:<7} {mean:+.2f} pts [{lo:+.2f}, {hi:+.2f}]  "
                      f"{x} better in {100 * share:.0f}% of {n} races")
    # where trend moves things: by level, on the test races
    print("\nby level (median bias rating -> trend, runners)")
    by = {}
    for race in test:
        for r in race["runners"]:
            if r.get("rating_s") and r.get("actual"):
                t = picker("trend", table)(race, r)
                by.setdefault(r["level"], []).append(
                    (100 * math.log(r["rating_s"] / r["actual"]),
                     100 * math.log(t[0] / r["actual"])))
    for lv, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= 30:
            print(f"  {lv:12} {statistics.median(a for a, _b in v):+6.2f}% -> "
                  f"{statistics.median(b for _a, b in v):+6.2f}%  n{len(v)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-from", required=True)
    ap.add_argument("--from", dest="lo", required=True,
                    help="first TEST day; training races end the day before")
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--train-divs", type=int, default=150)
    ap.add_argument("--divs", type=int, default=60)
    ap.add_argument("--min-field", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--with-model", action="store_true",
                    help="score blend and the model too (a few seconds a race more)")
    ap.add_argument("--write-table", default=None, metavar="JSON",
                    help="write the table fitted on the TRAIN races here "
                         "(predict reads engine/data/rating_trend.json)")
    a = ap.parse_args()
    train_hi = (datetime.date.fromisoformat(a.lo) - datetime.timedelta(days=1)).isoformat()

    import psycopg2.extras
    from database import getConn
    from backtest_predictions import sampleRaces

    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        rng = random.Random(a.seed)
        train = sampleRaces(cur, a.train_from, train_hi, "XC", a.min_field)
        test = sampleRaces(cur, a.lo, a.hi, "XC", a.min_field)
        conn.rollback()
        rng.shuffle(train)
        rng.shuffle(test)
        train, test = train[:a.train_divs], test[:a.divs]
        print(f"train {len(train)} races {a.train_from}..{train_hi}; "
              f"test {len(test)} races {a.lo}..{a.hi} (fields >= {a.min_field})")
        tr = collect(cur, train, label="train")
        table = fitTable(tr, train=(a.train_from, train_hi))
        table["team_rho"] = teamRho(tr, table)
        print(f"\ntable: overall {100 * table.get('overall', 0):+.2f}%, "
              f"slope_beta {table.get('slope_beta', 0):.2f}, "
              f"day_sd {100 * table.get('day_sd', 0):.2f}%, "
              f"place_k {table.get('place_k', 1):.2f}, "
              f"team_rho {table.get('team_rho') or 0:.2f}; levels "
              + ", ".join(f"{k} {100 * v:+.1f}%" for k, v in
                          sorted((table.get("levels") or {}).items())))
        te = collect(cur, test, with_model=a.with_model, label="test")
    bases = ("rating", "trend") + (("blend", "model") if a.with_model else ())
    report(te, bases, table, need_model=a.with_model)
    if a.write_table:
        with open(a.write_table, "w") as fh:
            json.dump(table, fh, indent=1, sort_keys=True)
        print(f"\nwrote {a.write_table} (served only under XCP_PREDICT_BASIS=trend)")


if __name__ == "__main__":
    main()
