"""
joint_solve.py -- the joint estimator: ability, difficulty, the per-athlete
sport offset, the race-day effect, the whole-year form curve, the opener rust
and the ability tilt fitted in ONE objective instead of six sequential stages.

    ln(t) = a[athlete_season]
          + beta[athlete_season] * sc            per-athlete sport offset
          + (mu[sport(cell)] + d[cell]) * h(a)   sport level + cell difficulty
          + u[race]                              race-day effect
          + amp(a) * f_pool(day of academic year)   the form curve
          + r_pool * is_first                    opener rust
          + eps

  a      nuisance ability per athlete-season, unpenalised
  beta   sport offset per athlete-season, ridge K (issue #70: K=0.5 is the
         held-out optimum; every unit of freedom given to beta comes out of
         the sport level, which is why mu exists as its own parameter)
  mu     ONE LEVEL PER SHRINKAGE GROUP, unpenalised. The cell prior is
         d_c ~ N(0, tau2[group]) AROUND mu, not around zero. A zero-centred
         prior shrinks XC cells and TF cells toward the same point and
         re-asserts "equal mean difficulty" in proportion to the shrinkage --
         the assumption the old engine's recentring exists to avoid.
  d      cell difficulty about its group level, hierarchically shrunk
  u      race-day effect, u_j ~ N(0, sigma_u2): the term the sequential engine
         does not have, and the reason day-level noise became permanent
         venue difficulty there
  f      the form curve: one piecewise-linear curve per pool over the
         academic year (knots every CURVE_KNOT_DAYS from 1 August), second-
         difference penalised so it is smooth ACROSS the November/December
         boundary. amp(a) is the ability tilt of the season-form amplitude
         (rust_fitness._TILT_PER_POINT, 1.0 at rating 100). The curve is a
         nuisance in the fit: it never enters a rating.
  r      opener rust per pool, unpenalised: the first race of each sport
         season is slow by ~1% and the curve, a function of the calendar,
         cannot see "first race".
  eps    ASYMMETRIC robust noise: a bad day is slow and unbounded, a good day
         is bounded by physiology, so the two tails get different thresholds

★ WHAT THE CURVE CHANGES ABOUT THE SPORT LEVEL. The old engine's bbar is the
  mean within-athlete fall-to-spring discrepancy: surface PLUS six months of
  fitness, in a proportion nobody chose, resolved by assuming the average
  athlete has no sport preference. Here the seasonal part lives in f and the
  level mu[TF] - mu[XC] is surface only. What identifies the split is the
  one place surface and calendar come apart: late XC in December against the
  indoor openers weeks later, under the smoothness penalty on f. That is a
  stated, checkable assumption (scripts/check_phase_year.py) where the old
  one was neither. Without December overlap in a pool the split is carried
  by the penalty alone -- the log prints the Nov->Mar move of each pool's
  curve beside the level so a reader can see which.

WHAT THIS REPLACES, and why each is a deletion rather than an addition:

  pair_engine.solveDelta + linkage_check.shrink   -> the penalised solve here
  linkage_check.recenterSport + data/sport_gap_bbar.json + the ridge guard
        mu is a parameter. There is nothing to recentre and no file to keep.
  rust_fitness.buildCorrection (a fixed input subtracted from the response)
        The curve and the rust are parameters fitted with the cells, so
        they cannot be charged to a venue and the venue cannot be charged
        to them.
  shrinkByLinkage / bridgeFraction / shortLabelDead
        Those gates detect weakly-identified cells and overwrite them, because
        cellVariance uses sigma2/A_ii -- the diagonal of the INFORMATION, not
        the diagonal of its INVERSE. cellPosteriorVar() below estimates
        diag(A^-1) by Hutchinson probing, so precision shrinkage subsumes
        identification shrinkage and the gates have nothing left to do.
  apply_tilt (post-pass)
        h is evaluated at the model's own ability, so there is no implicit
        equation to approximate.
  rowguard (diag/triage/apply)
        A robust WEIGHT cannot spiral: the row stays in with weight 0.05
        instead of vanishing.

Everything is implicit-operator + conjugate gradient; nothing materialises a
matrix. The parameter vector is packed (a | d | u | mu | beta | c | r) with
the optional blocks empty when not asked for, so the legacy three-block
callers keep working.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# ★ ASYMMETRIC BY DESIGN. Residual is in log-time, so POSITIVE = ran slow.
HUBER_SLOW = 2.5          # positive residuals, in robust scales
HUBER_FAST = 1.5          # negative residuals

TILT_K = -0.031           # per 10 rating points; matches racecast/tilt.py
TILT_RATING_LO = 70.0     # h is clamped to the band the tilt was fitted over
TILT_RATING_HI = 140.0

# The sport-offset ridge, in row units exactly as pair_engine.demeanWithinSport
# uses it (beta = sum(sc*v) / (sum(sc^2) + K)). Issue #70's optimum.
SPORT_RIDGE = 0.5

# The form curve: knots every 30 days from 1 August (academic day 0), 13 of
# them, so the last spans June into July. Piecewise linear: two nonzeros per
# row, no scipy, and a second difference is the natural roughness.
CURVE_KNOT_DAYS = 30.0
CURVE_N_KNOTS = 13
ACADEMIC_YEAR_START_DOY = 213          # 1 August

# Smoothness, as a multiple of a pool's rows-per-knot: at 1.0 a unit of
# curvature (second difference) costs as much as one knot's worth of rows
# carrying a unit residual. Not tunable by held-out error -- the winter step
# barely moves predictions -- so it is a stated prior, printed in the log.
CURVE_SMOOTH = 1.0
# The knot pinned at zero in every pool's curve (index 2 = academic day 60,
# 1 October). The reported curve is re-anchored to its row-weighted mean.
CURVE_REF_KNOT = 2

# ★ THE CURVE CARRIES NO NET LEVEL BETWEEN THE SPORTS' WINDOWS (issue 143).
#   The between-sport level mu and the curve's fall-to-spring step are
#   collinear on every row except the December bridge; the smoothness
#   prior alone decided the split, and on the real data it decided that
#   runners gain 13% over the winter and the track is 8% HARDER than XC
#   (mu[TF] +0.081 where the sequential engine's gap read -0.047), which
#   put every XC rating 14 points under the same athlete's track rating.
#   This penalty pins, per pool, the row-weighted mean of amp*f over the
#   non-reference rows (track) to the mean over the reference rows (XC), so
#   the curve is within-window form only and mu carries the whole
#   between-sport difference -- the sequential engine's identification (the
#   average dual-sport athlete has no sport preference), with the
#   within-season shape kept. It is the stiff direction now, so CG resolves
#   the level in the first outer instead of drifting toward it for six.
#   Weight in units of a pool's rows; 0 restores the penalty-only split.
CURVE_GAP_WEIGHT = 100.0


# Amplitude tilt: the season-form swing shrinks with ability
# (rust_fitness._TILT_PER_POINT and its clamps).
AMP_TILT_PER_POINT = 0.01135
AMP_FLOOR, AMP_CEIL = 0.15, 1.80

# Athlete-seasons with fewer races than this do not vote in the pool mean the
# ratings (and so the tilt and amplitude) are anchored on. pair_ratings uses 3.
POOL_MEAN_MIN_RACES = 3

CG_TOL = 1e-8
CG_MAX_ITER = 600
# ★ WHERE THE PRECISION IS SPENT (2026-09-02). The final outer iteration
#   solves to CG_TOL from a warm start. The outers before it only feed the
#   weight and variance updates, and a relative residual of 1e-6 moves
#   those by nothing a reader could see. A posterior probe is a Hutchinson
#   sample whose own error is 1/sqrt(n_probe) -- 25% at 16 -- so solving it
#   to 1e-8 was precision nobody used.
# ⚠ CG_TOL_OUTER WENT BACK TO CG_TOL (2026-09-02, the first live run).
#   At 1e-6 the five early outers left the LEVEL direction -- the
#   XC-to-track offset, which trades off against the sport offset and is
#   the slowest direction to converge -- unfinished, and the tight final
#   solve moved it from 0.047 to 0.079 in one step. The weights and
#   variances had been estimated under the old level. Warm starts make the
#   later outers cheap at full tolerance anyway; the saving was not worth a
#   level that swings on the last iteration. The probes keep 1e-4: they
#   size uncertainties, they do not move the point estimate.
CG_TOL_OUTER = CG_TOL
CG_TOL_PROBE = 1e-4
# ⚠ AND A PROBE IS CAPPED (first live run, 2026-09-03). A random probe
#   excites the level direction the solve finds hardest, and a probe that
#   cannot reach 1e-4 ran to CG_MAX_ITER: sixteen of those took most of a
#   night. At 150 iterations a probe is a usable Hutchinson sample -- the
#   estimate's own error is 1/sqrt(16) -- and the pass is under an hour.
CG_MAX_ITER_PROBE = 150
# bincount and fancy indexing release the GIL; the operator's independent
# block reductions run on a small pool. Sized to the box, capped at four:
# past that the scatter is memory-bound and more threads just contend.
_N_THREADS = max(1, min(4, os.cpu_count() or 1))


# ------------------------------------------------------------------ #
# THE DESIGN
# ------------------------------------------------------------------ #

class Design:
    """Index arrays for one set of rows, and the packing of theta.

    Required: athlete, cell, race (per row, dense codes).
    Optional: group_of_cell (per CELL; default one group), sc (per row,
    centred sport indicator -> beta block), pool_row + day (per row -> curve
    block; pool_row is the curve family, day the day-of-year), first (per
    row bool -> rust block, one coefficient per pool_row family).
    Sizes may be passed to keep a held-out design aligned with the training
    one; otherwise they come from the arrays.
    """

    def __init__(self, athlete, cell, race, group_of_cell=None, sc=None,
                 pool_row=None, day=None, first=None,
                 n_ath=None, n_cell=None, n_race=None, n_pool=None,
                 n_knot=CURVE_N_KNOTS, knot_days=CURVE_KNOT_DAYS):
        self.athlete = np.asarray(athlete, dtype=np.int64)
        self.cell = np.asarray(cell, dtype=np.int64)
        self.race = np.asarray(race, dtype=np.int64)
        self.n = self.athlete.size
        self.n_ath = int(n_ath if n_ath is not None else self.athlete.max() + 1)
        self.n_cell = int(n_cell if n_cell is not None else self.cell.max() + 1)
        self.n_race = int(n_race if n_race is not None else self.race.max() + 1)

        g = (np.zeros(self.n_cell, dtype=np.int64) if group_of_cell is None
             else np.asarray(group_of_cell, dtype=np.int64))
        self.group_of_cell = g
        self.n_group = int(g.max()) + 1
        self.group_row = g[self.cell]

        # ★ GROUP 0 IS THE REFERENCE: mu[0] = 0 by construction and the
        #   abilities carry that level. An unpenalised mu for every group
        #   would leave (a + c, mu - c) as an exact null direction, and CG
        #   on a singular operator wanders along it -- the posterior-variance
        #   probes came back six orders of magnitude too large that way.
        self.n_mu = self.n_group - 1
        self.mu_idx = np.maximum(self.group_row - 1, 0)
        self.mu_w = (self.group_row > 0).astype(np.float64)

        self.sc = None if sc is None else np.asarray(sc, dtype=np.float64)

        self.pool_row = (None if pool_row is None
                         else np.asarray(pool_row, dtype=np.int64))
        self.n_pool = int(n_pool if n_pool is not None else
                          (0 if self.pool_row is None
                           else self.pool_row.max() + 1))
        self.n_knot = int(n_knot)
        self.knot_days = float(knot_days)
        self.has_curve = self.pool_row is not None and day is not None
        self.n_c = 0
        if self.has_curve:
            t = academicDay(np.asarray(day, dtype=np.float64))
            k0 = np.minimum((t // self.knot_days).astype(np.int64),
                            self.n_knot - 2)
            w1 = np.clip((t - k0 * self.knot_days) / self.knot_days, 0.0, 1.0)
            self.k0 = self.pool_row * self.n_knot + k0       # full-grid index
            self.k1 = self.k0 + 1
            self.w0 = 1.0 - w1
            self.w1 = w1
            # ★ ONE REFERENCE KNOT PER POOL IS PINNED AT ZERO (CURVE_REF_KNOT,
            #   1 October), for the same reason as mu[0]: a pool's curve
            #   plus a constant is its abilities minus that constant. The
            #   free block holds the other knots; the row basis drops the
            #   pinned one by zeroing its weight.
            grid = np.arange(self.n_pool * self.n_knot)
            is_ref = (grid % self.n_knot) == CURVE_REF_KNOT
            self.col_of = np.full(grid.size, -1, dtype=np.int64)
            self.col_of[~is_ref] = np.arange(int((~is_ref).sum()))
            self.n_c = int((~is_ref).sum())
            self.c0 = np.maximum(self.col_of[self.k0], 0)
            self.c1 = np.maximum(self.col_of[self.k1], 0)
            self.w0 = np.where(self.col_of[self.k0] < 0, 0.0, self.w0)
            self.w1 = np.where(self.col_of[self.k1] < 0, 0.0, self.w1)
            self.free_grid = np.flatnonzero(~is_ref)
        self.has_rust = first is not None and self.pool_row is not None
        if self.has_rust:
            self.first = np.asarray(first, dtype=np.float64)

        # packing
        self.o_a = 0
        self.o_d = self.n_ath
        self.o_u = self.o_d + self.n_cell
        self.o_mu = self.o_u + self.n_race
        self.o_beta = self.o_mu + self.n_mu
        self.n_beta = self.n_ath if self.sc is not None else 0
        self.o_c = self.o_beta + self.n_beta
        self.o_r = self.o_c + self.n_c
        self.n_r = self.n_pool if self.has_rust else 0
        self.n_total = self.o_r + self.n_r

    def unpack(self, theta):
        """Blocks as FULL arrays: mu over every group (0 for the reference),
        c over the full pool x knot grid (0 at the pinned knot)."""
        mu = np.zeros(self.n_group)
        mu[1:] = theta[self.o_mu:self.o_beta]
        b = {"a": theta[self.o_a:self.o_d],
             "d": theta[self.o_d:self.o_u],
             "u": theta[self.o_u:self.o_mu],
             "mu": mu}
        b["beta"] = theta[self.o_beta:self.o_c] if self.n_beta else None
        if self.n_c:
            c = np.zeros(self.n_pool * self.n_knot)
            c[self.free_grid] = theta[self.o_c:self.o_r]
            b["c"] = c
        else:
            b["c"] = None
        b["r"] = theta[self.o_r:self.n_total] if self.n_r else None
        return b



def academicDay(doy):
    """0 at 1 August, so the winter is contiguous instead of wrapping."""
    return (np.asarray(doy, dtype=np.float64) - ACADEMIC_YEAR_START_DOY) % 365.0


# ------------------------------------------------------------------ #
# THE OPERATOR
# ------------------------------------------------------------------ #

def rowPrediction(b, D, h, amp, u_missing_zero=True):
    """The model's prediction for every row of design D from blocks b."""
    row = b["a"][D.athlete] + h * (b["mu"][D.group_row] + b["d"][D.cell])
    u = b["u"]
    if u.size == D.n_race:
        row = row + u[D.race]
    elif u_missing_zero:
        pass                          # a design whose races are not fitted
    if b.get("beta") is not None and D.sc is not None:
        row = row + b["beta"][D.athlete] * D.sc
    if b.get("c") is not None and D.has_curve:
        c = b["c"]
        row = row + amp * (D.w0 * c[D.k0] + D.w1 * c[D.k1])
    if b.get("r") is not None and D.has_rust:
        row = row + D.first * b["r"][D.pool_row]
    return row


