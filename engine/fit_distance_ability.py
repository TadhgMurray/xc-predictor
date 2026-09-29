# Project: xc-predictor
# File:    engine/fit_distance_ability.py  (2026-09-29)
# Purpose: Fit the distance curve BY ABILITY: one family per (gender, sport),
#          its shape a smooth function of how fast the runner is, from every
#          pool's same-athlete pairs. Writes engine/data/distance_ability.pkl
#          -- a NEW artifact; engine/data/distance_spline.pkl is never read
#          for writing, never touched. The evaluator is engine/
#          distance_ability.py; normalize_distance uses it only under
#          XCP_DISTANCE_BY=ability.
#
# ★ WHY (owner, 2026-09-29, approved "try to be safe and test it"). One run,
#   28:34 over Gans Creek's 10000 m, read HS-equivalent 156.6 / 150.0 /
#   147.6 as hs_m / college_m / ms_m. Most of the gap was the per-POOL
#   distance curve: 10000 -> 5000 is x0.4623 on hs_m's and x0.4855 on
#   college_m's, and hs_m's is an extrapolation past its 2.8k-5k data. How
#   much a runner slows over distance should depend on their speed, not on
#   their grade; fitted by ability, the college 10K pairs inform the fast
#   high schooler's 10K, and no pool is read off its own edge.
#
#     /srv/venv/bin/python engine/fit_distance_ability.py             # fit, report, write
#     /srv/venv/bin/python engine/fit_distance_ability.py --dry-run   # fit and report only
#
# THE PAIRS ARE THE POOL FITTER'S, UNCHANGED: fit_distance_exponent.
# loadAllPairs -- same athlete, two distances, <= 21 days apart, one pair
# per (athlete, transition, season), raw times, every guard and override
# already applied, cached in engine/data/distance_pairs_cache.pkl. Two
# fitters on one sample, so a difference between the artifacts is the
# model, never the data.
#
# THE MODEL (see engine/distance_ability.py for the evaluator):
#   y = log(t2/t1) = g(x2; A) - g(x1; A) + k0,      x = log(d / 5000)
#   g(x; A)        = sum_j alpha_j x^j + A * sum_j beta_j x^j
#   A              = the pair's ability, clamped to the range the pairs cover
#   k0             = the calendar offset (race 2 is the LATER race, so k0 is
#                    what the extra days of fitness are worth; the pool
#                    fitter's eps in its own orientation)
#
# ★ THE PAIR'S ABILITY IS THE MEAN OF ITS TWO LEGS' 5K EQUIVALENTS, NOT THE
#   FIRST LEG'S. With leg noises e1, e2 the response carries e2 - e1 and the
#   symmetric index carries (e1 + e2)/2; their covariance is
#   (var e2 - var e1)/2 = 0. Indexed by one leg, the index and the response
#   would share that leg's noise and the fit would find an ability effect
#   in pure regression to the mean.
#
# ★ NOTHING HERE IS A HAND CONSTANT; each is derived and printed:
#   ref_5k        the family's high-school median 5K equivalent (the index's
#                 zero is the typical high schooler of that gender)
#   a_lo, a_hi    the 1st / 99th percentiles of the pairs' abilities: past
#                 them the curve holds its edge shape instead of
#                 extrapolating a slope in ability
#   x_lo, x_hi    the 0.5 / 99.5 percentiles of the endpoints: the distance
#                 span that holds 99% of the evidence; beyond it, the
#                 boundary slope (the pool artifact's extrapolation policy)
#   degrees       chosen by 5-fold cross-validation over alpha 1..3 and
#                 beta 0..2, the simplest within one standard error of the
#                 best (the standard one-SE rule)
#   TUKEY_C       4.685, the bisquare's 95%-efficiency constant, as the pool
#                 fitter uses it

import math
import os
import pickle
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))

import distance_ability as DA                                   # noqa: E402

OUTPUT_FILE = os.path.join(_HERE, "data", "distance_ability.pkl")

TUKEY_C = 4.685              # bisquare, 95% Gaussian efficiency (mirrors
                             # fit_distance_exponent.TUKEY_C)
IRLS_ITERS = 8               # reweight rounds per outer pass; the fit
                             # stops early once coefficients stop moving
