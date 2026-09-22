# Project: xc-predictor
# File:    engine/diag_exponent_by_ability.py
# Purpose: Does the distance-fade exponent depend on ability? READ ONLY.
#
#     python engine/diag_exponent_by_ability.py
#     python engine/diag_exponent_by_ability.py --pool hs_m --deciles 10
#     python engine/diag_exponent_by_ability.py --fresh      # re-stream pairs
#
# Run from the PROJECT ROOT. Uses fit_distance_exponent's own pair loader
# (and its cache), so this judges exactly the pairs the spline was fitted on.
#
# ★ THE QUESTION. fit_distance_exponent fits ONE fade curve per pool|sport
#   and normalize_distance applies it to every row. If the true exponent
#   varies with ability -- mid-pack kids fading at ~1.2 while elites hold
#   ~1.05, which is what world-record ratios and known elite doubles imply --
#   then one curve is an average that is wrong at both ends, and it is wrong
#   in the direction that matters: strong athletes' short races normalize to
#   inflated 5K-equivalents and their ratings deflate. TF (raced at
#   800-3200, far from the anchor) eats almost all of it; XC (at the anchor)
#   eats almost none. That asymmetry is a sport gap that is NOT the sport's.
#
# ★ THE MEASUREMENT. Per pair, the implied exponent is
#       k = log(t2/t1) / log(d2/d1)
#   and the athlete's ability proxy is their better leg expressed as a
#   provisional 5K-equivalent (t * (5000/d)^K0). K0 only RANKS athletes --
#   deciles are coarse and the ranking is monotone in K0 over any plausible
#   value -- so the proxy is not circular with the thing being measured.
#   Pairs are bucketed into ability deciles within each pool|sport and the
#   decile's median k is reported with its IQR.
#
# ★ WHAT A POSITIVE RESULT LOOKS LIKE. Median k sliding monotonically from
#   ~1.2 in the slow deciles to ~1.05 in the fast ones. That confirms the
#   follow-on design: fit the potential per (pool|sport, pace band) and have
#   normalizeTime pick the band from the row's own pace -- observable at
#   call time from (time, distance) alone, no ratings involved. A flat
#   table means the single curve is fine and the conversion complaint is
#   about something else.
#
# ════════════════════════════════════════════════════════════════════════
# ★★ MEASURED 2026-09-22, college_m. THE ANSWER IS NO FOR TRACK.
#
#       college_m|TF   310,999 pairs
#         fastest decile (~14:17)  median k 1.128
#         slowest decile (~19:37)  median k 1.139      spread +0.010
#
#       college_m|XC   207,981 pairs
#         fastest decile (~14:34)  median k 0.998
#         slowest decile (~22:20)  median k 1.049      spread +0.051
#
#    TF is flat -- and flat is the verdict this header wrote in advance.
#    The single curve per pool is the right shape for the track; there is
#    no pace-band fit to build, and the "strong athletes' ratings deflate"
#    mechanism above does not operate. That is a design NOT to build,
#    which is worth as much as one to build.
#
# ⚠ AND IT CAUGHT A CONFOUND WORTH REMEMBERING. The same question asked
#   with scripts/diag_event_pairs.py -- bucket athletes by season best at
#   event A, take the median at B -- looked like a strong positive: k ran
#   1.201 at a 1:46 800 down to 1.125 at 2:12, a spread of 0.076, seven
#   times what the deciles show. That is REGRESSION TO THE MEAN, not
#   physiology. Bucketing on A puts specialists and lucky noise at A's
#   tails, and their B regresses toward the population; "fast at A implies
#   relatively worse at B" falls out of the conditioning whatever the true
#   exponent does. The tell was that BOTH pairs showed it in opposite
#   directions (fast 800s fading more, fast 10ks fading less), which is
#   selection-on-A's signature and not a law's.
#
#   This script avoids it by ranking on an ability proxy rather than on
#   either leg -- which is what the header above says it is for, and now
#   the reason is on the record.
#
#    XC's +0.051 is the larger number but it is not the ability effect
#    either: its fastest decile sits at 0.998, BELOW 1.0, which is the
#    known college_m|XC pathology (a 10k is the November championship at
#    peak fitness against an October 8k -- see fit_distance_exponent's
#    MIN_LOCAL_EXP note). The spread is that confound varying with
#    ability, not a fade that does.
# ════════════════════════════════════════════════════════════════════════
#
# ! DIAGNOSTIC-ONLY RATIO FLOOR. The fitter keeps near-zero ratios because
#   its aggregation handles them; a per-pair k at ratio ~1 divides by ~0 and
#   is pure noise, so this report requires |log(d2/d1)| >= log(MIN_RATIO).

import argparse
import math
import sys

PROVISIONAL_K = 1.10      # ability proxy only; see the docstring
MIN_RATIO = 1.2           # per-pair k needs a real distance gap
MIN_PAIRS_TO_PRINT = 1000 # a decile table over less is reading tea leaves


