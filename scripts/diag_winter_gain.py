"""
diag_winter_gain.py -- how much of the fall-to-spring change is fitness,
measured WITHOUT crossing sports (issues 113, 143). One-off.

    /srv/venv/bin/python scripts/diag_winter_gain.py
    /srv/venv/bin/python scripts/diag_winter_gain.py --npz engine/data/joint_difficulty.npz --since 2018

The idea. The engine cannot separate the track-versus-XC surface level from
the average athlete's winter fitness gain: both move every row the same way.
Same-sport comparisons carry no surface term, so:

    annual gain A      = same athlete, same sport, season Y -> Y+1, in log
                         rating (ratings are relative to a stationary pool
                         mean, so this is the athlete's own improvement)
    in-season gain W   = the joint curve's move from a season's typical first
                         race day to its last, per sport (log-time, negated)
    off-season O       = A - W_xc - W_tf   (winter + summer)
    winter share       = by the measured gap lengths (last XC race -> first
                         track race, last track race -> first XC race), and
                         two alternatives, since "summer holds slightly more
                         as it is longer and earlier" (owner) is a judgement
                         the durations only partly capture

The number to carry into the run is `--winter-gain` = O x winter share,
printed per pool and rows-weighted overall.

Known flaws, stated: the athletes present in two consecutive seasons are the
ones who kept running, and they improve more than the ones who stopped; the
annual gain mixes grades (a freshman's year is not a senior's); the curve is
evaluated at rating 100 (amp = 1). Read the number as a range, not a point.
"""
import argparse
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "racecast")
sys.path.insert(0, "engine")

from joint_solve import academicDay                               # noqa: E402

_SQL = """
SELECT r.person_id, rr.pool,
       (CASE WHEN substring(r.date, 6, 2)::int >= 8
             THEN substring(r.date, 1, 4)::int
             ELSE substring(r.date, 1, 4)::int - 1 END)   AS season,
       avg(ln(r.speed_rating))                            AS lr,
       count(*)                                           AS n,
       min(r.date)                                        AS first_date,
       max(r.date)                                        AS last_date
FROM   {table} r
JOIN   ranking_results rr ON rr.sport = %(sport)s AND rr.result_id = r.result_id
WHERE  r.speed_rating IS NOT NULL AND r.speed_rating > 0
  AND  r.person_id IS NOT NULL
  AND  r.date >= %(since)s
  AND  rr.pool NOT LIKE '%%unknown%%'
GROUP  BY 1, 2, 3
HAVING count(*) >= 2
"""


# ---------------------------------------------------------------- pure -- #

def _doy(date_text):
    """Day of year from 'YYYY-MM-DD' (1..366), no datetime per row."""
    import datetime as dt
    d = dt.date(int(date_text[:4]), int(date_text[5:7]), int(date_text[8:10]))
    return d.timetuple().tm_yday, d


def curveAt(curve_row, knot_days, aday):
    """Piecewise-linear value of one pool's curve at academic day(s)."""
    return np.interp(np.asarray(aday, dtype=np.float64), knot_days, curve_row)


def annualGains(rows):
    """rows: dicts with person_id, pool, sport, season, lr, n.
    Returns {(pool, sport): array of lr[Y+1] - lr[Y] over consecutive
    seasons of one person}."""
    by = defaultdict(dict)
    for r in rows:
        by[(r["person_id"], r["pool"], r["sport"])][int(r["season"])] = r["lr"]
    out = defaultdict(list)
    for (pid, pool, sport), seasons in by.items():
        for y, lr in seasons.items():
            nxt = seasons.get(y + 1)
            if nxt is not None:
                out[(pool, sport)].append(float(nxt) - float(lr))
    return {k: np.asarray(v) for k, v in out.items()}


def gapDays(rows):
    """Median winter (last XC -> first TF, same season) and summer (last TF
    -> first XC of the next season) lengths in days, per pool, and the
    count of athlete-seasons behind each."""
    xc, tf = {}, {}
    for r in rows:
        key = (r["person_id"], r["pool"], int(r["season"]))
        (xc if r["sport"] == "XC" else tf)[key] = r
    winter, summer = defaultdict(list), defaultdict(list)
    for key, rx in xc.items():
        rt = tf.get(key)
        if rt is not None:
            _, d_last_xc = _doy(rx["last_date"])
            _, d_first_tf = _doy(rt["first_date"])
            winter[key[1]].append((d_first_tf - d_last_xc).days)
        rx_next = xc.get((key[0], key[1], key[2] + 1))
        rt_this = tf.get(key)
        if rt_this is not None and rx_next is not None:
            _, d_last_tf = _doy(rt_this["last_date"])
            _, d_first_xc = _doy(rx_next["first_date"])
            summer[key[1]].append((d_first_xc - d_last_tf).days)
    out = {}
    for pool in set(winter) | set(summer):
        w = np.asarray(winter.get(pool, []))
        s = np.asarray(summer.get(pool, []))
        w = w[(w > 20) & (w < 250)] if w.size else w
        s = s[(s > 20) & (s < 250)] if s.size else s
        out[pool] = {"winter_days": float(np.median(w)) if w.size else np.nan,
                     "summer_days": float(np.median(s)) if s.size else np.nan,
                     "n_winter": int(w.size), "n_summer": int(s.size)}
    return out