OUTER_ITERS = 4              # ability <-> curve alternations; the index
                             # moves the shape at second order, so it
                             # settles in two or three (printed)
ALPHA_DEGREES = (1, 2, 3)    # the pool fitter's cap is a cubic (its
                             # "rigidity is structural" note)
BETA_DEGREES = (0, 1, 2)
CV_FOLDS = 5
CV_MAX_PAIRS = 300_000       # per family. The CV loss's standard error at
                             # n pairs is sd/sqrt(n): at 300k it is ~0.2%
                             # of the loss, far under any degree difference
                             # the rule acts on (printed beside each score)
MIN_FAMILY_PAIRS = 500       # = fit_distance_exponent.MIN_PAIRS_FOR_POOL_SPLINE:
                             # below this a pool curve was not fitted either
ABILITY_PCT = (1.0, 99.0)
SPAN_PCT = (0.5, 99.5)
SEED = 20260929


# ------------------------------------------------------------------ #
# ARRAYS
# ------------------------------------------------------------------ #

def pairArrays(pairs):
    """Pair dicts ({pool, distance1, distance2, time1, time2}) -> arrays."""
    n = len(pairs)
    pool = np.empty(n, dtype=object)
    d1 = np.empty(n); d2 = np.empty(n); t1 = np.empty(n); t2 = np.empty(n)
    for i, p in enumerate(pairs):
        pool[i] = p["pool"]
        d1[i], d2[i] = p["distance1"], p["distance2"]
        t1[i], t2[i] = p["time1"], p["time2"]
    ok = (d1 > 0) & (d2 > 0) & (t1 > 0) & (t2 > 0)
    return {"pool": pool[ok], "d1": d1[ok], "d2": d2[ok],
            "t1": t1[ok], "t2": t2[ok]}


def concatArrays(parts):
    parts = [p for p in parts if p and len(p["d1"])]
    if not parts:
        return {"pool": np.empty(0, dtype=object), "d1": np.empty(0),
                "d2": np.empty(0), "t1": np.empty(0), "t2": np.empty(0)}
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def subset(arr, mask):
    return {k: v[mask] for k, v in arr.items()}


def genderMask(arr, g):
    if g == "u":
        return np.ones(len(arr["d1"]), dtype=bool)
    suf = "_" + g
    return np.array([str(p).split("|")[0].endswith(suf) for p in arr["pool"]],
                    dtype=bool)


def hsMask(arr):
    return np.array([str(p).startswith("hs_") for p in arr["pool"]], dtype=bool)


# ------------------------------------------------------------------ #
# THE BASIS  (the evaluator's continuation, written on unit coefficients)
# ------------------------------------------------------------------ #

def basis(x, lo, hi, J):
    """n x J: column j is x^j inside [lo, hi] and its boundary tangent
    outside -- DA._pq on the unit coefficient vectors, vectorised, so the
    fit and the evaluator are one function of the coefficients."""
    x = np.asarray(x, dtype=np.float64)
    xb = np.clip(x, lo, hi)
    dx = x - xb
    out = np.empty((x.size, J))
    for j in range(1, J + 1):
        out[:, j - 1] = xb ** j + j * xb ** (j - 1) * dx
    return out


def _pqArrays(alpha, beta, x, lo, hi):
    P = basis(x, lo, hi, len(alpha)) @ np.asarray(alpha) if len(alpha) else np.zeros(np.size(x))
    Q = basis(x, lo, hi, len(beta)) @ np.asarray(beta) if len(beta) else np.zeros(np.size(x))
    return P, Q


def pairAbility(alpha, beta, lref, a_lo, a_hi, x1, x2, lt1, lt2, lo, hi):
    """Each pair's clamped ability, the symmetric index (header), closed form:
    a (1 + (Q1+Q2)/2) = (lt1+lt2)/2 - (P1+P2)/2 - lref."""
    P1, Q1 = _pqArrays(alpha, beta, x1, lo, hi)
    P2, Q2 = _pqArrays(alpha, beta, x2, lo, hi)
    den = 1.0 + 0.5 * (Q1 + Q2)
    den = np.where(np.abs(den) < 1e-6, 1e-6, den)
    a = (0.5 * (lt1 + lt2) - 0.5 * (P1 + P2) - lref) / den
    return np.clip(a, a_lo, a_hi), a


