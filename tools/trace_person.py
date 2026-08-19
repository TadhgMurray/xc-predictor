"""trace_person.py -- run the engine's own pipeline and print every array
value behind one athlete's ratings.

★ NO ARITHMETIC OF MY OWN. It calls linkage_check.prepare / solve / ratings /
  resultRatings -- the same functions --golive calls, in the same order -- and
  then indexes the resulting arrays at this person's rows. Every number
  printed is the number the engine used, not a reconstruction of it.

  Reconstructing was the mistake. rating = 100 * pool_mean * (1 + d) / norm is
  what the comments say, but resultRatings actually computes

      adjusted = norm / exp(eff)
      rating   = 100 * pm_c[group] / adjusted

  -- exp(eff), not (1 + d); eff scaled by the ability tilt h; pm_c from
  poolMeanPerGroup rather than from buildRatings; and the CAREER anchor, not
  the seasonal one that pair_athlete_season reports. Four places to be wrong
  about, which is four too many to guess at.

Usage:
    python tools/trace_person.py --person 29578044
    python tools/trace_person.py --person 29578044 --pack engine/data/packed_XC_TF.npz
"""
import sys, os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "engine"))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

import numpy as np


def parseArgs(argv):
    out = {"person": None,
           "pack": os.path.join("engine", "data", "packed_XC_TF.npz")}
    for i, a in enumerate(argv):
        if a == "--person" and i + 1 < len(argv):
            out["person"] = str(argv[i + 1])
        elif a == "--pack" and i + 1 < len(argv):
            out["pack"] = argv[i + 1]
    if out["person"] is None:
        sys.exit("need --person <id>")
    return out


def hdr(t):
    print("\n" + "=" * 74)
    print("  " + t)
    print("=" * 74)


def main():
    a = parseArgs(sys.argv[1:])
    pid = a["person"]

    import linkage_check as lc
    import pair_ratings as pr

    hdr(f"running the solve  ({a['pack']})")
    D = lc.prepare(a["pack"])
    lc.solve(D)
    lc.ratings(D, quiet=True)
    lc.resultRatings(D, anchor="career")

    # ---- which rows belong to this person -------------------------- #
    # athlete_keys[athlete_code] = (person_id, pool); the row's athlete code
    # is D["athlete"], and its athlete-SEASON code is D["group"].
    keys = D["cols"]["athlete_keys"]
    codes = [i for i, k in enumerate(keys) if str(k[0]) == pid]
    if not codes:
        print(f"\n  person {pid} is not in this pack")
        return
    hdr(f"athlete codes for person {pid}")
    for c in codes:
        print(f"    code {c}   key {keys[c]}")

    rows = np.isin(D["athlete"], codes)
    n = int(rows.sum())
    print(f"\n  {n} packed rows")
    if not n:
        return

    idx = np.nonzero(rows)[0]

    # ---- the per-row terms, straight out of the arrays -------------- #
    from pair_write_results import poolMeanPerGroup
    pm_c, pm_s = poolMeanPerGroup(D["rat"]["ability"], D["attrs"],
                                  D["rat"]["valid"],
                                  anchor=D["rat"].get("anchor"))

    eff = D["delta"][D["course"]]
    h_used = D.get("h") is not None
    if h_used:
        eff = D["h"] * eff
    beta_used = D.get("beta") is not None and D.get("sc") is not None
    if beta_used:
        eff = eff + D["beta"][D["group"]] * D["sc"]

    hdr("per-row terms  (exactly what resultRatings computes)")
    print(f"  ability tilt h applied: {h_used}    beta*sc applied: {beta_used}")
    print()
    print(f"  {'row':>8} {'course':>7} {'group':>8} {'norm':>9} "
          f"{'delta':>9} {'eff':>9} {'exp(eff)':>9} {'pm_c':>9} "
          f"{'pm_s':>9} {'career':>8} {'seasonal':>9} {'rated':>6}")
    for i in idx:
        c = int(D["course"][i])
        g = int(D["group"][i])
        print(f"  {i:>8} {c:>7} {g:>8} {D['norm'][i]:>9.2f} "
              f"{D['delta'][c]:>+9.4f} {eff[i]:>+9.4f} "
              f"{np.exp(eff[i]):>9.4f} {pm_c[g]:>9.1f} {pm_s[g]:>9.1f} "
              f"{D['r_career'][i]:>8.2f} {D['r_seasonal'][i]:>9.2f} "
              f"{bool(D['rated'][i])!s:>6}")

    # ---- where each number came from -------------------------------- #
    hdr("the two pool means, and why they differ")
    attrs = D["attrs"]
    for g in sorted({int(D["group"][i]) for i in idx}):
        pool_code = int(attrs["pool"][g])
        pool = attrs["pool_names"][pool_code] if pool_code >= 0 else "(none)"
        season = int(attrs["season"][g])
        print(f"    group {g}: pool {pool!r} season {season}  "
              f"ability {D['rat']['ability'][g]:.1f}  "
              f"valid {bool(D['rat']['valid'][g])}")
        print(f"      pm_c (career, by pool)          {pm_c[g]:>10.1f}")
        print(f"      pm_s (seasonal, by pool+season) {pm_s[g]:>10.1f}")
        print(f"      buildRatings career  rating     "
              f"{D['rat']['career'][g]:>10.2f}")
        print(f"      buildRatings seasonal rating    "
              f"{D['rat']['seasonal'][g]:>10.2f}")
        # ⚠ buildRatings and poolMeanPerGroup each compute a pool mean. If
        #   they disagree, the athlete table and the result table are on
        #   different scales -- which pair_write_results warns about by name.
        if D["rat"]["ability"][g] > 0 and D["rat"]["career"][g] > 0:
            implied = D["rat"]["ability"][g] * D["rat"]["career"][g] / 100.0
            print(f"      => buildRatings' career MEAN    {implied:>10.1f}")
            if abs(implied - pm_c[g]) > 1.0:
                print(f"      ⚠ DISAGREES WITH pm_c BY "
                      f"{pm_c[g] - implied:+.1f} -- the athlete table and the "
                      f"result table are on different scales")

    # ---- the course cell, named ------------------------------------- #
    hdr("the course cells these rows landed in")
    cell_names = D["cols"].get("course_keys") or D["cols"].get("cell_keys")
    for i in idx:
        c = int(D["course"][i])
        nm = cell_names[c] if cell_names is not None and c < len(cell_names) \
            else "(no name array in pack)"
        print(f"    row {i}: course {c}  delta {D['delta'][c]:+.4f}  "
              f"solved {bool(D['solved'][c])}  {nm}")


if __name__ == "__main__":
    main()