def inSeasonGain(curve_row, knot_days, first_aday, last_aday):
    """Fitness gained across a season window: -(f(last) - f(first))."""
    return float(-(curveAt(curve_row, knot_days, last_aday)
                   - curveAt(curve_row, knot_days, first_aday)))


def splitOffSeason(off, winter_days, summer_days):
    """The winter's share of the off-season gain under three rules."""
    tot = winter_days + summer_days
    dur = winter_days / tot if tot and np.isfinite(tot) else 0.5
    return {"half": 0.5 * off,
            "by duration": dur * off,
            "summer-heavy (40/60)": 0.4 * off}


# ------------------------------------------------------------- driver -- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", default=os.path.join("engine", "data",
                                                  "joint_difficulty.npz"))
    ap.add_argument("--since", default="2018-08-01")
    a = ap.parse_args()

    with np.load(a.npz, allow_pickle=False) as z:
        if "curve" not in z.files:
            sys.exit(f"{a.npz} has no curve; run with the year curve on")
        curve = z["curve"]
        knots = z["curve_knot_days"]
        pool_names = [str(p) for p in z["pool_names"]]
        mu = z["mu"]
    print(f"[curve] {a.npz}: pools {pool_names}, level mu[TF]-mu[XC] "
          f"{float(mu[1] - mu[0]):+.4f}")

    from database import getConn
    rows = []
    with getConn() as conn, conn.cursor() as cur:
        for table, sport in (("results", "XC"), ("results_tf", "TF")):
            cur.execute(_SQL.format(table=table),
                        {"sport": sport, "since": a.since})
            for rec in cur.fetchall():
                if isinstance(rec, dict):
                    r = dict(rec)
                else:
                    r = dict(zip(("person_id", "pool", "season", "lr", "n",
                                  "first_date", "last_date"), rec))
                r["sport"] = sport
                r["pool"] = str(r["pool"]).split("|", 1)[0]
                rows.append(r)
            print(f"[db] {sport}: {sum(1 for r in rows if r['sport'] == sport):,} "
                  f"athlete-seasons since {a.since}")

    gains = annualGains(rows)
    gaps = gapDays(rows)

    # typical season windows per (pool, sport): median first/last academic day
    win = defaultdict(lambda: {"first": [], "last": []})
    for r in rows:
        f_doy, _ = _doy(r["first_date"])
        l_doy, _ = _doy(r["last_date"])
        win[(r["pool"], r["sport"])]["first"].append(float(academicDay(f_doy)))
        win[(r["pool"], r["sport"])]["last"].append(float(academicDay(l_doy)))

    print()
    print(f"{'pool':<10} {'A_xc':>7} {'A_tf':>7} {'A':>7} {'W_xc':>7} {'W_tf':>7} "
          f"{'off':>7} {'win_d':>6} {'sum_d':>6} {'half':>7} {'dur':>7} {'40/60':>7}"
          f"   n_pairs")
    overall = defaultdict(float)
    weight_sum = 0.0
    for pool in pool_names:
        p = pool_names.index(pool)
        g_xc = gains.get((pool, "XC"), np.zeros(0))
        g_tf = gains.get((pool, "TF"), np.zeros(0))
        if g_xc.size < 200 or g_tf.size < 200:
            continue
        A_xc, A_tf = float(np.median(g_xc)), float(np.median(g_tf))
        n_pairs = g_xc.size + g_tf.size
        A = (A_xc * g_xc.size + A_tf * g_tf.size) / n_pairs
        W = {}
        for sport in ("XC", "TF"):
            w = win.get((pool, sport))
            if not w or not w["first"]:
                W[sport] = np.nan
                continue
            W[sport] = inSeasonGain(curve[p], knots, np.median(w["first"]),
                                    np.median(w["last"]))
        off = A - W["XC"] - W["TF"]
        gd = gaps.get(pool, {"winter_days": np.nan, "summer_days": np.nan})
        s = splitOffSeason(off, gd["winter_days"], gd["summer_days"])
        print(f"{pool:<10} {A_xc:+7.4f} {A_tf:+7.4f} {A:+7.4f} {W['XC']:+7.4f} "
              f"{W['TF']:+7.4f} {off:+7.4f} {gd['winter_days']:6.0f} "
              f"{gd['summer_days']:6.0f} {s['half']:+7.4f} "
              f"{s['by duration']:+7.4f} {s['summer-heavy (40/60)']:+7.4f}"
              f"   {n_pairs:,}")
        for k, v in s.items():
            overall[k] += v * n_pairs
        weight_sum += n_pairs
    print()
    if weight_sum:
        print("rows-weighted --winter-gain candidates (log-time; 0.02 = 2%):")
        for k, v in overall.items():
            print(f"    {k:<22} {v / weight_sum:+.4f}")
    print("\nColumns: A = same-sport annual gain in log rating (median of "
          "person-pairs); W = the curve's in-season gain per sport; off = "
          "A - W_xc - W_tf; win_d/sum_d = median gap lengths in days; the "
          "last three = the winter's share under each split rule.")
    print("Survivors only, grades mixed, curve at rating 100. A range, "
          "not a point.")


if __name__ == "__main__":
    main()