def pairFiveK(alpha, beta, A, x1, x2, lt1, lt2, lo, hi):
    """The pair's mean log 5K equivalent at ability A."""
    P1, Q1 = _pqArrays(alpha, beta, x1, lo, hi)
    P2, Q2 = _pqArrays(alpha, beta, x2, lo, hi)
    return 0.5 * ((lt1 - P1 - A * Q1) + (lt2 - P2 - A * Q2))


def design(x1, x2, A, lo, hi, Ja, Jb, extra=None):
    """Columns: alpha_1..Ja, beta_1..Jb (times A), k0, then any extra."""
    B1a, B2a = basis(x1, lo, hi, Ja), basis(x2, lo, hi, Ja)
    cols = [B2a - B1a]
    if Jb:
        B1b, B2b = basis(x1, lo, hi, Jb), basis(x2, lo, hi, Jb)
        cols.append((B2b - B1b) * A[:, None])
    cols.append(np.ones((x1.size, 1)))
    if extra is not None:
        cols.append(extra)
    return np.hstack(cols)


# ------------------------------------------------------------------ #
# THE ROBUST SOLVE
# ------------------------------------------------------------------ #

def robustScale(r):
    med = np.median(r)
    return max(1.4826 * float(np.median(np.abs(r - med))), 1e-9)


def tukey(r, scale):
    u = r / (TUKEY_C * scale)
    w = (1.0 - u ** 2) ** 2
    w[np.abs(u) >= 1.0] = 0.0
    return w


def wls(X, y, w):
    """Weighted least squares through the normal equations (k is small),
    with a whisper of ridge only to survive an exactly collinear column."""
    Xw = X * w[:, None]
    M = X.T @ Xw
    M[np.diag_indices_from(M)] += 1e-10 * max(np.trace(M) / M.shape[0], 1.0)
    return np.linalg.solve(M, Xw.T @ y), M


def irls(X, y, w0=None):
    w = np.ones(y.size) if w0 is None else w0.copy()
    beta, M = wls(X, y, w)
    scale = robustScale(y - X @ beta)
    for _ in range(IRLS_ITERS):
        r = y - X @ beta
        scale = robustScale(r)
        w_new = tukey(r, scale)
        if (w_new > 0).sum() <= X.shape[1] * 3:
            break                     # a consensus of almost nobody is none
        w = w_new
        nb, M = wls(X, y, w)
        if np.max(np.abs(nb - beta)) < 1e-7:
            beta = nb
            break
        beta = nb
    return beta, w, scale, M


# ------------------------------------------------------------------ #
# ONE FAMILY
# ------------------------------------------------------------------ #

def _spanOf(x1, x2):
    xs = np.concatenate([x1, x2])
    return (float(np.percentile(xs, SPAN_PCT[0])),
            float(np.percentile(xs, SPAN_PCT[1])))


