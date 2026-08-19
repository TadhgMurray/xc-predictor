# Project: xc-predictor
# File:    scripts/smoke_artifacts.py
# Purpose: Fire every artifact-gated branch in normalize_distance ONCE,
#          today, with toy artifacts — so the first execution of each branch
#          happens in ten seconds here instead of an hour into a real
#          stream. Born from the .ev crash: the distance-spline evaluation
#          line existed for months and had NEVER RUN, because its pickle
#          never existed; the missing-artifact no-op is graceful exactly
#          until the artifact lands, and then the branch runs for the first
#          time in production.
#
#          WHAT THIS IS NOT: validation. The synthetic tests prove the
#          solvers recover planted truths; this proves only that CALLING
#          each path doesn't throw and returns a finite number in the
#          right direction. Cheap question, cheap answer, asked early.
#
#          HOW IT WORKS: toy artifacts are INJECTED by assigning the
#          module's own globals (no temp files, no reload games), the
#          factor cache is cleared (stale cached factors would serve the
#          OLD artifacts — the one trap in this technique), every branch
#          is fired, and the originals are restored in a finally block so
#          an interactive session isn't left poisoned.
#
# Usage:   python scripts/smoke_artifacts.py     (exit 0 = no smoke)

import sys
sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

import math
import numpy as np
from scipy.interpolate import SmoothBivariateSpline

import normalize_distance as nd
from venue_geometry_overrides import applyVenueOverride, OVERRIDES

# ------------------------------------------------------------------ #
# TOY ARTIFACTS
# ------------------------------------------------------------------ #

# _toyDistanceSplines
# Purpose:   A real SmoothBivariateSpline (the actual class the consumer
#            evaluates — a stub would smoke-test nothing) fit on synthetic
#            exponent-1.06 points, so predictions are also CHECKABLE
#            against the closed form.
# Output:    {"global": spline} — the pickle's minimal legal shape.
def _toyDistanceSplines():
    log_x, log_z, log_y = [], [], []
    for d1 in (800, 1500, 3000, 5000, 8000, 10000):     # 6x6 grid: SciPy
        for d2 in (800, 1600, 3200, 5000, 8000, 12000): # needs >= 16 pts
            if d1 == d2:
                continue
            log_x.append(math.log(d2 / d1))
            log_z.append(math.log((d1 + d2) / 2))
            log_y.append(1.06 * math.log(d2 / d1))      # exact power law
    s = SmoothBivariateSpline(np.array(log_x), np.array(log_z),
                              np.array(log_y), kx=3, ky=3, s=0.001)
    return {"global": s}


# _toyEraArtifact
# Purpose:   A minimal era_banded artifact with KNOWN levels: TF|m5000|M
#            carries -0.02 log in 2015 (2% faster reference frame), and
#            TF|steeple|M exists as a borrowed copy — so both a native and
#            a borrowed key resolve at inference.
# Output:    the artifact dict, exactly _saveCurves' shape.
def _toyEraArtifact():
    m5000 = {"curve": {2015: -0.02, 2024: 0.0}, "reference": 2024,
             "dev_rates": {}, "n_steps": 1, "n_bench": 1}
    return {"kind": "era_banded",
            "curves": {"TF|m5000|M": m5000,
                       "TF|steeple|M": dict(m5000, borrowed_from="TF|m5000|M")},
            "trust_benchmark": 1.0, "shoe_window": (2017, 2019)}

# ------------------------------------------------------------------ #
# THE CHECKS (each: fire one branch, assert finite + direction)
# ------------------------------------------------------------------ #

def _ok(label, detail=""):
    print(f"  PASS  {label}{'  (' + detail + ')' if detail else ''}")


# _pctVs
# Purpose:   A value's net effect relative to a baseline, in percent —
#            the unit every geometry statement in this project speaks.
def _pctVs(value, base):
    return (value / base - 1.0) * 100.0


# _checkDistanceSpline
# Purpose:   The branch the .ev crash lived in. An 800m time must map to a
#            LARGER 5K-equivalent (you can't hold 800 pace for 5K), and the
#            toy's known exponent makes the value checkable to ~1%.
def _checkDistanceSpline():
    out = nd.normalizeTime(120.0, 800, "college_m")     # 2:00 800m
    expected = 120.0 * (5000 / 800) ** 1.06
    assert out is not None and math.isfinite(out), "non-finite"
    assert out > 120.0, "5K-equiv must exceed the 800 time"
    assert abs(out - expected) / expected < 0.01, \
        f"toy spline off closed form: {out:.1f} vs {expected:.1f}"
    _ok("distance LEGACY spline (.ev path)", f"{out:.1f}s vs {expected:.1f}s")


