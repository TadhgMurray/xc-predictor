# Project: xc-predictor
# File:    scripts/score_suspects.py   (v2 -- random-effects null)
# Purpose: READ-ONLY. Score suspects by SIGNIFICANCE instead of an arbitrary
#          percentage line. Writes nothing. Reuses diag_suspects' residual build.
#
# ============================================================================
# WHAT v1 GOT WRONG (and the run that proved it)
# ============================================================================
# v1 scored a division as z = c / SE(c) -- "is this field's median residual
# distinguishable from ZERO?". The XC run answered:
#
#     population 447,211   sigma0 = 2.007   ->  |z|>3.5 flags 73,277 (16%)
#
# 16% of all divisions cannot be broken. The test was wrong, and wrong in a
# specific way: ZERO IS THE WRONG NULL. A clean division on a hard course
# legitimately has c != 0 -- that IS what a hard course is, and the engine
# already models it as venue difficulty. So a clean division's median is:
#
#     c_j ~ N(0, tau^2 + SE_j^2)        tau = the real, honest spread of
#                                             division effects in the world
#     v1 assumed: c_j ~ N(0, SE_j^2)    i.e. tau = 0
#
# For a 160-runner division SE_j is tiny, so a genuine course effect got divided
# by almost nothing and produced a huge z. That is why the BIG divisions lit up.
# The v1 patch -- one multiplicative sigma0 from the MAD -- cannot fix it: the
# problem is ADDITIVE. tau dominates when n is large, SE dominates when n is
# small; no single factor covers both.
#
# The rows panel, by contrast, came back sigma0 = 0.958 -- the theory held. Row
# scoring needs no change; its null (a runner is at their own level) really is
# centred on zero.
#
# ============================================================================
# v2: ADD THE ONE MISSING TERM
# ============================================================================
#     z_j = c_j / sqrt(tau^2 + SE_j^2)
#
# Now "flagged" means: THIS FIELD IS OFF BY MORE THAN ANY REAL COURSE COULD
# EXPLAIN -- which is the question actually being asked. tau is estimated from
# the data, robustly, so nothing is chosen:
#
#   robust spread of c across divisions  = tau^2 + (average sampling noise)
#   => tau^2 = max(0, robust_var(c) - mean(SE^2))
#
# using MAD (not sd) so the contaminated tail cannot inflate the very thing
# meant to detect it.
#
# SELF-CHECK, and this is the point: after tau is included, the empirical null
# scale sigma0 should come back ~1.0. If it does, the model is right -- the
# spread of z is fully explained by (real course effects + sampling noise). If
# it is still ~2, something else is inflating division residuals and the number
# is telling you so. Watch that line; it grades the model.
#
# Everything else is unchanged: empirical null, then cut where the expected
# false positives die (FDR), so on clean data NOTHING flags -- which a
# percentile threshold can never do.
#
# USAGE (read-only, ~12 min)
#   python scripts/score_suspects.py --sport XC
#   python scripts/score_suspects.py --sport XC --fdr 0.001
#   python scripts/score_suspects.py --sport XC --tau 0.05   (pin it by hand)
#   python scripts/score_suspects.py --sport XC --out scripts
# ============================================================================

import argparse
import math
import os
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool

# Import (never copy) diag's residual definition, so `e` cannot drift.
from diag_suspects import (_TABLE, _buildResid, _divisionStats, _loadCorrections,
                           _alreadyTag)


# ================================================================== #
# CHUNK 1 -- CONSTANTS
# ================================================================== #

_IQR_TO_SD = 1.349          # IQR = 1.349 * sigma for a normal
_MEDIAN_SE_FACTOR = 1.2533  # SE of a median = 1.2533 * sigma / sqrt(n)

