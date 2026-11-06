#!/usr/bin/env python3
"""
kailo_sim.py -- Kailo's meet simulator (kailo.fit/kpace/meet-simulator/)
against our race model, on races both predicted BEFORE they were run.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/kailo_sim.py --snapshot      # keep today's data file (the recorder runs it)
    $P scripts/kailo_sim.py --compare       # score every preset meet that has run

★ WHY (owner, 2026-11-02: "Just use this"). Kailo's rankings API scores a
  handful of races (scripts/kailo_compare.py: 9 this season, none caught
  before the gun). The simulator prices any of 288 preset meets (conference
  championships, regionals, nationals) from one public file,
  data/meetsim.json: every college roster's 5K-equivalent times, ~1,300
  courses, the presets' dates and teams. The file says when it was built
  ("built") and how far its results reach ("results_through"); a preset run
  after that is a prediction made before the race.

★ THEIR TIME, AS THEIR PAGE PRICES IT (assets/meetsim-core.js model()):
    runner's time x exp(course + route level + the date's typical weather
    and season, all in %) x (distance / 5000) ^ riegel
  with the reader's own weather at zero (the page's default). Their page
  shows a simulation's median; this is the time those draws centre on.

★ OURS: the race model's forecast frozen before the race
  (pred_meet_forecast, the pre-race recorder; or --table). A race we did not
  freeze is skipped, never rebuilt: the served model has learned from it.

Matching (scripts/kailo_compare.py): the meet by date (+-1 day) and name or
venue; the division by distance (3%) and sex; a runner by name, ties broken
by school. Scored on the runners all three know: Kailo, us, and the result.
"""
import argparse
import datetime
import glob
import gzip
import json
import math
import os
import re
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

DATA = "https://kailo.fit/kpace/meet-simulator/data/meetsim.json"
STORE = os.environ.get("XCP_KAILO_SIM_STORE") or os.path.join(
    os.environ.get("XCP_KAILO_STORE") or os.path.join(_ROOT, "engine", "data", "kailo"), "sim")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ #
#  their model, ported from assets/meetsim-core.js
# ------------------------------------------------------------------ #

def seasonDay(iso):
    """Days from 15 August of the race's season, 0..120."""
    y, m, d = (int(x) for x in iso.split("-"))
    year = y if m >= 6 else y - 1
    days = (datetime.date(y, m, d) - datetime.date(year, 8, 15)).days
    return max(0, min(120, days))


def readCurve(knots, values, day):
    if day <= knots[0]:
        return values[0]
    if day >= knots[-1]:
        return values[-1]
    for i in range(1, len(knots)):
        if day <= knots[i]:
            return values[i - 1] + (day - knots[i - 1]) / (knots[i] - knots[i - 1]) * (
                values[i] - values[i - 1])
    raise AssertionError("unreachable")


def courseBasis(P, c):
    return "typical_day" if c and c[10] else (P.get("basis") or "typical_day")


def coursePct(P, c):
    if not c:
        return 0.0
    return c[3] if courseBasis(P, c) == "standard_day" else P["gamma"] * c[3]


def datedPct(P, c, iso):
    """The date's typical weather + season term, %, on a standard-day course."""
    if not c or courseBasis(P, c) != "standard_day":
        return 0.0
    S = P["seasonal"]
    day = seasonDay(iso)
    at = min(day, S["clip_day"])
    weather = readCurve(S["knots_day"], S["heat_pct"], at) + readCurve(
        S["knots_day"], S["rest_pct"], at) + c[9]
    season = S["phase_pct_per_day"] * max(0, S["phase_knot_day"] - day)
    return weather + season


def routeKm(dist):
    x = dist / 1000.0
    f = math.floor(x)
    r = x - f
    return f + 1 if r > 0.5 or (r == 0.5 and f % 2 == 1) else f


def routeLevel(c, dist):
    v = (c[8] or {}).get(str(routeKm(dist))) if c and c[8] else None
    return v if isinstance(v, (int, float)) and math.isfinite(v) else 0.0


