# Project: xc-predictor / engine
# File:    statespace.py
# Purpose: PROTOTYPE of the principled engine proposed in
#          docs/REVIEW-ENGINE-2026-10.md §3 -- one latent ability per PERSON
#          that moves through time (a Kalman random walk, smoothed), courses,
#          race-day effects nested venue-day > race with heavy tails, an
#          XC level ESTIMATED from the people who race both sports, and every
#          variance fitted by EM. Not wired into the pipeline; nothing here
#          writes to the database.
#
# ★ THE MODEL, per row i (person p, date t, race r on venue-day d, cell c):
#
#     y_i = theta_p(t) + f_lv(phase_i) + beta_p * xc_i
#           + h_i * (mu_g + c_c + w_d + v_r) + e_i
#
#     theta_p(t)   log time on a flat outdoor 400 at the gauge distance. ONE
#                  per person, all pools, all sports. A random walk in days
#                  (variance q_day per day) with a jump at each season seam
#                  (mean J_lv, variance q_jump). Fitness gain is the walk.
#     f_lv         the population's average shape through a season, a
#                  penalised spline in days since 1 August, per level.
#     beta_p       the person's grass specialisation, N(0, s_beta^2).
#     mu_g         the group level: XC, indoor, outdoor (outdoor = 0, the
#                  gauge). mu_XC IS the XC-to-track gap, and it is fitted
#                  -- by fitGLS through each runner's filter (fit() below is
#                  the first, block-step version, kept for the comparison
#                  rungs; its level estimate stalls, see fitGLS).
#     c_c          course x distance cell, N(0, tau_g^2).
#     w_d, v_r     venue-day and race-within-day, Student-t: a short course
#                  in one division is one big v_r, not a moved venue-day.
#     h_i          the tilt, 1 + kappa (theta - theta_ref), on the absolute
#                  scale theta is on -- no pool label enters it.
#     e_i          Student-t residual (IRLS weights), scale sigma.
#
# ★ THE FIT. Block coordinate EM: (1) every person's Kalman filter and RTS
#   smoother given the rest (numba, one pass over the rows, parallel over
#   people); (2) cells, venue-days, races as shrunk weighted means;
#   (3) mu, kappa, f, and every variance from the smoothed moments. No CG,
#   no window, no cap. A held-out row is a row the filter predicts through
#   without updating on it, so a held-out race and a forward season are the
#   same code path.
#
# ! A PROTOTYPE. The E-step for the cell / day / race variances uses the
#   per-block conditional variance (ENGINE.md §5 says that diagonal under-
#   states sigma_u on one-race cells; identified_priors is kept: tau and the
#   day variance are estimated only where a second day / race exists).
import numpy as np

try:
    from numba import njit, prange
except ImportError:                      # pragma: no cover -- numba is in requirements-engine
    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f
    prange = range


GROUP_XC, GROUP_OUT, GROUP_IN = 0, 1, 2
GAUGE_GROUP = GROUP_OUT                  # flat outdoor track is 0 by definition

# The only stated numbers. Each is a starting value or a degree of freedom,
# not a level: EM moves every variance from here.
START = dict(sigma2=0.02 ** 2, q_day=0.004 ** 2 / 30, q_jump=0.03 ** 2,
             s_beta2=0.015 ** 2, s_slope2=0.03 ** 2, tau2=(0.03 ** 2, 0.01 ** 2, 0.01 ** 2),
             s_w2=(0.015 ** 2, 0.006 ** 2, 0.006 ** 2),
             s_v2=(0.006 ** 2, 0.004 ** 2, 0.004 ** 2), kappa=0.0)
NU_ROW = 4.0        # Student-t df of a row; heavy on purpose (bad days, falls)
NU_DAY = 3.0        # Student-t df of a venue-day / race (a mis-measured course)
P0 = 0.3 ** 2       # diffuse start of a person's walk
SPLINE_KNOT_DAYS = 15.0
SEASON_DAYS = 366.0


# ------------------------------------------------------------------ #
# THE PERSON STEP: Kalman filter + RTS smoother, state (theta, slope,
# beta, eta)
# ------------------------------------------------------------------ #

NS = 4             # theta, the season's trend, grass, the runner's distance slope


@njit(cache=True, inline="always")
def _invN(A, out, W):
    """Inverse of a small symmetric positive definite NS x NS, Gauss-Jordan
    in place (W a 2 NS-wide scratch); no allocation in the RTS loop."""
    n = A.shape[0]
    for r in range(n):
        for c in range(n):
            W[r, c] = A[r, c]
            W[r, n + c] = 1.0 if r == c else 0.0
    for k in range(n):
        piv = W[k, k]
        if abs(piv) < 1e-300:
            piv = 1e-300
        for c in range(2 * n):
            W[k, c] /= piv
        for r in range(n):
            if r != k:
                f = W[r, k]
                if f != 0.0:
                    for c in range(2 * n):
                        W[r, c] -= f * W[k, c]
    for r in range(n):
        for c in range(n):
            out[r, c] = W[r, n + c]


@njit(cache=True, inline="always")
def _mm(A, B, out):
    n = A.shape[0]
    for r in range(n):
        for c in range(n):
            acc = 0.0
            for k in range(n):
                acc += A[r, k] * B[k, c]
            out[r, c] = acc


@njit(cache=True, inline="always")
def _mmT(A, B, out):
    """A @ B.T"""
    n = A.shape[0]
    for r in range(n):
        for c in range(n):
            acc = 0.0
            for k in range(n):
                acc += A[r, k] * B[c, k]
            out[r, c] = acc


@njit(parallel=True, cache=True)
def _smoothPeople(starts, t, season, lv, z, xc, rvar, train, jump_mean,
                  q_day, q_jump, s_slope2, s_beta2, m0, dynamic, ld, xe, s_eta2):
    """Per person, the smoothed state x = (theta, slope, beta, eta) at every
    row, and the moments the M-step needs. z is y minus everything but
    theta + beta*xc + eta*xe; rvar the row's observation variance (sigma2 /
    weight).

      theta  log time, a random walk q_day per day
      slope  this season's own trend, log time per 365 days: drawn fresh at
             every season seam from N(0, s_slope2), constant inside it --
             the runner who is getting fitter faster than the population
      beta   grass specialisation, constant for life
      eta    the runner's own distance curve, log time per unit of
             ln(distance / 5000) (xe), constant for life, N(0, s_eta2);
             s_eta2 ~ 0 holds it at 0

    The transition into row k is theta += slope * dt inside a season; at a
    seam theta += jump (variance q_jump) and the slope is redrawn.
    dynamic=False holds theta constant inside a season and independent
    across seasons, no slope (the current engine's athlete-season), for the
    like-for-like comparison. Returns X (n, 4) and V (n, 6): the four
    variances, then Cov(theta, beta) and Cov(theta, eta); and PR (n, 2),
    theta as predicted from the runner's other rows BEFORE this one and its
    variance -- free of the row's own noise, for the tilt's estimate."""
    n = t.size
    X = np.zeros((n, NS)); V = np.zeros((n, NS + 2))
    PR = np.zeros((n, 2))            # theta predicted before the row is seen, and its variance
    d2 = np.zeros(n); gap = np.zeros(n)
    n_people = starts.size - 1
    for p in prange(n_people):
        s, e = starts[p], starts[p + 1]
        m = e - s
        if m == 0:
            continue
        xp = np.zeros((m, NS)); Pp = np.zeros((m, NS, NS))
        xf = np.zeros((m, NS)); Pf = np.zeros((m, NS, NS))
        dtk = np.zeros(m); seamk = np.zeros(m, dtype=np.bool_)
        x = np.zeros(NS); P = np.zeros((NS, NS)); PH = np.zeros(NS)
        T1 = np.zeros((NS, NS)); T2 = np.zeros((NS, NS)); Fm = np.eye(NS)
        Pinv = np.zeros((NS, NS)); G = np.zeros((NS, NS)); Wk = np.zeros((NS, 2 * NS))
        x[0] = m0[lv[s]]
        P[0, 0] = P0
        P[1, 1] = s_slope2 if dynamic else 1e-12
        P[2, 2] = s_beta2
        P[3, 3] = max(s_eta2, 1e-12)
        for k in range(m):
            i = s + k
            if k > 0:
                dt = t[i] - t[i - 1]
                if season[i] != season[i - 1]:
                    seamk[k] = True
                    if dynamic:
                        # the seam: the season's trend ends, a new one starts
                        x[1] = 0.0
                        x[0] += jump_mean[lv[i]]
                        for c in range(NS):
                            P[1, c] = 0.0; P[c, 1] = 0.0
                        P[0, 0] += q_jump + q_day * min(dt, 60.0)
                        P[1, 1] = s_slope2
                    else:
                        x[0] = m0[lv[i]]
                        for c in range(NS):
                            P[0, c] = 0.0; P[c, 0] = 0.0
                        P[0, 0] = P0
                elif dynamic and dt > 0:
                    u = dt / 365.0
                    dtk[k] = u
                    x[0] += u * x[1]
                    # P <- F P F' with F = I + u e0 e1'
                    p00 = P[0, 0] + 2 * u * P[0, 1] + u * u * P[1, 1]
                    for c in range(1, NS):
                        v_ = P[0, c] + u * P[1, c]
                        P[0, c] = v_; P[c, 0] = v_
                    P[0, 0] = p00 + q_day * dt
            for r in range(NS):
                xp[k, r] = x[r]
                for c in range(NS):
                    Pp[k, r, c] = P[r, c]
            PR[i, 0] = x[0]; PR[i, 1] = P[0, 0]
            if train[i]:
                hx = xc[i]
                he = xe[i]
                l0 = ld[i]
                for c in range(NS):
                    PH[c] = l0 * P[c, 0] + hx * P[c, 2] + he * P[c, 3]
                S = l0 * PH[0] + hx * PH[2] + he * PH[3] + rvar[i]
                innov = z[i] - (l0 * x[0] + hx * x[2] + he * x[3])
                for r in range(NS):
                    x[r] += PH[r] / S * innov
                for r in range(NS):
                    for c in range(NS):
                        P[r, c] -= PH[r] * PH[c] / S
            for r in range(NS):
                xf[k, r] = x[r]
                for c in range(NS):
                    Pf[k, r, c] = P[r, c]
        # RTS backwards
        xs = xf[m - 1].copy(); Ps = Pf[m - 1].copy()
        i = s + m - 1
        for c in range(NS):
            X[i, c] = xs[c]; V[i, c] = Ps[c, c]
        V[i, NS] = Ps[0, 2]; V[i, NS + 1] = Ps[0, 3]
        nx = np.zeros(NS); nP = np.zeros((NS, NS)); a = np.zeros(NS); b = np.zeros(NS)
        for k in range(m - 2, -1, -1):
            i = s + k
            # F into k+1
            for r in range(NS):
                for c in range(NS):
                    Fm[r, c] = 1.0 if r == c else 0.0
            if seamk[k + 1]:
                if dynamic:
                    Fm[1, 1] = 0.0
                else:
                    Fm[0, 0] = 0.0
            else:
                Fm[0, 1] = dtk[k + 1]
            _mmT(Pf[k], Fm, T1)               # Pf F'
            _invN(Pp[k + 1], Pinv, Wk)
            _mm(T1, Pinv, G)                  # G = Pf F' Pp^-1
            for r in range(NS):
                nx[r] = xf[k, r]
                for c in range(NS):
                    nx[r] += G[r, c] * (xs[c] - xp[k + 1, c])
            for r in range(NS):
                for c in range(NS):
                    T1[r, c] = Ps[r, c] - Pp[k + 1, r, c]
            _mm(G, T1, T2)
            _mmT(T2, G, T1)
            for r in range(NS):
                for c in range(NS):
                    nP[r, c] = Pf[k, r, c] + T1[r, c]
            # E[(theta_{k+1} - theta_k - slope_k dt)^2], the walk's own step
            j = i + 1
            for r in range(NS):
                a[r] = 0.0; b[r] = 0.0
            a[0] = 1.0
            b[0] = -1.0; b[1] = -dtk[k + 1]
            _mmT(Ps, G, T2)                    # Cov(x_{k+1}, x_k | all)
            mean = xs[0] - nx[0] - dtk[k + 1] * nx[1]
            q = Ps[0, 0]
            r2 = 0.0; cr = 0.0
            for r in range(NS):
                for c in range(NS):
                    r2 += b[r] * nP[r, c] * b[c]
                    cr += a[r] * T2[r, c] * b[c]
            d2[j] = mean * mean + q + r2 + 2.0 * cr
            gap[j] = t[j] - t[i]
            for r in range(NS):
                xs[r] = nx[r]
                for c in range(NS):
                    Ps[r, c] = nP[r, c]
            for c in range(NS):
                X[i, c] = xs[c]; V[i, c] = Ps[c, c]
            V[i, NS] = Ps[0, 2]; V[i, NS + 1] = Ps[0, 3]
    return X, V, d2, gap, PR


