# Project: xc-predictor
# File:    scripts/test_eps_synthetic.py
# Purpose: Validate the eps-debiased distance fitter (v2: two-stage —
#          eps from matched contrasts, shape with eps fixed) on PLANTED
#          truth carrying the failure's MECHANISM (lesson: a test world
#          needs the mechanism, not just the shape). Six checks:
#            1. RECOVERY   — census-structured world (one-sided long
#                            transitions, 70/30 mid): eps AND curve both
#                            come back; the pooled eps debiases the
#                            one-sided edges too (rung 1 works).
#            2. LEGACY BIAS— same world, eps pinned 0: exponents biased
#                            LOW at the one-sided long end (the bug is
#                            reproduced, so the fix is fixing something).
#            3. DEGENERACY — 100% short-first world: no matched
#                            contrasts, the guard refuses rung 1
#                            (eps_source == "none"), never a confident
#                            wrong eps.
#            4. RESCUE     — same one-sided world, eps borrowed via
#                            eps_fixed: truth recovered (rung 2 works).
#            5. ANOMALY    — one matched transition planted with a
#                            WRONG-SIGNED contrast at full weight (the
#                            college_m 8000->10000 shape): the weighted
#                            MEDIAN must shrug it off where a mean (and
#                            v1's joint solve) would drag.
#            6. DECOUPLING — a label-noise transition the cubic cannot
#                            represent (the 4700->5000 class): eps must
#                            be untouched — v2's structural claim that
#                            shape misfit cannot reach eps.
#            7. INSTRUMENT — the residual report must SEE what check 6
#                            planted: the label-noise transition surfaces
#                            as the top-tension row with a negative
#                            exponent residual, while genuine transitions
#                            stay near zero. The measuring stick is
#                            proven on a world where the answer is known.
#            8. CEILING    — a unit-corrupt monster pair (5000 ->
#                            8,046,720m) dies at the cache filter.
#            9. AUTHORITY  — a tiny junk cell whose points agree by luck
#                            is dropped at n=2 (Fix C) and demoted to
#                            prior-floor weight at n=4 (Fix B), never
#                            again crowned with a giant cell's authority.
#           10. ROBUST SOLVE — the college_f mechanism: corrupt in-range
#                            long-span cells (exp ~0 over a 60% jump) are
#                            SILENCED by the IRLS reweight (weight -> 0,
#                            audited in the entry), and the curve stays
#                            on the honest evidence — no enumeration of
#                            the corruption class required.
#           11. NO KNIFE-EDGE — the methodology change's whole promise,
#                            measured: perturbing TUKEY_C (3.5 / 6.0)
#                            moves the fitted curve by < 0.005 in local
#                            exponent — the cutoff is data-scaled, so no
#                            hand constant is load-bearing.
#           12. STABILITY GATE FIRES — the college_f anatomy, with its
#                            real MECHANISM: fuzz cells inflate the
#                            robust scale, putting a corrupt tail
#                            cluster in the ~4-sigma band where the
#                            probe constants disagree about admission.
#                            Base fit healthy, but knob-contingent ->
#                            the gate must drop it.
#           13. STABILITY GATE HOLDS — check 10's fat honest world
#                            (corruption unambiguously silenced at every
#                            probe) must pass the gate untouched: no
#                            false positives on solid pools.
# Usage:   python scripts/test_eps_synthetic.py   (from repo root; in the
#          dev container, stub modules stand in for database/normalize_*)

import sys
import math
import random

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
import fit_distance_exponent as fde

# ------------------------------------------------------------------ #
# THE PLANTED WORLD
# ------------------------------------------------------------------ #

TRUE_A1, TRUE_A2 = 1.10, 0.05        # g(x) = 1.10x + 0.05x^2 — a gently
                                     # curving law: local exponent
                                     # 1.10 + 0.10x (x = log d - log 5K),
                                     # ~1.05 at 3K, 1.10 at 5K, 1.17 at 10K
TRUE_EPS = 0.012                     # earlier race 1.2% soft — the census's
                                     # HS/college-XC scale