def fitFamily(arr, Ja, Jb, span=None, hs=None, extra=None, verbose=False,
              label=""):
    """Fit one family at fixed degrees. Returns the entry dict plus the
    internals the reports need (under "_")."""
    d1, d2, t1, t2 = arr["d1"], arr["d2"], arr["t1"], arr["t2"]
    x1, x2 = np.log(d1 / DA.TARGET_M), np.log(d2 / DA.TARGET_M)
    lt1, lt2 = np.log(t1), np.log(t2)
    y = lt2 - lt1
    lo, hi = span if span is not None else _spanOf(x1, x2)
    hs = hsMask(arr) if hs is None else hs
    ref_rows = hs if hs.sum() >= MIN_FAMILY_PAIRS else np.ones(y.size, bool)

    # start from Riegel's 1.06 (the pool fitter's physical prior) with no
    # ability term; the first ref and range come off that
    alpha = np.zeros(Ja); alpha[0] = 1.06
    beta = np.zeros(Jb)
    k0 = 0.0
    lref = float(np.median(pairFiveK(alpha, beta, np.zeros(y.size),
                                     x1, x2, lt1, lt2, lo, hi)[ref_rows]))
    a_lo, a_hi = -np.inf, np.inf
    A = np.zeros(y.size)
    history = []
    w = None
    for outer in range(OUTER_ITERS):
        if outer:
            # the reference moves with the curve: the HS median 5K
            # equivalent. Re-read BEFORE the pass, never after the last
            # one: (alpha, beta) are fitted on abilities measured from THIS
            # ref, and the stored triple must be the one they were fitted on
            lref = float(np.median(pairFiveK(alpha, beta, A, x1, x2, lt1, lt2,
                                             lo, hi)[ref_rows]))
        # the range is re-read every pass: it is a fact about the pairs'
        # abilities, and those move with the curve
        _A, a_raw = pairAbility(alpha, beta, lref, -np.inf, np.inf,
                                x1, x2, lt1, lt2, lo, hi)
        a_lo, a_hi = (float(np.percentile(a_raw, ABILITY_PCT[0])),
                      float(np.percentile(a_raw, ABILITY_PCT[1])))
        A = np.clip(a_raw, a_lo, a_hi)
        X = design(x1, x2, A, lo, hi, Ja, Jb, extra)
        coef, w, scale, M = irls(X, y, w)
        alpha, beta = coef[:Ja], coef[Ja:Ja + Jb]
        k0 = float(coef[Ja + Jb])
        history.append((float(alpha[0]), float(beta[0]) if Jb else 0.0,
                        math.exp(lref), a_lo, a_hi))
    if verbose:
        print(f"    [{label}] outer passes (alpha1, beta1, ref_5k, a_lo, a_hi): "
              + "; ".join(f"({h[0]:.4f}, {h[1]:+.4f}, {h[2]:.1f}, "
                          f"{h[3]:+.3f}, {h[4]:+.3f})" for h in history))
    entry = {
        "alpha": [float(v) for v in alpha], "beta": [float(v) for v in beta],
        "ref_5k": math.exp(lref), "a_lo": a_lo, "a_hi": a_hi,
        "x_lo": lo, "x_hi": hi, "span": (DA.TARGET_M * math.exp(lo),
                                         DA.TARGET_M * math.exp(hi)),
        "calendar": k0, "n_pairs": int(y.size),
        "n_weighted": int((w > 0).sum()), "scale": float(scale),
        "degree": (Ja, Jb),
    }
    entry["_"] = {"coef": coef, "w": w, "M": M, "A": A, "X": X, "y": y,
                  "x1": x1, "x2": x2, "scale": scale}
    return entry


def predictPairs(entry, arr):
    """Predicted log(t2/t1) at each pair's own symmetric ability -- for the
    CV loss (both legs known, as in the fit)."""
    x1 = np.log(arr["d1"] / DA.TARGET_M); x2 = np.log(arr["d2"] / DA.TARGET_M)
    lt1, lt2 = np.log(arr["t1"]), np.log(arr["t2"])
    lo, hi = entry["x_lo"], entry["x_hi"]
    A, _ = pairAbility(entry["alpha"], entry["beta"], math.log(entry["ref_5k"]),
                       entry["a_lo"], entry["a_hi"], x1, x2, lt1, lt2, lo, hi)
    P1, Q1 = _pqArrays(entry["alpha"], entry["beta"], x1, lo, hi)
    P2, Q2 = _pqArrays(entry["alpha"], entry["beta"], x2, lo, hi)
    return (P2 + A * Q2) - (P1 + A * Q1) + entry["calendar"]