def impliedK(pair):
    """log(t2/t1) / log(d2/d1), or None when a leg is unusable."""
    d1, d2 = pair["distance1"], pair["distance2"]
    t1, t2 = pair["time1"], pair["time2"]
    if not all((d1, d2, t1, t2)) or min(d1, d2, t1, t2) <= 0:
        return None
    ld = math.log(d2 / d1)
    if abs(ld) < math.log(MIN_RATIO):
        return None
    return math.log(t2 / t1) / ld


def abilityProxy(pair, k0=PROVISIONAL_K):
    """The athlete's BETTER leg as a provisional 5K-equivalent (seconds).

    The faster of the two, because a sandbagged or JV leg says less about
    the athlete than their real race does -- and the proxy only ranks."""
    legs = []
    for d, t in ((pair["distance1"], pair["time1"]),
                 (pair["distance2"], pair["time2"])):
        if d and t and d > 0 and t > 0:
            legs.append(t * (5000.0 / d) ** k0)
    return min(legs) if legs else None


def _percentile(sorted_vals, q):
    """Simple percentile on a pre-sorted list (linear interpolation)."""
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def decileTable(pairs, n_deciles=10, k0=PROVISIONAL_K):
    """[(decile, n, proxy_time, k_p25, k_median, k_p75)] fast decile first.

    Decile 1 = the FASTEST athletes (best provisional 5K-equivalents),
    because they are who the boards rank and who the question is about."""
    rows = []
    for p in pairs:
        k = impliedK(p)
        a = abilityProxy(p, k0)
        if k is not None and a is not None:
            rows.append((a, k))
    if len(rows) < n_deciles:
        return []
    rows.sort()                                    # by proxy: fast first
    out = []
    per = len(rows) / n_deciles
    for i in range(n_deciles):
        chunk = rows[int(i * per):int((i + 1) * per)]
        if not chunk:
            continue
        ks = sorted(k for _a, k in chunk)
        mid_proxy = chunk[len(chunk) // 2][0]
        out.append((i + 1, len(chunk), mid_proxy,
                    _percentile(ks, 0.25), _percentile(ks, 0.50),
                    _percentile(ks, 0.75)))
    return out


def _fmtTime(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def printPool(label, pairs, n_deciles, k0):
    table = decileTable(pairs, n_deciles, k0)
    used = sum(n for _d, n, *_ in table)
    if used < MIN_PAIRS_TO_PRINT:
        print(f"\n  {label}: {used:,} usable pairs -- under the "
              f"{MIN_PAIRS_TO_PRINT:,} floor, skipped")
        return None
    print(f"\n  {label}  ({used:,} pairs with ratio >= {MIN_RATIO})")
    print(f"    {'decile':>7}{'n':>9}{'~5K equiv':>11}"
          f"{'k p25':>8}{'k med':>8}{'k p75':>8}")
    for d, n, proxy, p25, med, p75 in table:
        tag = "  <- fastest" if d == 1 else ("  <- slowest"
                                             if d == n_deciles else "")
        print(f"    {d:>7}{n:>9,}{_fmtTime(proxy):>11}"
              f"{p25:>8.3f}{med:>8.3f}{p75:>8.3f}{tag}")
    fast = table[0][4]
    slow = table[-1][4]
    print(f"    fastest-decile median k {fast:.3f} vs slowest {slow:.3f}"
          f"  (spread {slow - fast:+.3f})")
    return fast, slow


def main():
    ap = argparse.ArgumentParser(
        description="Implied distance exponent by ability decile. Read only.")
    ap.add_argument("--deciles", type=int, default=10)
    ap.add_argument("--pool", help="limit to one pool (e.g. hs_m)")
    ap.add_argument("--provisional-k", type=float, default=PROVISIONAL_K)
    ap.add_argument("--fresh", action="store_true",
                    help="re-stream pairs instead of using the cache")
    args = ap.parse_args()

    # Lazy, so the pure functions above stay importable without a DB.
    sys.path.insert(0, "scripts")
    sys.path.insert(0, "engine")
    from fit_distance_exponent import loadAllPairs
    xc_by_pool, tf_by_pool = loadAllPairs(use_cache=not args.fresh)

    spreads = []
    for sport, by_pool in (("XC", xc_by_pool), ("TF", tf_by_pool)):
        for pool in sorted(by_pool):
            if args.pool and pool != args.pool:
                continue
            got = printPool(f"{pool}|{sport}", by_pool[pool],
                            args.deciles, args.provisional_k)
            if got:
                spreads.append((f"{pool}|{sport}", *got))

    if spreads:
        print("\n  VERDICT GUIDE: a consistent positive spread (slow decile "
              "fading harder\n  than the fast one) confirms the ability-banded "
              "fit; ~0.00 spreads mean\n  the single per-pool curve is fine "
              "and the fade is not ability-dependent.")
        worst = max(spreads, key=lambda s: s[2] - s[1])
        print(f"  Largest spread: {worst[0]} "
              f"({worst[1]:.3f} fast -> {worst[2]:.3f} slow)")


if __name__ == "__main__":
    main()
