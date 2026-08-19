#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/reference_fit.py
# Purpose: fit course difficulty INDEPENDENTLY of the engine, then diff the two.
#
#     python scripts/reference_fit.py --sport XC
#     python scripts/reference_fit.py --sport TF --season-start 2026-01-01
#
# WHAT THIS IS
# ------------
# A deliberately naive estimator: log-space alternating least squares, one
# athlete effect, one cell effect, a mean-zero anchor, and NOTHING ELSE. No
# form term, no LINK_SHRINK_K, no DAMPING, no thin-cell gate, no decay.
#
# It is not a better engine. It is a CONTROL. Two estimates of the same
# quantity from the same rows should agree, and should agree BETTER as cells
# get thicker. Where they diverge, the difference is the engine's machinery --
# which is exactly the thing that is hard to see from inside the engine.
#
# WHAT IT CAUGHT (2026-07, one XC season, 1.64M rows, 4,917 cells)
#   - Detweiller Park, the fastest course in America, fitted POSITIVE by the
#     engine (+0.020) and NEGATIVE here (-0.011).
#   - Holmdel / Van Cortlandt / Sunken Meadow / Bowdoin, all genuinely brutal,
#     fitted negative by the engine and positive here.
#   - Ultimook +0.207 engine vs +0.068 here.
#   - Engine spread does NOT shrink as cells get thicker (sd 0.064 -> 0.061
#     from 100 to 1500+ rows); the reference spread does (0.043 -> 0.027).
#     That asymmetry is the signature of a systematic term, not sampling noise.
#   - The difference is organised BY STATE: OR/MI/WA inflated ~0.09.
#
# CAVEATS, STATED UP FRONT
#   - One season, no decay, so absolute values here are NOT better than the
#     engine's. Only the COMPARISON is meaningful.
#   - Consumes `normalized_time`, so every L4 correction (distance, geometry,
#     era, weather) is already baked in. This isolates the ENGINE step only.
#   - Restricted to one grade band, because normalized_time carries a
#     pool-dependent multiplier and mixing pools puts two conventions in one fit.

import argparse
import os
import sys

sys.path.insert(0, "scripts")            # database.py lives in scripts/

import numpy as np
import pandas as pd

from database import getConn, initPool, closePool


# ------------------------------------------------------------------ #
# CHUNK 0 — CONSTANTS
# ------------------------------------------------------------------ #

MIN_RACES_PER_ATHLETE = 3      # an athlete with 2 races cannot separate 2 cells
MIN_ROWS_PER_CELL     = 50     # below this a cell is noise, not an estimate
ALS_ITERATIONS        = 60     # converges in ~20; 60 is cheap insurance
DNF_SENTINEL          = 999999

# Only these grades. See the caveat above about pool-dependent normalisation.
HS_GRADES = ("9", "10", "11", "12", "Fr", "So", "Jr", "Sr")

# Courses whose real-world difficulty is known independently of any model.
# These are the closest thing this system has to labelled test data.
# sign: +1 = genuinely hard, -1 = genuinely fast.
TERRAIN_ANCHORS = {
    "Mt. San Antonio College":   +1,
    "Van Cortlandt Park":        +1,
    "Holmdel Park":              +1,
    "Sunken Meadow State Park":  +1,
    "Bowdoin Park":              +1,
    "Detweiller Park":           -1,
    "Woodward Park":             +1,
}


# ------------------------------------------------------------------ #
# CHUNK 1 — LOADING
# ------------------------------------------------------------------ #

