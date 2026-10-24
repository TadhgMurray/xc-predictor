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
#                  gauge). mu_XC IS the XC-to-track gap, and it is fitted.
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
# THE PERSON STEP: Kalman filter + RTS smoother, state (theta, beta)
# ------------------------------------------------------------------ #

@njit(cache=True, inline="always")
def _inv3(A, out):
    """Closed-form inverse of a 3x3 (symmetric positive definite here)."""
    a, b, c = A[0, 0], A[0, 1], A[0, 2]
    d, e, f = A[1, 0], A[1, 1], A[1, 2]
    g, h, i = A[2, 0], A[2, 1], A[2, 2]
    c00 = e * i - f * h; c01 = -(d * i - f * g); c02 = d * h - e * g
    det = a * c00 + b * c01 + c * c02
    if abs(det) < 1e-300:
        det = 1e-300
    out[0, 0] = c00 / det; out[1, 0] = c01 / det; out[2, 0] = c02 / det
    out[0, 1] = -(b * i - c * h) / det; out[1, 1] = (a * i - c * g) / det
    out[2, 1] = -(a * h - b * g) / det
    out[0, 2] = (b * f - c * e) / det; out[1, 2] = -(a * f - c * d) / det
    out[2, 2] = (a * e - b * d) / det


@njit(cache=True, inline="always")
def _mm(A, B, out):
    for r in range(3):
        for c in range(3):
            out[r, c] = A[r, 0] * B[0, c] + A[r, 1] * B[1, c] + A[r, 2] * B[2, c]


@njit(cache=True, inline="always")
def _mmT(A, B, out):
    """A @ B.T"""
    for r in range(3):
        for c in range(3):
            out[r, c] = A[r, 0] * B[c, 0] + A[r, 1] * B[c, 1] + A[r, 2] * B[c, 2]