def _curvePenaltyApply(c_free, D, lam):
    """lam * D2'D2 on the FREE curve block: expand to the full pool x knot
    grid (0 at the pinned knot), penalise, gather back. lam is per pool."""
    v = np.zeros(D.n_pool * D.n_knot)
    v[D.free_grid] = c_free
    v = v.reshape(D.n_pool, D.n_knot)
    z = (v[:, :-2] - 2.0 * v[:, 1:-1] + v[:, 2:]) * lam[:, None]
    out = np.zeros_like(v)
    out[:, :-2] += z
    out[:, 1:-1] -= 2.0 * z
    out[:, 2:] += z
    return out.reshape(-1)[D.free_grid]


def _curvePenaltyDiag(D, lam):
    k = D.n_knot
    col = np.full(k, 6.0)
    col[0] = col[-1] = 1.0
    if k > 1:
        col[1] = col[-2] = 5.0
    if k < 4:                       # degenerate tiny grids
        col[:] = 1.0
    return (lam[:, None] * col[None, :]).reshape(-1)[D.free_grid]



def curveGapVectors(D, w, amp):
    """Per pool, the vector g over the FREE curve block with
    g . c = mean over the pool's non-reference-group rows of amp*f
          - mean over its reference-group rows of amp*f,
    both row-weighted by w. None for a pool that has rows of only one
    group (nothing to balance)."""
    if not D.n_c:
        return []
    ref = D.group_row == 0
    wa = w * amp
    vecs = []
    for p in range(D.n_pool):
        m = D.pool_row == p
        g = np.zeros(D.n_c)
        ok = True
        for sign, mm in ((-1.0, m & ref), (1.0, m & ~ref)):
            tot = float(w[mm].sum())
            if tot <= 0:
                ok = False
                break
            g += sign * (np.bincount(D.c0[mm], weights=wa[mm] * D.w0[mm],
                                     minlength=D.n_c)
                         + np.bincount(D.c1[mm], weights=wa[mm] * D.w1[mm],
                                       minlength=D.n_c)) / tot
        vecs.append(g if ok else None)
    return vecs