# _resultsSql
# Purpose:   the season slice, joined to venue.
# Arguments: sport -- "XC" or "TF"; start, end -- ISO date STRINGS.
# Output:    SQL string.
# Syntax:    `date` is TEXT, so comparisons are lexical -- correct for ISO
#            dates, and the regex guards the 0023/2222 corruption.
#            The meets collapse is split in two on purpose: venue is a property
#            of the MEET, distance a property of the DIVISION. Collapsing both
#            to meet level would merge a varsity 5000 with a frosh 3200.
def _resultsSql(sport, start, end):
    if sport == "XC":
        return f"""
        WITH mc AS (
          SELECT meet_id, min(course_name) AS course_name,
                 min(state) AS state,
                 min(gps_lat) AS gps_lat, min(gps_long) AS gps_long
          FROM meets GROUP BY meet_id
        ),
        md AS (
          SELECT meet_id, div_id, source, min(distance) AS distance
          FROM meets GROUP BY meet_id, div_id, source
        )
        SELECT r.person_id, r.date, r.normalized_time, r.grade,
               mc.course_name, mc.state, mc.gps_lat, mc.gps_long, md.distance
        FROM results r
        JOIN mc USING (meet_id)
        LEFT JOIN md ON md.meet_id = r.meet_id
                    AND md.div_id  = r.div_id
                    AND md.source  = r.source
        WHERE r.normalized_time IS NOT NULL
          AND r.time_seconds < {DNF_SENTINEL}
          AND r.date ~ '^(19|20)[0-9]{{2}}'
          AND r.date >= '{start}' AND r.date < '{end}'
        """
    return f"""
    WITH mt AS (
      -- meets_tf fans out if keyed wrong; the real grain is these four columns
      SELECT meet_id, div_id, event_id, source,
             min(location_id) AS location_id, min(is_indoor) AS is_indoor,
             min(state) AS state
      FROM meets_tf GROUP BY meet_id, div_id, event_id, source
    )
    SELECT r.person_id, r.date, r.normalized_time, r.grade,
           mt.location_id, mt.is_indoor, mt.state
    FROM results_tf r
    JOIN mt ON mt.meet_id  = r.meet_id AND mt.div_id = r.div_id
           AND mt.event_id = r.event_id AND mt.source = r.source
    WHERE r.normalized_time IS NOT NULL
      AND r.time_seconds < {DNF_SENTINEL}
      AND r.is_relay = 0                   -- a relay split is not a race
      AND r.is_field = 0                   -- a shot put mark is not a time
      AND COALESCE(r.exhibition, 0) = 0
      AND r.date ~ '^(19|20)[0-9]{{2}}'
      AND r.date >= '{start}' AND r.date < '{end}'
    """


# _loadResults
# Purpose:   pull the slice into a DataFrame, with a fan-out guard.
# Output:    DataFrame.
# Note:      the guard exists because a wrong-grain join does NOT error -- it
#            returns plausible rows with one column silently all-null. That
#            exact failure cost a full export cycle; never trust a join that
#            has not been checked.
def _loadResults(sport, start, end):
    initPool()
    try:
        with getConn() as conn:
            df = pd.read_sql_query(_resultsSql(sport, start, end), conn)
    finally:
        closePool()

    key = "distance" if sport == "XC" else "location_id"
    if df[key].notna().mean() < 0.5:
        sys.exit(f"ABORT: `{key}` null on >50% of rows -- the join grain is wrong.")
    print(f"[load] {len(df):,} rows")
    return df


# ------------------------------------------------------------------ #
# CHUNK 2 — CELL KEYS
# ------------------------------------------------------------------ #

# _cellKey
# Purpose:   one string per difficulty cell, matching the engine's key.
# Output:    Series of strings.
# ★ WHY COORDINATES. 1,093 XC course names map to more than one canonical_id
#   -- "Riverside Park" to twenty of them. Keying on name alone silently merges
#   twenty different parks. Rounded gps pins the physical site without needing
#   the canonical table.
def _cellKey(df, sport):
    if sport == "TF":
        return df.location_id.astype("Int64").astype(str) + "|" + \
               df.is_indoor.astype("Int64").astype(str)

    snap = (df.distance / 100).round().astype("Int64") * 100   # engine snaps to 100m
    return (df.course_name.astype(str) + "|"
            + df.gps_lat.round(2).fillna(-999).astype(str) + "|"
            + df.gps_long.round(2).fillna(-999).astype(str) + "|"
            + snap.astype(str))


# _seasonOf
# Purpose:   which competition season a date belongs to.
# Arguments: dates -- Series of ISO date STRINGS.
# Output:    Series of ints. A season runs July..June, so 2025-11-22 and
#            2026-02-14 are both season 2025.
# Syntax:    string slicing beats to_datetime here -- the column is TEXT and
#            2.7M parses is slow for something two substrings can do.
def _seasonOf(dates):
    year = dates.str[:4].astype(int)
    month = dates.str[5:7].astype(int)
    return year.where(month >= 7, year - 1)