NOISE_SD = 0.02                      # per-pair log-time noise (~2%)
DISTANCES = (3000, 3200, 4000, 4800, 5000, 6000, 8000, 10000)


# _gTrue / _expTrue
# Purpose:   The planted law and its local exponent, for assertions.
# Arguments: ld — log distance; d — distance in meters.
def _gTrue(ld):
    x = ld - math.log(5000.0)
    return TRUE_A1 * x + TRUE_A2 * x * x


def _expTrue(d):
    return TRUE_A1 + 2 * TRUE_A2 * (math.log(d) - math.log(5000.0))


# _makeWorld
# Purpose:   n synthetic pairs in _makePair's exact shape: pick a
#            transition, compute the fair delta from the planted law,
#            corrupt it with eps ON THE EARLIER RACE plus noise, choose
#            the time order per-transition, and store the pair
#            time-ordered (distance1 = earlier) — the same contract the
#            real loader and cache obey.
#            THE MECHANISM (first draft's lesson, re-learned live): a
#            UNIFORM imbalance is NOT the failure mode — with both sides
#            fat everywhere, the per-direction cells all hit SE_FLOOR,
#            weights equalize, and +/-eps cancels in the lstsq even at
#            75/25. The bias that hurt the real fit lives at ONE-SIDED
#            transitions (census: college 8000->10000 had 365 sf / 0 lf
#            — nobody races the 10K first), so the world plants exactly
#            that: long transitions 100% short-first, mid ones 70/30.
# Arguments: n — pair count; p_sf — P(short-first) as a FUNCTION of
#            (d_lo, d_hi); seed.
# Output:    list of pair dicts.
def _makeWorld(n, p_sf, seed=0):
    rng = random.Random(seed)
    pairs = []
    while len(pairs) < n:
        d_lo, d_hi = sorted(rng.sample(DISTANCES, 2))
        if math.log(d_hi / d_lo) < fde.MIN_EDGE_SPAN_LOG:
            continue                          # the fitter drops it anyway
        fair = _gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo))
        short_first = rng.random() < p_sf(d_lo, d_hi)
        # canonical delta = log(T_at_hi / T_at_lo); the earlier race's
        # time is inflated by eps, so sf deflates it, lf inflates it
        y = fair + (-TRUE_EPS if short_first else TRUE_EPS) \
            + rng.gauss(0.0, NOISE_SD)
        if short_first:                       # earlier = the short race
            pairs.append({"pool": "syn", "distance1": d_lo,
                          "distance2": d_hi, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(y)})
        else:                                 # earlier = the long race
            pairs.append({"pool": "syn", "distance1": d_hi,
                          "distance2": d_lo, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(-y)})
    return pairs


# _collegeLike / _allShortFirst
# Purpose:   The two imbalance profiles the checks use: the census's
#            structure (one-sided long transitions, 70/30 mid), and the
#            fully degenerate case for the guard.
def _collegeLike(d_lo, d_hi):
    return 1.0 if d_hi >= 8000 else 0.70


def _allShortFirst(_d_lo, _d_hi):
    return 1.0