# FAN GATE (2026-07-18). A distance error shifts every runner by ONE constant,
# so it moves a division's median c WITHOUT widening its residual IQR. An
# anomalous race (slow field, ringers, corrupt times) scatters runners, so its
# IQR INFLATES above the honest baseline. A division whose iqr exceeds
# _FAN_MULT * honest-baseline is a FAN: its median is not distance-shaped, so no
# distance draft is auto-emitted for it -- it goes to the human worklist.
# NAMED tunable: calibrated against flag_slowrace_overrides.py's iqr histogram,
# set loose (pass a few fans rather than refuse a real fix). 163697/678411
# (iqr ~16%) is the worked example this exists to catch.
_FAN_MULT = 1.8
_MAD_TO_SD = 0.6745         # sd = MAD / 0.6745 for a normal
_NULL_CORE = 3.0            # |z| beyond this is presumed contamination
_MIN_FIELD = 12

_Z_GRID = (2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0)


# ================================================================== #
# CHUNK 2 -- SMALL STATISTICS HELPERS
# ================================================================== #

def _medianOf(values):
    """
    Purpose : plain median.
    Arguments: values -- non-empty list of floats.
    Output  : float. Used inside the MAD, so it must ignore the tail.
    """
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])


def _robustSd(values):
    """
    Purpose : sd estimated via the MAD -- immune to the contaminated tail.
    Arguments: values -- list of floats.
    Output  : (centre, sd). sd is 0.0 for a degenerate input; callers guard.
    Why MAD : an ordinary sd would be inflated BY the outliers we are hunting,
              which would then hide them. The MAD cannot be moved by <50% of
              the sample.
    """
    if not values:
        return 0.0, 0.0
    centre = _medianOf(values)
    mad = _medianOf([abs(v - centre) for v in values])
    return centre, mad / _MAD_TO_SD


def _seOfMedian(iqr, n):
    """
    Purpose : sampling SE of a division's median residual, from the spread that
              division itself shows. No new data, no new constant.
    Arguments: iqr -- p75(e)-p25(e) (diag already computes it); n -- finishers.
    Output  : float SE, or None when unestimable (n<2 or iqr<=0). iqr==0 means
              every runner has an identical residual: degenerate, refuse to score.
    """
    if n is None or n < 2 or iqr is None or iqr <= 0:
        return None
    return _MEDIAN_SE_FACTOR * (iqr / _IQR_TO_SD) / math.sqrt(n)


def _twoSidedTail(t):
    """
    Purpose : P(|Z| > t) for a standard normal = the false-positive RATE at cut t.
    Arguments: t -- positive cut.
    Output  : float. erfc is exact; no lookup table.
    """
    return math.erfc(t / math.sqrt(2.0))


# ================================================================== #
# CHUNK 3 -- THE NEW TERM: tau (real between-division spread)
# ================================================================== #

def _estimateTau(scored):
    """
    Purpose : estimate tau -- how much division medians REALLY vary for honest
              reasons (course, conditions, field composition). This is the term
              v1 was missing.
    Arguments:
      scored -- records carrying "c" (median residual) and "se" (sampling SE).
    Output  : float tau (log units, >= 0).
    Method  : moment-matching on a robust core.
                observed spread of c  =  tau^2 + average sampling noise
                => tau^2 = max(0, robust_var(c) - mean(SE^2))
              The core (|c - median| < 3 robust sd) excludes contamination, so
              the estimate describes CLEAN divisions -- otherwise broken races
              would inflate tau and then hide behind it.
    Guard   : returns 0.0 if the subtraction goes negative, i.e. the observed
              spread is fully explained by sampling noise -- in which case v1's
              null was right after all and nothing changes.
    """
    cs = [d["c"] for d in scored]
    centre, spread = _robustSd(cs)
    if spread <= 0:
        return 0.0

    core = [d for d in scored if abs(d["c"] - centre) < _NULL_CORE * spread]
    if len(core) < 100:
        core = scored

    _, core_sd = _robustSd([d["c"] for d in core])
    mean_se2 = sum(d["se"] ** 2 for d in core) / len(core)
    tau2 = core_sd ** 2 - mean_se2
    return math.sqrt(tau2) if tau2 > 0 else 0.0


