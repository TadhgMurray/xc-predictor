# Project: xc-predictor
# File:    scripts/check_paces.py
# Purpose: Self-check for racecast/paces.py. No database.
#
# ★ THE TESTS THAT MATTER HERE ARE THE ONES THE MODEL COULD FAIL. The previous
#   version of this file asserted that a tempo pace equalled a constant the
#   module had just been told to use -- a test that cannot fail. These check
#   properties nothing in the code arranges: that CS lands below 10K pace,
#   that D' falls in the published band, that two athletes with the same 1600
#   get different answers, and that impossible inputs are refused.
import sys

sys.path.insert(0, "racecast")
import paces as P                                        # noqa: E402

BAD = 0
MILE = 1609.344


def check(label, got, want):
    global BAD
    ok = got == want
    BAD += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
          + ("" if ok else f"  (want {want!r})"))


def s(x):
    m, sec = x.split(":")
    return int(m) * 60 + float(sec)


def secs(txt):
    txt = txt.split("-")[0]
    m, ss = txt.split(":")
    return int(m) * 60 + int(ss)


def main():
    print("THE ALGEBRA IS EXACT, NOT FITTED")
    r = [(1600.0, (1600 - 200) / 5.0), (5000.0, (5000 - 200) / 5.0)]
    got, why = P.criticalSpeed(r)
    check("two races recover CS exactly", round(got[0], 6), 5.0)
    check("and D'", round(got[1], 6), 200.0)

    print("\nWHAT IT REFUSES, AND WHY")
    check("one race", P.criticalSpeed([(5000.0, 900.0)])[0], None)
    check("two races too close in distance",
          "1.6x is needed" in P.criticalSpeed(
              [(3000.0, 540.0), (3200.0, 580.0)])[1], True)
    # 4:35 1600 with 9:12 3200: no runner has both.
    bad = P.criticalSpeed([(1600.0, s("4:35")), (3200.0, s("9:12"))])
    check("a pair no athlete could own", bad[0], None)
    check("and it says what is wrong with them",
          "anaerobic reserve" in bad[1], True)
    print(f"       {bad[1]}")

    print("\nPROPERTIES NOTHING IN THE CODE ARRANGES")
    # ⚠ CS MUST COME OUT BELOW 10K PACE. The literature puts CS between 5K and
    #   10K pace; no line in paces.py enforces it, so this can fail.
    for t1, t2 in (("4:10", "8:58"), ("4:30", "9:30"), ("5:00", "10:45")):
        races = [(1600.0, s(t1)), (3200.0, s(t2))]
        (cs, dp), _ = P.criticalSpeed(races)
        cs_pace = MILE / cs
        t10 = P._timeFor(cs, dp, 10000.0)
        p10 = t10 / (10000.0 / MILE)
        check(f"{t1}/{t2}: CS is slower than its own 10K pace",
              cs_pace > p10, True)
        check(f"{t1}/{t2}: by less than 15 s/mile, so it is NEAR 10K pace",
              cs_pace - p10 < 15.0, True)
        check(f"{t1}/{t2}: D' inside the published 150-450m",
              150 <= dp <= 450, True)

    print("\n  AND IT SEPARATES ATHLETES A ONE-RACE RULE CANNOT")
    same = {}
    for t2 in ("8:45", "8:58", "9:20"):
        (cs, _), _ = P.criticalSpeed([(1600.0, s("4:10")), (3200.0, s(t2))])
        same[t2] = MILE / cs
        print(f"       4:10 / {t2}  ->  CS {int(same[t2])//60}:"
              f"{int(same[t2])%60:02d}/mi")
    spread = max(same.values()) - min(same.values())
    check("three athletes with the same 1600 differ by 30+ s/mile",
          spread > 30, True)
    coach = P.coachRuleTempo(s("4:10") * (MILE / 1600))
    check("while the one-race rule gives them one answer",
          coach["per_mile"], "5:11-5:31")

    print("\nTHE PACE LADDER, FOR A 4:10 / 8:58 ATHLETE")
    paces = P.trainingPaces([(1600.0, s("4:10")), (3200.0, s("8:58"))])
    for p in paces:
        print(f"       {p['label']:<16}{p['per_mile']:>12}/mi"
              f"{p['per_km']:>12}/km   {p['source']}")
    by = {p["key"]: p for p in paces}
    # ! NO SEPARATE "tempo" ROW. CS x 1.08 and 10K + 15-20 s/mi are two
    #   estimates of the same zone that differ by ~14 s/mile; they are one row
    #   spanning both rather than two rows in an order neither source implies.
    order = ["interval", "critical_speed", "threshold", "steady", "easy"]
    check("every pace resolved", sorted(by), sorted(order))
    times = [secs(by[k]["per_mile"]) for k in order]
    check("and they are monotonically slower", times, sorted(times))
    check("the threshold row shows how far the two sources disagree",
          by["threshold"]["spread_s_per_mile"] > 5, True)
    check("critical speed is labelled as itself, not as threshold",
          by["critical_speed"]["source"], "derived")
    check("threshold names its one imported number",
          "1.08" in by["threshold"]["basis"], True)
    check("and the unbacked ones say so",
          [by[k]["source"] for k in ("steady", "easy")],
          ["unbacked", "unbacked"])

    print("\nVO2 MAX")
    v = P.vdot(960.0, 5000.0, "hs_m")
    check("a 16:00 5K is in the published VDOT range", 58 < v["value"] < 68,
          True)
    print(f"       16:00 5K -> {v['value']}")
    check("it refuses middle school", P.vdot(600.0, 3200.0, "ms_m")["value"],
          None)
    check("it refuses a sprint", P.vdot(50.0, 400.0, "hs_m")["value"], None)

    print("\n  all cases pass" if not BAD else f"\n  {BAD} FAILURES")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