def curveWindowGaps(c_free, vecs):
    """The realised track-minus-XC mean of amp*f per pool (nan where
    unbalanced), for the log."""
    return np.array([np.nan if g is None else float(g @ c_free)
                     for g in vecs])


class _Operator:
    """(Z'WZ + P) as a matvec, with its diagonal, for one outer iteration."""

    def __init__(self, D, w, h, amp, pen_cell, pen_race, ridge, lam,
                 lam_gap=None):
        self.D, self.w, self.h, self.amp = D, w, h, amp
        self.pen_cell, self.pen_race, self.ridge, self.lam = (
            pen_cell, pen_race, ridge, lam)
        # the window-balance penalty (CURVE_GAP_WEIGHT): rank one per pool
        self.gap = []
        if lam_gap is not None and D.n_c:
            for lg, g in zip(lam_gap, curveGapVectors(D, w, amp)):
                if g is not None and lg > 0:
                    self.gap.append((float(lg), g))
        self.pool = (ThreadPoolExecutor(_N_THREADS) if _N_THREADS > 1
                     else None)

    def _reduce(self, jobs):
        """Run independent block reductions, in parallel where there is
        a pool. Each job is a zero-argument callable returning one block;
        order is preserved."""
        if self.pool is None:
            return [j() for j in jobs]
        return list(self.pool.map(lambda j: j(), jobs))

    def adjoint(self, wr):
        """Z' applied to a per-row vector, packed as theta."""
        D, h, amp = self.D, self.h, self.amp
        jobs = [lambda: np.bincount(D.athlete, weights=wr, minlength=D.n_ath),
                lambda: np.bincount(D.cell, weights=wr * h, minlength=D.n_cell),
                lambda: np.bincount(D.race, weights=wr, minlength=D.n_race),
                lambda: np.bincount(D.mu_idx, weights=wr * h * D.mu_w,
                                    minlength=max(D.n_mu, 1))[:D.n_mu]]
        if D.n_beta:
            jobs.append(lambda: np.bincount(D.athlete, weights=wr * D.sc,
                                            minlength=D.n_ath))
        if D.n_c:
            jobs.append(lambda: np.bincount(D.c0, weights=wr * amp * D.w0,
                                            minlength=D.n_c)
                        + np.bincount(D.c1, weights=wr * amp * D.w1,
                                      minlength=D.n_c))
        if D.n_r:
            jobs.append(lambda: np.bincount(D.pool_row, weights=wr * D.first,
                                            minlength=D.n_pool))
        return np.concatenate(self._reduce(jobs))

    def matvec(self, theta):
        D = self.D
        b = D.unpack(theta)
        out = self.adjoint(self.w * rowPrediction(b, D, self.h, self.amp))
        out[D.o_d:D.o_u] += self.pen_cell * b["d"]
        out[D.o_u:D.o_mu] += self.pen_race * b["u"]
        if D.n_beta:
            out[D.o_beta:D.o_c] += self.ridge * b["beta"]
        if D.n_c:
            c_free = theta[D.o_c:D.o_r]
            out[D.o_c:D.o_r] += _curvePenaltyApply(c_free, D, self.lam)
            for lg, g in self.gap:
                out[D.o_c:D.o_r] += lg * g * float(g @ c_free)
        return out


    def rhs(self, y):
        return self.adjoint(self.w * y)

    def diag(self):
        D, w, h, amp = self.D, self.w, self.h, self.amp
        jobs = [lambda: np.bincount(D.athlete, weights=w, minlength=D.n_ath),
                lambda: np.bincount(D.cell, weights=w * h * h,
                                    minlength=D.n_cell) + self.pen_cell,
                lambda: np.bincount(D.race, weights=w, minlength=D.n_race)
                + self.pen_race,
                lambda: np.bincount(D.mu_idx, weights=w * h * h * D.mu_w,
                                    minlength=max(D.n_mu, 1))[:D.n_mu]]
        if D.n_beta:
            jobs.append(lambda: np.bincount(D.athlete, weights=w * D.sc * D.sc,
                                            minlength=D.n_ath) + self.ridge)
        if D.n_c:
            jobs.append(lambda: np.bincount(D.c0,
                                            weights=w * amp * amp * D.w0 * D.w0,
                                            minlength=D.n_c)
                        + np.bincount(D.c1, weights=w * amp * amp * D.w1 * D.w1,
                                      minlength=D.n_c)
                        + _curvePenaltyDiag(D, self.lam)
                        + sum((lg * g * g for lg, g in self.gap),
                              np.zeros(D.n_c)))
        if D.n_r:
            jobs.append(lambda: np.bincount(D.pool_row, weights=w * D.first,
                                            minlength=D.n_pool))
        return np.concatenate(self._reduce(jobs))