# _athleteKey
# Purpose:   the unit that gets one ability parameter.
# Arguments: df; mode -- "person" or "person-season".
# Output:    Series of strings.
# ★ WHY THIS MATTERS OVER A MULTI-YEAR WINDOW. One alpha per person assumes an
#   athlete has ONE ability across the whole window. A freshman-to-senior
#   improves 15-20%, so that assumption is badly wrong over four seasons and
#   the misfit has to land somewhere -- most likely in the venues they raced
#   early. "person-season" gives each athlete-year its own ability, which
#   removes the conflation at the cost of weakening cross-season links.
def _athleteKey(df, mode):
    if mode == "person":
        return df.person_id.astype(str)
    return df.person_id.astype(str) + "|" + _seasonOf(df.date).astype(str)


# _decayWeights
# Purpose:   exponential recency weights, matching the engine's decay.
# Arguments: dates; half_life -- in YEARS, or None for unweighted.
# Output:    ndarray[float].
# Syntax:    0.5 ** (age / half_life) -- one half-life halves the weight. The
#            engine's DECAY_K is per-race-day; this is the same curve expressed
#            in years so it can be swept.
def _decayWeights(dates, half_life):
    if half_life is None:
        return None
    season = _seasonOf(dates)
    age = season.max() - season
    return (0.5 ** (age / half_life)).to_numpy(np.float64)


# _trimToCore
# Purpose:   iterate the two quorum rules until both hold simultaneously.
# Output:    trimmed DataFrame.
# Syntax:    dropping thin cells can push an athlete below MIN_RACES, which can
#            then thin a cell, and so on. One pass is not enough; six is past
#            the fixed point on every season tested.
def _trimToCore(df):
    for _ in range(6):
        df = df[df.groupby("person_id").person_id.transform("size")
                >= MIN_RACES_PER_ATHLETE]
        df = df[df.groupby("cellk").cellk.transform("size")
                >= MIN_ROWS_PER_CELL]
    return df


# ------------------------------------------------------------------ #
# CHUNK 3 — THE FIT
# ------------------------------------------------------------------ #

# _alternatingFit
# Purpose:   solve  log(norm) = alpha[athlete] + delta[cell]  by ALS.
# Arguments: y -- log normalised times; ai, ci -- integer codes; na, nc -- sizes.
# Output:    delta, as a RELATIVE difficulty (the (1+d) convention).
#
# THE MATH. The model is multiplicative -- norm = ability x (1+d) x noise -- so
# in logs it is additive and each half-step is a plain group mean. Averaging
# logs is the GEOMETRIC mean, which is the consistent centre for a
# multiplicative model; an arithmetic mean of ratios carries an extra term of
# about half the field's relative variance.
#
# THE ANCHOR. `alpha + delta` is unchanged by adding k to every alpha and
# subtracting it from every delta, so the solve would slide along that ray
# forever. Re-centring delta to a weighted mean of zero each iteration pins it.
# Weighted by row count so a 50-row cell does not outvote a 50,000-row one.
def _alternatingFit(y, ai, ci, na, nc, w=None, trim=False):
    if w is None:
        w = np.ones(len(y))
    cnt_a = np.bincount(ai, weights=w, minlength=na)
    cnt_c = np.bincount(ci, weights=w, minlength=nc)
    delta = np.zeros(nc)
    alpha = np.zeros(na)

    for it in range(ALS_ITERATIONS):
        ww = w
        if trim and it > 0:
            # ★ THE SAME 2-SIGMA CUT, BUT ON LOG RESIDUALS. Race times are
            # right-skewed, so a symmetric cut in SECONDS removes more of the
            # slow tail than the fast one and biases every ability fast. In logs
            # the distribution is near-symmetric and the same cut is fair.
            resid = y - alpha[ai] - delta[ci]
            var = (np.bincount(ai, weights=w * resid * resid, minlength=na)
                   / np.maximum(np.bincount(ai, weights=w, minlength=na), 1e-9))
            sig = np.sqrt(np.maximum(var, 0.0))[ai]
            ww = w * ((np.abs(resid) <= 2.0 * sig) | (sig == 0.0))
            cnt_a = np.bincount(ai, weights=ww, minlength=na)
            cnt_c = np.bincount(ci, weights=ww, minlength=nc)
        alpha = np.bincount(ai, weights=ww * (y - delta[ci]), minlength=na) / np.maximum(cnt_a, 1e-9)
        delta = np.bincount(ci, weights=ww * (y - alpha[ai]), minlength=nc) / np.maximum(cnt_c, 1e-9)
        delta -= np.average(delta, weights=cnt_c)

    resid = y - alpha[ai] - delta[ci]
    print(f"[fit]  residual sd {resid.std():.5f}   delta sd (log) {delta.std():.5f}")
    # expm1 rather than exp(x)-1: exact near zero, and difficulty is small.
    return np.expm1(delta), np.bincount(ci, minlength=nc)


