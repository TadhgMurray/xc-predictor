# Project: xc-predictor
# File:    scripts/check_paces.py
# Purpose: Self-check for racecast/paces.py. Needs no database: the forward
#          conversion is passed in, so the pace logic is testable on its own.
#
# ★ THE DRIFT TEST IS THE POINT OF THIS FILE. paces.FIT_MAX_XC mirrors
#   fit_distance_exponent.POOL_MAX_DISTANCE_XC by hand, because importing the
#   fitter pulls in 1.45M lines of corrections.py. A mirror that silently
#   stops matching is worse than no mirror -- it would let the page derive a
#   pace from a part of the curve the fitter had already disowned. This reads
#   the fitter's SOURCE, not its module, and fails on any divergence.
import ast
import os
import re
import sys

sys.path.insert(0, "racecast")
sys.path.insert(0, "engine")
import paces as P                                        # noqa: E402

BAD = 0


def check(label, got, want):
    global BAD
    ok = got == want
    BAD += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
          + ("" if ok else f"  (want {want!r})"))


# A stand-in for conversions.normalized_to_time: the pure power law, which is
# what the spline reduces to. Good enough to exercise every branch here.
def fakeToTime(norm, ctx, k=1.06):
    d = ctx["distance"]
    return norm * (d / 5000.0) ** k


