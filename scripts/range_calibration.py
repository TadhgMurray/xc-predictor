#!/usr/bin/env python3
"""
range_calibration.py -- make the race model's ranges mean what they say,
from its own forecasts frozen before the race (pred_meet_forecast, the
pre-race recorder) against what happened.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/range_calibration.py --dry-run        # measure, write nothing
    $P scripts/range_calibration.py                  # and append the factor

★ WHY (owner's run, 2026-11-02). On 187 races frozen before the gun, 85% of
  runners finished inside the race model's +-1 sigma range, which should
  hold 68%. The ranges are fitted on the held-out season; the served trees
  are then refit on every season (race_trees --refit-all) and miss less.

★ THE FACTOR. For every runner we listed who finished: z = |ln(actual /
  predicted)| over the UNSCALED half-width (the stored range divided by the
  factor that was in force the day it was made, race_model.scaleOn). The
  68.27th percentile of z is the factor that makes +-1 sigma cover 68%. It
  is appended to race_model.CAL_PATH as {"from": today, "k": ...}; every
  range made from today is scaled by it (race_model.rangeScale).

! ONLY ON ENOUGH: at least MIN_RACES races and MIN_RUNNERS runners in the
  window, and the factor is kept within [K_MIN, K_MAX]. Otherwise it says so
  and writes nothing. READ ONLY on the database.
"""
import argparse
import datetime
import json
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_RACES, MIN_RUNNERS = 50, 2000
K_MIN, K_MAX = 0.5, 1.5


def zScores(fc, finishers, history):
    """|ln(actual / predicted)| over the unscaled half-width, for each runner
    of one frozen race who finished. Pure."""
    import race_model as M
    k0 = M.scaleOn(history, str(fc.get("made_on"))[:10])
    act = {}
    for f in finishers:
        if f.get("person_id") is not None and f["person_id"] not in act:
            act[f["person_id"]] = float(f["time_seconds"])
    out = []
    for r in fc.get("runners") or []:
        a, s, hi = act.get(r.get("person_id")), r.get("seconds"), r.get("hi")
        if a and s and hi and float(hi) > float(s) > 0:
            w = math.log(float(hi) / float(s)) / k0
            out.append(abs(math.log(a / float(s))) / w)
    return out


def factor(zs):
    """The 68.27th percentile of z, kept within [K_MIN, K_MAX]. Pure."""
    zs = sorted(zs)
    q = zs[min(len(zs) - 1, int(0.6827 * len(zs)))]
    return min(K_MAX, max(K_MIN, q))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=28, help="races in the last N days")
    ap.add_argument("--table", default="pred_meet_forecast")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.table.startswith("pred_") or not a.table.replace("_", "").isalnum():
        raise SystemExit(f"--table {a.table!r}: a pred_ table only")

    import psycopg2.extras
    import race_model as M
    import meet_forecast as MF
    from database import getConn
    from meet_recap import actualByDiv

    try:
        with open(M.CAL_PATH) as fh:
            cal = json.load(fh)
    except (OSError, ValueError):
        cal = {"history": []}
    today = datetime.date.today()
    lo = (today - datetime.timedelta(days=a.days)).isoformat()
    zs, races = [], 0
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        cur.execute(f"""SELECT source, meet_id, div_id, meet_date, made_on, runners,
                               teams, state, model_basis, model_version
                        FROM   {a.table}
                        WHERE  meet_date >= %s AND meet_date < CURRENT_DATE
                          AND  model_basis = 'race'""", (lo,))
        stored = [MF._rowOut(r) for r in cur.fetchall()]
        by_meet = {}
        for fc in stored:
            by_meet.setdefault((fc["source"], int(fc["meet_id"])), []).append(fc)
        for (src, mid), fcs in by_meet.items():
            try:
                actual = actualByDiv(cur, mid, src)
            except Exception as exc:                 # noqa: BLE001
                conn.rollback()
                print(f"  {src}:{mid}: {exc}")
                continue
            for fc in fcs:
                z = zScores(fc, actual.get(int(fc["div_id"])) or [], cal.get("history"))
                if len(z) >= 8:
                    zs += z
                    races += 1
        conn.rollback()

    print(f"{races} races, {len(zs):,} runners since {lo} ({a.table}, race basis)")
    if races < MIN_RACES or len(zs) < MIN_RUNNERS:
        print(f"  too few to calibrate on (need {MIN_RACES} races and {MIN_RUNNERS:,} runners)")
        return
    inside = sum(z <= 1.0 for z in zs) / len(zs)
    k = factor(zs)
    print(f"  unscaled +-1 sigma holds {100 * inside:.0f}% (honest = 68%); "
          f"the factor that makes it 68%: {k:.3f} "
          f"(in force now: {M.scaleOn(cal.get('history'), today.isoformat()):.3f})")
    if a.dry_run:
        return
    cal.setdefault("history", []).append({"from": today.isoformat(), "k": round(k, 4),
                                          "races": races, "runners": len(zs),
                                          "window_from": lo})
    tmp = M.CAL_PATH + ".new"
    with open(tmp, "w") as fh:
        json.dump(cal, fh, indent=1)
    os.replace(tmp, M.CAL_PATH)
    print(f"  wrote {M.CAL_PATH}: ranges made from {today} are scaled by {k:.3f}")


if __name__ == "__main__":
    main()
