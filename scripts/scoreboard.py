#!/usr/bin/env python3
"""
scoreboard.py -- the ratings' standing tests, every run, against the last.
READ ONLY on the database; appends one line to engine/data/scoreboard.jsonl.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/scoreboard.py [--log-dir logs/<run>]

★ WHY (owner, 2026-10-18: "the ratings just feel off" -- and a model change
  is only better if the numbers say so). Three checks on the PUBLISHED
  ratings, each with a known right answer, plus the owner's known cases:

  course credit   inside one runner's season, does a harder course or a
                  "slower" day rate higher? Right answer: b = 0 in every
                  ability band (scripts/diag_course_credit.py).
  fall pairs      a track race against the same runner's XC that fortnight.
                  Right answer: median 0, overall, by distance and by level
                  (scripts/diag_fall_pairs.py).
  altitude        a visitor's / a resident's altitude races against their
                  own sea-level ones. Right answer: b = 0 for both
                  (scripts/diag_altitude_credit.py).
  known cases     races the owner flagged, as the race's rating against the
                  runner's other races that season (0 = in line).

  Each number is printed beside the previous run's, so a change that makes
  any of them worse shows the morning after. Samples are fixed (the same
  1-in-N runners every run) so run-to-run moves are the model, not the draw.
"""
import argparse
import datetime
import json
import math
import os
import statistics
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import diag_course_credit as cc                                  # noqa: E402
import diag_fall_pairs as fp                                     # noqa: E402
import diag_altitude_credit as alt                               # noqa: E402

HISTORY = os.path.join(_ROOT, "engine", "data", "scoreboard.jsonl")
BANDS = cc.BANDS

# The owner's flagged races (result ids), with why. Their rating against the
# same runner's other XC races that season, in percent.
KNOWN = (
    (59538167, "Mt. SAC 2024, Tadhg Murray (day +3.4% at 70F)"),
    (59538147, "Mt. SAC 2024, Trey Caldwell"),
    (74224789, "Furrow Farm 2026, Cuyler Swanson (course +16.8%)"),
)


def bandLabel(lo, hi):
    return f"<{hi}" if lo == 0 else (f"{lo}+" if hi == 999 else f"{lo}-{hi}")


def courseCredit(cur, since, sample):
    rows = cc.load(cur, since, sample, None, say=lambda m: None, extra=True)
    if not rows:
        return {}, rows
    pid = np.array([r[0] for r in rows], dtype=np.int64)
    pool = np.array([r[1] for r in rows])
    yr = np.array([r[2] for r in rows], dtype=np.int64)
    y = np.array([r[3] for r in rows])
    X = np.column_stack([[r[4] for r in rows], [r[5] for r in rows],
                         np.array([r[6] for r in rows]) / 100.0])
    _, grp = np.unique(np.char.add(np.char.add(pid.astype(str), pool), yr.astype(str)),
                       return_inverse=True)
    k = int(grp.max()) + 1
    lvl = np.exp(np.bincount(grp, weights=y, minlength=k) / np.bincount(grp, minlength=k))[grp]
    out = {}
    b, _se, n = cc.withinFit(grp, y, X)
    if b is not None:
        out["all"] = {"b_course": round(float(b[0]), 4), "b_day": round(float(b[1]), 4), "n": n}
    for lo, hi in zip(BANDS, BANDS[1:]):
        m = (lvl >= lo) & (lvl < hi)
        b, _se, n = cc.withinFit(grp[m], y[m], X[m])
        if b is not None:
            out[bandLabel(lo, hi)] = {"b_course": round(float(b[0]), 4),
                                      "b_day": round(float(b[1]), 4), "n": n}
    return out, rows


def altitudeCredit(cur, rows):
    cur.execute("SELECT key, elevation_m FROM venue_elevation WHERE key LIKE 'XC:%%'")
    elev = {int(k[3:]): float(e) for k, e in cur.fetchall() if k[3:].isdigit()}
    keep = [r for r in rows if r[7] in elev]
    if not keep:
        return {}
    pid = np.array([r[0] for r in keep], dtype=np.int64)
    pool = np.array([r[1] for r in keep])
    yr = np.array([r[2] for r in keep], dtype=np.int64)
    y = np.array([r[3] for r in keep])
    km = np.array([max(elev[r[7]] - alt.ALT_FLOOR_M, 0.0) / 1000.0 for r in keep])
    X = np.column_stack([km, [r[4] for r in keep], [r[5] for r in keep],
                         np.array([r[6] for r in keep]) / 100.0])
    _, grp = np.unique(np.char.add(np.char.add(pid.astype(str), pool), yr.astype(str)),
                       return_inverse=True)
    home = alt.homeKm(grp, km)
    out = {}
    for name, m in (("visitors", home < 0.3), ("residents", home >= 0.7)):
        b, _se, n = cc.withinFit(grp[m], y[m], X[m])
        if b is not None:
            out[name] = {"b_alt": round(float(b[0]), 4), "n": n}
    return out