@njit(parallel=True, cache=True)
def _smoothPeople(starts, t, season, lv, z, xc, rvar, train, jump_mean,
                  q_day, q_jump, s_slope2, s_beta2, m0, dynamic):
    """Per person, the smoothed state x = (theta, slope, beta) at every row,
    and the moments the M-step needs. z is y minus everything but
    theta + beta*xc; rvar the row's observation variance (sigma2 / weight).

      theta  log time, a random walk q_day per day
      slope  this season's own trend, log time per 365 days: drawn fresh at
             every season seam from N(0, s_slope2), constant inside it --
             the runner who is getting fitter faster than the population
      beta   grass specialisation, constant for life

    The transition into row k is theta += slope * dt inside a season; at a
    seam theta += jump (variance q_jump) and the slope is redrawn.
    dynamic=False holds theta constant inside a season and independent
    across seasons, no slope (the current engine's athlete-season), for the
    like-for-like comparison."""
    n = t.size
    X = np.zeros((n, 3)); V = np.zeros((n, 3))
    d2 = np.zeros(n); gap = np.zeros(n)
    n_people = starts.size - 1
    for p in prange(n_people):
        s, e = starts[p], starts[p + 1]
        m = e - s
        if m == 0:
            continue
        xp = np.zeros((m, 3)); Pp = np.zeros((m, 3, 3))
        xf = np.zeros((m, 3)); Pf = np.zeros((m, 3, 3))
        dtk = np.zeros(m); seamk = np.zeros(m, dtype=np.bool_)
        x = np.zeros(3); P = np.zeros((3, 3)); PH = np.zeros(3)
        T1 = np.zeros((3, 3)); T2 = np.zeros((3, 3)); Fm = np.eye(3)
        Pinv = np.zeros((3, 3)); G = np.zeros((3, 3))
        x[0] = m0[lv[s]]
        P[0, 0] = P0
        P[1, 1] = s_slope2 if dynamic else 1e-12
        P[2, 2] = s_beta2
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
                        for c in range(3):
                            P[1, c] = 0.0; P[c, 1] = 0.0
                        P[0, 0] += q_jump + q_day * min(dt, 60.0)
                        P[1, 1] = s_slope2
                    else:
                        x[0] = m0[lv[i]]
                        for c in range(3):
                            P[0, c] = 0.0; P[c, 0] = 0.0
                        P[0, 0] = P0
                elif dynamic and dt > 0:
                    u = dt / 365.0
                    dtk[k] = u
                    x[0] += u * x[1]
                    # P <- F P F' with F = I + u e0 e1'
                    p00 = P[0, 0] + 2 * u * P[0, 1] + u * u * P[1, 1]
                    p01 = P[0, 1] + u * P[1, 1]
                    p02 = P[0, 2] + u * P[1, 2]
                    P[0, 0] = p00 + q_day * dt
                    P[0, 1] = p01; P[1, 0] = p01
                    P[0, 2] = p02; P[2, 0] = p02
            xp[k] = x; Pp[k] = P
            if train[i]:
                hx = xc[i]
                for c in range(3):
                    PH[c] = P[c, 0] + hx * P[c, 2]
                S = PH[0] + hx * PH[2] + rvar[i]
                innov = z[i] - (x[0] + hx * x[2])
                for r in range(3):
                    x[r] += PH[r] / S * innov
                for r in range(3):
                    for c in range(3):
                        P[r, c] -= PH[r] * PH[c] / S
            xf[k] = x; Pf[k] = P
        # RTS backwards
        xs = xf[m - 1].copy(); Ps = Pf[m - 1].copy()
        i = s + m - 1
        for c in range(3):
            X[i, c] = xs[c]; V[i, c] = Ps[c, c]
        nx = np.zeros(3); nP = np.zeros((3, 3)); a = np.zeros(3); b = np.zeros(3)
        for k in range(m - 2, -1, -1):
            i = s + k
            # F into k+1
            for r in range(3):
                for c in range(3):
                    Fm[r, c] = 1.0 if r == c else 0.0
            if seamk[k + 1]:
                if dynamic:
                    Fm[1, 1] = 0.0
                else:
                    Fm[0, 0] = 0.0
            else:
                Fm[0, 1] = dtk[k + 1]
            _mmT(Pf[k], Fm, T1)               # Pf F'
            _inv3(Pp[k + 1], Pinv)
            _mm(T1, Pinv, G)                  # G = Pf F' Pp^-1
            for r in range(3):
                nx[r] = xf[k, r]
                for c in range(3):
                    nx[r] += G[r, c] * (xs[c] - xp[k + 1, c])
            for r in range(3):
                for c in range(3):
                    T1[r, c] = Ps[r, c] - Pp[k + 1, r, c]
            _mm(G, T1, T2)
            _mmT(T2, G, T1)
            for r in range(3):
                for c in range(3):
                    nP[r, c] = Pf[k, r, c] + T1[r, c]
            # E[(theta_{k+1} - theta_k - slope_k dt)^2], the walk's own step
            j = i + 1
            a[0] = 1.0; a[1] = 0.0; a[2] = 0.0
            b[0] = -1.0; b[1] = -dtk[k + 1]; b[2] = 0.0
            _mmT(Ps, G, T2)                    # Cov(x_{k+1}, x_k | all)
            mean = xs[0] - nx[0] - dtk[k + 1] * nx[1]
            q = Ps[0, 0]
            r2 = 0.0; cr = 0.0
            for r in range(3):
                for c in range(3):
                    r2 += b[r] * nP[r, c] * b[c]
                    cr += a[r] * T2[r, c] * b[c]
            d2[j] = mean * mean + q + r2 + 2.0 * cr
            gap[j] = t[j] - t[i]
            for r in range(3):
                xs[r] = nx[r]
                for c in range(3):
                    Ps[r, c] = nP[r, c]
            for c in range(3):
                X[i, c] = xs[c]; V[i, c] = Ps[c, c]
    return X, V, d2, gap


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
        X, V, d2, gap = _smoothPeople(
            D["starts"], D["t"], D["season"], D["lv"], z, xc, rvar, train,
            jump, q_day, q_jump, s_slope2, s_beta2, m0, dynamic)
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