def cvDegrees(arr, rng, verbose=True, label=""):
    """5-fold CV over (alpha, beta) degrees; the one-SE rule picks."""
    n = len(arr["d1"])
    idx = np.arange(n)
    if n > CV_MAX_PAIRS:
        idx = rng.choice(n, CV_MAX_PAIRS, replace=False)
    sub = subset(arr, idx)
    x1 = np.log(sub["d1"] / DA.TARGET_M); x2 = np.log(sub["d2"] / DA.TARGET_M)
    span = _spanOf(x1, x2)
    hs = hsMask(sub)
    fold = rng.integers(0, CV_FOLDS, size=idx.size)
    scores, per_pair = {}, {}
    for Ja in ALPHA_DEGREES:
        for Jb in BETA_DEGREES:
            losses = []
            for k in range(CV_FOLDS):
                tr, te = fold != k, fold == k
                e = fitFamily(subset(sub, tr), Ja, Jb, span=span, hs=hs[tr])
                r = np.log(sub["t2"][te] / sub["t1"][te]) - predictPairs(e, subset(sub, te))
                cap = (TUKEY_C * e["scale"]) ** 2
                losses.append(np.minimum(r ** 2, cap))
            lo = np.concatenate(losses)
            per_pair[(Ja, Jb)] = lo
            scores[(Ja, Jb)] = (float(lo.mean()), float(lo.std() / math.sqrt(lo.size)))
    best = min(scores, key=lambda k: scores[k][0])
    # ★ THE ONE-SE RULE ON THE PAIRED DIFFERENCE. Every model is scored on
    #   the same held-out pairs in the same order, so the question "is this
    #   simpler model worse than the best?" is answered by the per-pair
    #   difference, whose SE is far smaller than either loss's own -- the
    #   unpaired SE is dominated by how hard the pairs are, which is common
    #   to both models, and let a misspecified cubic pass for the truth in
    #   the synthetic test.
    ok = []
    for k in scores:
        dlt = per_pair[k] - per_pair[best]
        if dlt.mean() <= dlt.std() / math.sqrt(dlt.size):
            ok.append(k)
    # the simplest eligible model: fewest parameters, then fewest ability
    # terms
    pick = min(ok, key=lambda k: (k[0] + k[1], k[1], k[0]))
    if verbose:
        print(f"    [{label}] CV (truncated squared error x1e4, +-SE) on "
              f"{idx.size:,} pairs:")
        for Ja in ALPHA_DEGREES:
            print("      " + "  ".join(
                f"a{Ja}b{Jb} {1e4 * scores[(Ja, Jb)][0]:.3f}+-{1e4 * scores[(Ja, Jb)][1]:.3f}"
                f"{'*' if (Ja, Jb) == pick else ' '}"
                for Jb in BETA_DEGREES))
        print(f"      best a{best[0]}b{best[1]}; one-SE pick a{pick[0]}b{pick[1]}")
    return pick, scores


# ------------------------------------------------------------------ #
# DOES THE POOL STILL MATTER?  (the owner: "say so honestly with numbers")
# ------------------------------------------------------------------ #

def poolResidual(arr, entry, min_pairs=MIN_FAMILY_PAIRS):
    """One extra exponent per pool, delta_p * (x2 - x1), fitted beside the
    shared shape at the family's own degrees and final weights. The
    reference is the family's high-school pool (delta = 0 by
    construction), so delta_p is 'how much more this pool slows per unit
    log distance than a high schooler of the same ability'. Returns
    {pool: (delta, se, n)}."""
    pools = np.array([str(p).split("|")[0] for p in arr["pool"]])
    names = [p for p in sorted(set(pools))
             if not p.startswith("hs_") and (pools == p).sum() >= min_pairs]
    if not names:
        return {}
    I = entry["_"]
    dx = (I["x2"] - I["x1"])
    extra = np.stack([(pools == p) * dx for p in names], axis=1)
    Ja, Jb = entry["degree"]
    X = design(I["x1"], I["x2"], I["A"], entry["x_lo"], entry["x_hi"], Ja, Jb, extra)
    w = I["w"]
    coef, M = wls(X, I["y"], w)
    r = I["y"] - X @ coef
    s2 = float(np.sum(w * r ** 2) / max(np.sum(w > 0) - X.shape[1], 1))
    cov = np.linalg.inv(M) * s2
    k = Ja + Jb + 1
    return {p: (float(coef[k + i]), float(math.sqrt(max(cov[k + i, k + i], 0.0))),
                int((pools == p).sum()))
            for i, p in enumerate(names)}