# _toyPotentialArtifact / _checkDistancePotential
# Purpose:   The SHIPPING artifact kind. A straight-line potential IS a
#            single exponent (values = 1.06 * (knot - log 5000)), so the
#            consumer's mirror evaluator is checkable against the same
#            closed form — including one point BEYOND the knots, which
#            exercises the boundary-slope extrapolation policy.
def _toyPotentialArtifact():
    l5k = math.log(5000.0)
    knots = [l5k + 0.15 * i for i in range(-8, 6)]      # ~1100m .. 10500m
    line = lambda e: [e * (k - l5k) for k in knots]     # exponent-e curve
    return {"kind": "distance_potential", "target": 5000.0,
            # one per-sport pool curve (exponent 0.97) + a per-sport
            # global (1.15) + the combined global (1.06): the toy spans
            # the whole lookup chain, so a mis-keyed consumer fails HERE.
            "pools": {"college_m|XC": {"knots": knots, "values": line(0.97)}},
            "global_by_sport": {"TF": {"knots": knots, "values": line(1.15)}},
            "global": {"knots": knots, "values": line(1.06)}}


def _checkDistancePotential():
    nd._SPLINES = _toyPotentialArtifact()
    _clearFactorCache()                    # stale factors = silent bypass
    for dist in (1600, 3200, 800):         # 800 lies BEYOND the toy knots
        out = nd.normalizeTime(100.0, dist, "college_m")
        expected = 100.0 * (5000 / dist) ** 1.06
        assert out is not None and math.isfinite(out), "non-finite"
        assert abs(out - expected) / expected < 0.005, \
            f"potential off closed form at {dist}: {out:.1f} vs {expected:.1f}"
    # THE LOOKUP CHAIN: each rung of pool|sport -> global|sport -> global
    # must resolve a DIFFERENT toy exponent. A consumer that drops or
    # mangles the sport key collapses these three onto one value.
    def _exp_of(pool, sport):
        out = nd.normalizeTime(100.0, 1600, pool, sport=sport)
        return math.log(out / 100.0) / math.log(5000 / 1600)
    for got, want, rung in ((_exp_of("college_m", "XC"), 0.97, "pool|XC"),
                            (_exp_of("college_m", "TF"), 1.15, "global|TF"),
                            (_exp_of("college_m", None), 1.06, "global")):
        assert abs(got - want) < 0.005, f"{rung}: {got:.3f} != {want}"
    _ok("distance POTENTIAL (dispatch + mirror + extrapolation + "
        "per-sport lookup chain)")


# _checkGeometryBranches
# Purpose:   Fire length + banking with whatever geometry pickle is REALLY
#            on disk. Present -> the corrections must move the result;
#            absent -> the no-op fallback must hold. Both are PASS states —
#            the test reports which world it ran in.
def _checkGeometryBranches():
    base   = nd.normalizeTime(300.0, 1609, "college_m")
    flat2  = nd.normalizeTime(300.0, 1609, "college_m",
                              track_length=200.0, track_type="Flat")
    banked = nd.normalizeTime(300.0, 1609, "college_m",
                              track_length=200.0, track_type="Banked")
    for v in (base, flat2, banked):
        assert v is not None and math.isfinite(v), "non-finite"
    if flat2 == base and banked == base:
        _ok("geometry (no pickle on disk -> no-op fallback held)")
        return
    # EVIDENCE BEFORE JUDGMENT: print the three values first — a failed
    # assert that hides its inputs forces a rerun to learn numbers this
    # function already computed.
    fp, bp = _pctVs(flat2, base), _pctVs(banked, base)
    print(f"        base {base:.2f}   flat200 {flat2:.2f} ({fp:+.2f}%)"
          f"   banked200 {banked:.2f} ({bp:+.2f}%)")
    # ASSERT ONLY INVARIANTS — claims backed by validated fitted facts:
    #   flat200 < base    <- g1(200) > 0 (short tracks are slower;
    #                        synthetic-recovered, placebo-checked)
    #   flat200 < banked  <- banking benefit > 0 (same standard)
    # NOT asserted: banked vs base. That sign is EMPIRICAL — the
    # difference of two fitted curves (banking benefit minus length
    # penalty at this distance), guaranteed by nothing, genuinely
    # contested in the sport, and predicted to MOVE at the post-override
    # banking refit (the banked cloud's known contamination diluted the
    # benefit). Two versions of this check asserted beliefs here — first
    # the wrong direction, then a direction that is a finding, not a
    # law. An assert is a transcribed invariant; a finding gets printed.
    assert flat2 < base, "length correction must fire and reduce (g1(200)>0)"
    assert flat2 < banked, "banking must refund something (benefit > 0)"
    for label, p in (("flat200", fp), ("banked200", bp)):
        assert abs(p) < 3.0, (f"{label} net effect {p:+.2f}% is outside "
                              f"the +/-3% sanity band — garbage, not fit")
    note = ""
    if banked >= base:
        note = ("  NOTE: net banked-200 >= base at the mile — the fitted "
                "banking benefit currently exceeds the length penalty "
                "here. Not an error; a READING. Revisit at the "
                "post-override banking refit.")
    _ok("geometry (invariants held; net banked sign reported, not assumed)",
        f"flat {fp:+.2f}% / banked {bp:+.2f}%") 
    if note:
        print(note)