def _honestIqr(scored):
    """
    Purpose : the TYPICAL residual IQR among honest (clean-core) divisions --
              the width a real distance error's IQR must not exceed.
    Arguments: scored -- records carrying "c" and "iqr".
    Output  : float (log units), floored tiny so the later division is safe.
    Method  : reuse _estimateTau's exact core -- centre the population on the
              median residual c, keep divisions within _NULL_CORE robust-sds
              (the contamination-free core), and take the MEDIAN of their iqr.
              Median, not mean: a few wide divisions that slip into the core
              must not drag the baseline up and thereby excuse the fans we hunt.
    """
    cs = [d["c"] for d in scored]
    centre, spread = _robustSd(cs)
    if spread <= 0:
        core = scored
    else:
        core = [d for d in scored if abs(d["c"] - centre) < _NULL_CORE * spread]
        if len(core) < 100:                     # too few clean -> use all
            core = scored
    baseline = _medianOf([d["iqr"] for d in core])
    return max(baseline, 1e-6)


def _tagFans(scored, baseline):
    """
    Purpose : mark divisions whose residuals FAN (iqr inflated past baseline),
              so the inversion refuses them a distance draft.
    Arguments: scored; baseline from _honestIqr.
    Output  : None; adds "fan" (bool) and "fan_ratio" (iqr/baseline) per record.
    Second pass on purpose: the baseline needs the whole population first, the
    same reason tau is a second pass. A per-row test cannot know the honest width.
    """
    for d in scored:
        ratio = d["iqr"] / baseline
        d["fan_ratio"] = ratio
        d["fan"] = ratio >= _FAN_MULT


def _empiricalNullScale(zs):
    """
    Purpose : measure the null's actual width rather than assuming N(0,1).
    Arguments: zs -- list of z scores.
    Output  : float sigma0 (>0); z/sigma0 then has a unit-width null.
    Reading : AFTER tau is included, sigma0 ~= 1.0 means the model is complete
              (spread = real effects + sampling noise, nothing unexplained).
              sigma0 >> 1 means a third source of variance is still unmodelled.
              This number grades the model; it is not a fudge factor.
    """
    core = [z for z in zs if abs(z) < _NULL_CORE]
    if len(core) < 100:
        core = zs
    if not core:
        return 1.0
    _, sd = _robustSd(core)
    return sd if sd > 1e-9 else 1.0


# ================================================================== #
# CHUNK 4 -- DIVISION SCORING
# ================================================================== #

def _scoreDivisions(rows):
    """
    Purpose : turn diag's (source, meet, div, n, c, iqr, d_eff) into records
              carrying the sampling SE. tau is added afterwards (it needs the
              whole population to estimate).
    Arguments: rows -- output of _divisionStats.
    Output  : list of dicts. Divisions with no estimable SE are dropped --
              silence beats a fabricated score.
    """
    scored = []
    for source, meet, div, n, c, iqr, d_eff in rows:
        se = _seOfMedian(iqr, n)
        if se is None or c is None:
            continue
        scored.append({"source": source, "meet": meet, "div": div, "n": n,
                       "c": float(c), "iqr": float(iqr), "d_eff": d_eff,
                       "se": se})
    return scored


def _applyRandomEffects(scored, tau):
    """
    Purpose : attach the corrected score. THIS IS THE v2 FIX, one line of maths.
    Arguments:
      scored -- records with "c" and "se" (mutated in place).
      tau    -- from _estimateTau, or a CLI override.
    Output  : None. Every record gains "z" = c / sqrt(tau^2 + SE^2).
    Meaning : the denominator is now "everything a CLEAN division could be off
              by" -- real course effect (tau) plus sampling noise (SE), added in
              quadrature because they are independent. Large-n divisions no
              longer explode: their tiny SE is floored by tau.
    """
    tau2 = tau ** 2
    for d in scored:
        d["z"] = d["c"] / math.sqrt(tau2 + d["se"] ** 2)