def curveAgreement(arr, entry, pool_a, pool_b, grid=25):
    """Fit the family's model on each pool ALONE and compare the two curves
    where BOTH have data: distances inside both pools' 5-95% endpoint
    ranges, abilities inside both pools' 5-95% ranges. Returns None when
    the supports do not overlap, else a dict of |difference| in log time
    relative to 5000 m (g(x; a) itself, since g(0) = 0)."""
    pools = np.array([str(p).split("|")[0] for p in arr["pool"]])
    ma, mb = pools == pool_a, pools == pool_b
    if ma.sum() < MIN_FAMILY_PAIRS or mb.sum() < MIN_FAMILY_PAIRS:
        return None
    Ja, Jb = entry["degree"]
    span = (entry["x_lo"], entry["x_hi"])
    ea = fitFamily(subset(arr, ma), Ja, Jb, span=span)
    eb = fitFamily(subset(arr, mb), Ja, Jb, span=span)
    # the comparison is on ONE ability scale: the family's
    for e in (ea, eb):
        e["ref_5k"] = entry["ref_5k"]
    sup = []
    for e, m in ((ea, ma), (eb, mb)):
        I = e["_"]
        xs = np.concatenate([I["x1"], I["x2"]])
        sub = subset(arr, m)
        x1 = np.log(sub["d1"] / DA.TARGET_M); x2 = np.log(sub["d2"] / DA.TARGET_M)
        _A, a_raw = pairAbility(entry["alpha"], entry["beta"],
                                math.log(entry["ref_5k"]), -np.inf, np.inf,
                                x1, x2, np.log(sub["t1"]), np.log(sub["t2"]),
                                *span)
        sup.append((np.percentile(xs, 5), np.percentile(xs, 95),
                    np.percentile(a_raw, 5), np.percentile(a_raw, 95)))
    xlo, xhi = max(sup[0][0], sup[1][0]), min(sup[0][1], sup[1][1])
    alo, ahi = max(sup[0][2], sup[1][2]), min(sup[0][3], sup[1][3])
    if not (xhi - xlo > 0.05 and ahi > alo):
        return {"overlap": None, "supports": sup}
    # g(0) = 0 for both, so |g_a - g_b| at x is the disagreement of the
    # 5000 m -> d conversion; compare away from x = 0 where it is trivially 0
    diffs = []
    for x in np.linspace(xlo, xhi, grid):
        if abs(x) < 0.02:
            continue
        for a in np.linspace(alo, ahi, 7):
            diffs.append((DA.g(ea, x, a) - DA.g(eb, x, a), x, a))
    if not diffs:
        return {"overlap": None, "supports": sup}
    d = np.array([v[0] for v in diffs])
    worst = diffs[int(np.argmax(np.abs(d)))]
    return {"overlap": (DA.TARGET_M * math.exp(xlo), DA.TARGET_M * math.exp(xhi),
                        alo, ahi),
            "median_abs": float(np.median(np.abs(d))),
            "max_abs": float(np.max(np.abs(d))),
            "worst": (float(worst[0]), DA.TARGET_M * math.exp(worst[1]), float(worst[2])),
            "supports": sup}


# ------------------------------------------------------------------ #
# SAFETY: the forward map must be increasing on the support
# ------------------------------------------------------------------ #

def checkFamily(entry):
    """(ok, min 1+Q, min local exponent) over the support grid. The forward
    map log t -> log T5 is increasing iff 1 + Q(x) > 0; time is increasing
    in distance iff dg/dx > 0 for every ability in range."""
    xs = np.linspace(entry["x_lo"] - 0.3, entry["x_hi"] + 0.3, 61)
    worst_den, worst_k = np.inf, np.inf
    for x in xs:
        p, q = DA._pq(entry, float(x))
        worst_den = min(worst_den, 1.0 + q)
        for a in np.linspace(entry["a_lo"], entry["a_hi"], 9):
            h = 1e-4
            k = (DA.g(entry, float(x) + h, a) - DA.g(entry, float(x) - h, a)) / (2 * h)
            worst_k = min(worst_k, k)
    return (worst_den > 0.2 and worst_k > 0.0), float(worst_den), float(worst_k)


def supportTable(entry, arr, n_bins=10):
    """Per ability decile: the pairs' endpoint distances (p1, p50, p99)."""
    I = entry["_"]
    A = I["A"]
    qs = np.percentile(A, np.linspace(0, 100, n_bins + 1))
    rows = []
    for i in range(n_bins):
        m = (A >= qs[i]) & (A <= qs[i + 1])
        if not m.any():
            continue
        ds = DA.TARGET_M * np.exp(np.concatenate([I["x1"][m], I["x2"][m]]))
        rows.append((float(qs[i]), float(qs[i + 1]), int(m.sum()),
                     float(np.percentile(ds, 1)), float(np.percentile(ds, 50)),
                     float(np.percentile(ds, 99))))
    return rows