def factor(P, c, dist, iso, eff=0.0):
    """Their multiplier from a runner's time to this race."""
    return math.exp((coursePct(P, c) + routeLevel(c, dist) + datedPct(P, c, iso) + eff) / 100.0) \
        * (dist / 5000.0) ** P["riegel"]


def presetTimes(D, preset):
    """[(name, team, seconds)] for every rostered runner of a preset meet."""
    courses = {c[0]: c for c in D["courses"]}
    c = courses.get(preset.get("course"))
    f = factor(D["params"], c, float(preset["dist"]), preset["date"])
    want = set(preset.get("teams") or [])
    out = []
    for key, teams in D["teams"].items():
        if not key.endswith("_" + preset["sex"]):
            continue
        for team, runners in teams:
            if team in want:
                out += [(r[0], team, r[1] * f) for r in runners]
        want -= {t for t, _r in teams}
    return out


# ------------------------------------------------------------------ #
#  the snapshots
# ------------------------------------------------------------------ #

def snapshot(store=STORE):
    """Save today's data file as <built>.json.gz, once per build."""
    import requests
    r = requests.get(DATA, timeout=60)      # no product name (owner, 2026-11-06: Kailo agreed)
    r.raise_for_status()
    D = r.json()
    built = str(D.get("built") or "")
    if not DATE.match(built):
        raise SystemExit(f"the data file has no build date ({built!r})")
    os.makedirs(store, exist_ok=True)
    path = os.path.join(store, f"{built}.json.gz")
    if os.path.exists(path):
        print(f"[kailo sim] {built} already kept ({len(D['presets'])} presets)")
        return path
    with gzip.open(path, "wt") as fh:
        json.dump(D, fh)
    print(f"[kailo sim] kept {path}: built {built}, results through "
          f"{D.get('results_through')}, {len(D['presets'])} presets")
    return path


def snapshots(store=STORE):
    """{built: path}, oldest first."""
    out = {}
    for p in sorted(glob.glob(os.path.join(store, "*.json.gz"))):
        b = os.path.basename(p)[:10]
        if DATE.match(b):
            out[b] = p
    return out


def beforeRace(snaps, race_day):
    """The newest build whose results stop before the race day: (built,
    path) or None. A build of the race day itself may have seen it."""
    best = None
    for b, p in snaps.items():
        if b < race_day:
            best = (b, p)
    return best


# ------------------------------------------------------------------ #
#  the comparison
# ------------------------------------------------------------------ #

def matchKailo(kailo, ours):
    """{person_id: kailo seconds}. kailo: [(name, team, s)]; ours:
    kailo_compare.ourResults rows. By name key, ties by school."""
    import kailo_compare as K
    by_name = {}
    for pid, key, school, _sec in ours:
        by_name.setdefault(key, []).append((pid, school))
    out = {}
    for name, team, s in kailo:
        cands = by_name.get(K.nameKey(name), [])
        if not cands:
            continue
        if len(cands) > 1:
            cands = sorted(cands, key=lambda c: -K.similarity(team, c[1]))
        out.setdefault(cands[0][0], s)
    return out


