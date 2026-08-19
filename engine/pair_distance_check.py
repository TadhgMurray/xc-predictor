# Is difficulty biased by DISTANCE rather than by terrain?
#
# The 4800m offset (+0.015 to +0.032 vs 5000m, present in every season bucket)
# lives in normalized_time. Cells are keyed (canonical_id, distance), so each
# cell has exactly one distance -- a distance effect is perfectly collinear with
# delta and is absorbed into it. This prints the absorbed amount.
#
#   python engine\pair_distance_check.py

import os
import sys

import numpy as np

ANCHORS = {                       # canonical_id -> (name, expected sign)
    "22248": ("Woodbridge HS", "-"), "9959": ("Silverlakes", "-"),
    "13433": ("Detweiller", "-"),
}


def distanceOf(key):
    """Snapped distance from an engine course key, or None."""
    if not key or not key.startswith("XC:"):
        return None
    _venue, tag, dist = key[3:].rpartition(":d")
    if not tag or not dist.isdigit():
        return None
    return int(dist)


def idOf(key):
    """canonical_id from 'XC:<id>:d<dist>', or None for the name: form."""
    venue, tag, _d = key[3:].rpartition(":d") if key.startswith("XC:") else ("", "", "")
    return venue if tag and venue.isdigit() else None


def withinVenue(d, solved, degree, keys, min_degree=10):
    """
    ★ THE DECISIVE TEST. Does the SAME venue rate harder at 4800 than at 5000?

    A gap ACROSS venues is confounded: 4800 is the California 3-mile convention,
    so 4800 cells are disproportionately hilly trail while 5000 cells skew flat
    midwest golf course. That gap could be real terrain.

    A gap WITHIN one venue cannot be terrain -- it is the same ground. It can
    only be the distance normalization.

      gap within ~= gap across  ->  normalization error, fix the potential
      gap within ~= 0           ->  terrain, and normalize_distance is fine
    """
    from collections import defaultdict

    by_id = defaultdict(dict)
    for i, k in enumerate(keys):
        cid, dm = idOf(k), distanceOf(k)
        if cid and dm and solved[i] and degree[i] >= min_degree:
            by_id[cid][dm] = d[i]

    pairs = defaultdict(list)
    for _cid, per_dist in by_id.items():
        if len(per_dist) < 2:
            continue
        for dm, val in per_dist.items():
            for dm2, val2 in per_dist.items():
                if dm < dm2:
                    pairs[(dm, dm2)].append(val - val2)

    print(f"\n[dist] WITHIN-VENUE distance gaps (degree >= {min_degree})")
    print("    pair            venues     mean_gap    median")
    shown = sorted(pairs.items(), key=lambda kv: -len(kv[1]))[:12]
    for (a, b), vals in shown:
        if len(vals) < 10:
            continue
        v = np.array(vals)
        print(f"    {a:>5} vs {b:<5} {len(vals):>10,}  {v.mean():>+10.4f} "
              f"{np.median(v):>+9.4f}")
    if not shown:
        print("    no venue hosts two distances with enough data --")
        print("    the offset is NOT identifiable within venue")


def main(path):
    with np.load(path, allow_pickle=False) as f:
        d = f["difficulty"]
        solved = f["solved"].astype(bool)
        degree = f["degree"]
        keys = [str(k) for k in f["course_keys"]]

    dist = np.array([distanceOf(k) or -1 for k in keys])

    print("[dist] mean difficulty by snapped distance (XC, solved cells)")
    print("    dist    cells    mean_d   median_d   vs 5000m")
    ref = None
    for dm in sorted(set(dist[dist > 0])):
        m = solved & (dist == dm) & (degree >= 10)   # degree gate: skip junk
        if m.sum() < 40:
            continue
        mean = float(d[m].mean())
        if dm == 5000:
            ref = mean
        print(f"    {dm:>5} {int(m.sum()):>8,} {mean:>+9.4f} "
              f"{float(np.median(d[m])):>+10.4f}"
              f"{'' if ref is None else f'  {mean - ref:>+8.4f}'}")

    withinVenue(d, solved, degree, keys)

    print("\n[dist] the anchors and the two fast courses")
    for i, k in enumerate(keys):
        cid = idOf(k)
        if cid in ANCHORS and solved[i]:
            name, want = ANCHORS[cid]
            ok = "OK " if (d[i] < 0) == (want == "-") else "BAD"
            print(f"    {ok} {name:<15} {k:<22} {d[i]:+.4f} "
                  f"(want {want})  degree {int(degree[i]):,}")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(here, "pair_validated.npz"))