def _applyNull(scored):
    """
    Purpose : rescale by the measured null width and store zz.
    Arguments: scored -- records with "z" (mutated).
    Output  : sigma0, for reporting.
    """
    sigma0 = _empiricalNullScale([d["z"] for d in scored])
    for d in scored:
        d["zz"] = d["z"] / sigma0
    return sigma0


# ================================================================== #
# CHUNK 5 -- ROW SCORING  (unchanged: v1's null was correct here)
# ================================================================== #

def _rowZQuery(min_field):
    """
    Purpose : SQL scoring every RESULT against its own division, off the same
              _resid temp table diag builds.
    Arguments: min_field -- skip smaller divisions.
    Output  : SQL string; columns source, meet, div, result_id, ident, e, dev,
              nt, z.
    Logic   : dev = e - c is the runner's swing AFTER the field's own swing is
              removed (so mud/heat cancel). Scale by the division's own
              sigma = iqr/1.349. NO sqrt(n): this is ONE observation, not a
              median of n. No tau term either -- the null here is "a runner is
              at their own level", which really is centred on zero, and the
              sigma0 = 0.958 measured in v1 confirms it.
    """
    return f"""
        WITH divstat AS (
            SELECT source, meet, div,
                   count(*) AS n,
                   percentile_cont(0.5)  WITHIN GROUP (ORDER BY e) AS c,
                   percentile_cont(0.75) WITHIN GROUP (ORDER BY e)
                 - percentile_cont(0.25) WITHIN GROUP (ORDER BY e) AS iqr
            FROM _resid
            GROUP BY source, meet, div
            HAVING count(*) >= {int(min_field)}
        )
        SELECT r.source, r.meet, r.div, r.result_id, r.ident,
               r.e, r.e - d.c AS dev, r.nt,
               (r.e - d.c) / NULLIF(d.iqr / {_IQR_TO_SD}, 0) AS z
        FROM _resid r
        JOIN divstat d USING (source, meet, div)
        WHERE d.iqr > 0
    """


def _fetchRowZ(cur, min_field):
    """
    Purpose : run the row scorer, return only what the FDR needs.
    Arguments: cur, min_field.
    Output  : list of (result_id, meet, div, nt, z) -- lean on purpose; this is
              ~30M rows and the transfer is the cost, not the group-by.
    """
    cur.execute(_rowZQuery(min_field))
    return [(r[3], r[1], r[2], r[7], float(r[8]))
            for r in cur.fetchall() if r[8] is not None]


# ================================================================== #
# CHUNK 6 -- FDR
# ================================================================== #

def _fdrCurve(zz, grid=_Z_GRID):
    """
    Purpose : the calibration table -- at each cut: how many flag, how many the
              null ALONE explains, and the resulting false-discovery rate.
    Arguments: zz -- null-scaled scores; grid -- cuts.
    Output  : list of (t, observed, expected_false, fdr). fdr=1.0 when nothing
              is observed, so an empty cut can never be "picked".
    """
    n = len(zz)
    out = []
    for t in grid:
        observed = sum(1 for z in zz if abs(z) > t)
        expected = _twoSidedTail(t) * n
        out.append((t, observed, expected,
                    min((expected / observed) if observed else 1.0, 1.0)))
    return out


def _pickCut(curve, target):
    """
    Purpose : smallest cut meeting the tolerance -- keep the most real signal
              while holding contamination to `target`.
    Arguments: curve, target (0.01 = at most 1% of the worklist is noise).
    Output  : (t, observed, fdr) or None -- and None is a real answer: no
              separable contamination.
    """
    for t, observed, expected, fdr in curve:
        if observed > 0 and fdr <= target:
            return t, observed, fdr
    return None