# ---- the legacy three-block operator, kept for its callers ---------------- #

def applyOperator(theta, athlete, cell, race, w, h,
                  n_ath, n_cell, n_race, pen_cell, pen_race):
    a = theta[:n_ath]
    d = theta[n_ath:n_ath + n_cell]
    u = theta[n_ath + n_cell:]
    row = a[athlete] + d[cell] * h + u[race]
    wr = w * row
    out_a = np.bincount(athlete, weights=wr, minlength=n_ath)
    out_d = np.bincount(cell, weights=wr * h, minlength=n_cell) + pen_cell * d
    out_u = np.bincount(race, weights=wr, minlength=n_race) + pen_race * u
    return np.concatenate([out_a, out_d, out_u])


def _rhs(y, athlete, cell, race, w, h, n_ath, n_cell, n_race):
    wy = w * y
    return np.concatenate([
        np.bincount(athlete, weights=wy, minlength=n_ath),
        np.bincount(cell, weights=wy * h, minlength=n_cell),
        np.bincount(race, weights=wy, minlength=n_race)])


def _operatorDiag(athlete, cell, race, w, h, n_ath, n_cell, n_race,
                  pen_cell, pen_race):
    return np.concatenate([
        np.bincount(athlete, weights=w, minlength=n_ath),
        np.bincount(cell, weights=w * h * h, minlength=n_cell) + pen_cell,
        np.bincount(race, weights=w, minlength=n_race) + pen_race])