# ------------------------------------------------------------------ #
# ALL FAMILIES
# ------------------------------------------------------------------ #

FAMILY_GENDERS = ("m", "f", "u")
FAMILY_SPORTS = ("XC", "TF", "*")


def fitAll(xc, tf, verbose=True, degrees=None, agreement=True):
    """xc, tf: pairArrays of each sport. Returns the artifact dict."""
    rng = np.random.default_rng(SEED)
    fams, report = {}, {}
    by_sport = {"XC": xc, "TF": tf, "*": concatArrays([xc, tf])}
    for s in FAMILY_SPORTS:
        for gdr in FAMILY_GENDERS:
            key = f"{gdr}|{s}"
            arr = subset(by_sport[s], genderMask(by_sport[s], gdr))
            n = len(arr["d1"])
            if n < MIN_FAMILY_PAIRS:
                if verbose:
                    print(f"  {key}: {n:,} pairs -- under {MIN_FAMILY_PAIRS}, no family")
                continue
            t0 = time.time()
            if verbose:
                print(f"\n  {key}: {n:,} pairs")
            pick = (degrees or {}).get(key)
            cv = None
            if pick is None:
                pick, cv = cvDegrees(arr, rng, verbose=verbose, label=key)
            e = fitFamily(arr, *pick, verbose=verbose, label=key)
            ok, den, kmin = checkFamily(e)
            e["check"] = {"ok": ok, "min_denominator": den, "min_local_exponent": kmin}
            e["support"] = supportTable(e, arr)
            rep = {"cv": cv, "degree": pick}
            if s != "*" and gdr != "u":
                rep["pool_residual"] = poolResidual(arr, e)
                e["pool_residual"] = {p: v[0] for p, v in rep["pool_residual"].items()}
                e["pool_residual_se"] = {p: v[1] for p, v in rep["pool_residual"].items()}
                if agreement:
                    rep["agreement"] = {}
                    for other in ("college", "ms", "pro"):
                        rep["agreement"][other] = curveAgreement(
                            arr, e, f"hs_{gdr}", f"{other}_{gdr}")
            e.pop("_", None)
            fams[key] = e
            report[key] = rep
            if verbose:
                printFamily(key, e, rep)
                print(f"    ({time.time() - t0:.0f}s)")
    return {"kind": DA.KIND, "version": 1, "target": DA.TARGET_M,
            "families": fams, "report": report,
            "fitted": time.strftime("%Y-%m-%d %H:%M:%S")}


def _pct(v):
    return f"{100 * math.expm1(v):+.2f}%"


