"""joint_golive.buildLive on the synthetic two-sport year: the ratings it
would write have the properties a race page relies on. No database.

    python tests/test_joint_golive.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.dirname(__file__))
import joint_solve as js                                        # noqa: E402
import joint_golive as jg                                       # noqa: E402
import test_joint_year as T                                     # noqa: E402


def synthetic_pack(seed=11):
    y, D, truth, raw = T.world(indoor=True, seed=seed)
    n = y.size
    pool_names = ["hs_m", "hs_f"]
    athlete_keys = [(1000 + i, pool_names[p])
                    for i, p in enumerate(truth["pool_of_ath"])]
    cols = {"athlete": raw["ath"], "year": np.full(n, 2025),
            "norm": np.exp(y), "sport": raw["sport_of_cell"][raw["cel"]],
            "result_id": np.arange(n) + 500_000,
            "athlete_keys": athlete_keys,
            "course_keys": [("XC:" if s == 0 else "TF:") + f"v{i}:d5000"
                            for i, s in enumerate(raw["sport_of_cell"])]}
    keep = np.ones(n, dtype=bool)
    out = js.solveJoint(y, design=D, athlete_pool=truth["pool_of_ath"],
                        n_outer=6, tilt=True, n_probe=4)
    return out, D, cols, keep, truth, raw


def main():
    out, D, cols, keep, truth, raw = synthetic_pack()
    live = jg.buildLive(out, D, cols, keep)
    jg.report(live)
    s = live["summary"]

    # coverage: every row whose cell was solved gets a rating
    assert s["n_rated"] == s["n_rows"], s
    assert s["n_cells"] == D.n_cell and len(live["diffs"]) == D.n_cell
    print("  every row rated, every cell priced .................... OK")

    # inside one race, faster time = higher rating, up to the tilt: the
    # cell's effect is h(rating) * delta, and apply_tilt has published
    # exactly that per-row tilt since it shipped, so two finishers whose
    # ratings straddle a difficult course can swap. It must be rare, and
    # it must be ONLY the tilt: with h forced to 1 the order is exact.
    chosen, norm = live["chosen"], cols["norm"]
    pool_row = truth["pool_of_ath"][raw["ath"]]

    def inversions(rating):
        viol = pairs = 0
        for r in np.unique(raw["rac"])[:150]:
            m = raw["rac"] == r
            for p in (0, 1):
                mm = m & (pool_row == p)
                if mm.sum() < 2:
                    continue
                o = np.argsort(norm[mm])
                viol += int((np.diff(rating[mm][o]) > 1e-9).sum())
                pairs += int(mm.sum()) - 1
        return viol, pairs

    viol, pairs = inversions(chosen)
    flat = dict(out); flat["h"] = np.ones_like(out["h"])
    viol0, _ = inversions(jg.buildLive(flat, D, cols, keep)["chosen"])
    assert viol0 == 0, viol0
    assert viol / max(pairs, 1) < 0.02, (viol, pairs)
    print(f"  finishing order: exact without the tilt, {viol}/{pairs} "
          f"adjacent swaps with it ... OK")


    # a season rating and its per-race ratings agree: the median per-race
    # rating of an athlete-season sits near its season rating
    rat = live["rat"]
    med = np.array([np.median(live["r_career"][raw["ath"] == i])
                    for i in range(D.n_ath)])
    ok = rat["valid"]
    gap = med[ok] - rat["career"][ok]
    assert np.abs(np.median(gap)) < 1.0 and np.percentile(np.abs(gap), 90) < 4.0, (
        np.median(gap), np.percentile(np.abs(gap), 90))
    print(f"  per-race vs season rating: median gap {np.median(gap):+.2f}, "
          f"p90 |gap| {np.percentile(np.abs(gap), 90):.2f} pts ... OK")

    # the race-day effect is in the rating by default and out on request,
    # and the difference is exactly the effect
    live0 = jg.buildLive(out, D, cols, keep, use_race_effect=False)
    ratio = live["chosen"] / live0["chosen"]
    # tilted like the course (156) and clipped at the cap (187)
    u = np.clip(out["race_effect"][raw["rac"]], -js.RACE_DAY_CAP, js.RACE_DAY_CAP)
    expect = np.exp(out["h"] * u)
    assert np.allclose(ratio, expect, rtol=1e-9)
    print("  race-day effect enters the rating multiplicatively ...... OK")

    # the form curve and the rust are NOT in the rating: two rows of one
    # athlete on the same race with the same time rate identically whatever
    # their day-of-year says (they share cell, race and pool)
    #   -- implied by the effect having no curve term; check the term list
    src = open(os.path.join(os.path.dirname(__file__), "..", "engine",
                            "joint_golive.py")).read()
    body = src[src.index("def buildLive"):src.index("def report")]
    assert "curve" not in body.split("eff = ")[1].split("\n")[0]
    assert "rust" not in body and '"r"]' not in body
    print("  curve and rust stay out of the rating ................. OK")

    # the tilt is inside: an elite's effect is h * delta with h < 1
    elite = out["rating"][raw["ath"]] > 120
    if elite.any():
        assert (out["h"][elite] < 1.0).all()
    print("  tilt applied at the athlete's own rating ............... OK")

    # the difficulty file has the shape the diagnostics read
    for k in ("difficulty", "difficulty_raw", "solved", "degree", "weight",
              "cell_var", "course_keys"):
        assert k in live["npz"], k
    assert abs(np.average(live["npz"]["difficulty"][live["npz"]["solved"]],
                          weights=live["npz"]["degree"][live["npz"]["solved"]])
               ) < 0.01
    print("  pair_difficulty.npz shape, anchored ................... OK")

    # athlete_ratings keyed (person_id, pool), one row per pair
    k0 = next(iter(live["athletes"]))
    assert isinstance(k0, tuple) and k0[1] in ("hs_m", "hs_f")
    assert "XC" in live["per_sport"] and "TF" in live["per_sport"]
    print("\nall joint_golive tests passed")


if __name__ == "__main__":
    main()
