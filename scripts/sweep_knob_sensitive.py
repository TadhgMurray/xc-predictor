# Project: xc-predictor
# File:    scripts/sweep_knob_sensitivity.py
# Purpose: Answer "is the shipped distance fit a property of the DATA or
#          of the KNOBS?" — the last doubt on the distance saga. Refits
#          everything under one-at-a-time perturbations of the five
#          admission constants and measures how far each pool's curve
#          moves from the shipped baseline.
#
#          PRE-REGISTERED VERDICT (decided before running):
#            every pool's max local-exp shift < 0.02  -> STABLE: the
#              curves are data, not knob artifacts; the accuracy claim
#              stands and the saga closes.
#            any pool > 0.05 (or a gate FLIP on a major pool) -> that
#              value is knife-edge; the legitimate trigger for the IRLS
#              robust-solve methodology change.
#
#          Read-only with respect to artifacts: fits are in-memory,
#          nothing is saved, the shipped pickle is untouched.
#
# Usage:   python scripts/sweep_knob_sensitivity.py   (repo root;
#          expect ~5-15 min: ten full fits over the cached pairs)

import io
import sys
import math
import contextlib

import numpy as np

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
import fit_distance_exponent as fde

# ------------------------------------------------------------------ #
# CONSTANTS — the variants and the pre-registered thresholds
# ------------------------------------------------------------------ #

# One knob per variant, each moved to the nearest value someone could
# reasonably have chosen instead. Names are the report's column labels.
VARIANTS = [
    # the retired gate's knobs are gone (2026-07-06, IRLS); in their
    # place, the ROBUST solve's own constants are swept — the change's
    # promise is that NONE of these is load-bearing
    ("tukey_3.5",       {"TUKEY_C": 3.5}),
    ("tukey_6.0",       {"TUKEY_C": 6.0}),
    ("irls_iters_3",    {"IRLS_MAX_ITERS": 3}),
    ("prior_0.01",      {"PRIOR_PAIR_SIGMA": 0.01}),
    ("prior_0.04",      {"PRIOR_PAIR_SIGMA": 0.04}),
    ("minpairs_5",      {"MIN_PAIRS_PER_EDGE": 5}),
    ("ceiling_15000",   {"MAX_DISTANCE_METERS": 15_000.0}),
]

PROBE_POINTS = 9          # log-spaced probe distances per pool
SHIFT_OK = 0.02           # pre-registered: below this everywhere = STABLE
SHIFT_ALARM = 0.05        # pre-registered: above this anywhere = knife-edge

# ------------------------------------------------------------------ #
# KNOB INJECTION (set module globals, ALWAYS restore)
# ------------------------------------------------------------------ #

# _setKnobs / _restoreKnobs
# Purpose:   Inject a variant's overrides into the fitter's module
#            globals and hand back the originals for the finally-block.
#            Every fitter function reads these constants AT CALL TIME,
#            so setattr on the module is a complete injection — the
#            smoke test's toy-artifact pattern, applied to constants.
# Arguments: overrides — {constant_name: value}.
# Output:    {constant_name: original_value} (feed to _restoreKnobs).
def _setKnobs(overrides):
    originals = {name: getattr(fde, name) for name in overrides}
    for name, value in overrides.items():
        setattr(fde, name, value)
    return originals


def _restoreKnobs(originals):
    for name, value in originals.items():
        setattr(fde, name, value)

# ------------------------------------------------------------------ #
# ONE FIT (quiet), ONE COMPARISON
# ------------------------------------------------------------------ #

# _fitQuiet
# Purpose:   Run the REAL fitAllPotentials — never a re-implementation,
#            so the sweep can't drift from the fitter — with its report
#            captured instead of printed (ten variants of full output
#            would bury the verdict). The ceiling is applied here too,
#            per variant, because it is itself a swept knob.
# Arguments: xc_raw, tf_raw — the UN-ceilinged cached pair lists.
# Output:    the artifact dict (curves live in "pools" / "global*").
def _fitQuiet(xc_raw, tf_raw):
    xc = fde._dropOverMaxPairs(xc_raw, "XC")
    tf = fde._dropOverMaxPairs(tf_raw, "TF")
    with contextlib.redirect_stdout(io.StringIO()):
        return fde.fitAllPotentials(fde._groupByPool(xc),
                                    fde._groupByPool(tf))


# _allCurves
# Purpose:   Flatten an artifact into one {label: entry} dict so pools,
#            the per-sport globals, and the combined global are compared
#            by the same code — the fallbacks matter as much as the
#            pools (gated pools LAND on them).
# Arguments: art — fitAllPotentials' output.
# Output:    {label: entry}.
def _allCurves(art):
    curves = dict(art["pools"])
    for sport, entry in art["global_by_sport"].items():
        curves[f"global|{sport}"] = entry
    curves["global"] = art["global"]
    return curves


# _probeGrid
# Purpose:   The distances a comparison is allowed to probe: log-spaced
#            INSIDE the baseline entry's own support span. Outside it,
#            both fits run the linear-extension POLICY, and comparing
#            policies measures a choice, not the data.
# Arguments: entry — the BASELINE curve (its span rules).
# Output:    list of probe distances in meters.
def _probeGrid(entry):
    lo, hi = entry["span"]
    return list(np.exp(np.linspace(math.log(lo), math.log(hi),
                                   PROBE_POINTS)))


