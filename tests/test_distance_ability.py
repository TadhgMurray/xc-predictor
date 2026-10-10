# Project: xc-predictor / tests
# File:    test_distance_ability.py
# Purpose: the distance curve by ABILITY (owner, 2026-09-29, approved behind
#          switches: "try to be safe and test it"). Synthetic, no database:
#
#   1. the fit recovers a planted ability-dependent curve, its calendar
#      offset, and the degrees that generated it -- and finds no pool
#      effect where none was planted, but does find one that was;
#   2. the evaluator's forward and inverse are exact inverses and the
#      forward is increasing in the time (two runners never swap);
#   3. ONE SCALE: under XCP_DISTANCE_BY=ability the same run gives the same
#      normalized_time, and (with XCP_ONE_SCALE=1) the same HS-equivalent,
#      under every pool label of one gender -- where the pool curves gave
#      the report's 156.6 / 150.0 / 147.6;
#   4. the conversions round trip closes by ability, and the backfill
#      refuses a partial write in a new mode;
#   5. THE PER-POOL RESIDUAL (owner, 2026-09-29: "adopt the ability curve
#      with the per-pool residual applied"): the joint fit recovers a
#      planted residual (closer than the measured-after number), applies
#      only a significant one with support, the conversion applies it and
#      inverts exactly, high school is unchanged, the label dependence it
#      brings back is the size the delta says, and the mode is recorded.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_distance_ability.py
import math
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "backfill")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    # ! THE FITTER'S TWO SQL BUILDERS ARE CALLED AT ITS IMPORT: a stub that
    #   answers them with a dict breaks every later file that imports it
    _c.distanceDropSQL = lambda *a, **k: ""
    _c.__getattr__ = lambda name: (  # not dunders: see test_model_one_scale
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c

import distance_ability as DA                                # noqa: E402
import fit_distance_ability as F                             # noqa: E402
import normalize_distance as nd                              # noqa: E402

REF = 1050.0                     # the planted population's 5K reference


def g_true(x, a, pool_delta=0.0):
    """The planted law: the exponent rises with slowness (a > 0 slower),
    the curvature falls with it."""
    return (1.06 + 0.12 * a + pool_delta) * x + (0.03 - 0.05 * a) * x ** 2


def _pairs(rng, pool, n, mu, sd, dists, pool_delta=0.0, noise=0.02, cal=-0.003):
    a = rng.normal(mu, sd, n)
    out = []
    for i in range(n):
        d1, d2 = rng.choice(dists, 2, replace=False)
        x1, x2 = math.log(d1 / 5000.0), math.log(d2 / 5000.0)
        t1 = REF * math.exp(a[i] + g_true(x1, a[i], pool_delta) + rng.normal(0, noise))
        t2 = REF * math.exp(a[i] + g_true(x2, a[i], pool_delta) + rng.normal(0, noise) + cal)
        out.append({"pool": pool, "distance1": float(d1), "distance2": float(d2),
                    "time1": t1, "time2": t2})
    return out


def _corpus(seed=1, college_delta=0.0, n=6000, ms_delta=0.0, extra=()):
    """Three pools of different speed over different distances: high school
    3k-8k, college (faster) 5k-10k, middle school (slower) 1600-3200 --
    no pool covers the whole range, the family does. `extra`: more
    (pool, n, mu, sd, dists, delta) groups."""
    rng = np.random.default_rng(seed)
    pairs = (_pairs(rng, "hs_m", n, 0.0, 0.08, [3000, 4000, 4828, 5000, 8000])
             + _pairs(rng, "college_m", n, -0.12, 0.06, [5000, 6000, 8000, 10000],
                      pool_delta=college_delta)
             + _pairs(rng, "ms_m", n, 0.25, 0.10, [1600, 2414, 3000, 3200],
                      pool_delta=ms_delta))
    for pool, k, mu, sd, dists, dl in extra:
        pairs += _pairs(rng, pool, k, mu, sd, dists, pool_delta=dl)
    return F.pairArrays(pairs)


# ------------------------------------------------------------------ #
# 1. THE FIT
# ------------------------------------------------------------------ #

def test_fit_recovers_the_planted_ability_curve():
    arr = _corpus()
    e = F.fitFamily(arr, 2, 2)
    # the index's zero is the high-school median: the planted 0
    assert e["ref_5k"] == pytest.approx(REF, rel=0.01)
    assert e["calendar"] == pytest.approx(-0.003, abs=0.001)
    worst = 0.0
    for d in (1600, 2414, 3000, 5000, 8000, 10000):
        x = math.log(d / 5000.0)
        for a in (-0.15, -0.05, 0.0, 0.1, 0.25):
            if not e["a_lo"] <= a <= e["a_hi"]:
                continue
            # compared at the SAME runner: the fit's index is log(T5/ref_5k)
            a_fit = a + math.log(REF / e["ref_5k"])
            worst = max(worst, abs(DA.g(e, x, a_fit) - g_true(x, a)))
    assert worst < 0.003, f"the planted curve came back {100 * worst:.2f}% off"
    # the ability term is found, not invented: the exponent at 5000 m rises
    # by 0.12 per unit of log-slowness
    assert e["beta"][0] == pytest.approx(0.12, abs=0.02)


def test_no_pool_effect_is_found_where_none_was_planted():
    arr = _corpus()
    e = F.fitFamily(arr, 2, 2)
    for pool, (dlt, se, n) in F.poolResidual(arr, e, min_pairs=100).items():
        assert abs(dlt) < 3 * se + 0.002, f"{pool} {dlt:+.4f} +- {se:.4f}"


def test_a_real_pool_effect_is_reported():
    """The honest half: if equal-ability college runners really slow more,
    the residual says so, with its size."""
    arr = _corpus(college_delta=0.03)
    e = F.fitFamily(arr, 2, 2)
    res = F.poolResidual(arr, e, min_pairs=100)
    dlt, se, _n = res["college_m"]
    assert dlt == pytest.approx(0.03, abs=0.01) and abs(dlt) > 3 * se


def test_cross_validation_picks_the_planted_degrees():
    arr = _corpus(n=4000)
    pick, scores = F.cvDegrees(arr, np.random.default_rng(3), verbose=False)
    assert pick == (2, 2), pick


def test_the_family_passes_its_own_safety_check():
    e = F.fitFamily(_corpus(), 2, 2)
    ok, den, kmin = F.checkFamily(e)
    assert ok and den > 0.5 and kmin > 0.5


def test_fit_all_keys_families_by_gender_and_sport():
    xc = _corpus(n=1500)
    tf = F.pairArrays([])
    art = F.fitAll(xc, tf, verbose=False, degrees={k: (2, 2) for k in
                                                     ("m|XC", "u|XC", "m|*", "u|*")},
                   agreement=False, degrees_joint={"m|XC": (2, 2)})
    assert art["kind"] == DA.KIND and art["target"] == 5000.0
    assert {"m|XC", "u|XC", "m|*", "u|*"} <= set(art["families"])
    assert "f|XC" not in art["families"]            # no women's pairs, no family
    # the lookup falls back gender-blind, then sport-blind
    assert DA.family(art, "hs_f", "XC") is art["families"]["u|XC"]
    assert DA.family(art, "hs_m", "TF") is art["families"]["m|*"]


def test_the_writer_refuses_the_pool_artifact(tmp_path):
    with pytest.raises(SystemExit):
        F.writeArtifact({"kind": DA.KIND}, str(tmp_path / "distance_spline.pkl"))


# ------------------------------------------------------------------ #
# 2. THE EVALUATOR
# ------------------------------------------------------------------ #

FAM_M = {"alpha": [1.06, 0.03], "beta": [0.12, -0.05], "ref_5k": REF,
         "a_lo": -0.25, "a_hi": 0.45, "x_lo": math.log(1600 / 5000.0),
         "x_hi": math.log(10000 / 5000.0), "calendar": 0.0}
FAM_F = dict(FAM_M, ref_5k=1250.0, alpha=[1.07, 0.02])
ART = {"kind": DA.KIND, "target": 5000.0,
       "families": {"m|XC": FAM_M, "f|XC": FAM_F, "u|XC": FAM_M,
                    "m|*": FAM_M, "f|*": FAM_F, "u|*": FAM_M}}


def test_forward_and_inverse_are_exact_inverses_and_monotone():
    for d in (800.0, 1600.0, 3000.0, 5000.0, 8000.0, 10000.0, 15000.0):
        x = math.log(d / 5000.0)
        last = None
        # from far faster than the fastest pair to far slower than the
        # slowest: the clamped tails included
        for t5 in np.linspace(REF * 0.5, REF * 2.0, 60):
            lt = DA.inverseLog(FAM_M, math.log(t5), x)
            assert DA.forwardLog(FAM_M, lt, x) == pytest.approx(math.log(t5), abs=1e-12)
            if last is not None:
                assert lt > last, "a slower 5K must be a slower race"
            last = lt


def test_the_curve_is_the_runners_not_the_pools():
    """Fast and slow runners convert 10K -> 5K differently (the ability
    term); the same runner converts the same whatever the pool."""
    fast = DA.factorForNorm(FAM_M, REF * math.exp(-0.2), 10000.0)
    slow = DA.factorForNorm(FAM_M, REF * math.exp(0.3), 10000.0)
    assert fast > slow * 1.01       # a fast runner fades less over 10K
    for pool in ("hs_m", "college_m", "ms_m", "pro_m"):
        assert DA.family(ART, pool, "XC") is FAM_M


# ------------------------------------------------------------------ #
# 3. ONE SCALE
# ------------------------------------------------------------------ #

@pytest.fixture
def ability(monkeypatch):
    monkeypatch.setattr(nd, "_ABILITY", ART)
    for fn in (nd._normalizationFactorCached, nd._otherFactorCached, nd._abilityPQ):
        fn.cache_clear()
    yield
    for fn in (nd._normalizationFactorCached, nd._otherFactorCached, nd._abilityPQ):
        fn.cache_clear()


def test_the_same_run_is_one_number_under_every_label(ability):
    t, d = 28 * 60 + 34.0, 10000.0          # the report's run
    norms = {p: nd.normalizeTime(t, d, p, sport="XC")
             for p in ("hs_m", "college_m", "ms_m", "pro_m", "elem_m")}
    assert len(set(norms.values())) == 1, norms
    # and it is THIS runner's conversion, read off his own time
    want = t * DA.factorForTime(FAM_M, t, d)
    assert norms["hs_m"] == pytest.approx(want, abs=0.006)
    # a gender is not a label: the women's family is a different curve
    assert nd.normalizeTime(t, d, "hs_f", sport="XC") != norms["hs_m"]
    # one anchor for everyone, so nothing downstream rescales
    for p in ("hs_m", "college_m", "ms_m", "college_f"):
        assert nd.targetFor(p, "XC") == 5000.0
        assert nd.anchorShift(p, "XC") == 1.0
        assert nd.poolBandFor(p) == (nd.PACE_FLOOR * 5000.0, nd.PACE_CEIL * 5000.0)


def test_factor_for_norm_inverts_normalize_time(ability):
    for t, d in ((540.0, 3200.0), (900.0, 5000.0), (1714.0, 10000.0),
                 (1500.0, 8000.0), (4000.0, 10000.0), (300.0, 1600.0)):
        norm = t * nd.factorForTime(t, d, "college_m", sport="XC")
        assert t == pytest.approx(norm / nd.factorForNorm(norm, d, "ms_m", sport="XC"),
                                  rel=1e-12)


def test_the_pool_mode_is_untouched(monkeypatch):
    """Default OFF: with no ability artifact the factor is the cached pool
    factor, time-free, exactly as before."""
    monkeypatch.setattr(nd, "_ABILITY", None)
    assert not nd.abilityMode()
    f1 = nd.factorForTime(600.0, 5000.0, "hs_m", sport="XC")
    f2 = nd.factorForTime(1200.0, 5000.0, "hs_m", sport="XC")
    assert f1 == f2 == nd._normalizationFactorCached(5000.0, "hs_m", None, None,
                                                     None, "XC", None)


# a pool artifact whose three curves disagree the way the live ones do
_K = [math.log(800.0), math.log(20000.0)]
_POOLS_ART = {"kind": "distance_potential", "target": 5000.0,
              "pools": {"hs_m|XC": {"knots": _K, "values": [1.04 * k for k in _K]},
                        "college_m|XC": {"knots": _K, "values": [1.10 * k for k in _K]},
                        "ms_m|XC": {"knots": _K, "values": [1.07 * k for k in _K]}},
              "global": {"knots": _K, "values": [1.06 * k for k in _K]},
              "pool_targets": {"hs_m": 5000.0, "college_m": 8000.0, "ms_m": 3200.0}}
_MEANS = {"hs_m": 1000.0, "college_m": 900.0, "ms_m": 1150.0, "pro_m": 900.0}


def _freshPoolView():
    """pool_view as its source defines it. ! Other files in a whole-suite
    run REPLACE pool_view._poolConstant and _forward_factor for good
    (test_hs_factor_per_pool assigns them), so the module in sys.modules is
    not the one on disk by the time this runs; a private copy is."""
    import importlib.util
    import conversions                                           # noqa: F401
    spec = importlib.util.spec_from_file_location(
        "pool_view_fresh", os.path.join(_ROOT, "racecast", "pool_view.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hsEquivalents(monkeypatch):
    """own = 100 * pm(pool) / norm, then x pool_view's factor under
    XCP_ONE_SCALE=1: the HS view every page shows, for the report's run
    under each label."""
    import conversions as C
    PV = _freshPoolView()
    monkeypatch.setenv("XCP_ONE_SCALE", "1")
    monkeypatch.setattr(C, "engineScale",
                        lambda pool, sport=None: (_MEANS[pool.split("|")[0]], 0.0, 0.0))
    t, d = 28 * 60 + 34.0, 10000.0
    out = {}
    for pool in ("hs_m", "college_m", "ms_m"):
        norm = nd.normalizeTime(t, d, pool, sport="XC")
        own = 100.0 * _MEANS[pool] / norm
        out[pool] = own * PV.hsFactor(pool, "XC", d)
    return out


def test_one_scale_hs_equivalent_does_not_depend_on_the_label(monkeypatch, ability):
    hs = _hsEquivalents(monkeypatch)
    assert max(hs.values()) / min(hs.values()) - 1 < 1e-9, hs


def test_by_pool_the_label_moves_the_hs_equivalent(monkeypatch):
    """The control: the same arithmetic on disagreeing pool curves is the
    bug the switch removes (the report's 156.6 / 150.0 / 147.6)."""
    monkeypatch.setattr(nd, "_ABILITY", None)
    monkeypatch.setattr(nd, "_SPLINES", _POOLS_ART)
    nd._normalizationFactorCached.cache_clear()
    try:
        hs = _hsEquivalents(monkeypatch)
        assert max(hs.values()) / min(hs.values()) - 1 > 0.02, hs
    finally:
        nd._normalizationFactorCached.cache_clear()


def test_the_one_scale_factor_is_the_engine_means(monkeypatch, ability):
    import conversions as C
    PV = _freshPoolView()
    monkeypatch.setenv("XCP_ONE_SCALE", "1")
    monkeypatch.setattr(C, "engineScale",
                        lambda pool, sport=None: (_MEANS[pool.split("|")[0]], 0.0, 0.0))
    assert PV.hsFactor("college_m", "XC", 5000) == pytest.approx(1000.0 / 900.0)
    assert PV.hsFactor("ms_m", "TF", 1600) == pytest.approx(1000.0 / 1150.0)
    # read, never cached into the sidecar that serves the switch's OFF side
    assert not PV._CONST_CACHE


def test_one_scale_off_keeps_the_sampled_constants(monkeypatch):
    import conversions as C
    PV = _freshPoolView()
    monkeypatch.setenv("XCP_ONE_SCALE", "0")
    monkeypatch.setattr(C, "engineScale", lambda pool, sport=None: (1.0, 0.0, 0.0))
    assert PV._oneScale() is False
    monkeypatch.setattr(PV, "_poolConstant",
                        lambda pool, sport: {"hs_m": 1237.0, "college_m": 1100.0}.get(pool))
    monkeypatch.setattr(PV, "_forward_factor", lambda *a, **k: 1.0)
    assert PV.hsFactor("college_m", "XC", 5000) == pytest.approx(1237.0 / 1100.0)


# ------------------------------------------------------------------ #
# 4. CONVERSIONS AND THE BACKFILL GUARD
# ------------------------------------------------------------------ #

def test_the_conversion_round_trip_closes_by_ability(monkeypatch, ability):
    import conversions as C
    monkeypatch.setattr(C, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(C, "pool_mean", lambda pool, sport=None: _MEANS.get(pool.split("|")[0]))
    monkeypatch.setattr(C, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(C._DEFAULT_DIFFICULTY_CACHE, "XC", 0.0)
    for t, d in ((1714.0, 10000.0), (900.0, 5000.0), (3000.0, 8000.0)):
        norm = C._norm_from_time(t, d, "college_m", 0.0, sport="XC")
        back = C.normalized_to_time(norm, {"distance": d, "pool": "hs_m",
                                           "sport": "XC", "difficulty": 0.0})
        assert back == pytest.approx(t, abs=0.01)


def test_rating_to_5k(monkeypatch, ability):
    import conversions as C
    monkeypatch.setattr(C, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(C, "pool_mean", lambda pool, sport=None: _MEANS.get(pool.split("|")[0]))
    monkeypatch.setattr(C, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(C._DEFAULT_DIFFICULTY_CACHE, "XC", 0.05)
    monkeypatch.setitem(C._DEFAULT_DIFFICULTY_CACHE, "TF", 0.0)
    fk = C.fiveKForHsRating(150.0, "M")
    # rating r <=> 5K = mean x 100 / r, then the average venue put back
    assert fk["norm"] == pytest.approx(1000.0 * 100 / 150.0, abs=0.01)
    assert fk["xc"] == pytest.approx(fk["norm"] * 1.05, rel=1e-3)
    assert fk["track"] == pytest.approx(fk["norm"], rel=1e-3)
    assert C.fiveKForRating(0, "hs_m") is None
    assert C.fiveKForHsRating(150.0, "unknown") is None


def _guard():
    """backfill_normalize.distanceModeGuard, lifted out of the file: the
    module itself needs the real psycopg2, which other files in a
    whole-suite run replace with a stub. The function imports only
    normalize_distance."""
    import ast
    path = os.path.join(_ROOT, "backfill", "backfill_normalize.py")
    src = open(path, encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "distanceModeGuard":
            ns = {}
            exec(ast.get_source_segment(src, node), ns)            # noqa: S102
            return ns["distanceModeGuard"]
    raise AssertionError("distanceModeGuard is gone from backfill_normalize.py")


def test_the_backfill_refuses_a_partial_write_in_a_new_mode(monkeypatch, ability):
    guard = _guard()
    monkeypatch.setattr(nd, "appliedDistanceModes", lambda: {"XC": "pool", "TF": "pool"})
    assert guard(["XC"], apply=True, only_changed=True, limit=0)
    assert guard(["TF"], apply=True, only_changed=False, limit=500)
    assert guard(["XC"], apply=True, only_changed=False, limit=0) is None
    assert guard(["XC"], apply=False, only_changed=True, limit=0) is None
    # asked for ability with no artifact: refused before a row is read
    monkeypatch.setattr(nd, "_ABILITY", None)
    monkeypatch.setattr(nd, "DISTANCE_BY", "ability")
    assert "distance_ability.pkl" in guard(["XC"], True, False, 0)


def test_the_backfill_records_and_checks_the_mode():
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py"),
               encoding="utf-8").read()
    main = src[src.index("def main():"):]
    assert "distanceModeGuard(sports, args.apply, args.only_changed or args.new_only," in main
    assert "recordAppliedDistance(sport)" in main
    # recorded only after a FULL write, beside the weather record
    i = main.index("recordAppliedDistance(sport)")
    assert ("if args.apply and not args.limit and not args.only_changed and not args.new_only:"
            in main[i - 200:i])


def test_the_mode_follows_the_rows(monkeypatch, tmp_path):
    """No switch: the mode the last full backfill recorded. A switch wins.
    Two sports in two modes read as pool, loudly."""
    import json
    monkeypatch.setattr(nd, "_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("XCP_DISTANCE_BY", raising=False)
    assert nd._resolveDistanceMode() == ("pool", "default")
    for sp in ("XC", "TF"):
        (tmp_path / f"distance_applied_{sp}.json").write_text(
            json.dumps({"distance_by": "ability"}))
    assert nd._resolveDistanceMode() == ("ability", "recorded")
    monkeypatch.setenv("XCP_DISTANCE_BY", "pool")
    assert nd._resolveDistanceMode() == ("pool", "env")
    monkeypatch.delenv("XCP_DISTANCE_BY")
    (tmp_path / "distance_applied_TF.json").write_text(json.dumps({"distance_by": "pool"}))
    assert nd._resolveDistanceMode() == ("pool", "mixed")


def test_group_reference_is_the_harmonic_mean():
    import build_group_means as G
    seasons = ([{"pool": "college_m", "r": 100.0, "division": "D1"}] * 40
               + [{"pool": "college_m", "r": 80.0, "division": "D3"}] * 40)
    rows = {(k, p, g): (n, own, hs) for k, p, g, n, own, hs in
            G.groupRefs(seasons, {"college_m": 1.2})}
    n, own, hs = rows[("pool", "college_m", "college_m")]
    assert n == 80 and own == pytest.approx(2 / (1 / 100 + 1 / 80))
    assert hs == pytest.approx(own * 1.2)
    assert rows[("division", "college_m", "D1")][1] == pytest.approx(100.0)
    # a group under MIN_SEASONS is an anecdote and is left out
    assert not G.groupRefs(seasons[:10], {"college_m": 1.2})


# ------------------------------------------------------------------ #
# 5. THE PER-POOL RESIDUAL
# ------------------------------------------------------------------ #

def test_the_joint_fit_recovers_a_planted_residual():
    """College planted +0.03 of exponent, middle school -0.05, at equal
    ability. The joint fit finds both, never gives high school one, and
    lands closer than the number measured after the shared fit (whose curve
    had already bent toward them)."""
    arr = _corpus(college_delta=0.03, ms_delta=-0.05)
    j, why = F.fitJoint(arr, 2, 2, span=None)
    assert why is None
    got = j[DA.APPLIED_KEY]
    assert set(got) == {"college_m", "ms_m"}
    assert "hs_m" not in j["residual_tested"]
    after = F.poolResidual(arr, F.fitFamily(arr, 2, 2))
    for pool, true in (("college_m", 0.03), ("ms_m", -0.05)):
        # ! 20%: at the corpus's leg noise (2%) the ability index is read
        #   off noisy legs and the alternation settles a little short of
        #   the planted value (exact at 0.1% noise: the next test)
        assert got[pool] == pytest.approx(true, rel=0.2), (pool, got[pool])
        assert abs(got[pool] - true) < abs(after[pool][0] - true), (pool, got, after)
    assert F.checkFamily(j)[0]


def test_the_joint_fit_is_exact_without_noise():
    rng = np.random.default_rng(5)
    arr = F.pairArrays(
        _pairs(rng, "hs_m", 3000, 0.0, 0.08, [3000, 4000, 5000, 8000], noise=0.001)
        + _pairs(rng, "college_m", 3000, -0.12, 0.06, [5000, 6000, 8000, 10000],
                 pool_delta=0.03, noise=0.001)
        + _pairs(rng, "elem_m", 3000, 0.4, 0.08, [1000, 1600, 2000, 3000],
                 pool_delta=-0.04, noise=0.001))
    j, _ = F.fitJoint(arr, 2, 2, span=None)
    assert j[DA.APPLIED_KEY]["college_m"] == pytest.approx(0.03, abs=0.001)
    assert j[DA.APPLIED_KEY]["elem_m"] == pytest.approx(-0.04, abs=0.001)


def test_a_residual_without_significance_or_support_is_not_applied():
    """College planted nothing: tested, not significant, not applied. An
    elementary pool with too few pairs for a pool curve gets no column at
    all, whatever its own residual. Middle school's is applied.
    ! Seeded: a null residual is a draw, and at another seed college's comes
      out past 1.96 SE about as often as that bar says it should (the SE is
      conditional on the abilities, as the measured-after one always was)."""
    arr = _corpus(seed=4, ms_delta=-0.05,
                  extra=[("elem_m", F.MIN_FAMILY_PAIRS // 2, 0.45, 0.08,
                          [1000, 1600, 2000], -0.04)])
    j, _ = F.fitJoint(arr, 2, 2, span=None)
    applied, tested = j[DA.APPLIED_KEY], j["residual_tested"]
    assert "ms_m" in applied
    assert "elem_m" not in tested and "elem_m" not in applied
    assert not tested["college_m"]["significant"] and not tested["college_m"]["applied"]
    assert "college_m" not in applied
    # the evaluator gives an unapplied pool exactly nothing
    assert DA.delta(j, "college_m") == 0.0 and DA.delta(j, "elem_m|XC") == 0.0
    assert DA.delta(j, "ms_m|XC") == applied["ms_m"]


def test_no_high_school_pairs_no_joint_fit():
    arr = _corpus(ms_delta=-0.05)
    no_hs = F.subset(arr, ~F.hsMask(arr))
    j, why = F.fitJoint(no_hs, 2, 2, span=None)
    assert j is None and "high-school" in why


def test_the_artifact_says_which_residuals_are_applied():
    xc = _corpus(n=1500, ms_delta=-0.05)
    art = F.fitAll(xc, F.pairArrays([]), verbose=False, agreement=False,
                   degrees={k: (2, 2) for k in ("m|XC", "u|XC", "m|*", "u|*")},
                   degrees_joint={"m|XC": (2, 2)})
    j = art["families"]["m|XC"][DA.JOINT_KEY]
    assert art["residual_applied"] == {"m|XC": j[DA.APPLIED_KEY]}
    assert "ms_m" in j[DA.APPLIED_KEY] and j["check"]["ok"]
    # the gender-blind and sport-blind families pool what it separates
    for k in ("u|XC", "m|*", "u|*"):
        assert DA.JOINT_KEY not in art["families"][k]
    # the shared curve is still there, for XCP_DISTANCE_RESIDUAL=0
    assert DA.family(art, "ms_m", "XC") is art["families"]["m|XC"]
    assert DA.family(art, "ms_m", "XC", residual=True) is j


JOINT_M = dict(FAM_M, alpha=[1.05, 0.03],
               applied_residual={"college_m": -0.017, "ms_m": -0.058, "elem_m": -0.039})
FAM_MJ = dict(FAM_M, joint=JOINT_M)
ART_J = {"kind": DA.KIND, "target": 5000.0,
         "families": {"m|XC": FAM_MJ, "f|XC": FAM_F, "u|XC": FAM_MJ,
                      "m|*": FAM_MJ, "f|*": FAM_F, "u|*": FAM_MJ}}


def _clearCaches():
    for fn in (nd._normalizationFactorCached, nd._otherFactorCached, nd._abilityPQ):
        fn.cache_clear()


@pytest.fixture
def residual(monkeypatch):
    monkeypatch.setattr(nd, "_ABILITY", ART_J)
    monkeypatch.setattr(nd, "DISTANCE_RESIDUAL", True)
    _clearCaches()
    yield
    _clearCaches()


def _refTime(fam, d, a=0.0):
    """The raw time at d of the runner at ability a on the reference curve."""
    x = math.log(d / 5000.0)
    return math.exp(DA.inverseLog(fam, math.log(fam["ref_5k"]) + a, x))


def test_the_conversion_applies_the_pools_residual(residual):
    assert nd.residualMode()
    for d in (1600.0, 3000.0, 8000.0, 10000.0):
        x = math.log(d / 5000.0)
        for a in (-0.1, 0.0, 0.2):
            t = _refTime(JOINT_M, d, a)
            f_hs = nd.factorForTime(t, d, "hs_m", sport="XC")
            # ★ HIGH SCHOOL IS THE REFERENCE: the joint curve, no residual
            assert f_hs == pytest.approx(DA.factorForTime(JOINT_M, t, d), rel=1e-12)
            _p, q = DA._pq(JOINT_M, x)
            for pool, dl in JOINT_M["applied_residual"].items():
                f = nd.factorForTime(t, d, pool, sport="XC")
                # unclamped: log T5_p - log T5_hs = -delta x / (1 + Q(x))
                assert math.log(f / f_hs) == pytest.approx(-dl * x / (1 + q), abs=1e-12)
                norm = nd.normalizeTime(t, d, pool, sport="XC")
                assert norm == pytest.approx(t * f, abs=0.006)
    # a pool the fitter did not apply converts exactly as high school
    t = _refTime(JOINT_M, 10000.0)
    assert (nd.factorForTime(t, 10000.0, "pro_m", sport="XC")
            == nd.factorForTime(t, 10000.0, "hs_m", sport="XC"))
    # 5000 m is 5000 m under every label: g_p(0) = 0
    assert len({nd.normalizeTime(900.0, 5000.0, p, sport="XC")
                for p in ("hs_m", "college_m", "ms_m", "elem_m")}) == 1
    # the women's family has no joint fit: its shared curve, untouched
    assert (nd.factorForTime(2000.0, 6000.0, "college_f", sport="XC")
            == pytest.approx(DA.factorForTime(FAM_F, 2000.0, 6000.0), rel=1e-12))


def test_the_residual_inverts_exactly(residual):
    for pool in ("hs_m", "college_m", "ms_m", "elem_m", "pro_m"):
        for d in (800.0, 1600.0, 3000.0, 5000.0, 8000.0, 10000.0, 15000.0):
            x = math.log(d / 5000.0)
            last = None
            # the clamped tails included: far faster / slower than any pair
            for t5 in np.linspace(REF * 0.5, REF * 2.0, 40):
                lt = DA.inverseLog(JOINT_M, math.log(t5), x, pool)
                assert DA.forwardLog(JOINT_M, lt, x, pool) == pytest.approx(
                    math.log(t5), abs=1e-12)
                if last is not None:
                    assert lt > last, "a slower 5K must be a slower race"
                last = lt
                t = math.exp(lt)
                norm = t * nd.factorForTime(t, d, pool, sport="XC")
                assert t == pytest.approx(
                    norm / nd.factorForNorm(norm, d, pool, sport="XC"), rel=1e-12)


def test_the_conversion_round_trip_closes_with_the_residual(monkeypatch, residual):
    import conversions as C
    monkeypatch.setattr(C, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(C, "pool_mean", lambda pool, sport=None: _MEANS.get(pool.split("|")[0]))
    monkeypatch.setattr(C, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(C._DEFAULT_DIFFICULTY_CACHE, "XC", 0.0)
    for pool in ("college_m", "ms_m", "hs_m"):
        for t, d in ((1714.0, 10000.0), (900.0, 5000.0), (560.0, 3000.0)):
            norm = C._norm_from_time(t, d, pool, 0.0, sport="XC")
            back = C.normalized_to_time(norm, {"distance": d, "pool": pool,
                                               "sport": "XC", "difficulty": 0.0})
            # normalizeTime rounds to the hundredth: 0.005 s of a 5K
            # equivalent is 0.011 s back at 10 km
            assert back == pytest.approx(t, rel=1e-5)


def test_the_anchor_and_bands_do_not_move(residual):
    for p in ("hs_m", "college_m", "ms_m", "elem_m"):
        assert nd.targetFor(p, "XC") == 5000.0
        assert nd.anchorShift(p, "XC") == 1.0
        assert nd.poolBandFor(p) == (nd.PACE_FLOOR * 5000.0, nd.PACE_CEIL * 5000.0)
    # a move to another anchor is the pool's reference runner's: it differs
    # from high school's by exactly the residual
    x = math.log(8000.0 / 5000.0)
    assert (nd.anchorShift("ms_m", "XC", 8000.0) / nd.anchorShift("hs_m", "XC", 8000.0)
            == pytest.approx(math.exp(-0.058 * x), rel=1e-12))


def test_the_residual_off_is_the_shared_curve(monkeypatch, residual):
    monkeypatch.setattr(nd, "DISTANCE_RESIDUAL", False)
    _clearCaches()
    assert not nd.residualMode()
    t, d = 28 * 60 + 34.0, 10000.0
    norms = {p: nd.normalizeTime(t, d, p, sport="XC")
             for p in ("hs_m", "college_m", "ms_m", "elem_m")}
    assert len(set(norms.values())) == 1, norms
    assert norms["hs_m"] == pytest.approx(t * DA.factorForTime(FAM_M, t, d), abs=0.006)


def test_the_label_dependence_is_the_residuals_size(residual):
    """Section A of diag_one_scale in miniature: the report's run under each
    label. Middle school's -0.058 at 10 km is ~4%; college's -0.017 ~1%."""
    t, d = 28 * 60 + 34.0, 10000.0
    hs = nd.normalizeTime(t, d, "hs_m", sport="XC")
    _p, q = DA._pq(JOINT_M, math.log(2.0))
    for pool, dl in JOINT_M["applied_residual"].items():
        rel = hs / nd.normalizeTime(t, d, pool, sport="XC") - 1
        assert rel == pytest.approx(math.expm1(dl * math.log(2.0) / (1 + q)), abs=1e-5)
    assert 0.03 < abs(hs / nd.normalizeTime(t, d, "ms_m", sport="XC") - 1) < 0.05


def test_the_one_scale_factor_ignores_the_residual(monkeypatch, residual):
    """The F ratio converts anchors; by ability every anchor is 5000 m, so
    it is 1 -- the residual lives in the rows' normalized_time, not in a
    per-pool multiplier read at a representative 1600."""
    import conversions as C
    PV = _freshPoolView()
    monkeypatch.setenv("XCP_ONE_SCALE", "1")
    monkeypatch.setattr(C, "engineScale",
                        lambda pool, sport=None: (_MEANS[pool.split("|")[0]], 0.0, 0.0))
    assert PV.hsFactor("ms_m", "TF", 1600) == pytest.approx(1000.0 / 1150.0, rel=1e-12)
    assert PV.hsFactor("college_m", "XC", 5000) == pytest.approx(1000.0 / 900.0, rel=1e-12)
    try:
        import run_joint as rj
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"run_joint unavailable here: {exc}")
    got = rj.tiltScaleInputs(["hs_m", "college_m", "ms_m"], verbose=False)
    assert list(got["f_ratio"]) == [1.0, 1.0, 1.0]


def _lift(name, extra_ns=None):
    """One function lifted out of backfill_normalize.py (see _guard)."""
    import ast
    path = os.path.join(_ROOT, "backfill", "backfill_normalize.py")
    src = open(path, encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = dict(extra_ns or {})
            exec(ast.get_source_segment(src, node), ns)            # noqa: S102
            return ns[name]
    raise AssertionError(f"{name} is gone from backfill_normalize.py")


def test_the_record_carries_the_residual(monkeypatch, residual, tmp_path):
    import json
    (tmp_path / "engine" / "data").mkdir(parents=True)
    record = _lift("recordAppliedDistance",
                   {"_os": os, "__file__": str(tmp_path / "backfill" / "b.py")})
    applied = {"m|XC": {"ms_m": -0.058}, "m|TF": {"elem_m": -0.039}}
    monkeypatch.setattr(nd, "_ABILITY", dict(ART_J, residual_applied=applied,
                                             fitted="2026-09-29 12:00:00"))
    record("XC")
    path = tmp_path / "engine" / "data" / "distance_applied_XC.json"
    rec = json.loads(path.read_text())
    assert rec["distance_by"] == "ability" and rec["residual"] is True
    assert rec["residual_applied"] == {"m|XC": {"ms_m": -0.058}}
    # and the readers read it back as the rows' state
    monkeypatch.setattr(nd, "_DATA_DIR", str(tmp_path / "engine" / "data"))
    assert nd.appliedResiduals() == {"XC": True}
    monkeypatch.setattr(nd, "DISTANCE_RESIDUAL", False)
    record("XC")
    rec = json.loads(path.read_text())
    assert rec["residual"] is False and "residual_applied" not in rec


def test_the_residual_is_resolved_like_the_mode(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(nd, "_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("XCP_DISTANCE_RESIDUAL", raising=False)
    assert nd._resolveResidual("pool", "default") == (False, "n/a")
    # the writer asking for ability gets its residual by default
    assert nd._resolveResidual("ability", "env")[0] is True
    # a record from before the residual: the rows carry none
    for sp in ("XC", "TF"):
        (tmp_path / f"distance_applied_{sp}.json").write_text(
            json.dumps({"distance_by": "ability"}))
    assert nd._resolveResidual("ability", "recorded") == (False, "recorded")
    for sp in ("XC", "TF"):
        (tmp_path / f"distance_applied_{sp}.json").write_text(
            json.dumps({"distance_by": "ability", "residual": True}))
    assert nd._resolveResidual("ability", "recorded") == (True, "recorded")
    monkeypatch.setenv("XCP_DISTANCE_RESIDUAL", "0")
    assert nd._resolveResidual("ability", "recorded") == (False, "env")
    assert nd._resolveResidual("ability", "env") == (False, "env")
    monkeypatch.delenv("XCP_DISTANCE_RESIDUAL")
    (tmp_path / "distance_applied_TF.json").write_text(
        json.dumps({"distance_by": "ability", "residual": False}))
    assert nd._resolveResidual("ability", "recorded") == (False, "mixed")


def test_the_backfill_refuses_a_partial_write_across_the_residual(monkeypatch, residual):
    guard = _guard()
    monkeypatch.setattr(nd, "appliedDistanceModes", lambda: {"XC": "ability", "TF": "ability"})
    monkeypatch.setattr(nd, "appliedResiduals", lambda: {"XC": False, "TF": False})
    assert "residual" in guard(["XC"], apply=True, only_changed=True, limit=0)
    assert guard(["XC"], apply=True, only_changed=False, limit=0) is None
    monkeypatch.setattr(nd, "appliedResiduals", lambda: {"XC": True, "TF": True})
    assert guard(["XC"], apply=True, only_changed=True, limit=0) is None
    # the residual asked for over an artifact that has none: refused
    monkeypatch.setattr(nd, "_ABILITY", ART)
    monkeypatch.setattr(nd, "DISTANCE_BY", "ability")
    assert "XCP_DISTANCE_RESIDUAL=0" in guard(["XC"], True, False, 0)
    monkeypatch.setattr(nd, "DISTANCE_RESIDUAL", False)
    assert guard(["XC"], True, False, 0) is None