# _ratioFit
# Purpose:   the ENGINE's estimator -- ratio space, ARITHMETIC means -- so the
#            two can be compared with everything else held identical.
# Arguments: norm -- raw normalised times (NOT logs); ai, ci, na, nc; w;
#            trim -- apply the engine's 2-sigma outlier cut if True.
# Output:    (delta as relative difficulty, counts).
#
# ★ THIS IS THE COMPARISON THAT MATTERS. Everything else in this script is held
# fixed -- same rows, same cell key, same anchor, same iteration count. The ONLY
# difference from _alternatingFit is that abilities are an arithmetic mean of
# TIMES rather than a geometric one, and cell effects are an arithmetic mean of
# RATIOS rather than of log differences.
#
# Why that could matter: E[x/y] exceeds E[x]/E[y] by roughly half the relative
# variance of the field, so a cell picks up a term set by how SPREAD OUT its
# runners are, on top of its terrain. And the 2-sigma trim is symmetric in
# seconds while race times are right-skewed -- you can blow up by two minutes,
# you cannot run two minutes faster -- so it cuts more of the slow tail and
# biases every ability fast.
def _ratioFit(norm, ai, ci, na, nc, w=None, trim=False):
    if w is None:
        w = np.ones(len(norm))
    delta = np.zeros(nc)
    cnt_c = np.bincount(ci, weights=w, minlength=nc)

    for it in range(ALS_ITERATIONS):
        adjusted = norm / (1.0 + delta[ci])
        ww = w
        if trim and it > 0:
            mean0 = (np.bincount(ai, weights=w * adjusted, minlength=na)
                     / np.maximum(np.bincount(ai, weights=w, minlength=na), 1e-9))
            resid = adjusted - mean0[ai]
            var = (np.bincount(ai, weights=w * resid * resid, minlength=na)
                   / np.maximum(np.bincount(ai, weights=w, minlength=na), 1e-9))
            sig = np.sqrt(np.maximum(var, 0.0))[ai]
            ww = w * ((np.abs(resid) <= 2.0 * sig) | (sig == 0.0))

        ability = (np.bincount(ai, weights=ww * adjusted, minlength=na)
                   / np.maximum(np.bincount(ai, weights=ww, minlength=na), 1e-9))
        ability = np.clip(ability, 1.0, 1e6)          # the engine's ABILITY_MIN/MAX
        dev = norm / ability[ai] - 1.0
        delta = (np.bincount(ci, weights=w * dev, minlength=nc)
                 / np.maximum(cnt_c, 1e-9))
        delta -= np.average(delta, weights=cnt_c)

    resid = np.log(norm) - np.log(ability[ai] * (1.0 + delta[ci]))
    print(f"[fit]  residual sd {resid.std():.5f}   delta sd {delta.std():.5f}")
    return delta, np.bincount(ci, minlength=nc)


# runReferenceFit
# Purpose:   load -> key -> trim -> fit. The whole estimator.
# Output:    DataFrame of (cellk, d_ref, n, state).
def runReferenceFit(sport, start, end, athlete_key="person", half_life=None,
                    space="log", trim=False):
    df = _loadResults(sport, start, end)
    df = df[df.grade.isin(HS_GRADES)]
    df = df[df.normalized_time.notna() & df.person_id.notna()]
    if sport == "XC":
        df = df[df.distance.notna()]

    df = df.assign(cellk=_cellKey(df, sport))
    df = _trimToCore(df)

    ai, _ = pd.factorize(_athleteKey(df, athlete_key))
    ci, cells = pd.factorize(df.cellk)
    y = np.log(df.normalized_time.to_numpy(np.float64))
    w = _decayWeights(df.date, half_life)
    print(f"[fit]  {len(df):,} rows   {ai.max()+1:,} athletes   {len(cells):,} cells")

    if space == "log":
        d_ref, counts = _alternatingFit(y, ai, ci, ai.max() + 1, len(cells), w, trim)
    else:
        d_ref, counts = _ratioFit(df.normalized_time.to_numpy(np.float64),
                                  ai, ci, ai.max() + 1, len(cells), w, trim)
    state = df.groupby("cellk").state.agg(
        lambda s: s.mode().iloc[0] if len(s.mode()) else None)

    return pd.DataFrame({"cellk": cells, "d_ref": d_ref, "n": counts}) \
             .merge(state.rename("state"), left_on="cellk", right_index=True)