def compare(cur, table, lo, hi, show=0):
    import kailo_compare as K
    import meet_forecast as MF
    snaps = snapshots()
    if not snaps:
        print("[kailo sim] no snapshots kept yet: run --snapshot (the recorder does, daily)")
        return
    today = datetime.date.today().isoformat()
    loaded, rows_all, per, order = {}, ([], []), [], [0, 0, 0]
    by_race_o, by_race_t = [], []
    seen = set()
    for built, path in snaps.items():
        if path not in loaded:
            with gzip.open(path, "rt") as fh:
                loaded[path] = json.load(fh)
    presets = {}
    for built, path in snaps.items():
        for p in loaded[path]["presets"]:
            presets.setdefault((p["key"], p["date"]), p)
    for (key, day), p in sorted(presets.items(), key=lambda kv: kv[0][1]):
        if not (lo <= day <= hi) or day >= today:
            continue
        pick = beforeRace(snaps, day)
        label = f"{p['name']} {p['sex']} ({day})"
        if pick is None:
            print(f"  ✗ {label}: no snapshot built before the race")
            continue
        D = loaded[pick[1]]
        found = K.ourMeet(cur, p["name"], day, course=p.get("venue"))
        if not found:
            print(f"  ✗ {label}: no tfrrs meet of ours matches")
            continue
        mid, mname, divs = found
        div = K.pickDivision(divs, float(p["dist"]), p["sex"], p["name"])
        if div is None or (mid, div) in seen:
            print(f"  ✗ {label}: our meet {mid} ({mname}) has no matching division")
            continue
        seen.add((mid, div))
        cur.execute(f"""SELECT runners, model_basis FROM {table}
                        WHERE source = 'tfrrs' AND meet_id = %s AND div_id = %s""",
                    (mid, div))
        row = cur.fetchone()
        if not row:
            print(f"  ✗ {label}: not frozen in {table}")
            continue
        runners = row[0] if not isinstance(row, dict) else row["runners"]
        runners = MF._load(runners) if isinstance(runners, str) else runners
        frozen = {int(r["person_id"]): float(r["seconds"]) for r in runners or []
                  if r.get("person_id") is not None and r.get("seconds")}
        actual = K.ourResults(cur, mid, div)
        theirs = matchKailo(presetTimes(D, p), actual)
        act = {pid: sec for pid, _k, _s, sec in actual}
        rows = [(frozen[pid], theirs[pid], act[pid]) for pid in act
                if pid in frozen and pid in theirs]
        if len(rows) < 5:
            print(f"  ✗ {label}: {len(rows)} runners known to both and finished")
            continue
        mo, mt, (go, gt, n) = K.scores(rows)
        rows_all[0].extend(mo)
        rows_all[1].extend(mt)
        by_race_o.append(mo)
        by_race_t.append(mt)
        order = [order[0] + go, order[1] + gt, order[2] + n]
        per.append((label, pick[0], len(rows), K.summary(mo), K.summary(mt),
                    go / n if n else None, gt / n if n else None))
    if not per:
        print("[kailo sim] nothing to score yet")
        return
    print(f"\n  {'race':<52}{'kailo built':<12}{'runners':>8}  {'miss ours':>10}{'Kailo':>8}"
          f"  {'order ours':>11}{'Kailo':>8}")
    for label, built, n, so, st, oo, ot in (per[:show] if show else per):
        print(f"  {label[:51]:<52}{built:<12}{n:>8}  {so['mean']:>9.2f}%{st['mean']:>7.2f}%"
              f"  {100 * (oo or 0):>10.1f}%{100 * (ot or 0):>7.1f}%")
    so, st = K.summary(rows_all[0]), K.summary(rows_all[1])
    print(f"\n  ALL {so['n']:,} runners in {len(per)} races, both predicted before the race:")
    for name, s in (("ours", so), ("Kailo", st)):
        print(f"    {name:<6} average miss {s['mean']:.2f}%   median {s['median']:.2f}%   "
              f"within 2% {100 * s['within2']:.0f}%   bias {s['bias']:+.2f}%")
    if order[2]:
        print(f"    order: ours {100 * order[0] / order[2]:.1f}%   Kailo "
              f"{100 * order[1] / order[2]:.1f}%   ({order[2]:,} pairs)")
    so_, st_ = K.spread(by_race_o), K.spread(by_race_t)
    if so_ is not None:
        print(f"    with each race's own level taken off: ours {so_:.2f}%   Kailo {st_:.2f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--table", default="pred_meet_forecast")
    ap.add_argument("--from", dest="lo", default="2026-08-01")
    ap.add_argument("--to", dest="hi", default="2099-12-31")
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    if not (a.table == "meet_forecast" or (a.table.startswith("pred_")
                                           and a.table.replace("_", "").isalnum())):
        raise SystemExit(f"--table {a.table!r}: meet_forecast or a pred_ name only")
    if a.snapshot:
        snapshot()
    if a.compare:
        from database import getConn
        with getConn() as conn:
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '600s'")
            compare(cur, a.table, a.lo, a.hi, a.show)
            conn.rollback()
    if not (a.snapshot or a.compare):
        ap.print_help()


if __name__ == "__main__":
    main()