# ------------------------------------------------------------------ #
# THE SHARED STEPS
# ------------------------------------------------------------------ #

def _shrunkMean(code, n, num_w, den_w, prior_prec):
    """Per group: sum(num) / (sum(den) + prior_prec[group]) and its
    conditional variance factor 1 / (sum(den) + prior_prec)."""
    num = np.bincount(code, weights=num_w, minlength=n)
    den = np.bincount(code, weights=den_w, minlength=n)
    q = den + prior_prec
    return num / q, 1.0 / q, den


def cellMarginal(cell_of_day, n_cell, day, num_w, den_w, sigma2, s_w2_of_day):
    """Each cell's own estimate with its race days INTEGRATED OUT: per day
    the mean residual (course and day left in), variance sigma2 / den +
    s_w2; per cell the precision-weighted mean over its days. ! NOT the
    estimate given the fitted days: once two cells are tied, their days
    soak up the real difference between them and the cells then look the
    same -- on the planted world that merged 1.7%-apart courses 200 m
    apart. Returns (estimate, variance), variance inf where no data."""
    nd = np.bincount(day, weights=num_w, minlength=cell_of_day.size)
    dd = np.bincount(day, weights=den_w, minlength=cell_of_day.size)
    ok = (dd > 0) & (cell_of_day >= 0)
    md = np.where(ok, nd / np.where(ok, dd, 1.0), 0.0)
    prec = np.where(ok, 1.0 / (sigma2 / np.where(ok, dd, 1.0) + s_w2_of_day), 0.0)
    cd = np.maximum(cell_of_day, 0)
    P = np.bincount(cd, weights=prec, minlength=n_cell)
    M = np.bincount(cd, weights=prec * md, minlength=n_cell)
    est = np.where(P > 0, M / np.where(P > 0, P, 1.0), 0.0)
    return est, np.where(P > 0, 1.0 / np.where(P > 0, P, 1.0), np.inf)