# ------------------------------------------------------------------ #
# CHUNK 4 — THE COMPARISON (this is the point of the script)
# ------------------------------------------------------------------ #

# _loadEngine
# Purpose:   the engine's stored parameter, keyed to match _cellKey.
# Note:      XC cells are 'XC:<name>' with distance in its own column; the
#            reference key also carries coordinates, so the merge is on
#            (name, distance) and ambiguous names are dropped rather than
#            guessed at.
def _loadEngine(sport):
    initPool()
    try:
        with getConn() as conn:
            e = pd.read_sql_query("SELECT * FROM course_difficulties", conn)
    finally:
        closePool()

    e = e[e.course_name.str.startswith(sport + ":")].copy()
    if sport == "TF":
        parts = e.course_name.str.split(":")
        e["cellk"] = parts.str[2] + "|" + (parts.str[3] == "in").astype(int).astype(str)
    else:
        e["venue"] = e.course_name.str[3:]
        # keep the biggest cell where a name is ambiguous; see _cellKey
        e = e.sort_values("n_results", ascending=False) \
             .drop_duplicates(["venue", "distance_m"])
    return e


# _reportBySize
# Purpose:   ★ THE DIAGNOSTIC. Does agreement improve as cells get thicker?
# Note:      two estimates of one quantity must converge as data accumulates.
#            If the engine's sd stays flat while the reference's falls, the
#            engine is carrying a systematic term, not noise.
def _reportBySize(cmp):
    print("\n=== agreement vs cell size ===")
    bins = pd.cut(cmp.n, [0, 200, 500, 1500, 10 ** 9],
                  labels=["100-200", "200-500", "500-1500", "1500+"])
    for label, g in cmp.groupby(bins, observed=True):
        if len(g) < 20:
            continue
        print(f"  n={label:<9} cells={len(g):>5}  corr={g.d_ref.corr(g.difficulty):+.3f}"
              f"   sd_engine={g.difficulty.std():.4f}   sd_ref={g.d_ref.std():.4f}")


# _reportByState
# Purpose:   is the disagreement organised geographically?
def _reportByState(cmp):
    cmp = cmp.assign(drift=cmp.d_ref - cmp.difficulty)
    # ★ NEVER name an aggregation output `engine`: DataFrameGroupBy.agg has a
    # real `engine=` parameter (the numba switch), so pandas swallows it as a
    # keyword and silently DROPS the column instead of raising.
    g = cmp.groupby("state").agg(cells=("drift", "size"), drift=("drift", "mean"),
                                 eng=("difficulty", "mean"), ref=("d_ref", "mean"))
    g = g[g.cells >= 20].sort_values("drift", ascending=False)
    print("\n=== engine vs reference, by state (drift = reference - engine) ===")
    print(g.round(4).to_string())
    print(f"\n  spread of state means: engine {g.eng.std():.4f}"
          f"   reference {g.ref.std():.4f}")
    return g[["drift"]]


# _reportAnchors
# Purpose:   check both estimates against courses whose difficulty is KNOWN.
# Output:    number of anchors the reference gets the right SIGN on, and same
#            for the engine. This is the closest thing to ground truth here.
def _reportAnchors(cmp):
    print("\n=== terrain anchors (known real-world difficulty) ===")
    ok_ref = ok_eng = total = 0
    for venue, sign in TERRAIN_ANCHORS.items():
        rows = cmp[cmp.cellk.str.startswith(venue + "|")]
        if rows.empty:
            continue
        r = rows.nlargest(1, "n").iloc[0]
        total += 1
        ok_ref += (np.sign(r.d_ref) == sign)
        ok_eng += (np.sign(r.difficulty) == sign)
        flag = "  <-- ENGINE SIGN WRONG" if np.sign(r.difficulty) != sign else ""
        print(f"  {venue:<26} ref={r.d_ref:+.4f}  engine={r.difficulty:+.4f}"
              f"  expect {'hard' if sign > 0 else 'fast'}{flag}")
    print(f"\n  correct signs: reference {ok_ref}/{total}   engine {ok_eng}/{total}")
    return ok_ref, ok_eng, total