# ================================================================== #
# CHUNK 7 -- REPORTING
# ================================================================== #

def _printCurve(title, curve, sigma0, n, tau=None):
    """
    Purpose : print the whole curve, not just the chosen cut, so the decision is
              auditable and a different tolerance can be read off by eye.
    Arguments: title, curve, sigma0, n, tau (divisions only).
    Output  : None.
    """
    print(f"\n  {title}")
    if tau is not None:
        print(f"    tau (real division spread) = {tau:.4f} log "
              f"= {100*(math.exp(tau)-1):.1f}%  <- a clean field can be this far off")
    verdict = ("model complete" if abs(sigma0 - 1) < 0.15
               else "UNMODELLED VARIANCE REMAINS")
    print(f"    population {n:,}   empirical null sigma0 = {sigma0:.3f}  ({verdict})")
    print(f"    {'cut |z|':>8} {'flagged':>12} {'null-explains':>14} {'FDR':>8}")
    for t, observed, expected, fdr in curve:
        print(f"    {t:>8.1f} {observed:>12,} {expected:>14,.0f} {fdr:>8.1%}")


def _printPicked(kind, picked, target):
    """
    Purpose : state the decision in one line, or state plainly there is none.
    Arguments: kind, picked, target.
    Output  : chosen t, or None.
    """
    if picked is None:
        print(f"    -> no cut reaches FDR <= {target:.1%}: no separable "
              f"contamination in {kind}.")
        return None
    t, observed, fdr = picked
    print(f"    -> CUT |z| > {t:.1f}: {observed:,} {kind} flagged, "
          f"FDR {fdr:.2%} (<= {target:.1%})")
    return t