# _knifeEdgeWorld
# Purpose:   Reproduce the college_f|XC instability with its MECHANISM
#            (a shape-only copy would silence cleanly, as tuning
#            proved): (1) a tight honest core whose coverage ENDS at
#            6000m; (2) SCALE INFLATORS — many mildly-off cells playing
#            the fuzz's statistical role of keeping the robust sigma
#            large; (3) a corrupt tail cluster placed ~4.1 of those
#            sigmas out — the ambiguity band where c=3.5 silences it,
#            the shipped 4.685 barely admits it, and c=6.0 admits it at
#            triple weight, so the fitted tail is a function of c.
# Arguments: seed; sigma_mult — corrupt offset in inflated-scale units;
#            n_corr_trans — corrupt transition count (leverage).
# Output:    pair list (fitter-shaped).
def _knifeEdgeWorld(seed, sigma_mult, n_corr_trans):
    rng = random.Random(seed)
    pairs = []
    honest = (3000, 3200, 4000, 4800, 5000, 6000)
    while len(pairs) < 20_000:                       # the tight core
        d_lo, d_hi = sorted(rng.sample(honest, 2))
        if math.log(d_hi / d_lo) < fde.MIN_EDGE_SPAN_LOG:
            continue
        fair = _gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo))
        sf = rng.random() < 0.7
        y = fair + (-TRUE_EPS if sf else TRUE_EPS)             + rng.gauss(0.0, NOISE_SD)
        _appendDirected(pairs, d_lo, d_hi, y, sf)
    for k in range(20):                              # scale inflators
        d_lo = 3050.0 + 70 * k
        d_hi = d_lo * 1.09
        off = 0.010 * (1 if k % 2 else -1)
        fair = _gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo))
        for i in range(300):
            sf = (i % 2 == 0)
            y = fair + off + (-TRUE_EPS if sf else TRUE_EPS)                 + rng.gauss(0.0, NOISE_SD)
            _appendDirected(pairs, d_lo, d_hi, y, sf)
    scale = 1.4826 * 0.010                           # the inflated sigma
    for k in range(n_corr_trans):                    # ambiguity-band tail
        d_lo = 6200.0 + 90 * k
        d_hi = d_lo * 1.32
        fair = _gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo))
        for i in range(300):
            sf = (i % 2 == 0)
            y = fair - sigma_mult * scale                 + (-TRUE_EPS if sf else TRUE_EPS) + rng.gauss(0.0, NOISE_SD)
            _appendDirected(pairs, d_lo, d_hi, y, sf)
    return pairs


# _appendDirected
# Purpose:   One pair in _makePair's stored shape from its canonical
#            delta and time order — the three world builders above all
#            shared this eight-line tail; now it lives once.
# Arguments: pairs — extended in place; d_lo, d_hi — distances;
#            y — canonical delta log(T_hi/T_lo); sf — short race first.
def _appendDirected(pairs, d_lo, d_hi, y, sf):
    if sf:
        pairs.append({"pool": "syn", "distance1": d_lo, "distance2": d_hi,
                      "time1": 1000.0, "time2": 1000.0 * math.exp(y)})
    else:
        pairs.append({"pool": "syn", "distance1": d_hi, "distance2": d_lo,
                      "time1": 1000.0, "time2": 1000.0 * math.exp(-y)})


# _appendAnomaly
# Purpose:   Plant the college_m 8000->10000 shape: ONE matched
#            transition (2800->3000 — deliberately OFF the base world's
#            distance grid so its cells are pure) whose contrast tells a
#            WRONG-SIGNED eps story (earlier race FASTER by |eps|), at
#            full cell weight. Under a weighted mean this voice drags
#            the pool's eps toward it; the median must hold.
# Arguments: pairs — the base world (extended in place); n — pairs to
#            add (split evenly between directions); seed.
def _appendAnomaly(pairs, n, seed):
    rng = random.Random(seed)
    d_lo, d_hi = 2800.0, 3000.0
    fair = _gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo))
    for i in range(n):
        short_first = (i % 2 == 0)              # balanced: it MUST match
        # REVERSED sign: + for sf, - for lf (the anomaly's whole point)
        y = fair + (TRUE_EPS if short_first else -TRUE_EPS) \
            + rng.gauss(0.0, NOISE_SD)
        if short_first:
            pairs.append({"pool": "syn", "distance1": d_lo,
                          "distance2": d_hi, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(y)})
        else:
            pairs.append({"pool": "syn", "distance1": d_hi,
                          "distance2": d_lo, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(-y)})