# _crossSportDrift
# Purpose:   ★ THE STRONGEST DIAGNOSTIC. XC and TF are fit from separate tables,
#            separate cells, separate athletes. A real geographic effect has no
#            reason to be identical in both. An ALGORITHMIC drift does.
# Output:    None; prints the correlation once both sports have been run.
def _crossSportDrift(sport):
    other = "TF" if sport == "XC" else "XC"
    path = f"exports/state_drift_{other}.csv"
    if not os.path.exists(path):
        print(f"\n  (run --sport {other} too for the cross-sport check)")
        return
    a = pd.read_csv(f"exports/state_drift_{sport}.csv", index_col=0)
    b = pd.read_csv(path, index_col=0)
    j = a.join(b, lsuffix="_a", rsuffix="_b").dropna()
    r = j.iloc[:, 0].corr(j.iloc[:, 1])
    print(f"\n=== cross-sport drift: corr(XC, TF) = {r:+.3f} over {len(j)} states ===")
    if r > 0.7:
        print("  >0.7 means the drift is a property of the SOLVER, not of geography.")


def compareToEngine(ref, sport):
    e = _loadEngine(sport)
    if sport == "TF":
        cmp = ref.merge(e[["cellk", "difficulty", "n_results"]], on="cellk")
    else:
        parts = ref.cellk.str.rsplit("|", n=3, expand=True)
        ref = ref.assign(venue=parts[0], distance_m=parts[3].astype(float))
        cmp = ref.merge(e[["venue", "distance_m", "difficulty", "n_results"]],
                        on=["venue", "distance_m"])

    cmp = cmp[cmp.n >= 100]
    print(f"\n=== {len(cmp):,} cells in both ===")
    print(f"  engine     sd={cmp.difficulty.std():.4f}  mean={cmp.difficulty.mean():+.4f}")
    print(f"  reference  sd={cmp.d_ref.std():.4f}  mean={cmp.d_ref.mean():+.4f}")
    print(f"  corr = {cmp.d_ref.corr(cmp.difficulty):+.3f}")

    _reportBySize(cmp)
    drift = _reportByState(cmp)
    drift.to_csv(f"exports/state_drift_{sport}.csv")
    _crossSportDrift(sport)
    return _reportAnchors(cmp) if sport == "XC" else (0, 0, 0)


# ------------------------------------------------------------------ #
# CHUNK 5 — ENTRY POINT
# ------------------------------------------------------------------ #

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sport", choices=["XC", "TF"], default="XC")
    p.add_argument("--season-start", default="2025-07-01")
    p.add_argument("--season-end",   default="2026-07-01")
    p.add_argument("--out", default="exports/reference_fit.csv")
    p.add_argument("--athlete-key", choices=["person", "person-season"],
                   default="person",
                   help="one ability per athlete, or one per athlete-season")
    p.add_argument("--half-life", type=float, default=None,
                   help="recency decay half-life in YEARS (default: none)")
    p.add_argument("--space", choices=["log", "ratio"], default="log",
                   help="ratio = the engine's arithmetic-mean-of-ratios estimator")
    p.add_argument("--trim", action="store_true",
                   help="apply the engine's symmetric 2-sigma outlier cut")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 if the engine gets fewer anchor signs right "
                        "than the reference -- for CI")
    args = p.parse_args()

    ref = runReferenceFit(args.sport, args.season_start, args.season_end,
                          args.athlete_key, args.half_life,
                          args.space, args.trim)
    ok_ref, ok_eng, total = compareToEngine(ref, args.sport)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    ref.to_csv(args.out, index=False)
    print(f"\n[out]  {args.out}  ({len(ref):,} cells)")

    if args.strict and total and ok_eng < ok_ref:
        sys.exit(f"FAIL: engine {ok_eng}/{total} anchor signs, reference {ok_ref}/{total}")


if __name__ == "__main__":
    main()