# _checkEraBranches
# Purpose:   The NEXT never-run branch (era_curve.pkl doesn't exist yet).
#            Native key, borrowed key, unknown band, and no-sport must all
#            behave: the first two correct by the planted -0.02, the last
#            two must be exact no-ops.
def _checkEraBranches():
    plain = nd.normalizeTime(900.0, 5000, "college_m")
    era   = nd.normalizeTime(900.0, 5000, "college_m",
                             season=2015, sport="TF", event_short="5000m")
    assert era is not None and math.isfinite(era), "non-finite"
    assert era != plain, ("era_banded branch did not fire — key mismatch "
                          "between the fitter's save and the consumer's "
                          "lookup? THIS is the failure this test exists for")
    moved = math.log(era / plain)
    assert abs(moved - 0.02) < 0.002, f"expected ~+0.02 log, got {moved:+.4f}"

    borrowed = nd.normalizeTime(560.0, 3000, "college_m", season=2015,
                                sport="TF", event_short="3000mSteeple")
    assert borrowed != nd.normalizeTime(560.0, 3000, "college_m"), \
        "borrowed steeple key did not resolve"

    unknown = nd.normalizeTime(900.0, 5000, "college_f",
                               season=2015, sport="TF", event_short="5000m")
    no_sport = nd.normalizeTime(900.0, 5000, "college_m", season=2015)
    assert unknown == nd.normalizeTime(900.0, 5000, "college_f"), \
        "unfitted partition must no-op"
    assert no_sport == plain, "banded era must no-op without sport"
    _ok("era banded (native / borrowed / unfitted no-op / no-sport no-op)",
        f"2015 shift {moved:+.4f} log")


# _checkOverrides
# Purpose:   The override's three behaviors on the real table: a pre-reno
#            correction, Iowa's multi-field rule, and the no-date abstention.
def _checkOverrides():
    if not OVERRIDES:
        _ok("venue overrides (table empty — nothing to fire)")
        return
    pre, r1 = applyVenueOverride(
        {"track_type": "Banked", "track_length": 200.0, "is_indoor": 1},
        63253, "2010-02-01")
    assert r1 is not None and pre["track_type"] == "Flat", "Clemson pre-reno"
    iowa, r2 = applyVenueOverride(
        {"track_type": "Banked", "track_length": 299.0, "is_indoor": 1},
        85120, "2019-01-15")
    assert r2 is not None and iowa["track_length"] == 200.0, "Iowa length fix"
    same, r3 = applyVenueOverride(
        {"track_type": "Banked", "track_length": 200.0, "is_indoor": 1},
        63253, None)
    assert r3 is None and same["track_type"] == "Banked", "no-date abstention"
    _ok("venue overrides (renovation / multi-field / no-date)")

# ------------------------------------------------------------------ #
# THE RUNNER (inject -> clear cache -> fire -> restore, ALWAYS)
# ------------------------------------------------------------------ #

# _clearFactorCache
# Purpose:   The one trap in global-injection: _normalizationFactorCached
#            memoizes factors keyed on inputs, so factors computed under
#            the ORIGINAL artifacts would silently serve after injection.
#            Cleared before AND after; getattr-guarded in case the cache
#            decorator ever changes.
def _clearFactorCache():
    getattr(nd._normalizationFactorCached, "cache_clear", lambda: None)()


def main():
    print("=== artifact smoke test ===")
    saved = (nd._SPLINES, nd._ERA)          # originals, restored no matter what
    try:
        nd._SPLINES = _toyDistanceSplines()
        nd._ERA     = _toyEraArtifact()
        _clearFactorCache()
        _checkDistanceSpline()
        _checkDistancePotential()
        _checkGeometryBranches()
        _checkEraBranches()
        _checkOverrides()
    finally:
        nd._SPLINES, nd._ERA = saved        # leave the module as found
        _clearFactorCache()
    print("no smoke: every artifact-gated branch executed clean")


if __name__ == "__main__":
    main()