#!/usr/bin/env python3
"""
oct23_score.py -- an Oct 23 arm's ratings as track-5K times, through the
site's own conversion with the arm's own tables; and the arms side by side.
READ-ONLY on the database.

    python scripts/oct23_score.py --repo /srv/xc-predictor --arm-dir /srv/oct23/A
    python scripts/oct23_score.py --repo /srv/xc-predictor --table /srv/oct23/{A,B,C,D}

★ ONE ARM. arm_ratings.npz (scripts/oct23_dry.py) holds every rated row's
  rating and pool and the engine_scale, sport_gain and distance_offset rows
  the go-live would have written. conversions.fiveKForRating reads those
  three tables through module caches; they are filled here from the arm
  and pinned, and XCP_TILT_SCALE is the arm's, so a rating converts exactly
  as the site would convert it had this arm gone live. Writes five.npz
  (result_id, sport 0 XC / 1 track, five_k_s) for lacctic_compare
  --ours-npz, and prints the Hammerand pair (the nightly's third test).
★ THE TABLE. For rows every arm rated: the median rating move of each arm
  against the first, per (pool, sport), and over the college XC rows the
  placement touched most. Which factor moved what, before any test.
"""
import argparse
import ast
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
        if a.startswith("--repo="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.abspath(os.path.join(HERE, ".."))


REPO = _repo()
for _p in (os.path.join(REPO, "racecast"), os.path.join(REPO, "engine"),
           os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# the nightly's Hammerand pair (results_tf ids)
HAMMERAND = (277459367, 293998421)
SPORTS = (("XC", 0), ("TF", 1))


def loadArm(arm_dir):
    z = np.load(os.path.join(arm_dir, "arm_ratings.npz"))
    arm = {k: z[k] for k in z.files}
    arm["meta"] = ast.literal_eval(str(arm["meta"][0]))
    return arm


def pinTables(C, arm):
    """conversions' engine_scale / sport_gain / distance_offset caches, as
    its loaders would build them from the arm's rows, never reloaded."""
    b = C._bare
    scale = {}
    for p, s, pm, me, sh in zip(arm["scale_pool"], arm["scale_sport"], arm["scale_pool_mean"],
                                arm["scale_median_effect"], arm["scale_shift"]):
        scale[(b(str(p)), str(s).upper())] = (float(pm), float(me), float(sh))
    gain = {}
    order = np.lexsort((arm["gain_anchor"], arm["gain_sport"], arm["gain_pool"]))
    for i in order:
        key = (b(str(arm["gain_pool"][i])), str(arm["gain_sport"][i]).upper())
        sh = arm["gain_shift"][i]
        gain.setdefault(key, []).append((float(arm["gain_anchor"][i]),
                                         0.0 if not np.isfinite(sh) else float(sh)))
    offs = {}
    for p, s, m, band, off in zip(arm["dist_pool"], arm["dist_sport"], arm["dist_m"],
                                  arm["dist_band"], arm["dist_off"]):
        offs[(b(str(p)), str(s).upper(), int(m), int(band))] = \
            0.0 if not np.isfinite(off) else float(off)
    for cache, m in ((C._scale, scale), (C._gain, gain), (C._offsets, offs)):
        cache["map"] = m
        cache["at"] = math.inf                 # never stale: no reload from the live tables
    return len(scale), len(gain), len(offs)


def modeArm(a):
    arm = loadArm(a.arm_dir)
    os.environ["XCP_TILT_SCALE"] = arm["meta"].get("tilt_scale") or "own"
    import conversions as C
    n = pinTables(C, arm)
    print(f"[oct23] {a.arm_dir}: tilt {os.environ['XCP_TILT_SCALE']}; tables pinned "
          f"(engine_scale {n[0]}, sport_gain {n[1]} pools, distance_offset {n[2]})")
    rid_all, sp_all, five_all = [], [], []
    no_scale = set()
    for name, code in SPORTS:
        if f"{name}_result_id" not in arm:
            continue
        rid, rating, pool = arm[f"{name}_result_id"], arm[f"{name}_rating"], arm[f"{name}_pool"]
        # one conversion per (rating, pool): ratings carry two decimals
        pcode = np.unique(pool.astype(str), return_inverse=True)
        key = pcode[1].reshape(-1).astype(np.int64) * 10 ** 7 + np.rint(rating * 100).astype(np.int64)
        uk, inv = np.unique(key, return_inverse=True)
        vals = np.full(uk.size, np.nan)
        for u, k in enumerate(uk):
            p, r = str(pcode[0][k // 10 ** 7]), (k % 10 ** 7) / 100.0
            if C.engineScale(p, name) is None:
                no_scale.add(C._bare(p))
            f = C.fiveKForRating(r, p)
            vals[u] = (f or {}).get("track") or np.nan
        five = vals[inv.reshape(-1)]
        ok = np.isfinite(five)
        print(f"[oct23]   {name}: {rid.size:,} rated rows, {int(ok.sum()):,} with a 5K")
        rid_all.append(rid[ok]); sp_all.append(np.full(int(ok.sum()), code)); five_all.append(five[ok])
    if no_scale:
        print(f"[oct23]   ⚠ pools with no engine_scale row in the arm (the live pool mean was "
              f"used): {sorted(no_scale)}")
    rid, sp, five = (np.concatenate(x) for x in (rid_all, sp_all, five_all))
    np.savez(os.path.join(a.arm_dir, "five.npz"), result_id=rid, sport=sp, five_k_s=five)
    print(f"[oct23]   wrote {a.arm_dir}/five.npz ({rid.size:,} rows)")
    tf = {int(r): f for r, s, f in zip(rid, sp, five) if s == 1 and int(r) in HAMMERAND}
    h = [tf.get(r) for r in HAMMERAND]
    rt = {}
    if "TF_result_id" in arm:
        for r, v in zip(arm["TF_result_id"], arm["TF_rating"]):
            if int(r) in HAMMERAND:
                rt[int(r)] = float(v)
    if all(h):
        gap_r = (f", rating gap {rt[HAMMERAND[1]] - rt[HAMMERAND[0]]:+.1f}"
                 if len(rt) == 2 else "")
        print(f"[oct23]   Hammerand: {HAMMERAND[0]} {h[0]:.1f}s, {HAMMERAND[1]} {h[1]:.1f}s, "
              f"gap {h[0] - h[1]:+.1f}s ({100 * math.log(h[0] / h[1]):+.2f}%){gap_r}")
    else:
        print(f"[oct23]   Hammerand: not both rated in this arm ({h})")


def modeTable(dirs):
    arms = [loadArm(d) for d in dirs]
    names = [os.path.basename(os.path.normpath(d)) for d in dirs]
    print("arm  tilt  pack")
    for nm, arm in zip(names, arms):
        print(f"{nm:4s} {arm['meta'].get('tilt_scale', ''):5s} {arm['meta'].get('pack', '')}")
    for sport, _code in SPORTS:
        if any(f"{sport}_result_id" not in arm for arm in arms):
            continue
        common = arms[0][f"{sport}_result_id"]
        for arm in arms[1:]:
            common = np.intersect1d(common, arm[f"{sport}_result_id"])
        got = []
        for arm in arms:
            rid = arm[f"{sport}_result_id"]
            o = np.argsort(rid)
            j = o[np.searchsorted(rid, common, sorter=o)]
            got.append((arm[f"{sport}_rating"][j], arm[f"{sport}_pool"][j]))
        pool0 = np.char.partition(got[0][1].astype(str), "|")[:, 0]
        print(f"\n{sport}: {common.size:,} rows rated in every arm; median rating move "
              f"against {names[0]} (|move| in brackets)")
        print(f"  {'pool':12s} {'rows':>9s} " + " ".join(f"{nm:>15s}" for nm in names[1:]))
        for p in np.unique(pool0):
            m = pool0 == p
            if m.sum() < 1000:
                continue
            cells = []
            for g in got[1:]:
                d = g[0][m] - got[0][0][m]
                cells.append(f"{np.median(d):+6.2f} ({np.median(np.abs(d)):4.2f})")
            print(f"  {p:12s} {int(m.sum()):9,d} " + " ".join(f"{c:>15s}" for c in cells))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("--arm-dir", default=None)
    ap.add_argument("--table", nargs="+", default=None, metavar="ARM_DIR")
    a = ap.parse_args()
    if a.table:
        modeTable(a.table)
    elif a.arm_dir:
        modeArm(a)
    else:
        ap.error("--arm-dir or --table")


if __name__ == "__main__":
    main()