def _writeDivisions(path, scored, cut, corr):
    """
    Purpose : worksheet of divisions surviving the cut, worst first.
    Arguments: path, scored (with zz), cut (None -> nothing), corr for the
               already-overridden tag (that is what makes a REGRESSION visible).
    Output  : count written.
    """
    if cut is None:
        return 0
    hits = sorted((d for d in scored if abs(d["zz"]) > cut),
                  key=lambda d: -abs(d["zz"]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# source\tmeet_id\tdiv_id\tn\tc_pct\tiqr_pct\tz\t"
                "fan\tfan_ratio\talready\n")
        for d in hits:
            # .get() so a scored record built before the fan pass (or by a
            # caller that skipped it) still writes, marked unknown rather than
            # crashing -- absence is not "not a fan".
            fan = "FAN" if d.get("fan") else "-"
            fan_ratio = d.get("fan_ratio")
            fan_ratio_s = f"{fan_ratio:.2f}" if fan_ratio is not None else "?"
            f.write(f"{d['source']}\t{d['meet']}\t{d['div']}\t{d['n']}\t"
                    f"{100*(math.exp(d['c'])-1):+.2f}\t{100*d['iqr']:.2f}\t"
                    f"{d['zz']:+.1f}\t{fan}\t{fan_ratio_s}\t"
                    f"{_alreadyTag(corr, d['meet'], d['div'])}\n")
    return len(hits)


def _writeRows(path, rowzz, cut, limit):
    """
    Purpose : worksheet of individually implausible RESULTS.
    Arguments: path, rowzz, cut, limit on lines written.
    Output  : true count crossing (caller prints; file holds up to `limit`).
    """
    if cut is None:
        return 0
    hits = sorted((r for r in rowzz if abs(r[4]) > cut), key=lambda r: -abs(r[4]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# result_id\tmeet_id\tdiv_id\tnt\tz\n")
        for rid, meet, div, nt, z in hits[:limit]:
            f.write(f"{rid}\t{meet}\t{div}\t{nt:.1f}\t{z:+.1f}\n")
    return len(hits)


# ================================================================== #
# CHUNK 8 -- ORCHESTRATION
# ================================================================== #

def _run(sport, target, min_field, out_dir, row_limit, corr_path, tau_override):
    """
    Purpose : one pass -- residuals, score divisions (with tau) and rows, print
              both FDR curves, optionally write worksheets. Never writes the DB.
    Arguments: sport, target FDR, min_field, out_dir (None = report only),
               row_limit, corr_path, tau_override (None = estimate).
    Output  : None.
    """
    corr = _loadCorrections(corr_path, sport)

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '2GB'")
        print("  building bracket residual (heavy pass, same as diag) ...")
        resid_n = _buildResid(cur, _TABLE[sport])
        print(f"  {resid_n:,} results earned a bracket residual")

        scored = _scoreDivisions(_divisionStats(cur, min_field))
        tau = tau_override if tau_override is not None else _estimateTau(scored)
        _applyRandomEffects(scored, tau)
        sigma0 = _applyNull(scored)

        # FAN GATE: measure the honest residual-IQR baseline from the clean core,
        # then tag divisions whose IQR is inflated past it. A fan's median is not
        # a trustworthy distance signal (678411: slow race, not a mislabel), so
        # the adjudicator will refuse it a distance draft and route it to a human.
        fan_baseline = _honestIqr(scored)
        _tagFans(scored, fan_baseline)
        n_fan = sum(1 for d in scored if d["fan"])
        print(f"  fan gate: honest IQR baseline = {100*fan_baseline:.2f}% ; "
              f"{n_fan:,} of {len(scored):,} scored divisions are FANS "
              f"(IQR >= {_FAN_MULT} x baseline) -> no auto distance draft")
        div_curve = _fdrCurve([d["zz"] for d in scored])

        print("\n  fetching row scores ...")
        rowz = _fetchRowZ(cur, min_field)
        row_sigma0 = _empiricalNullScale([r[4] for r in rowz])
        rowzz = [(r[0], r[1], r[2], r[3], r[4] / row_sigma0) for r in rowz]
        row_curve = _fdrCurve([r[4] for r in rowzz])

        conn.rollback()                      # read-only, always

    _printCurve("DIVISIONS -- is this field off by more than a real course could explain?",
                div_curve, sigma0, len(scored), tau=tau)
    div_cut = _printPicked("divisions", _pickCut(div_curve, target), target)

    _printCurve("ROWS -- is this runner off their own field?",
                row_curve, row_sigma0, len(rowzz))
    row_cut = _printPicked("rows", _pickCut(row_curve, target), target)

    if not out_dir:
        print("\n  (report only; pass --out to write worksheets)")
        return

    os.makedirs(out_dir, exist_ok=True)
    dp = os.path.join(out_dir, f"scored_div_{sport.lower()}.tsv")
    rp = os.path.join(out_dir, f"scored_row_{sport.lower()}.tsv")
    nd = _writeDivisions(dp, scored, div_cut, corr)
    nr = _writeRows(rp, rowzz, row_cut, row_limit)
    print(f"\n  wrote:\n    {dp}  ({nd:,} divisions)")
    print(f"    {rp}  ({nr:,} rows crossing; wrote up to {row_limit:,})")


def main():
    ap = argparse.ArgumentParser(
        description="Score suspects by significance against a random-effects "
                    "null, cut by FDR. READ-ONLY.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--fdr", type=float, default=0.01,
                    help="max tolerated false-discovery rate (default 0.01)")
    ap.add_argument("--tau", type=float, default=None,
                    help="pin the real between-division spread (log units) "
                         "instead of estimating it; 0 reproduces v1")
    ap.add_argument("--min-field", type=int, default=_MIN_FIELD)
    ap.add_argument("--row-limit", type=int, default=50000)
    ap.add_argument("--out", default=None,
                    help="directory for worksheets; omit to report only")
    ap.add_argument("--corrections", default=None)
    args = ap.parse_args()

    initPool()
    _run(args.sport, args.fdr, args.min_field, args.out, args.row_limit,
         args.corrections, args.tau)


if __name__ == "__main__":
    main()