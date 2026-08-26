# Project: xc-predictor / scripts
# File:    check_bridge.py
# Purpose: Will the self-reference gate catch a given venue? Answered from
#          the EXISTING pack, before spending a rebuild to find out.
#
#     python scripts/check_bridge.py "Cabell Midland"
#     python scripts/check_bridge.py "Cabell Midland" --pack path\to.npz
#
# ★ NO REBUILD NEEDED. bridgeFraction is deterministic on arrays the last
#   07_pack already wrote (engine/data/packed_XC_TF.npz): course, group,
#   keys. The verdict printed here is the verdict 08_golive will apply --
#   the only difference a rebuild makes is refreshed rows.
#
# Read-only. Loads the pack (a couple of minutes for the 2GB file) and, if
# the database is reachable, resolves the pattern through course_canonical
# so canonical-id keys ("XC:1234:d3000") match as well as name keys.

import argparse
import os
import sys

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

import numpy as np                                    # noqa: E402

import linkage_check as L                             # noqa: E402
import pair_engine as pe                              # noqa: E402

_DEFAULT_PACK = os.path.join("engine", "data", "packed_XC_TF.npz")


def liteD(pack_path):
    """prepare() minus the form correction -- the gate's inputs only."""
    cols = pe.loadPack(pack_path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    course = cols["course"][keep].astype(np.int64)
    group, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                            cols["year"][keep])
    d = {"course": course, "group": group, "n_groups": n_groups,
         "n_cells": len(cols["course_keys"]),
         "keys": [str(k) for k in cols["course_keys"]],
         # degree is a solve output; for externalFraction's fallback use a
         # distinct-group count per cell, which is what degree measures.
         "degree": np.zeros(len(cols["course_keys"]), dtype=np.int64)}
    if "cell_days" in cols:
        d["cell_days"] = np.asarray(cols["cell_days"])
    uniq = np.unique(group.astype(np.int64) * d["n_cells"] + course)
    d["degree"] = np.bincount((uniq % d["n_cells"]).astype(np.int64),
                              minlength=d["n_cells"])
    return d


def tagsFor(pattern):
    """Key fragments that identify the venue: its name, and -- when the DB
    is reachable -- its canonical ids."""
    tags = [pattern.lower()]
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT canonical_id, course_name "
                        "FROM course_canonical "
                        "WHERE course_name ILIKE %s", (f"%{pattern}%",))
            for cid, name in cur.fetchall():
                tags.append(f"xc:{cid}:")
                tags.append(name.lower())
    except Exception as exc:                          # noqa: BLE001
        print(f"  (no DB for canonical lookup: {type(exc).__name__} -- "
              "matching on the name only)")
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", help="venue/course name fragment")
    ap.add_argument("--pack", default=_DEFAULT_PACK)
    args = ap.parse_args()

    tags = tagsFor(args.pattern)
    D = liteD(args.pack)
    bridge = L.bridgeFraction(D)
    frac = L.externalFraction(D)
    days = np.asarray(D.get("cell_days", D["degree"]))

    hits = [i for i, k in enumerate(D["keys"])
            if any(t in k.lower() for t in tags)]
    if not hits:
        print(f"\n  no cell key matches {args.pattern!r} "
              f"(tried: {', '.join(tags[:4])})")
        return 1

    xc = np.array([k.startswith("XC:") for k in D["keys"]], dtype=bool)
    q = np.percentile(bridge[xc], [1, 5, 10, 25, 50]) if xc.any() else []
    print(f"\n  corpus context -- bridge over {int(xc.sum()):,} XC cells: "
          + "  ".join(f"p{p} {v:.2f}" for p, v in zip((1, 5, 10, 25, 50), q)))
    print(f"  gate: XC cell with bridge < {L.BRIDGE_MIN:.2f} -> sport "
          "default when solved\n")
    print(f"    {'cell key':<36}{'bridge':>8}{'ext':>7}{'days':>6}  verdict")
    print("    " + "-" * 70)
    for i in sorted(hits, key=lambda i: bridge[i]):
        k = D["keys"][i]
        fires = k.startswith("XC:") and bridge[i] < L.BRIDGE_MIN
        verdict = ("REPLACED by sport default" if fires else
                   "kept (its own solve stands)" if k.startswith("XC:")
                   else "TF -- exempt today")
        print(f"    {k[:36]:<36}{bridge[i]:>8.2f}{frac[i]:>7.2f}"
              f"{int(days[i]):>6}  {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