def fallPairs(cur, before):
    cur.execute(fp._SQL, {"days": 14, "pool": None, "before": before})
    pairs = []
    for pool, m, _yr, r, xr, _n in cur.fetchall():
        b = fp.bucket(float(m))
        if b and r and xr:
            pairs.append((pool, b, math.sqrt(float(r) * float(xr)),
                          math.log(float(r) / float(xr))))
    if not pairs:
        return {}
    out = {"all": round(100 * statistics.median(g for *_x, g in pairs), 2), "n": len(pairs)}
    for k, n, med, _lo, _hi in fp.summarise([(b, g) for _p, b, _l, g in pairs]):
        if n >= 30:
            out[k] = round(med, 2)
    lv = sorted(l for _p, _b, l, _g in pairs)
    cut = [lv[int(q * (len(lv) - 1))] for q in (0.25, 0.5, 0.75)]
    band = lambda l: ("Q1" if l < cut[0] else "Q2" if l < cut[1] else "Q3" if l < cut[2] else "Q4")
    for k, n, med, _lo, _hi in fp.summarise([(band(l), g) for _p, _b, l, g in pairs]):
        out[k] = round(med, 2)
    return out


def knownCases(cur):
    out = {}
    for rid, why in KNOWN:
        cur.execute("""SELECT person_id, pool, year, speed_rating FROM ranking_results
                       WHERE result_id = %s AND sport = 'XC'""", (rid,))
        row = cur.fetchone()
        if not row:
            out[why] = None
            continue
        pid, pool, yr, r = row
        cur.execute("""SELECT exp(avg(ln(speed_rating))) FROM ranking_results
                       WHERE person_id = %s AND pool = %s AND year = %s AND sport = 'XC'
                         AND result_id <> %s AND speed_rating > 0""", (pid, pool, yr, rid))
        other = cur.fetchone()[0]
        out[why] = {"rating": round(float(r), 1),
                    "vs_season_pct": (round(100 * (float(r) / float(other) - 1), 2)
                                      if other else None)}
    return out


def lastRun():
    try:
        with open(HISTORY, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + " / "))
        else:
            out[key] = v
    return out


def render(now, prev):
    a, b = flatten(now), flatten(prev or {})
    lines = [f"{'test':<62}{'now':>10}{'last run':>10}"]
    for k, v in a.items():
        if k.startswith("meta") or (k.endswith("/ n") or k == "n"):
            continue
        p = b.get(k)
        # slopes (b_*) to 4 places, percents to 2, ratings as stored
        fmt = lambda x: ("-" if x is None else
                         f"{x:+.4f}" if "/ b_" in k else
                         f"{x:+.2f}" if ("pct" in k and isinstance(x, (int, float))) else str(x))
        lines.append(f"{k:<62}{fmt(v):>10}{fmt(p):>10}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir")
    ap.add_argument("--since", type=int, default=2023)
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    from database import getConn
    t0 = time.time()
    today = datetime.date.today()
    before = None          # every autumn in full: stable run to run
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        credit, rows = courseCredit(cur, a.since, a.sample)
        print(f"[scoreboard] course credit: {len(rows):,} races ({time.time() - t0:.0f}s)", flush=True)
        altitude = altitudeCredit(cur, rows) if rows else {}
        pairs = fallPairs(cur, before)
        print(f"[scoreboard] fall pairs: {pairs.get('n', 0):,} ({time.time() - t0:.0f}s)", flush=True)
        known = knownCases(cur)
        conn.rollback()
    now = {"meta": {"date": today.isoformat(), "log_dir": a.log_dir or "",
                    "since": a.since, "sample": a.sample, "before": before},
           "course_credit": credit, "altitude": altitude,
           "fall_pairs_pct": pairs, "known_cases": known}
    prev = lastRun()
    text = render(now, prev)
    print(text)
    if not a.no_write:
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(now) + "\n")
        if a.log_dir:
            with open(os.path.join(a.log_dir, "SCOREBOARD.txt"), "w", encoding="utf-8") as f:
                f.write(text + "\n")
    print(f"[scoreboard] done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