def printFamily(key, e, rep):
    print(f"    degree alpha {e['degree'][0]}, beta {e['degree'][1]}; "
          f"alpha {np.round(e['alpha'], 4).tolist()} beta {np.round(e['beta'], 4).tolist()}")
    print(f"    ref 5K {e['ref_5k']:.1f}s ({int(e['ref_5k'] // 60)}:"
          f"{e['ref_5k'] % 60:04.1f}); ability range {e['a_lo']:+.3f}..{e['a_hi']:+.3f} "
          f"(5K {e['ref_5k'] * math.exp(e['a_lo']):.0f}s..{e['ref_5k'] * math.exp(e['a_hi']):.0f}s); "
          f"span {e['span'][0]:.0f}-{e['span'][1]:.0f} m; calendar {_pct(e['calendar'])}; "
          f"robust sd {e['scale']:.4f}; {e['n_weighted']:,}/{e['n_pairs']:,} pairs weighted")
    c = e["check"]
    print(f"    monotone check: {'ok' if c['ok'] else 'FAILED'} (min 1+Q "
          f"{c['min_denominator']:.3f}, min local exponent {c['min_local_exponent']:.3f})")
    # the conversion the owner asked about, across the ability range
    for dist in (3000.0, 8000.0, 10000.0):
        x = math.log(dist / DA.TARGET_M)
        cells = []
        for a in (e["a_lo"], 0.0, e["a_hi"]):
            cells.append(f"a{a:+.2f} x{math.exp(-DA.g(e, x, a)):.4f}")
        print(f"    {dist:>6.0f} -> 5000: " + "  ".join(cells))
    print("    support by ability decile (a range, pairs, distance p1/p50/p99):")
    for lo, hi, n, p1, p50, p99 in e["support"]:
        print(f"      {lo:+.3f}..{hi:+.3f} {n:>9,}  {p1:>6.0f} {p50:>6.0f} {p99:>6.0f}")
    pr = rep.get("pool_residual") or {}
    if pr:
        print("    pool residual (extra exponent vs the HS pool at equal ability; "
              "effect on a 5K->10K, 95% CI):")
        for p, (dlt, se, n) in sorted(pr.items()):
            eff = dlt * math.log(2.0)
            sig = abs(dlt) > 1.96 * se
            print(f"      {p:<12} delta {dlt:+.4f} +- {se:.4f}  -> 10K "
                  f"{100 * math.expm1(eff):+.2f}% ({100 * math.expm1(eff - 1.96 * se * math.log(2)):+.2f}.."
                  f"{100 * math.expm1(eff + 1.96 * se * math.log(2)):+.2f})  n={n:,}"
                  f"{'  SIGNIFICANT' if sig else ''}")
    ag = rep.get("agreement") or {}
    for other, res in ag.items():
        if res is None:
            continue
        if res.get("overlap") is None:
            print(f"    hs vs {other}: no common support (distance and ability)")
            continue
        o = res["overlap"]
        wv, wd, wa = res["worst"]
        print(f"    hs vs {other} fitted separately, where BOTH have data "
              f"({o[0]:.0f}-{o[1]:.0f} m, ability {o[2]:+.2f}..{o[3]:+.2f}): "
              f"median |diff| {100 * res['median_abs']:.2f}%, max {100 * res['max_abs']:.2f}% "
              f"(at {wd:.0f} m, a {wa:+.2f})")


# ------------------------------------------------------------------ #
# LOAD, FIT, WRITE
# ------------------------------------------------------------------ #

def loadPairs(fresh=False):
    """The pool fitter's pairs (cache first). Imported lazily: that module
    needs the database config and corrections.py, the tests need neither."""
    import fit_distance_exponent as F
    xc_by_pool, tf_by_pool = F.loadAllPairs(use_cache=not fresh)
    xc = pairArrays([p for ps in xc_by_pool.values() for p in ps])
    tf = pairArrays([p for ps in tf_by_pool.values() for p in ps])
    return xc, tf


def writeArtifact(art, path=OUTPUT_FILE):
    # ! NEVER THE POOL ARTIFACT. A caller that points --out at it is refused:
    #   the two are read by different switches and must both exist.
    if os.path.basename(path) == "distance_spline.pkl":
        raise SystemExit("refusing to overwrite distance_spline.pkl")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(art, f)
    os.replace(tmp, path)
    print(f"\n  written -> {path}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fresh", action="store_true",
                    help="re-stream the pairs instead of the pool fitter's cache")
    ap.add_argument("--dry-run", action="store_true", help="fit and report, write nothing")
    ap.add_argument("--out", default=OUTPUT_FILE)
    ap.add_argument("--no-agreement", action="store_true",
                    help="skip the per-pool refits (the hs-vs-college comparison)")
    args = ap.parse_args()
    t0 = time.time()
    xc, tf = loadPairs(args.fresh)
    print(f"  pairs: XC {len(xc['d1']):,}, TF {len(tf['d1']):,}")
    art = fitAll(xc, tf, agreement=not args.no_agreement)
    bad = [k for k, e in art["families"].items() if not e["check"]["ok"]]
    if bad:
        print(f"\n  ⚠ monotone check FAILED for {bad}: NOT writing. A family whose "
              f"forward map is not increasing would reorder two runners on one "
              f"course.")
        raise SystemExit(1)
    if args.dry_run:
        print("\n  --dry-run: nothing written")
    else:
        writeArtifact(art, args.out)
    print(f"  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