# Purpose:   preconditioned conjugate gradient on the normal equations.
# Detail:    Jacobi preconditioner -- the operator's own diagonal, which is
#            available in closed form and costs one bincount per block.
def conjugateGradient(rhs, matvec, diag, tol=CG_TOL, max_iter=CG_MAX_ITER,
                      x0=None):
    x = np.zeros_like(rhs) if x0 is None else x0.copy()
    r = rhs - matvec(x)
    inv_diag = 1.0 / np.maximum(diag, 1e-12)
    z = inv_diag * r
    p = z.copy()
    rz = float(r @ z)
    rhs_norm = max(float(np.linalg.norm(rhs)), 1e-30)

    for it in range(max_iter):
        Ap = matvec(p)
        pAp = float(p @ Ap)
        if pAp <= 0:
            break                       # not positive definite; stop clean
        alpha = rz / pAp
        x += alpha * p
        r -= alpha * Ap
        if float(np.linalg.norm(r)) / rhs_norm < tol:
            return x, it + 1
        z = inv_diag * r
        rz_new = float(r @ z)
        p = z + (rz_new / rz) * p
        rz = rz_new
    return x, max_iter


# ------------------------------------------------------------------ #
# ROBUST WEIGHTS -- replaces rowguard
# ------------------------------------------------------------------ #

# ! NOTHING IS EVER DROPPED. A row that would have been condemned gets a small
#   weight and stays in the design, so it can still anchor its athlete and can
#   recover on the next iteration if the fit moves.
def robustWeights(resid, scale=None):
    if scale is None:
        mad = float(np.median(np.abs(resid - np.median(resid))))
        scale = max(1.4826 * mad, 1e-9)
    z = resid / scale
    cut = np.where(z >= 0, HUBER_SLOW, HUBER_FAST)
    w = np.ones_like(z)
    big = np.abs(z) > cut
    w[big] = cut[big] / np.abs(z[big])
    return w, scale


# ------------------------------------------------------------------ #
# RATINGS FROM THE MODEL'S OWN ABILITY -- the tilt and the amplitude
# ------------------------------------------------------------------ #

# ★ NO CIRCULARITY. apply_tilt must evaluate h at the PRE-tilt rating because
#   the tilted rating is not known until after the tilt. Here ability is a
#   parameter, so h is exact at every iteration.
def tiltFromAbility(a_row, pool_mean_row):
    with np.errstate(over="ignore", invalid="ignore"):
        rating = 100.0 * pool_mean_row / np.exp(a_row)
    rating = np.clip(np.nan_to_num(rating, nan=100.0), TILT_RATING_LO,
                     TILT_RATING_HI)
    return 1.0 + TILT_K * (rating - 100.0) / 10.0


def amplitudeFromRating(rating):
    """The season-form swing relative to a rating-100 athlete of the pool."""
    return np.clip(1.0 - AMP_TILT_PER_POINT * (rating - 100.0),
                   AMP_FLOOR, AMP_CEIL)


def ratingsFromAbility(a, athlete_pool, n_races, n_pool,
                       min_races=POOL_MEAN_MIN_RACES):
    """Per athlete-season: 100 * pool_mean / exp(a), the pool mean over
    athlete-seasons with enough races. Gauge-free: a constant added to every
    a of a pool cancels, which is why the curve's and mu's null directions
    never reach a rating."""
    ability = np.exp(a - a.mean())           # centred for exp() safety only
    vote = n_races >= min_races
    tot = np.bincount(athlete_pool[vote], weights=ability[vote],
                      minlength=n_pool)
    cnt = np.bincount(athlete_pool[vote], minlength=n_pool)
    mean = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
    pm = mean[athlete_pool]
    fallback = float(np.nanmean(mean)) if np.isfinite(mean).any() else 1.0
    pm = np.where(np.isfinite(pm), pm, fallback)
    return 100.0 * pm / ability


# ------------------------------------------------------------------ #
# THE SPORT-OFFSET RECENTRE -- the one assumption the curve does not remove
# ------------------------------------------------------------------ #