def main():
    print("THE MIRROR OF THE FITTER'S DOMAIN CAP")
    src = open(os.path.join("engine", "fit_distance_exponent.py"),
               encoding="utf-8").read()
    m = re.search(r"POOL_MAX_DISTANCE_XC\s*=\s*(\{.*?\n\})", src, re.S)
    if not m:
        check("the fitter's cap table is still findable", bool(m), True)
    else:
        # ast.literal_eval, not eval: this is source read off disk.
        theirs = ast.literal_eval(re.sub(r"#.*", "", m.group(1)))
        for pool, cap in theirs.items():
            check(f"{pool} matches the fitter", P.FIT_MAX_XC.get(pool), cap)

    print("\nWHAT THE CURVE MAY AND MAY NOT BE ASKED")
    # 16:00 for 5000m -> normalized_time 960 under the stand-in law.
    paces = P.trainingPaces(960.0, "hs_m", "XC", to_time=fakeToTime)
    by = {p["key"]: p for p in paces}
    check("interval comes from our own curve",
          by["interval"]["basis"], "your distance curve")
    # ⚠ TEMPO IS NO LONGER DERIVED. It used to be 20-minute race pace, which
    #   for a 4:10 miler is 4:34/mile -- a correct 7K race pace and a wildly
    #   wrong tempo. A tempo run sits near threshold, and an hour of racing is
    #   past the fit.
    check("tempo does NOT", by["tempo"]["basis"], "coaching convention")
    check("nor does easy", by["easy"]["basis"], "coaching convention")
    check("and the derived ones say which race they are equivalent to",
          by["interval"]["equivalent_race_m"] < P.FIT_MAX_XC["hs_m"], True)

    print("\nTHE PACES THEMSELVES, FOR A 16:00 5K")
    for p in paces:
        eq = (f"  (= a {p['equivalent_race_m']:,}m race)"
              if p.get("equivalent_race_m") else "")
        print(f"       {p['label']:<18}{str(p['per_mile']):>12}/mi"
              f"{str(p['per_km']):>12}/km   {p['basis']}{eq}")

    print("\n  AND THEY HAVE TO BE IN THE RIGHT ORDER")
    def secs(k, end=0):
        """seconds/mile for a pace; convention paces are ranges, end=1 picks
        the slow end."""
        part = by[k]["per_mile"].split("-")[end if "-" in by[k]["per_mile"]
                                           else 0]
        mm, ss = part.split(":")
        return int(mm) * 60 + int(ss)
    check("interval is faster than tempo", secs("interval") < secs("tempo"),
          True)
    check("tempo is faster than steady", secs("tempo") < secs("steady"), True)
    check("steady is faster than easy", secs("steady") < secs("easy"), True)
    check("and easy is not absurd (6:30-9:00 for this runner)",
          390 < secs("easy") < 540, True)

    print("\n  THE COACHING RULE IT IS CALIBRATED TO")
    # tempo = mile race pace + 60-80 s/mi, checked against the actual rule
    # rather than against my own restatement of it.
    for t1600, label in ((250.0, "4:10"), (310.0, "5:10")):
        norm = t1600 * (5000.0 / 1600.0) ** 1.06
        pc = {p["key"]: p for p in
              P.trainingPaces(norm, "hs_m", "XC", to_time=fakeToTime)}
        mile_t = fakeToTime(norm, {"distance": P._MILE_ANCHOR_M})
        lo, hi = pc["tempo"]["per_mile"].split("-")
        def sec(x):
            mm, ss = x.split(":")
            return int(mm) * 60 + int(ss)
        check(f"a {label} 1600 gets tempo at mile +60 to +80",
              (round(sec(lo) - mile_t), round(sec(hi) - mile_t)), (60, 80))
        print(f"       {label} 1600 -> mile {int(mile_t)//60}:"
              f"{int(mile_t)%60:02d}, tempo {pc['tempo']['per_mile']}/mi")

    print("\n  NOTHING REAL IS EXTRAPOLATED...")
    # ⚠ A SLOWER ATHLETE'S 20-MINUTE RACE IS SHORTER, NOT LONGER -- they cover
    #   less ground in the same time. So the durations chosen sit inside the
    #   fit for everybody, and the honest test of that is to sweep the range
    #   rather than to contrive one athlete who trips the guard.
    worst = 0
    for pool in ("hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f"):
        for norm in (700.0, 900.0, 1200.0, 1800.0, 2400.0):
            for pc in P.trainingPaces(norm, pool, "XC", to_time=fakeToTime):
                if pc.get("equivalent_race_m"):
                    worst = max(worst,
                                pc["equivalent_race_m"] / P.FIT_MAX_XC[pool])
    check("every derived pace across every pool stays inside the fit",
          worst < 1.0, True)
    print(f"       closest any of them came to the cap: {worst:.0%} of it")

    print("\n  ...AND THE GUARD STILL FIRES IF A DURATION IS RAISED")
    # tighten the cap rather than invent an athlete: this tests the branch,
    # not a story about who might trip it.
    real = P.FIT_MAX_XC["hs_m"]
    P.FIT_MAX_XC["hs_m"] = 2000
    tight = {p["key"]: p for p in
             P.trainingPaces(960.0, "hs_m", "XC", to_time=fakeToTime)}
    P.FIT_MAX_XC["hs_m"] = real
    # ! INTERVAL, NOT TEMPO. Tempo stopped being derived when it stopped
    #   pretending to be a 20-minute race; interval is the only pace left
    #   that consults the curve, so it is the only one the guard can protect.
    check("interval is refused rather than extrapolated",
          tight["interval"]["basis"], "out of range")
    check("and says how far past the fit it would have gone",
          "past the 2,000m" in tight["interval"]["note"], True)
    check("with no pace attached to a refusal",
          tight["interval"]["per_mile"], None)
    check("while the convention paces still resolve",
          tight["easy"]["per_mile"] is not None, True)

    print("\nVO2 MAX")
    v = P.vdot(960.0, 5000.0, "hs_m")
    check("a 16:00 5K lands in the published VDOT range",
          58 < v["value"] < 68, True)
    print(f"       16:00 5K -> {v['value']}")
    print(f"       {v['note']}")
    check("a 4:10 mile is higher than a 16:00 5K",
          P.vdot(250.0, 1609.34, "hs_m")["value"] > v["value"], True)
    check("it refuses middle school",
          P.vdot(600.0, 3200.0, "ms_m")["value"], None)
    check("with a reason that says why",
          "trained adults" in P.vdot(600.0, 3200.0, "ms_m")["note"], True)
    check("it refuses a sprint", P.vdot(50.0, 400.0, "hs_m")["value"], None)
    check("and a missing time", P.vdot(None, 5000.0, "hs_m")["value"], None)

    print("\n  all cases pass" if not BAD else f"\n  {BAD} FAILURES")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