# _appendLabelNoise
# Purpose:   Plant the 4700->5000 class: a transition (4500->5000, off
#            the base grid) whose TRUE delta is only 30% of the law's —
#            nominal labels differing while reality barely does. No
#            cubic through the real transitions can also fit this point,
#            so it manufactures exactly the shape misfit that poisoned
#            v1's joint eps. Its pairs carry the NORMAL eps and balanced
#            directions, so its own contrast is honest — the check is
#            that the pool eps stays at truth despite the misfit.
# Arguments: pairs — extended in place; n; seed.
def _appendLabelNoise(pairs, n, seed):
    rng = random.Random(seed)
    d_lo, d_hi = 4500.0, 5000.0
    fair = 0.3 * (_gTrue(math.log(d_hi)) - _gTrue(math.log(d_lo)))
    for i in range(n):
        short_first = (i % 2 == 0)
        y = fair + (-TRUE_EPS if short_first else TRUE_EPS) \
            + rng.gauss(0.0, NOISE_SD)
        if short_first:
            pairs.append({"pool": "syn", "distance1": d_lo,
                          "distance2": d_hi, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(y)})
        else:
            pairs.append({"pool": "syn", "distance1": d_hi,
                          "distance2": d_lo, "time1": 1000.0,
                          "time2": 1000.0 * math.exp(-y)})

# ------------------------------------------------------------------ #
# THE CHECKS
# ------------------------------------------------------------------ #

# _maxExpError
# Purpose:   Worst local-exponent miss vs the planted law across the
#            in-support distances — the single number each check judges.
# Arguments: entry — a fitted curve.
def _maxExpError(entry):
    return max(abs(fde._localExponent(entry, d) - _expTrue(d))
               for d in (3200, 4000, 5000, 6000, 8000))