# ★ A UNIFORM beta AND THE LEVEL ARE THE SAME PARAMETER, EXACTLY. Add b to
#   every athlete's sport offset, subtract it from mu[TF], shift each
#   athlete-season's ability by b * (sbar_g - s_ref), and no row's
#   prediction moves. The indoor bridge cannot see this direction either:
#   it separates the CURVE's winter step from the level, not the mean
#   offset from the level. The ridge only decides how the level is split
#   between the two, in proportion to K against sum(sc^2), and CG resolves
#   that near-null direction last and slowly (measured: the level came out
#   at 40% of the truth with the rest sitting in mean beta).
#
#   So the joint model carries the same stated assumption pair_recenter
#   states: the sum(sc^2)-weighted mean specialisation is zero. What the
#   curve changes is WHAT that assumption is applied to -- a level with the
#   season already removed, i.e. the surface -- not whether it is needed.
#   Applied after every CG pass as a reparameterisation, so the next pass
#   warm-starts on the centred point.
def recentreSportOffset(b, D, w=None):
    """Move the weighted mean of beta into mu (and the abilities). Returns
    (b, bbar); b is modified in place."""
    if b.get("beta") is None or D.sc is None:
        return b, 0.0
    ww = np.ones(D.n) if w is None else w
    wsum = np.bincount(D.athlete, weights=ww * D.sc * D.sc, minlength=D.n_ath)
    ok = wsum > 0
    if not ok.any():
        return b, 0.0
    bbar = float(np.average(b["beta"][ok], weights=wsum[ok]))
    # s per row is sc + sbar_g; the group's mean s and each athlete's sbar
    s_row = D.sc + (np.bincount(D.athlete, weights=D.sc, minlength=D.n_ath)
                    / np.maximum(np.bincount(D.athlete, minlength=D.n_ath), 1)
                    )[D.athlete]
    # (sc is centred per athlete-season, so its per-athlete mean is ~0 and
    #  s_row recovers the raw +-0.5 indicator only when sbar is added back)
    cnt_a = np.maximum(np.bincount(D.athlete, minlength=D.n_ath), 1)
    return _recentreWith(b, D, bbar, s_row, cnt_a)


def _recentreWith(b, D, bbar, s_row, cnt_a):
    s_grp = (np.bincount(D.group_row, weights=s_row, minlength=D.n_group)
             / np.maximum(np.bincount(D.group_row, minlength=D.n_group), 1))
    s_ref = s_grp[0]
    b["beta"] = b["beta"] - bbar
    b["mu"] = b["mu"] + bbar * (s_grp - s_ref)
    sbar_a = np.bincount(D.athlete, weights=s_row, minlength=D.n_ath) / cnt_a
    b["a"] = b["a"] - bbar * (sbar_a - s_ref)
    return b, bbar


# ★ THE LEVEL MUST BE MOVED INTO mu BY HAND AFTER EVERY PASS. A constant
#   added to every d of a sport group and subtracted from that group's mu
#   changes no prediction; only the penalty on d prefers the mean in mu, and
#   that direction's eigenvalue is the penalty weight -- tiny against the
#   data blocks -- so CG resolves it last and, at any finite tolerance,
#   incompletely. Measured on the synthetic year: two thirds of a 3% track
#   level stayed in the cells' group mean, and the hierarchical tau2 then
#   read that offset as spread and never shrank it out. The race-day block
#   has the same null direction against mu (a uniform u over one sport's
#   races) and against the abilities (a uniform u over all races). The
#   penalised optimum has every such mean at exactly zero, so moving them
#   is a step toward the optimum, not away from it.
def recentreLevels(b, D):
    """Zero the per-group mean of d and of u, moving them into mu (and,
    for the reference group, into every ability). In place."""
    g = D.group_of_cell
    d_mean = (np.bincount(g, weights=b["d"], minlength=D.n_group)
              / np.maximum(np.bincount(g, minlength=D.n_group), 1))
    b["d"] = b["d"] - d_mean[g]
    b["mu"] = b["mu"] + d_mean

    race_group = np.zeros(D.n_race, dtype=np.int64)
    race_group[D.race] = D.group_row
    seen = np.bincount(D.race, minlength=D.n_race) > 0
    u_mean = np.zeros(D.n_group)
    for gg in range(D.n_group):
        m = seen & (race_group == gg)
        if m.any():
            u_mean[gg] = float(b["u"][m].mean())
    b["u"] = np.where(seen, b["u"] - u_mean[race_group], b["u"])
    b["mu"] = b["mu"] + u_mean

    # mu[0] is pinned at zero: whatever landed there is a global constant
    shift = float(b["mu"][0])
    b["mu"] = b["mu"] - shift
    b["a"] = b["a"] + shift
    return b


def _pack(b, D):

    parts = [b["a"], b["d"], b["u"], b["mu"][1:]]
    if D.n_beta:
        parts.append(b["beta"])
    if D.n_c:
        parts.append(b["c"][D.free_grid])
    if D.n_r:
        parts.append(b["r"])
    return np.concatenate(parts)


# ------------------------------------------------------------------ #
# POSTERIOR VARIANCE -- the number the gate stack exists to approximate
# ------------------------------------------------------------------ #