def eraCells(D):
    """D with a cell per (course, season) and each cell's course and
    season, for terms "era_walk". Pure; D is not changed."""
    E = dict(D)
    cell = np.asarray(D["cell"]); has = cell >= 0
    key = np.where(has, cell.astype(np.int64) * 4096 + np.asarray(D["season"]) % 4096, -1)
    u, inv = np.unique(key[has], return_inverse=True)
    E["cell"] = np.full(cell.size, -1, dtype=np.int64)
    E["cell"][has] = inv.reshape(-1)
    E["cell_course"] = (u // 4096).astype(np.int64)
    E["cell_season"] = (u % 4096).astype(np.int64)
    return E


def eraPairs(cell_course, cell_season):
    """Per course, each season's cell against the course's previous raced
    season's: (a, b, seasons apart)."""
    o = np.lexsort((cell_season, cell_course))
    same = cell_course[o][1:] == cell_course[o][:-1]
    a, b = o[:-1][same], o[1:][same]
    return a, b, (cell_season[b] - cell_season[a]).astype(np.float64)


def eraStep(est, est_var, a, b, gap, q_prev):
    """The course's walk variance per season, by moments on the cells' own
    estimates (days integrated out): E (c_b - c_a)^2 = q gap + var_a + var_b.
    ! NOT FROM THE TIED ESTIMATES: a tie pulls two seasons together, the next
    step reads less drift, ties harder, and the walk collapses to zero."""
    ok = np.isfinite(est_var[a]) & np.isfinite(est_var[b])
    if ok.sum() < 10:
        return q_prev
    d2 = (est[b] - est[a])[ok] ** 2
    v = (est_var[a] + est_var[b])[ok]
    g = gap[ok]
    w = 1.0 / (q_prev * g + v) ** 2
    return float(max(np.sum(w * g * (d2 - v)) / np.sum(w * g * g), 1e-7))


def _tiedMeans(num, q, a, b, delta, W, c0, sweeps=8):
    """Shrunk means with pairs tied: c_b - c_a pulled toward delta with
    weight W (units of q). Jacobi sweeps; returns (c, variance / sigma2)."""
    n = c0.size
    Wsum = np.bincount(a, weights=W, minlength=n) + np.bincount(b, weights=W, minlength=n)
    c = c0
    for _ in range(sweeps):
        pull = (np.bincount(a, weights=W * (c[b] - delta), minlength=n)
                + np.bincount(b, weights=W * (c[a] + delta), minlength=n))
        c = (num + pull) / (q + Wsum)
    return c, 1.0 / (q + Wsum)


def _splineBasis(phase, n_knots):
    """Piecewise-linear hat basis on [0, SEASON_DAYS] (the season curve)."""
    step = SEASON_DAYS / (n_knots - 1)
    x = np.clip(phase / step, 0, n_knots - 1 - 1e-9)
    k = x.astype(np.int64)
    fr = x - k
    return k, fr


def _fitCurve(phase, resid, wt, lv, n_lv, lam):
    """Per level, the season curve: hat-basis least squares with a second-
    difference penalty lam (chosen by GCV in fit()). Centred to weighted
    mean zero so theta keeps the level."""
    n_knots = int(SEASON_DAYS // SPLINE_KNOT_DAYS) + 1
    k, fr = _splineBasis(phase, n_knots)
    D = np.diff(np.eye(n_knots), 2, axis=0)
    pen = D.T @ D
    coefs = np.zeros((n_lv, n_knots))
    for L in range(n_lv):
        m = lv == L
        if m.sum() < 50:
            continue
        kk, ff, rr, ww = k[m], fr[m], resid[m], wt[m]
        A = np.zeros((n_knots, n_knots)); b = np.zeros(n_knots)
        for (ka, wa), (kb, wb) in (((kk, 1 - ff), (kk, 1 - ff)),
                                   ((kk, 1 - ff), (kk + 1, ff)),
                                   ((kk + 1, ff), (kk, 1 - ff)),
                                   ((kk + 1, ff), (kk + 1, ff))):
            np.add.at(A, (ka, np.minimum(kb, n_knots - 1)), ww * wa * wb)
        np.add.at(b, kk, ww * (1 - ff) * rr)
        np.add.at(b, np.minimum(kk + 1, n_knots - 1), ww * ff * rr)
        sol = np.linalg.solve(A + lam * pen + 1e-9 * np.eye(n_knots), b)
        fit = sol[kk] * (1 - ff) + sol[np.minimum(kk + 1, n_knots - 1)] * ff
        sol -= np.average(fit, weights=ww)
        coefs[L] = sol
    return coefs, n_knots


def curveAt(coefs, n_knots, phase, lv):
    k, fr = _splineBasis(phase, n_knots)
    k1 = np.minimum(k + 1, n_knots - 1)
    return coefs[lv, k] * (1 - fr) + coefs[lv, k1] * fr


# ------------------------------------------------------------------ #
# THE FIT
# ------------------------------------------------------------------ #

def fit(D, train, outer=40, dynamic=True, nested=True, heavy=True, heavy_rows=True,
        fix_mu=None, fit_mu=True, fit_kappa=True, curve_lam=30.0, verbose=False):
    """D: dict of row arrays sorted by (person, t) -- person, t (days),
    season, lv, phase, y, xc (0/1), group (per row: the cell's group),
    cell, day, race (dense ints, -1 for none) and starts (person segment
    starts, length n_people + 1). train: bool per row.

    dynamic   False = the current engine's athlete-season ability
    nested    False = one race-day term per venue-day (XCP_RACE_KEY=venue)
    heavy     False = Gaussian venue-days / races (the shipped prior)
    heavy_rows  Student-t rows (the stand-in for the shipped Huber weights)
    fix_mu    a dict {group: level} to ASSERT a level (XCP_SPORT_LEVEL)
    Returns a dict of fitted parameters and per-row predictions."""
    n = D["y"].size
    y, xc, grp = D["y"], D["xc"].astype(np.float64), D["group"]
    cell, day, race = D["cell"], D["day"], D["race"]
    n_cell = int(cell.max()) + 1
    n_day = int(day.max()) + 1
    n_race = int(race.max()) + 1
    n_lv = int(D["lv"].max()) + 1
    cell_grp = np.zeros(n_cell, dtype=np.int64)
    cell_grp[cell[cell >= 0]] = grp[cell >= 0]
    day_grp = np.zeros(n_day, dtype=np.int64); day_grp[day] = grp
    race_grp = np.zeros(n_race, dtype=np.int64); race_grp[race] = grp
    tr = train.astype(np.float64)

    P = dict(START)
    tau2 = np.array(P["tau2"]); s_w2 = np.array(P["s_w2"]); s_v2 = np.array(P["s_v2"])
    sigma2, q_day, q_jump, s_beta2, kappa = (P["sigma2"], P["q_day"], P["q_jump"],
                                            P["s_beta2"], P["kappa"])
    s_slope2 = P["s_slope2"]
    mu = np.zeros(3)
    if fix_mu:
        for g, v in fix_mu.items():
            mu[g] = v
    c = np.zeros(n_cell); w = np.zeros(n_day); v = np.zeros(n_race)
    lam_w = np.ones(n_day); lam_v = np.ones(n_race)
    jump = np.zeros(n_lv)
    m0 = np.array([np.median(y[D["lv"] == L]) if (D["lv"] == L).any()
                   else np.median(y) for L in range(n_lv)])
    coefs, n_knots = np.zeros((n_lv, int(SEASON_DAYS // SPLINE_KNOT_DAYS) + 1)), \
        int(SEASON_DAYS // SPLINE_KNOT_DAYS) + 1
    f = np.zeros(n)
    wrow = np.ones(n)
    th = np.full(n, np.median(y)); be = np.zeros(n); vth = np.full(n, 0.01)
    has_cell = cell >= 0
    cidx = np.where(has_cell, cell, 0)

    # identified variances: a cell with 2+ days, a day with 2+ races
    cell_days = np.bincount(np.unique(cidx[has_cell] * (n_day + 1) + day[has_cell])
                            // (n_day + 1), minlength=n_cell)
    day_races = np.bincount(np.unique(day * (n_race + 1) + race) // (n_race + 1),
                            minlength=n_day)
    race_day = day_of_race(day, race, n_race)
    day_cell = np.zeros(n_day, dtype=np.int64)
    day_cell[day[has_cell]] = cidx[has_cell]

    for it in range(outer):
        theta_ref = np.median(th[train])
        h = 1.0 + kappa * (th - theta_ref)
        Dsum = mu[grp] + np.where(has_cell, c[cidx], 0.0) + w[day] + v[race]

        # (1) people
        z = y - f - h * Dsum
        rvar = sigma2 / np.maximum(wrow, 1e-6)
        X, V, d2, gap, _pr = _smoothPeople(
            D["starts"], D["t"], D["season"], D["lv"], z, xc, rvar, train,
            jump, q_day, q_jump, s_slope2, s_beta2, m0, dynamic, np.ones(n),
            np.zeros(n), 0.0)
        th, sl, be = X[:, 0], X[:, 1], X[:, 2]
        vth, vsl, vbe = V[:, 0], V[:, 1], V[:, 2]
        base = y - f - th - be * xc                    # = h * Dsum + e
        h = 1.0 + kappa * (th - theta_ref)
        ww = wrow * tr

        # (2a) cells: shrunk toward the group level
        r = base - h * (mu[grp] + w[day] + v[race])
        num = np.where(has_cell, ww * h * r, 0.0)
        den = np.where(has_cell, ww * h * h, 0.0)
        c, vc, cden = _shrunkMean(cidx, n_cell, num, den, sigma2 / tau2[cell_grp])
        # (2b) venue-days
        r = base - h * (mu[grp] + np.where(has_cell, c[cidx], 0.0) + v[race])
        w, vw, wden = _shrunkMean(day, n_day, ww * h * r, ww * h * h,
                                  sigma2 * lam_w / s_w2[day_grp])
        # (2c) races inside the day
        if nested:
            r = base - h * (mu[grp] + np.where(has_cell, c[cidx], 0.0) + w[day])
            v, vv, vden = _shrunkMean(race, n_race, ww * h * r, ww * h * h,
                                      sigma2 * lam_v / s_v2[race_grp])
        # (2d) the group levels: XC and indoor against the outdoor gauge
        if fit_mu and not fix_mu:
            r = base - h * (np.where(has_cell, c[cidx], 0.0) + w[day] + v[race])
            for g in (GROUP_XC, GROUP_IN):
                m = (grp == g) & train
                if m.any():
                    mu[g] = np.sum(ww[m] * h[m] * r[m]) / np.sum(ww[m] * h[m] ** 2)
        Dsum = mu[grp] + np.where(has_cell, c[cidx], 0.0) + w[day] + v[race]

        # (3a) the tilt, one number, on theta's own scale
        xk = (th - theta_ref) * Dsum
        rk = base - Dsum
        den_k = np.sum(ww * xk * xk)
        if fit_kappa and den_k > 0:
            kappa = float(np.sum(ww * xk * rk) / den_k)
        h = 1.0 + kappa * (th - theta_ref)

        # (3b) the season curve on what theta and the effects leave
        # (y less everything but f: the curve's own target, f included)
        res_f = y - th - be * xc - h * Dsum
        coefs, n_knots = _fitCurve(D["phase"], res_f, ww, D["lv"], n_lv, curve_lam)
        f = curveAt(coefs, n_knots, D["phase"], D["lv"])

        # (3c) row scale and weights
        res = y - f - th - be * xc - h * Dsum
        e2 = res ** 2 + vth
        sigma2 = float(np.sum(ww * e2) / max(tr.sum(), 1))
        if heavy_rows:
            wrow = (NU_ROW + 1.0) / (NU_ROW + res ** 2 / sigma2)

        # (3d) the walk
        if dynamic:
            same = (gap > 0) & (D["season"] == np.roll(D["season"], 1))
            same[D["starts"][:-1]] = False
            if same.any():
                q_day = float(np.mean(d2[same] / gap[same]))
            seam = (D["season"] != np.roll(D["season"], 1))
            seam[D["starts"][:-1]] = False
            if seam.any():
                prev = np.roll(th, 1)
                dj = th[seam] - prev[seam]
                lvs = D["lv"][seam]
                for L in range(n_lv):
                    mL = lvs == L
                    if mL.sum() > 20:
                        jump[L] = float(np.mean(dj[mL]))
                qj = (d2[seam] - 2.0 * jump[lvs] * dj + jump[lvs] ** 2
                      - q_day * np.minimum(gap[seam], 60.0))
                q_jump = float(max(np.mean(qj), 1e-6))
            # the season trend's spread, read at each season's first row
            first_s = seam.copy(); first_s[D["starts"][:-1]] = True
            s_slope2 = float(max(np.mean(sl[first_s] ** 2 + vsl[first_s]), 1e-8))
        firsts = D["starts"][:-1][np.diff(D["starts"]) > 0]
        s_beta2 = float(max(np.mean(be[firsts] ** 2 + vbe[firsts]), 1e-8))

        # (3e) group variances, each only where it is identified
        for g in range(3):
            m = (cell_grp == g) & (cell_days >= 2) & (cden > 0)
            if m.sum() >= 10:
                tau2[g] = max(np.mean(c[m] ** 2 + sigma2 * vc[m]), 1e-8)
            md = (day_grp == g) & (wden > 0) & (cell_days[day_cell] >= 2)
            if md.sum() >= 10:
                s_w2[g] = max(np.mean(lam_w[md] * (w[md] ** 2 + sigma2 * vw[md])), 1e-8)
            if nested:
                mr = (race_grp == g) & (vden > 0) & (day_races[race_day] >= 2)
                if mr.sum() >= 10:
                    s_v2[g] = max(np.mean(lam_v[mr] * (v[mr] ** 2 + sigma2 * vv[mr])), 1e-8)
        if heavy:
            lam_w = (NU_DAY + 1.0) / (NU_DAY + w ** 2 / s_w2[day_grp])
            if nested:
                lam_v = (NU_DAY + 1.0) / (NU_DAY + v ** 2 / s_v2[race_grp])
        if verbose and (it % 5 == 0 or it == outer - 1):
            print(f"[ss] {it:3d} sigma {np.sqrt(sigma2):.4f} q_day*30 "
                  f"{np.sqrt(q_day * 30):.4f} q_jump {np.sqrt(q_jump):.4f} "
                  f"slope {np.sqrt(s_slope2):.4f} "
                  f"mu_XC {mu[0]:+.4f} mu_in {mu[2]:+.4f} kappa {kappa:+.3f} "
                  f"tau {np.sqrt(tau2).round(4)} s_w {np.sqrt(s_w2).round(4)} "
                  f"s_v {np.sqrt(s_v2).round(4)}")

    pred_known = f + th + be * xc + h * (mu[grp] + np.where(has_cell, c[cidx], 0.0))
    # what a held-out row can know: its day only if another race that day trained
    day_trained = np.bincount(day, weights=tr, minlength=n_day) > 0
    race_trained = np.bincount(race, weights=tr, minlength=n_race) > 0
    pred = pred_known + h * (np.where(day_trained[day], w[day], 0.0)
                             + np.where(race_trained[race], v[race], 0.0))
    return dict(pred=pred, h=h, theta=th, beta=be, theta_var=vth, mu=mu, kappa=kappa,
                sigma2=sigma2, q_day=q_day, q_jump=q_jump, jump=jump, s_slope2=s_slope2, tau2=tau2,
                s_w2=s_w2, s_v2=s_v2, cell=c, day=w, race=v, curve=coefs,
                n_knots=n_knots, s_beta2=s_beta2)


def day_of_race(day, race, n_race):
    """Per race, its venue-day."""
    out = np.zeros(n_race, dtype=np.int64)
    out[race] = day
    return out


def segments(person):
    """Segment starts of a person-sorted array, length n_people + 1."""
    n_p = int(person.max()) + 1
    return np.concatenate([[0], np.cumsum(np.bincount(person, minlength=n_p))]).astype(np.int64)


# ------------------------------------------------------------------ #
# THE SCORE (the same three numbers forward_holdout prints)
# ------------------------------------------------------------------ #

def score(y, pred, race, mask):
    """bias, median |e|, the race-centred median |e| (the spread, ability
    only), and head-to-head: per race the share of pairs in the predicted
    order, one number per race, mean over races."""
    e = y[mask] - pred[mask]
    rr = race[mask]
    order = np.argsort(rr, kind="stable")
    e_s, r_s = e[order], rr[order]
    yy, pp = y[mask][order], pred[mask][order]
    cuts = np.flatnonzero(np.diff(r_s)) + 1
    starts = np.r_[0, cuts]; ends = np.r_[cuts, r_s.size]
    cent = np.empty_like(e_s); shares = []
    for a, b in zip(starts, ends):
        cent[a:b] = e_s[a:b] - np.median(e_s[a:b])
        if b - a >= 2:
            dy = np.sign(yy[a:b, None] - yy[None, a:b])
            dp = np.sign(pp[a:b, None] - pp[None, a:b])
            iu = np.triu_indices(b - a, 1)
            ok = dy[iu] != 0
            if ok.any():
                shares.append(float(np.mean((dy[iu] == dp[iu])[ok]
                                            + 0.5 * (dp[iu] == 0)[ok])))
    return dict(n=int(mask.sum()), bias=float(np.mean(e)),
                med_abs=float(np.median(np.abs(e))),
                spread=float(np.median(np.abs(cent))),
                h2h=float(np.mean(shares)) if shares else float("nan"))


# ------------------------------------------------------------------ #
# THE SHARED EFFECTS BY GLS THROUGH EVERY RUNNER'S OWN TRAJECTORY
# ------------------------------------------------------------------ #
#
# ★ WHY (owner, 2026-10-24: "the entire point is you need to figure this
#   out ... what a track rating is in terms of xc"). fit()'s block steps
#   estimate the XC level from residuals AFTER each runner's state has
#   moved: the walk and the season curve soak the level up first, and the
#   level never comes back (it sat at +0.1% on a world planted at +6%). The
#   textbook answer is regression with state-space errors (de Jong 1991,
#   the augmented Kalman filter): run every runner's filter on y AND on
#   each shared regressor, and the innovations give the exact GLS estimate
#   of the shared effects with each runner's trajectory integrated out.
#   An XC race and an indoor race three weeks later by one runner then
#   inform the gap by exactly as much as that runner's fitness could have
#   moved in three weeks -- a bridge is weighted by its own drift, and no
#   window is chosen.
#
# ★ THE SHARED EFFECTS (the columns of X):
#     mu_XC, mu_in     the XC and indoor levels against the flat outdoor
#                      gauge, per level of the pool ("mu_by_level"); their
#                      slope in ability is the loading (1 + lam) on the
#                      runner's state, fitted by EM (fitGLS), not a column
#     altitude         per surface, per km above ALT_FLOOR_M (a condition)
#     season curve     per level, a hat basis every SPLINE_KNOT_DAYS from
#                      1 August, one knot (1 October) pinned at 0, its
#                      curvature variance fitted by EM
#     seam             per level: the summer's step INTO that level (a 9th
#                      grader becoming a 10th grader; a senior a freshman),
#                      as a count of seams crossed
#     peak             the last race of a sport's season (4+ races): the
#                      taper and the championship
#     recovery         the races after a 21-90 day break inside a season,
#                      decaying over RECOVERY_DAYS: what a December break
#                      costs the first indoor races
#     event            track distance classes per level, 1600 m pinned
#   Peak and recovery are FITNESS: they explain the trajectory so the gap
#   is not asked to, and they stay in a runner's rating. The level, the
#   altitude, the event offset, the course, the day and the race come out
#   of a rating (perf); its exp is the 5K column, on the day, no forecast.

KMAX_LOCAL = 256           # shared columns one runner can touch (an error past it)
# ⚠ THE START MUST BE DIFFUSE, AND z CENTRED. A prior N(0, 1) on theta with
#   log times near 7 is a pseudo-row pulling every runner toward 0; harmless
#   for a runner, but the gap sits on a nearly flat direction of the fit and
#   that pull alone moved it +1.3% on the planted world (the kernel's S
#   matched a direct person-FE solve to 6e-6; its right-hand side did not).
#   fitGLS hands the kernel z less its median, and theta starts at 1e4.
DIFFUSE = 1e4
KAPPA_STATED = 0.31
RECOVERY_DAYS = 30.0
BREAK_BINS = (21.0, 45.0, 90.0)
EVENT_EDGES = (1000.0, 2000.0, 4000.0, 7000.0)   # classes; 1000-2000 is the pin
PIN_PHASE = 60.0           # 1 October: the season curve's zero
SEASON_CLOSED_DAYS = 21.0  # run_joint's: a season raced in more recently may still be running
# ★ THE WEATHER, FITTED (terms "weather", D["wx_temp"/"wx_wind"/"wx_wet"]):
#   per surface (XC, outdoor track) a hat basis in apparent temperature
#   with its zero at the cool optimum, a slope in wind and one in wet
#   ground (soil moisture on grass, rain on the track). Conditions: out of
#   a rating like altitude. Identified by runners seen in more than one
#   kind of weather, each against their own trajectory; the race effect
#   takes what a grid cell misses (a night race on a hot day).
# ★ THE DISTANCE CURVE, FITTED (terms "dist_curve", D["ld"] = ln(d / 5000)
#   with the pool's distance leg taken back off y): per level a hat basis in
#   ln distance, zero at 5000 m, in place of the per-pool curves the
#   backfill applies and the coarse event classes on top of them. Measured
#   by runners seen at several distances, against their own trajectory;
#   with "endurance" each runner's own slope sits on top.
LD_KNOTS = tuple(np.log(np.array([600.0, 1000.0, 1600.0, 3000.0, 5000.0, 8000.0, 12000.0]) / 5000.0))
LD_PIN = 4                                                    # 5000 m: zero
WX_TEMP_KNOTS = (30.0, 45.0, 55.0, 65.0, 75.0, 85.0, 100.0)   # the artifact's apparent temp, F
WX_TEMP_PIN = 2                                              # 55 F: zero
ALT_DIST_KNOTS = ((800.0, 0.21), (1600.0, 0.83), (3000.0, 0.93),
                  (5000.0, 1.00), (10000.0, 1.05))   # joint_solve's, NCAA tables


@njit(parallel=True, cache=True)
def _glsPeople(starts, t, season, z, xc, Xc, Xv, rvar, train, K, q_day,
               q_jump, s_slope2, s_beta2, n_chunks, ld, xe, s_eta2):
    """Per runner, the Kalman filter run on z and on every shared column it
    touches (same gains); accumulates S = sum v_X v_X' / F, s = sum v_X v_z
    / F and the log-likelihood pieces. Diffuse start: theta's first race is
    absorbed, never scored. State as _smoothPeople's."""
    n_people = starts.size - 1
    S_all = np.zeros((n_chunks, K, K))
    s_all = np.zeros((n_chunks, K))
    over = np.zeros(n_chunks, dtype=np.int64)
    m = Xc.shape[1]
    for ch in prange(n_chunks):
        lo = ch * n_people // n_chunks
        hi = (ch + 1) * n_people // n_chunks
        loc = np.full(K, -1, dtype=np.int64)
        glob = np.zeros(KMAX_LOCAL, dtype=np.int64)
        A = np.zeros((NS, KMAX_LOCAL))
        vx = np.zeros(KMAX_LOCAL)
        xrow = np.zeros(KMAX_LOCAL)
        x = np.zeros(NS); P = np.zeros((NS, NS)); PH = np.zeros(NS)
        for p in range(lo, hi):
            s, e = starts[p], starts[p + 1]
            kp = 0
            for i in range(s, e):
                for j in range(m):
                    c = Xc[i, j]
                    if c >= 0 and loc[c] < 0:
                        if kp < KMAX_LOCAL:
                            loc[c] = kp
                            glob[kp] = c
                            kp += 1
                        else:
                            over[ch] += 1
            for r in range(NS):
                x[r] = 0.0
                for c in range(NS):
                    P[r, c] = 0.0
            for j in range(kp):
                for r in range(NS):
                    A[r, j] = 0.0
            P[0, 0] = DIFFUSE
            P[1, 1] = s_slope2
            P[2, 2] = s_beta2
            P[3, 3] = max(s_eta2, 1e-12)
            for i in range(s, e):
                if i > s:
                    dt = t[i] - t[i - 1]
                    if season[i] != season[i - 1]:
                        x[1] = 0.0
                        for j in range(kp):
                            A[1, j] = 0.0
                        for c in range(NS):
                            P[1, c] = 0.0; P[c, 1] = 0.0
                        P[0, 0] += q_jump + q_day * min(dt, 60.0)
                        P[1, 1] = s_slope2
                    elif dt > 0:
                        u = dt / 365.0
                        x[0] += u * x[1]
                        for j in range(kp):
                            A[0, j] += u * A[1, j]
                        p00 = P[0, 0] + 2 * u * P[0, 1] + u * u * P[1, 1]
                        for c in range(1, NS):
                            v_ = P[0, c] + u * P[1, c]
                            P[0, c] = v_; P[c, 0] = v_
                        P[0, 0] = p00 + q_day * dt
                if not train[i]:
                    continue
                hx = xc[i]
                he = xe[i]
                l0 = ld[i]
                for c in range(NS):
                    PH[c] = l0 * P[c, 0] + hx * P[c, 2] + he * P[c, 3]
                F = l0 * PH[0] + hx * PH[2] + he * PH[3] + rvar[i]
                vz = z[i] - (l0 * x[0] + hx * x[2] + he * x[3])
                for j in range(kp):
                    xrow[j] = 0.0
                for j in range(m):
                    c = Xc[i, j]
                    if c >= 0 and loc[c] >= 0:
                        xrow[loc[c]] += Xv[i, j]
                for j in range(kp):
                    vx[j] = xrow[j] - (l0 * A[0, j] + hx * A[2, j] + he * A[3, j])
                Sc = S_all[ch]
                sc = s_all[ch]
                for a in range(kp):
                    if vx[a] == 0.0:
                        continue
                    ga = glob[a]
                    va = vx[a] / F
                    sc[ga] += va * vz
                    for b in range(kp):
                        Sc[ga, glob[b]] += va * vx[b]
                for r in range(NS):
                    g = PH[r] / F
                    x[r] += g * vz
                    for j in range(kp):
                        A[r, j] += g * vx[j]
                for r in range(NS):
                    for c in range(NS):
                        P[r, c] -= PH[r] * PH[c] / F
            for j in range(kp):
                loc[glob[j]] = -1
    S = np.zeros((K, K)); sv = np.zeros(K)
    for ch in range(n_chunks):
        S += S_all[ch]
        sv += s_all[ch]
    return S, sv, over.sum()


def sharedFeatures(D):
    """Per row, the calendar facts the shared columns need, from the person-
    sorted rows: peak (last race of a sport's season, 4+ races), recovery
    (break length bin and days since the return, inside one season), and
    the track distance class. Pure."""
    n = D["y"].size
    person, t, season, grp = D["person"], D["t"], D["season"], D["group"]
    # peak: per (person, season, GROUP) the last date, if 4+ races. ! INDOOR
    #   IS ITS OWN SEASON: an indoor championship is tapered for, and with
    #   indoor and outdoor as one "track" the indoor taper went unexplained
    #   and pulled the indoor level 1.2% fast on the planted world.
    key = (person.astype(np.int64) * 4096 + season.astype(np.int64) % 4096) * 4 + grp
    _, kinv, kcnt = np.unique(key, return_inverse=True, return_counts=True)
    kinv = kinv.reshape(-1)
    last = np.full(kcnt.size, -np.inf)
    np.maximum.at(last, kinv, t)
    # ! ONLY A SEASON THAT HAS ENDED. Mid-season a runner's newest race is
    #   not their last; read as a taper it would credit last week's race.
    #   A season counts as over once SEASON_CLOSED_DAYS pass with no race in
    #   it (run_joint's own rule); D["t_now"] is the pack's or the cut's day.
    t_now = float(D.get("t_now", t.max()))
    peak = (t >= last[kinv]) & (kcnt[kinv] >= 4) & (last[kinv] <= t_now - SEASON_CLOSED_DAYS)
    # recovery: the most recent 21-90 day gap inside this season
    gap_len = np.zeros(n); since = np.full(n, np.inf)
    starts = D["starts"]
    first = np.zeros(n, dtype=bool); first[starts[:-1][np.diff(starts) > 0]] = True
    dt = np.r_[0.0, np.diff(t)]
    new_gap = (~first) & (dt >= BREAK_BINS[0]) & (season == np.r_[season[:1], season[:-1]])
    reset = first | (season != np.r_[season[:1], season[:-1]])
    cur_len, cur_t = 0.0, 0.0
    for i in range(n):              # one pass; cheap next to the fit
        if reset[i]:
            cur_len, cur_t = 0.0, 0.0
        if new_gap[i]:
            cur_len, cur_t = dt[i], t[i]
        if cur_len > 0:
            gap_len[i] = cur_len
            since[i] = t[i] - cur_t
    dist = D.get("dist_m", np.zeros(n))
    ev = np.where((grp != GROUP_XC) & (dist > 0),
                  np.searchsorted(EVENT_EDGES, dist), -1)
    return dict(peak=peak, gap_len=gap_len, since=since, ev=ev, dist=np.asarray(dist, float))


def sharedDesign(D, feats, ability, fix_mu=None, n_knots=None,
                 terms=("peak", "recovery", "event")):
    """The shared columns as (Xc, Xv, names, blocks): per row up to 16
    (column, value) pairs, -1 padded. blocks maps a name to its column ids
    so the caller can read the curve, the levels and the rest back.
    ability: per row the runner's level less the reference (log time), for
    the gap's slope with ability."""
    n = D["y"].size
    lv, phase, grp = D["lv"], D["phase"], D["group"]
    n_lv = int(lv.max()) + 1
    nk = n_knots or int(SEASON_DAYS // SPLINE_KNOT_DAYS) + 1
    pin = int(round(PIN_PHASE / (SEASON_DAYS / (nk - 1))))
    names, blocks = [], {}

    def add(name, count):
        ids = list(range(len(names), len(names) + count))
        names.extend(f"{name}[{k}]" for k in range(count)) if count > 1 else names.append(name)
        blocks[name] = np.array(ids, dtype=np.int64)
        return blocks[name]
    mu_ids, lam_ids = {}, {}
    by_level = "mu_by_level" in terms
    for g, nm in ((GROUP_XC, "XC"), (GROUP_IN, "in")):
        if not (fix_mu and g in fix_mu):
            mu_ids[g] = add("mu_" + nm, n_lv if by_level else 1)
        if "lam_in_gls" in terms:          # the plug-in version, for the test only
            lam_ids[g] = add("lam_" + nm, 1)[0]
    curve = add("curve", n_lv * nk).reshape(n_lv, nk)
    seam = add("seam", n_lv)
    peak = add("peak", 1)[0]
    rec = add("recovery", len(BREAK_BINS) - 1)
    ev_n = len(EVENT_EDGES) + 1
    ev = add("event", n_lv * ev_n).reshape(n_lv, ev_n)
    alt = add("altitude", 2)          # per km above ALT_FLOOR_M: XC, track
    dc_on = "dist_curve" in terms and "ld" in D
    if dc_on:
        n_ld = len(LD_KNOTS)
        dcurve = add("dist_curve", n_lv * n_ld).reshape(n_lv, n_ld)
    wx_on = "weather" in terms and "wx_temp" in D
    if wx_on:
        n_tk = len(WX_TEMP_KNOTS)
        wx_t = add("wx_temp", 2 * n_tk).reshape(2, n_tk)
        wx_w = add("wx_wind", 2)
        wx_e = add("wx_wet", 2)
    K = len(names)

    cols, vals = [], []
    def put(c, v):
        cols.append(np.asarray(c, dtype=np.int64)); vals.append(np.asarray(v, dtype=np.float64))
    # one slot for both levels: a row is XC or indoor, never both
    mu_col = np.full(n, -1, dtype=np.int64)
    for g, cid in mu_ids.items():
        on = grp == g
        mu_col = np.where(on, cid[lv] if by_level else cid[0], mu_col)
    if mu_ids:
        put(mu_col, np.ones(n))
    for g, cid in lam_ids.items():
        on = grp == g
        put(np.where(on, cid, -1), np.where(on, ability, 0.0))
    k, fr = _splineBasis(phase, nk)
    k1 = np.minimum(k + 1, nk - 1)
    c0 = np.where(k == pin, -1, curve[lv, k])
    c1 = np.where(k1 == pin, -1, curve[lv, k1])
    if "nocurve" not in terms:
        put(c0, 1 - fr); put(c1, fr)
    # seams: the count crossed INTO each level so far, per person.
    # ! FOUR SLOTS, NOT ONE PER LEVEL. A runner enters at most four levels
    #   (elementary, ms, hs, college or pro) but the corpus has a dozen pools; a column per
    #   level on every row was ~10 GB of -1s on the full pack.
    starts = D["starts"]
    season = D["season"]
    is_seam = np.r_[False, season[1:] != season[:-1]]
    is_seam[starts[:-1]] = False
    cols3, vals3 = _seamSlots(starts, lv, is_seam, n_lv, seam.astype(np.int64))
    for j in range(cols3.shape[1]):
        put(cols3[:, j], vals3[:, j])
    if "peak" in terms:
        put(np.where(feats["peak"], peak, -1), np.ones(n))
    for b in range(len(BREAK_BINS) - 1):
        if "recovery" not in terms:
            break
        on = (feats["gap_len"] >= BREAK_BINS[b]) & (feats["gap_len"] < BREAK_BINS[b + 1])
        put(np.where(on, rec[b], -1),
            np.where(on, np.exp(-np.minimum(feats["since"], 1e6) / RECOVERY_DAYS), 0.0))
    e = feats["ev"]
    pin_ev = 1
    on = (e >= 0) & (e != pin_ev) & ("event" in terms)
    put(np.where(on, ev[lv, np.maximum(e, 0)], -1), np.ones(n))
    # ★ ALTITUDE, FITTED: km above the floor times the event's share of the
    #   5000's cost (joint_solve.ALT_DIST_KNOTS, NCAA tables); identified by
    #   runners seen at more than one elevation, each against their own
    #   trajectory, so a resident's ability is not their altitude.
    ak = D.get("alt_km")
    if ak is not None:
        share = np.interp(np.log(np.maximum(feats["dist"], 1.0)),
                          np.log([k for k, _ in ALT_DIST_KNOTS]),
                          [v for _, v in ALT_DIST_KNOTS])
        share = np.where(feats["dist"] > 0, share, 1.0)
        on = ak > 0
        put(np.where(on, alt[(grp != GROUP_XC).astype(np.int64)], -1),
            np.where(on, ak * np.where(grp == GROUP_XC, 1.0, share), 0.0))
    if dc_on:
        kn = np.asarray(LD_KNOTS)
        x = np.clip(np.asarray(D["ld"], dtype=np.float64), kn[0], kn[-1] - 1e-9)
        kk = np.searchsorted(kn, x, side="right") - 1
        fr = (x - kn[kk]) / (kn[kk + 1] - kn[kk])
        for kcol, wv in ((kk, 1 - fr), (kk + 1, fr)):
            on = kcol != LD_PIN
            put(np.where(on, dcurve[lv, np.minimum(kcol, n_ld - 1)], -1), np.where(on, wv, 0.0))
    if wx_on:
        # XC and outdoor track only; indoor and rows with no weather take none
        sf = np.where(grp == GROUP_XC, 0, np.where(grp == GROUP_OUT, 1, -1))
        tp = np.asarray(D["wx_temp"], dtype=np.float64)
        okw = (sf >= 0) & np.isfinite(tp)
        kn = np.asarray(WX_TEMP_KNOTS)
        x = np.clip(np.where(okw, tp, kn[0]), kn[0], kn[-1] - 1e-9)
        kk = np.searchsorted(kn, x, side="right") - 1
        fr = (x - kn[kk]) / (kn[kk + 1] - kn[kk])
        sfi = np.maximum(sf, 0)
        for kcol, wv in ((kk, 1 - fr), (kk + 1, fr)):
            on = okw & (kcol != WX_TEMP_PIN)
            put(np.where(on, wx_t[sfi, np.minimum(kcol, n_tk - 1)], -1), np.where(on, wv, 0.0))
        for arr, ids in ((D.get("wx_wind"), wx_w), (D.get("wx_wet"), wx_e)):
            if arr is None:
                continue
            a_ = np.asarray(arr, dtype=np.float64)
            on = (sf >= 0) & np.isfinite(a_)
            put(np.where(on, ids[sfi], -1), np.where(on, a_, 0.0))
    Xc = np.stack(cols, axis=1)
    Xv = np.stack(vals, axis=1)
    Xv[Xc < 0] = 0.0
    # columns nobody touches stay out of the solve
    return Xc, Xv, names, blocks, K, nk, pin


@njit(cache=True)
def _seamSlots(starts, lv, is_seam, n_lv, seam_ids):
    """Per row, up to 4 (seam column, count crossed into that level so far)."""
    n = lv.size
    cols = np.full((n, 4), -1, dtype=np.int64)
    vals = np.zeros((n, 4))
    cnt = np.zeros(n_lv, dtype=np.int64)
    order = np.zeros(4, dtype=np.int64)
    for p in range(starts.size - 1):
        s, e = starts[p], starts[p + 1]
        for L in range(n_lv):
            cnt[L] = 0
        k = 0
        for i in range(s, e):
            if is_seam[i]:
                L = lv[i]
                if cnt[L] == 0 and k < 4:
                    order[k] = L
                    k += 1
                cnt[L] += 1
            for j in range(k):
                cols[i, j] = seam_ids[order[j]]
                vals[i, j] = cnt[order[j]]
    return cols, vals


def applyDesign(Xc, Xv, b):
    """X b per row."""
    bb = np.r_[b, 0.0]
    return np.sum(np.where(Xc >= 0, Xv * bb[np.where(Xc >= 0, Xc, -1)], 0.0), axis=1)


def fitGLS(D, train, outer=30, fix_mu=None, nested=True, heavy=True,
           fit_kappa=False, verbose=False, curve_sd=None, n_chunks=None,
           terms=("peak", "recovery", "event"), fit_curve_sd=True, kappa0=None):
    """The engine of the review's §4: shared effects by GLS through each
    runner's filter, the runner's state smoothed, courses / venue-days /
    races as shrunk means, every variance by EM. fix_mu {group: level}
    asserts a level instead of measuring it (the shipped XCP_SPORT_LEVEL).
    Returns parameters, their GLS standard errors, per-row predictions and
    per-row performances (y less tilted level, course, day and race)."""
    import os
    n = D["y"].size
    y, xc, grp = D["y"], D["xc"].astype(np.float64), D["group"]
    cell, day, race = D["cell"], D["day"], D["race"]
    has_cell = cell >= 0
    cidx = np.where(has_cell, cell, 0)
    n_cell = int(cidx.max()) + 1
    n_day = int(day.max()) + 1
    n_race = int(race.max()) + 1
    n_lv = int(D["lv"].max()) + 1
    cell_grp = np.zeros(n_cell, dtype=np.int64); cell_grp[cidx[has_cell]] = grp[has_cell]
    day_grp = np.zeros(n_day, dtype=np.int64); day_grp[day] = grp
    race_grp = np.zeros(n_race, dtype=np.int64); race_grp[race] = grp
    tr = train.astype(np.float64)
    n_chunks = n_chunks or max(4 * (os.cpu_count() or 4), 8)

    P = dict(START)
    tau2 = np.array(P["tau2"]); s_w2 = np.array(P["s_w2"]); s_v2 = np.array(P["s_v2"])
    sigma2, q_day, q_jump, s_beta2 = P["sigma2"], P["q_day"], P["q_jump"], P["s_beta2"]
    s_slope2 = P["s_slope2"]
    # ★ THE TILT, FITTED (fit_kappa=True, what ss_eval runs): the within-race
    #   step below. It once read -0.29 on a world planted at +0.30; since the
    #   diffuse start and the covariance terms it reads +0.26 / +0.29 / +0.31
    #   (planted 0.30) and -0.04 / -0.01 / +0.01 (planted 0), three seeds. A
    #   step on the level as predicted before the row (no row noise in the
    #   regressor) was tried and is far noisier (-0.15 to +0.44). Without
    #   fit_kappa the tilt is the current engine's TILT_K, 0.31.
    kappa = KAPPA_STATED if kappa0 is None else float(kappa0)
    curve_var = (curve_sd or 0.01) ** 2
    mu_fixed = np.zeros(3)
    lam = np.zeros(3)
    if fix_mu:
        for g, v in fix_mu.items():
            mu_fixed[g] = v
    c = np.zeros(n_cell); w = np.zeros(n_day); v = np.zeros(n_race)
    lam_w = np.ones(n_day); lam_v = np.ones(n_race)
    wrow = np.ones(n)
    th = np.full(n, np.median(y[train])); be = np.zeros(n); vth = np.full(n, 0.01)
    feats = sharedFeatures(D)
    two_piece = "two_piece" in terms
    # ★ HOW SURE THE COURSE IS REACHES THE RUNNER (terms "prop_var"): a row
    #   counts in its runner's filter with variance sigma2 / weight plus
    #   h^2 Var(course + day + race) from the last pass, so a result on a
    #   course raced once moves the runner's level less than one on a
    #   course raced a hundred times. The block steps otherwise treat each
    #   course as known exactly when they fit the runner.
    prop = "prop_var" in terms
    cond_var = np.zeros(n)
    side_s2 = np.array([P["sigma2"], P["sigma2"]])     # fast, slow
    # ★ A COURSE'S NUMBER WALKS FROM SEASON TO SEASON (terms "era_walk",
    #   D["cell_course"] / D["cell_season"] per cell): a cell per (course,
    #   season), each tied to the course's previous season by a walk whose
    #   variance is the data's. A re-routed course moves; one that did not
    #   pools every season it was raced. Replaces the fixed 2-year eras.
    era = "era_walk" in terms and "cell_course" in D
    q_era = 0.005 ** 2
    if era:
        ea, eb, egap = eraPairs(np.asarray(D["cell_course"]), np.asarray(D["cell_season"]))
    day_cell_t = np.full(n_day, -1, dtype=np.int64)       # a venue-day's cell, -1 for none
    day_cell_t[day[has_cell]] = cidx[has_cell]
    # ★ THE RUNNER'S OWN DISTANCE CURVE (terms "endurance"): eta_p, how much
    #   slower than the population's curve this runner gets per unit of
    #   ln(distance / 5000) -- a miler's 3200 normalised to 5000 reads
    #   faster than their 5000 does. Static per runner, N(0, s_eta2), its
    #   variance by EM; a runner seen at one distance stays at 0. It moves
    #   a result to the runner's OWN 5000, so the 5K column carries it.
    endur = "endurance" in terms and "dist_m" in D
    x_end = (np.log(np.where(np.asarray(D["dist_m"]) > 0, np.asarray(D["dist_m"]), 5000.0) / 5000.0)
             if endur else np.zeros(n))
    n_pp = D["starts"].size - 1
    eta = np.zeros(n_pp); s_eta2 = 0.02 ** 2; en = np.zeros(n)
    spread_p = np.zeros(n_pp, dtype=bool)
    if endur:
        # runners whose distances vary inside one surface: they alone measure s_eta
        pg = D["person"].astype(np.int64) * 3 + grp
        lo_ = np.full(3 * n_pp, np.inf); hi_ = np.full(3 * n_pp, -np.inf)
        np.minimum.at(lo_, pg[train], x_end[train]); np.maximum.at(hi_, pg[train], x_end[train])
        spread_p = ((hi_ - lo_) > 0.2).reshape(n_pp, 3).any(axis=1)
    # ★ A TEAM INSIDE A RACE (terms "team_race", D["team"]): a shortfall
    #   every runner of one team shares in one race -- a team told to run
    #   tempo at a league meet (owner, 2026-10-18). Nested in the race,
    #   Student-t, only where the team has 2+ runners in it; out of the
    #   rating like the race's own effect.
    team_on = "team_race" in terms and "team" in D
    rt = np.full(n, -1, dtype=np.int64); n_rt = 0
    if team_on:
        tm_ = np.asarray(D["team"]).astype(np.int64)
        okt = tm_ >= 0
        key_t = race[okt] * (int(tm_.max()) + 2) + tm_[okt]
        u_t, inv_t, cnt_t = np.unique(key_t, return_inverse=True, return_counts=True)
        code = np.where(cnt_t[inv_t.reshape(-1)] >= 2, inv_t.reshape(-1), -1)
        good = np.unique(code[code >= 0])
        remap = np.full(u_t.size, -1, dtype=np.int64); remap[good] = np.arange(good.size)
        rt[okt] = np.where(code >= 0, remap[np.maximum(code, 0)], -1)
        n_rt = int(good.size)
    tmv = np.zeros(max(n_rt, 1)); lam_t = np.ones(max(n_rt, 1)); s_t2 = 0.005 ** 2
    has_rt = rt >= 0
    tmr = np.zeros(n)
    cell_days = np.bincount(np.unique(cidx[has_cell] * (n_day + 1) + day[has_cell])
                            // (n_day + 1), minlength=n_cell)
    day_races = np.bincount(np.unique(day * (n_race + 1) + race) // (n_race + 1),
                            minlength=n_day)
    race_day = day_of_race(day, race, n_race)
    day_cell = np.zeros(n_day, dtype=np.int64); day_cell[day[has_cell]] = cidx[has_cell]
    fitness = np.zeros(n)          # the shared fitness part of X b
    # ★ ONE REFERENCE, FIXED. Both kernels see z - C and theta - C, so the
    #   loading (1 + lam) acts on theta - C in both and mu is the gap of a
    #   runner at C (the median row). Two medians, one per kernel, made the
    #   loading's intercept differ between them and the gap walked off.
    C = float(np.median(y[train]))
    n_p = D["starts"].size - 1
    on_gauge = (np.bincount(D["person"], weights=((grp == GROUP_OUT) & train).astype(float),
                            minlength=n_p) > 0)[D["person"]]
    b = None
    se = None

    for it in range(outer):
        level = th + fitness
        theta_ref = np.median(level[train])
        h = 1.0 + kappa * (level - theta_ref)
        Xc, Xv, names, blocks, K, nk, pin = sharedDesign(D, feats, level - theta_ref,
                                                          fix_mu, terms=terms)
        eff = np.where(has_cell, c[cidx], 0.0) + w[day] + v[race] + tmr
        z = y - mu_fixed[grp] - h * eff
        rvar = sigma2 / np.maximum(wrow, 1e-6) + (h * h * cond_var if prop else 0.0)
        ld = 1.0 + lam[grp]
        zc = z - C
        S, s, over = _glsPeople(D["starts"], D["t"], D["season"], zc,
                                xc, Xc, Xv, rvar,
                                train, K, q_day, q_jump, s_slope2, s_beta2, n_chunks, ld,
                                x_end, s_eta2 if endur else 0.0)
        if over:
            raise RuntimeError(f"statespace: {over} shared columns past KMAX_LOCAL "
                               f"({KMAX_LOCAL}) for some runner; raise it")
        # the curve's curvature prior, per level, second differences
        Pen = np.zeros((K, K))
        Dm = np.diff(np.eye(nk), 2, axis=0)
        pen_c = Dm.T @ Dm / curve_var
        for L in range(n_lv):
            ids = blocks["curve"].reshape(n_lv, nk)[L]
            Pen[np.ix_(ids, ids)] += pen_c
        Pen[np.diag_indices(K)] += 1e-6
        # an untouched column (a pinned knot, a level with no seam) is held at 0
        dead = np.diag(S) <= 0
        Pen[dead, dead] += 1.0
        Sinv = np.linalg.inv(S + Pen)
        b = Sinv @ s
        se = np.sqrt(np.maximum(np.diag(Sinv), 0))
        # curvature variance by EM, over the curves' second differences
        cids = blocks["curve"].reshape(n_lv, nk)
        num, cnt = 0.0, 0
        for L in range(n_lv):
            ids = cids[L]
            if np.diag(S)[ids].sum() <= 0:
                continue
            d2 = Dm @ b[ids]
            V2 = Dm @ Sinv[np.ix_(ids, ids)] @ Dm.T
            num += float(np.sum(d2 ** 2) + np.trace(V2)); cnt += d2.size
        if cnt and fit_curve_sd:
            curve_var = max(num / cnt, 1e-8)
        Xb = applyDesign(Xc, Xv, b)
        mu = mu_fixed.copy()
        for g, nm in ((GROUP_XC, "XC"), (GROUP_IN, "in")):
            if "mu_" + nm in blocks:
                ids = blocks["mu_" + nm]
                # one number per group for the log lines: the row-weighted
                # mean of the level's gaps (the per-level ones are in b)
                if ids.size > 1:
                    on = (grp == g) & train
                    mu[g] = float(np.mean(b[ids][D["lv"][on]])) if on.any() else 0.0
                else:
                    mu[g] = b[ids[0]]
        gap_cols = np.zeros(b.size, dtype=bool)
        for nm in ("mu_XC", "mu_in", "lam_XC", "lam_in"):
            if nm in blocks:
                gap_cols[blocks[nm]] = True
        Gest = applyDesign(Xc, Xv, np.where(gap_cols, b, 0.0))
        # ★ CONDITIONS COME OUT OF A RATING, FITNESS STAYS IN. Altitude and
        #   the track event offsets (what the distance curve gets wrong) are
        #   conditions; the season curve, the summer step, the taper and the
        #   post-break recovery are the runner's fitness that day.
        cond_cols = np.zeros(b.size, dtype=bool)
        for nm in ("altitude", "event", "wx_temp", "wx_wind", "wx_wet", "dist_curve"):
            if nm in blocks:
                cond_cols[blocks[nm]] = True
        cond = applyDesign(Xc, Xv, np.where(cond_cols, b, 0.0))
        fitness = Xb - Gest - cond          # curve, seam, peak, recovery
        # the gap's own slope with ability is the loading (1 + lam) on theta:
        # G = mu + lam * theta, read at the reference (theta - theta_ref)
        G0 = Gest + mu_fixed[grp]

        # the runner's state, given the shared part
        zs = y - Xb - mu_fixed[grp] - h * eff
        m0 = np.array([np.median(zs[(D["lv"] == L) & train]) if ((D["lv"] == L) & train).any()
                       else np.median(zs[train]) for L in range(n_lv)])
        X, V, d2s, gap, PR = _smoothPeople(D["starts"], D["t"], D["season"], D["lv"], zs - C, xc,
                                      rvar, train, np.zeros(n_lv), q_day, q_jump,
                                      s_slope2, s_beta2, m0 - C, True, ld,
                                      x_end, s_eta2 if endur else 0.0)
        th = X[:, 0] + C
        sl, be = X[:, 1], X[:, 2]
        vth, vsl, vbe = V[:, 0], V[:, 1], V[:, 2]
        if endur:
            # ★ eta IS A STATE, NOT A STEP: fitted with theta and its
            #   uncertainty, so a noisy distance slope widens the level
            #   rather than moving it. (As a block step after theta it made
            #   cross-country forecasts worse on the planted world, 1.94 ->
            #   1.98%, while fixing the track ratings.) Its variance by EM
            #   over runners whose distances vary inside one surface.
            eta_row, veta = X[:, 3], V[:, 3]
            first_p = D["starts"][:-1][np.diff(D["starts"]) > 0]
            sp = spread_p[D["person"][first_p]]
            if sp.sum() >= 10:
                s_eta2 = float(max(np.mean(eta_row[first_p][sp] ** 2 + veta[first_p][sp]), 1e-8))
            eta = np.zeros(n_pp); eta[D["person"]] = eta_row
            en = eta_row * x_end
        # the part of theta's error the row's own residual shares: Var(theta)
        # and, on XC rows, Cov(theta, beta) (the two trade on grass)
        vres = vth + xc * V[:, 4]
        level = th + fitness
        h = 1.0 + kappa * (level - theta_ref)
        G = G0 + lam[grp] * (th - C)                # the level at this runner's ability
        base_all = y - th - be * xc - fitness - G - cond - en   # = h (c + w + v + team) + e
        base = base_all - h * tmr
        ww = wrow * tr
        # courses, venue-days, races
        r = base - h * (w[day] + v[race])
        c, vc, cden = _shrunkMean(cidx, n_cell, np.where(has_cell, ww * h * r, 0.0),
                                  np.where(has_cell, ww * h * h, 0.0), sigma2 / tau2[cell_grp])
        if era:
            cq = cden + sigma2 / tau2[cell_grp]
            rr = base - h * v[race]
            est, est_var = cellMarginal(day_cell_t, n_cell, day,
                                        np.where(has_cell, ww * h * rr, 0.0),
                                        np.where(has_cell, ww * h * h, 0.0), sigma2,
                                        s_w2[day_grp] / lam_w)
            q_era = eraStep(est, est_var, ea, eb, egap, q_era)
            c, vc = _tiedMeans(c * cq, cq, ea, eb, np.zeros(ea.size), sigma2 / (q_era * egap), c)
        r = base - h * (np.where(has_cell, c[cidx], 0.0) + v[race])
        w, vw, wden = _shrunkMean(day, n_day, ww * h * r, ww * h * h,
                                  sigma2 * lam_w / s_w2[day_grp])
        if nested:
            r = base - h * (np.where(has_cell, c[cidx], 0.0) + w[day])
            v, vv, vden = _shrunkMean(race, n_race, ww * h * r, ww * h * h,
                                      sigma2 * lam_v / s_v2[race_grp])
        # ★ EACH GROUP'S COURSES, DAYS AND RACES AVERAGE ZERO. Their prior
        #   mean is zero, but a block step can still slide every XC cell by
        #   +d while mu_XC slides -d (the same rows, the same fit); the
        #   prior resists that only weakly, and the slide is the gap. The
        #   group means come out here, so mu is "the average XC course
        #   against the average flat outdoor track" by definition, and the
        #   next GLS step re-measures it. (joint_solve.recentreLevels is the
        #   same move.)
        for g in range(3):
            mc = (cell_grp == g) & (cden > 0)
            if mc.any():
                c[mc] -= c[mc].mean()
            md = (day_grp == g) & (wden > 0)
            if md.any():
                w[md] -= w[md].mean()
            if nested:
                mr = (race_grp == g) & (vden > 0)
                if mr.any():
                    v[mr] -= v[mr].mean()
        Dsum = np.where(has_cell, c[cidx], 0.0) + w[day] + v[race]
        if prop:
            cond_var = sigma2 * (np.where(has_cell, vc[cidx], 0.0) + vw[day]
                                 + (vv[race] if nested else 0.0))
        if team_on and n_rt:
            r = base_all - h * Dsum
            tmv, vt, tden = _shrunkMean(np.maximum(rt, 0), n_rt, np.where(has_rt, ww * h * r, 0.0),
                                        np.where(has_rt, ww * h * h, 0.0), sigma2 * lam_t / s_t2)
            okk = tden > 0
            if okk.sum() >= 10:
                s_t2 = float(max(np.mean(lam_t[okk] * (tmv[okk] ** 2 + sigma2 * vt[okk])), 1e-8))
            if heavy:
                lam_t = (NU_DAY + 1.0) / (NU_DAY + tmv ** 2 / s_t2)
            tmr = np.where(has_rt, tmv[np.maximum(rt, 0)], 0.0)
        base = base_all - h * tmr
        # ★ THE SLOPES BY EM, NOT BY PLUGGING IN THE ESTIMATE. A runner's
        #   level is estimated from the very rows a slope is fitted on, so a
        #   runner whose XC races ran slow looks both slower and more
        #   grass-bound: regressing on the estimate found a gap slope of
        #   +0.07 and a tilt of -0.42 on a world with neither. The complete-
        #   data M-step carries the level's posterior variance V:
        #     lam   = (sum a r - sum V) / (sum a^2 + sum V),   a = theta - ref
        #     kappa = (sum D a r - sum D V) / (sum D^2 a^2 + sum D^2 V)
        #   (each runner's own error, removed). Rows of the group only.
        a_ = th - C
        # ! ONLY RUNNERS WHO ALSO RACE ON THE GAUGE. For a runner who never
        #   races outdoor track, theta's scale on XC rows IS (1 + lam): any
        #   lam fits them equally, and with V in the M-step their rows walk
        #   lam to -1 (it did, in 20 passes). The slope is a fact about
        #   people seen on both surfaces, and only they measure it.
        if "noslope" not in terms:
            for g in (GROUP_XC, GROUP_IN):
                m = (grp == g) & train & on_gauge
                if m.sum() > 100:
                    # r: the row less everything but lam * theta
                    rr = (base + lam[g] * a_ - h * Dsum)[m]
                    wa = ww[m]
                    num = np.sum(wa * a_[m] * (rr - np.average(rr, weights=wa))) - np.sum(wa * vres[m])
                    den = np.sum(wa * (a_[m] - np.average(a_[m], weights=wa)) ** 2) + np.sum(wa * vth[m])
                    lam[g] = float(num / den)
        # ★ THE TILT FROM INSIDE EACH RACE. Every row of a race shares its
        #   course, day and race effect, so an error in those (shrinkage
        #   differs between a fast field and a slow one) is constant within
        #   the race and drops out of a within-race contrast. The tilt is
        #   then: does the spread between a race's fast and slow runners
        #   grow with the race's difficulty? On the planted world the
        #   pooled regression read -0.44 with no tilt planted; within races
        #   it reads near zero, and finds a planted 0.30.
        if fit_kappa:
            a_k = level - theta_ref
            wsum = np.bincount(race, weights=ww, minlength=n_race)
            ok_r = wsum > 0
            a_bar = np.bincount(race, weights=ww * a_k, minlength=n_race)
            a_bar[ok_r] /= wsum[ok_r]
            r_k = base - Dsum
            r_bar = np.bincount(race, weights=ww * r_k, minlength=n_race)
            r_bar[ok_r] /= wsum[ok_r]
            ad = a_k - a_bar[race]
            rd = r_k - r_bar[race]
            nr = np.bincount(race, minlength=n_race)[race]
            shrink = np.where(nr > 1, 1.0 - 1.0 / np.maximum(nr, 1), 0.0)
            num_k = np.sum(ww * Dsum * ad * rd) - np.sum(ww * Dsum * vres * shrink)
            den_k = np.sum(ww * Dsum ** 2 * (ad * ad + vth * shrink))
            if den_k > 0:
                kappa = float(num_k / den_k)
        h = 1.0 + kappa * (level - theta_ref)
        res = base - h * Dsum
        sigma2 = float(np.sum(ww * (res ** 2 + vth)) / max(tr.sum(), 1))
        if two_piece:
            # ★ A BAD DAY IS SLOW, NEVER FAST (terms "two_piece"): the
            #   residual's slow side gets its own scale, so a runner who
            #   was ill or ran tempo pulls their level far less than a
            #   good race does, and the level is the runner's normal day
            slow = res > 0
            for side, m_s in ((1, slow), (0, ~slow)):
                ws = ww * m_s
                if ws.sum() > 10:
                    side_s2[side] = float(np.sum(ws * (res ** 2 + vth)) / ws.sum())
            s2_row = np.where(slow, side_s2[1], side_s2[0])
            wrow = (NU_ROW + 1.0) / (NU_ROW + res ** 2 / s2_row)
            wrow = wrow * sigma2 / s2_row          # rvar = sigma2 / wrow = side's scale / weight
        else:
            wrow = (NU_ROW + 1.0) / (NU_ROW + res ** 2 / sigma2)
        # the walk and the trend
        same = (gap > 0) & (D["season"] == np.r_[D["season"][:1], D["season"][:-1]])
        same[D["starts"][:-1]] = False
        if same.any():
            q_day = float(np.mean(d2s[same] / gap[same]))
        seamr = np.r_[False, D["season"][1:] != D["season"][:-1]]
        seamr[D["starts"][:-1]] = False
        if seamr.any():
            q_jump = float(max(np.mean(d2s[seamr] - q_day * np.minimum(gap[seamr], 60.0)), 1e-6))
            first_s = seamr.copy(); first_s[D["starts"][:-1]] = True
            s_slope2 = float(max(np.mean(sl[first_s] ** 2 + vsl[first_s]), 1e-8))
        firsts = D["starts"][:-1][np.diff(D["starts"]) > 0]
        s_beta2 = float(max(np.mean(be[firsts] ** 2 + vbe[firsts]), 1e-8))
        for g in range(3):
            mc = (cell_grp == g) & (cell_days >= 2) & (cden > 0)
            if mc.sum() >= 10:
                tau2[g] = max(np.mean(c[mc] ** 2 + sigma2 * vc[mc]), 1e-8)
            md = (day_grp == g) & (wden > 0) & (cell_days[day_cell] >= 2)
            if md.sum() >= 10:
                s_w2[g] = max(np.mean(lam_w[md] * (w[md] ** 2 + sigma2 * vw[md])), 1e-8)
            if nested:
                mr = (race_grp == g) & (vden > 0) & (day_races[race_day] >= 2)
                if mr.sum() >= 10:
                    s_v2[g] = max(np.mean(lam_v[mr] * (v[mr] ** 2 + sigma2 * vv[mr])), 1e-8)
        if heavy:
            lam_w = (NU_DAY + 1.0) / (NU_DAY + w ** 2 / s_w2[day_grp])
            if nested:
                lam_v = (NU_DAY + 1.0) / (NU_DAY + v ** 2 / s_v2[race_grp])
        if verbose and (it % 5 == 0 or it == outer - 1):
            pk = b[blocks["peak"][0]]
            rc = b[blocks["recovery"]]
            print(f"[ss] {it:3d} sigma {np.sqrt(sigma2):.4f} walk/30d {np.sqrt(q_day * 30):.4f} "
                  f"trend {np.sqrt(s_slope2):.4f} seam sd {np.sqrt(q_jump):.4f} "
                  f"mu_XC {mu[GROUP_XC]:+.4f} lam_XC {lam[GROUP_XC]:+.3f} "
                  f"mu_in {mu[GROUP_IN]:+.4f} lam_in {lam[GROUP_IN]:+.3f} "
                  f"kappa {kappa:+.3f} peak {pk:+.4f} "
                  f"recovery {np.round(rc, 4)} curve sd {np.sqrt(curve_var):.4f}")

    eff_c = np.where(has_cell, c[cidx], 0.0)
    pred_known = th + be * xc + fitness + G + cond + h * eff_c + en
    day_trained = np.bincount(day, weights=tr, minlength=n_day) > 0
    race_trained = np.bincount(race, weights=tr, minlength=n_race) > 0
    rt_trained = (np.bincount(np.maximum(rt, 0), weights=tr * has_rt, minlength=max(n_rt, 1)) > 0)
    pred = pred_known + h * (np.where(day_trained[day], w[day], 0.0)
                             + np.where(race_trained[race], v[race], 0.0)
                             + np.where(has_rt & rt_trained[np.maximum(rt, 0)], tmr, 0.0))
    if two_piece:
        # a held-out row's best guess is the residual's median, which a
        # heavier slow side puts a little past the level
        res_tr = (base - h * Dsum)[train]
        pred = pred + float(np.median(res_tr))
    perf = y - G - cond - h * (eff_c + w[day] + v[race] + tmr) - en
    # ★ EVERY RATING CARRIES ITS SD (rating = perf on the pool's scale):
    #   the course, day and race it was corrected by are estimates, and how
    #   well each is known is their posterior variance (sigma2 units). A
    #   result on a course raced once, on a day nobody else ran, is a
    #   rating with a wide band; a board can show it as such or hold it.
    vcell = np.where(has_cell, vc[cidx], 0.0)
    perf_sd = np.sqrt(np.maximum(sigma2 * h * h * (vcell + vw[day] + (vv[race] if nested else 0.0)),
                                 0.0))
    out = dict(pred=pred, perf=perf, perf_sd=perf_sd, h=h, theta=th, beta=be, theta_var=vth, mu=mu,
               lam=lam, theta_ref=theta_ref, level=th + fitness,
               kappa=kappa, sigma2=sigma2, q_day=q_day, q_jump=q_jump,
               s_slope2=s_slope2, s_beta2=s_beta2, tau2=tau2, s_w2=s_w2, s_v2=s_v2,
               cell=c, day=w, race=v, b=b, se=se, names=names, blocks=blocks,
               curve_sd=np.sqrt(curve_var), n_knots=nk, fitness=fitness,
               cell_var=sigma2 * vc, sigma_fast=np.sqrt(side_s2[0]), sigma_slow=np.sqrt(side_s2[1]))
    if era:
        out["era_sd"] = float(np.sqrt(q_era))
    if endur:
        out.update(eta=eta, eta_sd=float(np.sqrt(s_eta2)), endurance_row=en)
    if team_on:
        out.update(team_sd=float(np.sqrt(s_t2)), team_row=tmr, n_team_races=n_rt)
    for nm in ("mu_XC", "mu_in", "lam_XC", "lam_in"):
        out[nm + "_se"] = float(se[blocks[nm][0]]) if nm in blocks else 0.0
    # ★ EACH LEVEL READ AT ITS OWN TYPICAL RUNNER. With the slope in
    #   ability, mu is the gap of a runner at C; a college man sits ~15%
    #   faster than C and a middle schooler ~20% slower, and reading their
    #   gap at C is reading it for nobody. Per level: mu_L + lam (median
    #   theta of that level's rows of that surface - C).
    out["gap_by_level"] = {}
    for nm, g in (("mu_XC", GROUP_XC), ("mu_in", GROUP_IN)):
        if nm not in blocks:
            continue
        ids = blocks[nm]
        vals, ses, at = np.zeros(n_lv), np.zeros(n_lv), np.zeros(n_lv)
        for L in range(n_lv):
            m = (grp == g) & (D["lv"] == L) & train
            if not m.any():
                continue
            k = ids[L] if ids.size > 1 else ids[0]
            at[L] = float(np.median(th[m]))
            vals[L] = b[k] + lam[g] * (at[L] - C)
            ses[L] = se[k]
        out["gap_by_level"][nm] = (vals, ses, at)
    # the gap at the reference runner (mu absorbs -lam * ref through the loading)
    out["C"] = C
    out["gap_ref"] = {g: float(mu[g]) for g in (GROUP_XC, GROUP_IN)}
    out["G"] = G
    out["cond"] = cond
    for nm in ("wx_temp", "wx_wind", "wx_wet", "dist_curve"):
        if nm in blocks:
            out[nm] = b[blocks[nm]]; out[nm + "_se"] = se[blocks[nm]]
    if "altitude" in blocks:
        out["altitude"] = b[blocks["altitude"]]
        out["altitude_se"] = se[blocks["altitude"]]
    out["base"] = base
    out["Dsum"] = Dsum
    return out


# ------------------------------------------------------------------ #
# THE BRIDGES: what can measure the gap, and how much of it there is
# ------------------------------------------------------------------ #
#
# ★ WHAT DECIDES THE GAP (planted world, review §4.4). The XC level is
#   measured only by runners seen on both surfaces close enough in time
#   that their own trajectory carries one to the other -- and only through
#   venues that are ALSO raced at other times, because a venue raced only
#   at bridge time has its own course number to absorb the gap. On the
#   planted world, December bridges alone (XC finals, the first indoor
#   races) missed the gap by 1.3-3.5% seed to seed; with fall and summer
#   track in, every seed landed within 0.5%. So the report counts each
#   bridge kind's rows, runners and venues -- and how many of those venues
#   are raced outside the bridge -- and refits without each kind.
BRIDGE_KINDS = ("december_xc", "fall_track", "summer_track", "december_indoor",
                "indoor_outdoor")
BRIDGE_DAYS = 45.0


def bridgeMasks(D):
    """Per bridge kind, the rows that ARE the bridge (bool per row):
      december_xc      XC from 1 December (phase >= 122) by a runner with a
                       track race the same season
      fall_track       outdoor track before 1 December by a runner with an XC
                       race within BRIDGE_DAYS
      summer_track     outdoor track from 15 June (phase >= 318) by a runner
                       whose next XC race is within 90 days
      december_indoor  indoor within BRIDGE_DAYS after the runner's XC race
      indoor_outdoor   indoor within BRIDGE_DAYS of the runner's outdoor race"""
    person, t, g, ph, season = D["person"], D["t"], D["group"], D["phase"], D["season"]
    n = t.size
    out = {k: np.zeros(n, dtype=bool) for k in BRIDGE_KINDS}
    starts = D["starts"]
    for p in range(starts.size - 1):
        s, e = starts[p], starts[p + 1]
        if e - s < 2:
            continue
        gg, tt, pp, se = g[s:e], t[s:e], ph[s:e], season[s:e]
        xc_t = tt[gg == GROUP_XC]
        out_t = tt[gg == GROUP_OUT]
        trk_seasons = set(se[gg != GROUP_XC].tolist())
        for j in range(e - s):
            i = s + j
            if gg[j] == GROUP_XC and pp[j] >= 122 and se[j] in trk_seasons:
                out["december_xc"][i] = True
            elif gg[j] == GROUP_OUT:
                if pp[j] < 122 and xc_t.size and np.min(np.abs(xc_t - tt[j])) <= BRIDGE_DAYS:
                    out["fall_track"][i] = True
                if pp[j] >= 318:
                    nxt = xc_t[xc_t > tt[j]]
                    if nxt.size and nxt.min() - tt[j] <= 90:
                        out["summer_track"][i] = True
            elif gg[j] == GROUP_IN:
                prev = xc_t[xc_t <= tt[j]]
                if prev.size and tt[j] - prev.max() <= BRIDGE_DAYS:
                    out["december_indoor"][i] = True
                if out_t.size and np.min(np.abs(out_t - tt[j])) <= BRIDGE_DAYS:
                    out["indoor_outdoor"][i] = True
    return out


def bridgeReport(D, masks=None):
    """Lines: per bridge kind and level, rows, runners, venues, and the share
    of those venues raced outside the bridge (what lets the bridge pin the
    gap rather than its own course numbers)."""
    masks = masks or bridgeMasks(D)
    lv = D["lv"]
    names = D.get("lv_names") or [str(i) for i in range(int(lv.max()) + 1)]
    lines = [f"{'bridge':18s} {'level':12s} {'rows':>9s} {'runners':>8s} {'venues':>7s} "
             f"{'raced otherwise':>16s}"]
    cell = D["cell"]
    any_bridge = np.zeros(cell.size, dtype=bool)
    for m in masks.values():
        any_bridge |= m
    other_cells = set(np.unique(cell[~any_bridge]).tolist())
    for k in BRIDGE_KINDS:
        m = masks[k]
        for L in range(int(lv.max()) + 1):
            mm = m & (lv == L)
            if not mm.any():
                continue
            cells = np.unique(cell[mm])
            shared = sum(1 for c in cells.tolist() if c in other_cells)
            lines.append(f"{k:18s} {names[L]:12s} {int(mm.sum()):>9,} "
                         f"{np.unique(D['person'][mm]).size:>8,} {cells.size:>7,} "
                         f"{shared:>9,} ({shared / max(cells.size, 1):.0%})")
    return lines


def subsetRows(D, keep):
    """D restricted to rows `keep`, codes re-densed, segments rebuilt."""
    E = {k: (v[keep] if isinstance(v, np.ndarray) and v.shape[:1] == D["y"].shape else v)
         for k, v in D.items()}
    for k in ("person", "cell", "day", "race"):
        if k in E:
            _, inv = np.unique(E[k], return_inverse=True)
            E[k] = inv.reshape(-1).astype(np.int64)
    E["starts"] = segments(E["person"])
    return E


def regionCheck(D, R, region, min_people=30):
    """Per region: does XC there sit right against the track, for the runners
    who race both? Each such runner's grass offset (beta) plus their mean
    XC residual less their mean track residual is how much faster their XC
    reads than their track says it should; its mean over a region's dual
    runners is that region's XC miscalibration (0 if right). The prior holds
    beta's mean at 0 overall, not per region, so this is a real check.
    region: per row a region code (-1 none); the XC rows' region counts.
    Returns rows of (region, runners, mean, se), mean in log time."""
    res = D["y"] - R["pred"]
    xc = D["group"] == GROUP_XC
    tf = ~xc
    p = D["person"]
    n_p = int(p.max()) + 1
    nx = np.bincount(p, weights=xc.astype(float), minlength=n_p)
    nt = np.bincount(p, weights=tf.astype(float), minlength=n_p)
    rx = np.bincount(p, weights=np.where(xc, res, 0.0), minlength=n_p)
    rt = np.bincount(p, weights=np.where(tf, res, 0.0), minlength=n_p)
    beta = np.zeros(n_p)
    beta[p] = R["beta"]
    dual = (nx >= 2) & (nt >= 2)
    d = beta + rx / np.maximum(nx, 1) - rt / np.maximum(nt, 1)
    # a runner's region: the mode of their XC rows' regions
    reg_p = np.full(n_p, -1)
    m = xc & (region >= 0)
    if m.any():
        key = p[m].astype(np.int64) * (int(region.max()) + 1) + region[m]
        u, cnt = np.unique(key, return_counts=True)
        pp, rr = u // (int(region.max()) + 1), u % (int(region.max()) + 1)
        order = np.lexsort((-cnt, pp))
        first = np.r_[True, pp[order][1:] != pp[order][:-1]]
        reg_p[pp[order][first]] = rr[order][first]
    out = []
    for r in np.unique(reg_p[dual & (reg_p >= 0)]):
        v = d[dual & (reg_p == r)]
        if v.size >= min_people:
            out.append((int(r), int(v.size), float(v.mean()),
                        float(v.std(ddof=1) / np.sqrt(v.size))))
    return out