def _check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main():
    print("=== test_eps_synthetic.py ===\n")
    ok = True
    rngGate = random.Random(11)           # check 10's noise source

    # 1. RECOVERY — the census's structure (one-sided long transitions,
    #    70/30 mid), joint solve: eps identified from the two-sided mid
    #    transitions must debias the ONE-SIDED long edges too — the
    #    whole argument for one eps per pool.
    world = _makeWorld(120_000, p_sf=_collegeLike, seed=1)
    fit = fde._fitOnePotential(world)
    ok &= _check("recovery: eps_source", fit["eps_source"] == "own",
                 f"got {fit['eps_source']!r}")
    ok &= _check("recovery: eps value",
                 abs(fit["eps"] - TRUE_EPS) < 0.002,
                 f"eps {fit['eps']:+.4f} vs planted {TRUE_EPS:+.4f}")
    ok &= _check("recovery: curve", _maxExpError(fit) < 0.02,
                 f"max local-exp error {_maxExpError(fit):.4f}")

    # 2. LEGACY BIAS — same world, eps pinned to 0 (rung 3 = shipped
    #    behavior). The bias lives at the one-sided LONG end: measure
    #    the 10K local exponent, which the census-scale eps depresses
    #    by ~0.03 there. (A uniform-imbalance world does NOT show this
    #    — both directions' cells floor to equal weight and cancel;
    #    the mechanism is one-sidedness, not imbalance per se.)
    legacy = fde._fitOnePotential(world, eps_fixed=0.0)
    bias = (fde._localExponent(fit, 10000)
            - fde._localExponent(legacy, 10000))
    ok &= _check("legacy bias reproduced", bias > 0.015,
                 f"debiased minus eps=0 exponent at 10K = {bias:+.4f} "
                 f"(eps=0 fits the one-sided long end LOW, as the "
                 f"census predicted)")

    # 3. DEGENERACY — 100% short-first: rung 1 must REFUSE
    one_sided = _makeWorld(50_000, p_sf=_allShortFirst, seed=2)
    deg = fde._fitOnePotential(one_sided)
    ok &= _check("degeneracy guard", deg["eps_source"] == "none",
                 f"eps_source {deg['eps_source']!r}, eps {deg['eps']:+.4f}")

    # 4. RESCUE — same one-sided world, the level's eps borrowed
    rescued = fde._fitOnePotential(one_sided, eps_fixed=TRUE_EPS)
    ok &= _check("rung-2 rescue", _maxExpError(rescued) < 0.02,
                 f"max local-exp error {_maxExpError(rescued):.4f} "
                 f"(vs undebiased {_maxExpError(deg):.4f})")

    # 5. ANOMALY — one wrong-signed matched contrast at full weight;
    #    the median must hold near truth. The weighted MEAN over the
    #    same contrasts is printed alongside to show what it (and v1's
    #    joint solve) would have conceded to the anomaly.
    anom_world = _makeWorld(120_000, p_sf=_collegeLike, seed=4)
    _appendAnomaly(anom_world, 30_000, seed=5)
    anom = fde._fitOnePotential(anom_world)
    cons = fde._matchedContrasts(fde._aggregatePairEdges(anom_world))
    mean_eps = (sum(c[0] * c[1] for c in cons)
                / sum(c[1] for c in cons))
    ok &= _check("anomaly: median holds",
                 abs(anom["eps"] - TRUE_EPS) < 0.002,
                 f"median eps {anom['eps']:+.4f} vs planted "
                 f"{TRUE_EPS:+.4f} (weighted MEAN would say "
                 f"{mean_eps:+.4f})")

    # 6. DECOUPLING — a label-noise transition the cubic cannot fit;
    #    eps must be untouched because stage 1 never sees the shape.
    ln_world = _makeWorld(120_000, p_sf=_collegeLike, seed=6)
    _appendLabelNoise(ln_world, 60_000, seed=7)
    ln = fde._fitOnePotential(ln_world)
    ok &= _check("decoupling: misfit cannot reach eps",
                 abs(ln["eps"] - TRUE_EPS) < 0.002,
                 f"eps {ln['eps']:+.4f} vs planted {TRUE_EPS:+.4f} "
                 f"despite an unfittable transition in the cloud")

    # 7. INSTRUMENT — the residual report must SEE the plant: the
    #    label-noise transition (true exp 0.3x the law's) surfaces as
    #    the TOP-TENSION row with a strongly negative residual, and the
    #    genuine transitions' weighted-median residual stays near zero.
    #    (Proving the measuring stick on known truth BEFORE reading it
    #    on real data — the instrument's own smoke test.)
    rows = fde._residualRows(
        ln, fde._matchedMidpoints(fde._aggregatePairEdges(ln_world)))
    top = max(rows, key=lambda r: r["tension"])
    is_plant = abs(top["d1"] - 4500) < 100 and abs(top["d2"] - 5000) < 100
    ok &= _check("instrument: plant surfaces as top tension",
                 is_plant and top["res_exp"] < -0.2,
                 f"top row {top['d1']:.0f}->{top['d2']:.0f}m, "
                 f"res exp {top['res_exp']:+.3f}")
    genuine = [r for r in rows
               if not (abs(r["d1"] - 4500) < 100
                       and abs(r["d2"] - 5000) < 100)]
    gmed = fde._weightedPercentile([r["res_exp"] for r in genuine],
                                   [r["w"] for r in genuine], 50)
    ok &= _check("instrument: genuine transitions read ~0",
                 abs(gmed) < 0.05,
                 f"genuine wmedian residual {gmed:+.3f}")

    # 8. CEILING — the cache-side ceiling kills the monster class
    sane = _makeWorld(200, p_sf=_collegeLike, seed=8)
    monster = {"pool": "syn", "distance1": 5000.0,
               "distance2": 8_046_720.0, "time1": 1000.0, "time2": 1010.0}
    kept = fde._dropOverMaxPairs(sane + [monster], "TEST")
    ok &= _check("ceiling: monster pair dropped",
                 len(kept) == len(sane) and monster not in kept,
                 f"{len(sane) + 1} in, {len(kept)} out")

    # 9. AUTHORITY — junk cells: n=2 cannot testify; n=4 gets at most
    #    the prior-floor weight (1 / (1.253*0.02/sqrt(4)) ~ 79.8), never
    #    the SE_FLOOR maximum (~333) the old code granted lucky agreement
    def _junkCell(n):
        # n pairs, one odd transition, deltas agreeing to ~1e-4 (the
        # lucky-agreement mechanism that used to floor the SE)
        return [{"pool": "syn", "distance1": 2898.0, "distance2": 3209.0,
                 "time1": 1000.0, "time2": 1000.0 * math.exp(0.03 + i * 1e-4)}
                for i in range(n)]
    ok &= _check("authority: n=2 cell dropped",
                 fde._aggregatePairEdges(_junkCell(2)) == [],
                 "no edge emitted below MIN_PAIRS_PER_EDGE")
    e4 = fde._aggregatePairEdges(_junkCell(4))
    w4 = e4[0][3] if e4 else float("nan")
    ok &= _check("authority: n=4 cell demoted",
                 e4 and abs(w4 - 79.8) < 2.0,
                 f"weight {w4:.1f} (old code: ~333)")

    # 10. EVIDENCE GATE — plant corrupt long-span cells (the 5001->8046m
    #     class: delta ~ 0 over a ~48% distance jump) in a normal world;
    #     the gate must refuse them, the audit must count them, and the
    #     curve must hold to the planted truth despite them.
    gate_world = _makeWorld(120_000, p_sf=_collegeLike, seed=10)
    for i in range(600):                  # two directional cells' worth
        sfb = (i % 2 == 0)
        y = 0.0 + rngGate.gauss(0.0, NOISE_SD)   # times EQUAL across 5K->8K
        if sfb:
            gate_world.append({"pool": "syn", "distance1": 5001.0,
                               "distance2": 8046.0, "time1": 1000.0,
                               "time2": 1000.0 * math.exp(y)})
        else:
            gate_world.append({"pool": "syn", "distance1": 8046.0,
                               "distance2": 5001.0, "time1": 1000.0,
                               "time2": 1000.0 * math.exp(-y)})
    gated = fde._fitOnePotential(gate_world)
    ok &= _check("robust solve: corrupt cells silenced",
                 gated["n_robust_down"] >= 2,
                 f"{gated['n_robust_down']} cells under w="
                 f"{fde.ROBUST_AUDIT_W} (planted 2)")
    ok &= _check("robust solve: curve holds truth",
                 _maxExpError(gated) < 0.02,
                 f"max local-exp error {_maxExpError(gated):.4f} "
                 f"despite the poison")

    # 11. NO KNIFE-EDGE — same poisoned world refit at perturbed Tukey
    #     constants; the curve must barely move (the data-scaled cutoff
    #     means no hand constant is load-bearing — measured, not argued)
    shifts = []
    for c in (3.5, 6.0):
        orig = fde.TUKEY_C
        fde.TUKEY_C = c
        try:
            refit = fde._fitOnePotential(gate_world)
        finally:
            fde.TUKEY_C = orig
        shifts.append(max(abs(fde._localExponent(refit, d)
                              - fde._localExponent(gated, d))
                          for d in (3200, 4000, 5000, 6000, 8000)))
    ok &= _check("no knife-edge: Tukey-c insensitive",
                 max(shifts) < 0.005,
                 f"max curve shift {max(shifts):.5f} across c=3.5/6.0")

    # 12. STABILITY GATE FIRES — construction pinned by seed (knife-
    #     edges are knife-edges: trans=25 in tuning read 0.0005 where
    #     trans=40 read 0.037 — asserts transcribe MEASURED invariants,
    #     so the measured configuration is frozen verbatim).
    knife = _knifeEdgeWorld(seed=31, sigma_mult=4.1, n_corr_trans=40)
    kf = fde._fitOnePotential(knife)
    ok &= _check("stability gate: fragile base is healthy",
                 fde._isHealthy(kf),
                 f"base {fde._healthNote(kf)}")
    ok &= _check("stability gate: fragile pool falls",
                 fde._applyStabilityGate("knife|SYN", knife, kf) is None,
                 "knob-contingent curve dropped to its sport global")

    # 13. STABILITY GATE HOLDS — no false positives on a solid pool
    ok &= _check("stability gate: solid pool holds",
                 fde._applyStabilityGate("solid|SYN", gate_world, gated)
                 is not None,
                 "unambiguous corruption does not destabilize")

    print("\nALL PASS" if ok else "\nFAILURES — do not ship")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()