# Purpose:   diag(A^-1) for the cell block, by Hutchinson probing.
# ⚠ EACH PROBE IS ONE CG SOLVE, and the error falls as 1/sqrt(n_probe).
#   64 is a usable default for shrinkage weights; use several hundred before
#   PUBLISHING a per-cell standard error.
def cellPosteriorVar(matvec, diag, n_total, n_ath, n_cell, sigma2,
                     n_probe=64, seed=0, tol=CG_TOL_PROBE, verbose=False):
    # ★ NO PROBES: THE INFORMATION-DIAGONAL BOUND. sigma2 / A_ii is a lower
    #   bound on the posterior variance (it ignores the off-diagonal
    #   coupling), which is what the outer loop's own updates already use.
    #   n_probe=0 is the fast path for a go-live run that does not need
    #   per-cell standard errors that night; the probes are telemetry.
    if not n_probe or n_probe <= 0:
        d = diag[n_ath:n_ath + n_cell]
        if verbose:
            print("  [joint] probes off: cell variance from the information "
                  "diagonal (a lower bound)", flush=True)
        return sigma2 / np.maximum(d, 1e-12)
    rng = np.random.default_rng(seed)
    acc = np.zeros(n_cell)
    t0 = time.time()
    for k in range(n_probe):
        z = rng.integers(0, 2, size=n_total).astype(np.float64) * 2.0 - 1.0
        x, iters = conjugateGradient(z, matvec, diag, tol=tol,
                                     max_iter=CG_MAX_ITER_PROBE)
        if verbose:
            # ! SAY SO. Sixteen probes on 59M rows is hours of silence
            #   otherwise, and a silent step reads as a hung one.
            print(f"  [joint] probe {k + 1}/{n_probe}: cg {iters} iters "
                  f"[{time.time() - t0:.0f}s]", flush=True)
        acc += z[n_ath:n_ath + n_cell] * x[n_ath:n_ath + n_cell]
    return sigma2 * np.maximum(acc / n_probe, 1e-12)


# ------------------------------------------------------------------ #
# THE SOLVE
# ------------------------------------------------------------------ #

# Purpose:   fit the joint model by block coordinate descent: one CG solve
#            per outer iteration with the weights, the tilt, the amplitude
#            and the variance components held; then update them.
# Input:     y, athlete, cell, race -- as Design; group -- per CELL
#            shrinkage group; design -- a prebuilt Design (then the index
#            arguments are ignored); athlete_pool -- pool code per
#            athlete-season, which enables ratings from the model's own
#            ability (tilt and amplitude) without a file; pool_mean_row --
#            the legacy per-row pool mean for the tilt.
# Output:    dict of fitted blocks, variance components and diagnostics.
def solveJoint(y, athlete=None, cell=None, race=None, group=None,
               pool_mean_row=None, n_outer=6, robust=True, tilt=True,
               n_probe=64, seed=0, verbose=False,
               design=None, athlete_pool=None, ridge=SPORT_RIDGE,
               curve_smooth=CURVE_SMOOTH, cg_max_iter=CG_MAX_ITER,
               curve_gap=CURVE_GAP_WEIGHT):
    y = np.asarray(y, dtype=np.float64)
    D = design if design is not None else Design(athlete, cell, race,
                                                 group_of_cell=group)
    n = y.size
    assert D.n == n, "design and response disagree on the row count"

    n_races = np.bincount(D.athlete, minlength=D.n_ath)
    if athlete_pool is not None:
        athlete_pool = np.asarray(athlete_pool, dtype=np.int64)
        n_pool_r = int(athlete_pool.max()) + 1

    # ★ START WEAK, NOT AT ZERO. tau2 = inf would be a flat prior and a
    #   singular first solve; these are loosened by the updates below.
    tau2 = np.full(D.n_group, 0.05)
    sigma_u2 = 0.01
    sigma2 = 1.0
    scale = None
    w = np.ones(n)
    h = np.ones(n)
    amp = np.ones(n)
    lam = np.zeros(max(D.n_pool, 1))
    if D.has_curve:
        rows_per_pool = np.bincount(D.pool_row, minlength=D.n_pool)
        lam = curve_smooth * rows_per_pool / float(D.n_knot)
        lam = np.maximum(lam, 1.0)
    lam_gap = None
    if D.has_curve and curve_gap and curve_gap > 0:
        lam_gap = float(curve_gap) * rows_per_pool.astype(np.float64)
    theta = None
    rating = None

    for outer in range(n_outer):
        pen_cell = sigma2 / np.maximum(tau2[D.group_of_cell], 1e-12)
        pen_race = sigma2 / max(sigma_u2, 1e-12)
        op = _Operator(D, w, h, amp, pen_cell, pen_race, ridge, lam,
                       lam_gap)
        diag = op.diag()
        # the last outer carries the published numbers; see CG_TOL_OUTER
        theta, iters = conjugateGradient(
            op.rhs(y), op.matvec, diag, max_iter=cg_max_iter, x0=theta,
            tol=CG_TOL if outer == n_outer - 1 else CG_TOL_OUTER)
        b = D.unpack(theta)
        bbar = 0.0
        if D.n_beta:
            b, bbar = recentreSportOffset(b, D, w)
        b = recentreLevels(b, D)
        theta = _pack(b, D)

        resid = y - rowPrediction(b, D, h, amp)

        # --- variance components ------------------------------------ #

        # ! NOT PLAIN mean(d^2): a shrunk estimate has less spread than the
        #   truth, and updating tau2 from it alone shrinks harder every
        #   iteration. Add the sampling variance the estimate has lost,
        #   sigma2/A_ii -- a lower bound on the posterior variance, so tau2
        #   is still conservative, but no longer collapsing.
        sigma2 = float(np.average(resid ** 2, weights=w))
        d_var = sigma2 / np.maximum(diag[D.o_d:D.o_u], 1e-12)
        u_var = sigma2 / np.maximum(diag[D.o_u:D.o_mu], 1e-12)
        sigma_u2 = max(float(np.mean(b["u"] ** 2 + u_var)), 1e-9)
        for g in range(D.n_group):
            m = D.group_of_cell == g
            if m.any():
                tau2[g] = max(float(np.mean(b["d"][m] ** 2 + d_var[m])), 1e-9)

        # --- robust reweighting (replaces rowguard) ------------------ #
        if robust:
            w, scale = robustWeights(resid)

        # --- the tilt and the amplitude, at the model's own ability -- #
        if athlete_pool is not None:
            rating = ratingsFromAbility(b["a"], athlete_pool, n_races,
                                        n_pool_r)
            r_row = rating[D.athlete]
            if tilt:
                r_clip = np.clip(r_row, TILT_RATING_LO, TILT_RATING_HI)
                h = 1.0 + TILT_K * (r_clip - 100.0) / 10.0
            if D.has_curve:
                amp = amplitudeFromRating(r_row)
        elif tilt and pool_mean_row is not None:
            h = tiltFromAbility(b["a"][D.athlete], np.asarray(pool_mean_row))

        if verbose:
            extra = ""
            if D.n_group > 1:
                extra += f", level mu {np.round(b['mu'] - b['mu'][0], 4)}"
            if D.n_beta:
                extra += (f", |beta| mean {np.abs(b['beta']).mean():.4f}, "
                          f"recentred by {bbar:+.5f}")
            if D.n_c:
                gaps = curveWindowGaps(theta[D.o_c:D.o_r],
                                       curveGapVectors(D, w, amp))
                extra += f", curve TF-XC window gap {np.round(gaps, 4)}"

            print(f"  [joint] outer {outer + 1}/{n_outer}: cg {iters} iters, "
                  f"sigma {np.sqrt(sigma2):.5f}, sigma_u "
                  f"{np.sqrt(sigma_u2):.5f}, tau {np.sqrt(tau2).mean():.5f}, "
                  f"mean w {w.mean():.3f}{extra}")

    # --- posterior variance, and the shrinkage it licenses ----------- #
    pen_cell = sigma2 / np.maximum(tau2[D.group_of_cell], 1e-12)
    pen_race = sigma2 / max(sigma_u2, 1e-12)
    op = _Operator(D, w, h, amp, pen_cell, pen_race, ridge, lam, lam_gap)
    diag_final = op.diag()
    cell_var = cellPosteriorVar(op.matvec, diag_final, D.n_total, D.n_ath,
                                D.n_cell, sigma2, n_probe=n_probe, seed=seed, verbose=verbose)

    b = D.unpack(theta)
    out = {
        "ability": b["a"],
        "delta": b["mu"][D.group_of_cell] + b["d"],   # the full difficulty
        "d": b["d"], "mu": b["mu"],
        "race_effect": b["u"],
        "beta": b["beta"],
        "rust": b["r"],
        "cell_var": cell_var, "cell_se": np.sqrt(cell_var),
        "sigma2": sigma2, "sigma_u2": sigma_u2, "tau2": tau2,
        "weights": w, "robust_scale": scale, "h": h, "amp": amp,
        "rating": rating, "n_races": n_races,
        "n_downweighted": int((w < 0.999).sum()),
        "theta": theta,
    }
    if D.has_curve:
        c = b["c"].reshape(D.n_pool, D.n_knot)
        # ★ THE CURVE'S GAUGE IS THE SEASON RATING'S MEANING. The fit pins
        #   one knot (1 October) at zero, so the raw abilities are "ability
        #   at October form". Per-result ratings leave the curve out and so
        #   scatter around ability at the athlete's AVERAGE form over the
        #   year; the two only agree if the curve is anchored to its
        #   row-weighted mean per pool and the abilities are shifted by the
        #   same amount, scaled by each athlete-season's own amplitude --
        #   an exact reparameterisation, since amp is constant within an
        #   athlete-season. Measured on the synthetic year before this: the
        #   per-race median sat 1.1 points above the season rating.
        f_row = D.w0 * b["c"][D.k0] + D.w1 * b["c"][D.k1]
        mean_p = (np.bincount(D.pool_row, weights=w * f_row, minlength=D.n_pool)
                  / np.maximum(np.bincount(D.pool_row, weights=w,
                                           minlength=D.n_pool), 1e-12))
        amp_g = np.ones(D.n_ath)
        amp_g[D.athlete] = amp
        pool_g = np.zeros(D.n_ath, dtype=np.int64)
        pool_g[D.athlete] = D.pool_row
        out["ability_raw"] = b["a"]
        out["ability"] = b["a"] + amp_g * mean_p[pool_g]
        out["curve"] = c
        out["curve_anchored"] = c - mean_p[:, None]
        out["curve_knot_days"] = np.arange(D.n_knot) * D.knot_days
        out["curve_lambda"] = lam
        out["curve_gap_weight"] = float(curve_gap or 0.0)
        out["curve_window_gap"] = curveWindowGaps(
            theta[D.o_c:D.o_r], curveGapVectors(D, w, amp))
    return out