# _maxShift
# Purpose:   The comparison itself: worst |local exponent difference|
#            between a variant's curve and the baseline's, over the
#            baseline probe grid — the same units as the health gate,
#            the census, and the residual instrument speak.
# Arguments: base, var — two fitted entries for the same label.
# Output:    the max shift as float.
def _maxShift(base, var):
    return max(abs(fde._localExponent(var, d) - fde._localExponent(base, d))
               for d in _probeGrid(base))

# ------------------------------------------------------------------ #
# THE SWEEP
# ------------------------------------------------------------------ #

# _compareVariant
# Purpose:   One variant against the baseline: per-curve shifts, gate
#            flips (a curve present on one side only), DEMOTIONS (the
#            ladder's third state — same label, different degree: the
#            variant world's own gate fired and the pool re-certified
#            at less freedom), and the eps drift check (eps is
#            decoupled from the shape solve BY DESIGN, so knob-
#            sensitive eps would falsify that wall — a finding in its
#            own right). Cross-degree shifts are still reported but a
#            demotion tag warns the reader: that number conflates the
#            knob's pull with the model swap, and the tag says which.
# Arguments: base_curves, var_curves — {label: entry} for each.
# Output:    (shifts {label: float}, flips [str], demotions [str],
#             eps_drift float).
def _compareVariant(base_curves, var_curves):
    shifts, flips, demotions, eps_drift = {}, [], [], 0.0
    for label, base in base_curves.items():
        var = var_curves.get(label)
        if var is None:
            flips.append(f"{label} saved->GATED")
            continue
        if var["degree"] != base["degree"]:
            demotions.append(f"{label} deg {base['degree']}->"
                             f"{var['degree']}")
        shifts[label] = _maxShift(base, var)
        eps_drift = max(eps_drift, abs(var["eps"] - base["eps"]))
    for label in var_curves:
        if label not in base_curves:
            flips.append(f"{label} gated->SAVED")
    return shifts, flips, demotions, eps_drift


# _reportVariant
# Purpose:   One verdict line per variant, with a detail line only when
#            something clears the pre-registered attention threshold —
#            the report should be readable top to bottom in one screen.
#            Demotions print like flips (they ARE gate events, softened
#            by the ladder), and a hot shift on a demoted label carries
#            a "(demoted)" tag so the reader knows the number measures
#            a model swap, not a same-model knob pull.
# Arguments: name — variant label; shifts, flips, demotions, eps_drift
#            — from _compareVariant.
# Output:    the variant's worst shift (for the final verdict).
def _reportVariant(name, shifts, flips, demotions, eps_drift):
    worst_label = max(shifts, key=shifts.get) if shifts else "-"
    worst = shifts.get(worst_label, 0.0)
    flip_note = f"  FLIPS: {', '.join(flips)}" if flips else ""
    demo_note = (f"  DEMOTES: {', '.join(demotions)}"
                 if demotions else "")
    print(f"  {name:>16}: max shift {worst:.4f} ({worst_label}), "
          f"eps drift {eps_drift:.4f}{flip_note}{demo_note}")
    demoted = {d.split(" deg ")[0] for d in demotions}
    hot = {k: v for k, v in shifts.items() if v >= SHIFT_OK}
    if hot:
        detail = ", ".join(
            f"{k} {v:.3f}" + (" (demoted)" if k in demoted else "")
            for k, v in sorted(hot.items(), key=lambda kv: -kv[1]))
        print(f"                    over {SHIFT_OK}: {detail}")
    return worst


def main():
    print("=== sweep_knob_sensitivity.py ===\n")
    cached = fde._loadCache()
    if cached is None:
        print("No pair cache — run fit_distance_exponent.py first.")
        sys.exit(1)
    xc_raw, tf_raw = cached

    print("\nBaseline fit (shipped knobs)...")
    base_curves = _allCurves(_fitQuiet(xc_raw, tf_raw))
    print(f"  {len(base_curves)} curves fitted\n")

    worst_overall, any_flip = 0.0, False
    for name, overrides in VARIANTS:
        originals = _setKnobs(overrides)
        try:                                   # knobs ALWAYS restored,
            var_curves = _allCurves(_fitQuiet(xc_raw, tf_raw))   # even on
        finally:                               # a crash mid-variant
            _restoreKnobs(originals)
        shifts, flips, demotions, eps_drift = _compareVariant(base_curves,
                                                              var_curves)
        worst = _reportVariant(name, shifts, flips, demotions, eps_drift)
        worst_overall = max(worst_overall, worst)
        any_flip = any_flip or bool(flips)

    print("\n" + "=" * 60)
    if worst_overall < SHIFT_OK and not any_flip:
        print(f"VERDICT: STABLE — worst shift {worst_overall:.4f} < "
              f"{SHIFT_OK}, no gate flips. The curves are data, not "
              f"knob artifacts.")
    elif worst_overall >= SHIFT_ALARM:
        print(f"VERDICT: KNIFE-EDGE — worst shift {worst_overall:.4f} >= "
              f"{SHIFT_ALARM}. A saved curve is knob-contingent — per "
              f"the standing rule it belongs on its sport global. The "
              f"fitter's STABILITY GATE should have caught this; if it "
              f"is live and this still fired, investigate the gap "
              f"(probes vs variants?) before trusting either.")
    else:
        print(f"VERDICT: MIXED — worst shift {worst_overall:.4f}"
              f"{' with gate flips' if any_flip else ''}. Read the "
              f"detail lines; judgment call on the flagged pools.")


if __name__ == "__main__":
    main()