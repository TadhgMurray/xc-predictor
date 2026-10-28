#!/usr/bin/env python3
"""
oct23_dry.py -- engine/run_joint.py as the pipeline's 08_golive runs it,
with --golive-dry, keeping what the go-live WOULD have written: every
rated row's rating and pool, and the engine_scale, sport_gain and
distance_offset rows the conversions read. Nothing reaches the database.

    python scripts/oct23_dry.py --repo /srv/xc-predictor --arm-dir DIR -- <run_joint flags>

scripts/oct23_arm.sh builds the flags from the repo's own run_pipeline.sh,
so an arm is the nightly solve exactly, on the pack and tilt it is given.

★ WHAT IT WRITES, ALL IN --arm-dir:
    arm_ratings.npz     XC_/TF_ result_id, rating, pool; scale_*, gain_*,
                        dist_* (the three tables' rows); meta
    joint_difficulty.npz, pair_difficulty.npz, *_state.npz
                        run_joint's own files, here because --out is here
! --out MUST BE IN THE ARM DIR. run_joint writes pair_difficulty.npz next
  to --out even under --golive-dry, and the site reads the one in
  engine/data; oct23_arm.sh passes --out <arm dir>/joint_difficulty.npz
  and this file refuses anything else.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _take(flag, default=None):
    """Pop `flag VALUE` (or flag=VALUE) from sys.argv."""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            v = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            return v
        if a.startswith(flag + "="):
            del sys.argv[i]
            return a.split("=", 1)[1]
    return default


REPO = os.path.abspath(_take("--repo", os.path.join(HERE, "..")))
ARM = _take("--arm-dir")
sys.path.insert(0, os.path.join(REPO, "engine"))


def _flag(name):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def armArrays(live):
    """The go-live's would-be writes as plain arrays."""
    out = {}
    for name, (rid, rating, pool) in live["per_sport"].items():
        out[f"{name}_result_id"] = np.asarray(rid, dtype=np.int64)
        out[f"{name}_rating"] = np.asarray(rating, dtype=np.float64)
        out[f"{name}_pool"] = np.asarray([str(p) for p in pool])
    tabs = {"scale": (live.get("scale_rows") or [],
                      ("pool", "sport", "pool_mean", "median_effect", "shift", "n")),
            "gain": (live.get("gain_rows") or [],
                     ("pool", "sport", "band", "anchor", "target", "realised", "shift", "n")),
            "dist": (live.get("dist_rows") or [],
                     ("pool", "sport", "m", "band", "off", "n"))}
    for tab, (rows, names) in tabs.items():
        for j, nm in enumerate(names):
            vals = [r[j] for r in rows]
            if nm in ("pool", "sport"):
                out[f"{tab}_{nm}"] = np.asarray([str(v) for v in vals], dtype="U64")
            else:
                out[f"{tab}_{nm}"] = np.asarray([np.nan if v is None else float(v) for v in vals])
    return out


def main():
    global ARM
    if not ARM:
        raise SystemExit("[oct23] --arm-dir DIR is required")
    ARM = os.path.abspath(ARM)
    if "--" in sys.argv:
        sys.argv.remove("--")
    out = _flag("--out")
    if not out or os.path.dirname(os.path.abspath(out)) != ARM:
        raise SystemExit(f"[oct23] --out must be in {ARM} (run_joint writes "
                         "pair_difficulty.npz beside it)")
    if "--golive" in sys.argv or "--golive-dry" not in sys.argv:
        raise SystemExit("[oct23] --golive-dry only")
    os.makedirs(ARM, exist_ok=True)
    import joint_golive as jg
    import run_joint

    def writeNpz(live, path, _orig=jg.writeNpz):
        _orig(live, path)
        arrays = armArrays(live)
        meta = {"tilt_scale": os.environ.get("XCP_TILT_SCALE", ""),
                "pack": _flag("--pack") or "", "repo": REPO,
                "argv": " ".join(sys.argv[1:])}
        np.savez(os.path.join(ARM, "arm_ratings.npz"),
                 meta=np.array([repr(meta)]), **arrays)
        n = sum(arrays[k].size for k in arrays if k.endswith("_result_id"))
        print(f"[oct23] wrote {ARM}/arm_ratings.npz: {n:,} rated rows, "
              f"{arrays['scale_pool'].size} engine_scale rows")
    jg.writeNpz = writeNpz
    sys.argv[0] = os.path.join(REPO, "engine", "run_joint.py")
    run_joint.main()


if __name__ == "__main__":
    main()