# ------------------------------------------------------------------ #
# HELD-OUT SCORING
# ------------------------------------------------------------------ #

def predictHeldOut(out, D_train, D_test, athlete_pool=None, tilt=True):
    """Predictions for rows of D_test from a fit on D_train.

    A test row is covered when its athlete-season and its cell were both
    fitted; a race unseen in training contributes 0 (its prior mean).
    Returns (prediction, covered mask)."""
    b = D_train.unpack(out["theta"])
    a_cnt = np.bincount(D_train.athlete, minlength=D_train.n_ath)
    c_cnt = np.bincount(D_train.cell, minlength=D_train.n_cell)
    r_cnt = np.bincount(D_train.race, minlength=D_train.n_race)
    covered = (a_cnt[D_test.athlete] > 0) & (c_cnt[D_test.cell] > 0)

    h = np.ones(D_test.n)
    amp = np.ones(D_test.n)
    if out.get("rating") is not None:
        r_row = out["rating"][D_test.athlete]
        if tilt:
            r_clip = np.clip(r_row, TILT_RATING_LO, TILT_RATING_HI)
            h = 1.0 + TILT_K * (r_clip - 100.0) / 10.0
        if D_test.has_curve:
            amp = amplitudeFromRating(r_row)
    u = np.where(r_cnt > 0, b["u"], 0.0)
    bb = dict(b)
    bb["u"] = u
    pred = rowPrediction(bb, D_test, h, amp)
    return pred, covered
