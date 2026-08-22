# Project: xc-predictor
# File:    scripts/diag_override_check.py
# Purpose: READ-ONLY. Decide, for every _DISTANCE_OVERRIDES entry, whether the
#          override actually reached the data -- WITHOUT a distance column to
#          read. It backs the distance out of the arithmetic instead.
#
# ============================================================================
# THE MAIN IDEA
# ============================================================================
# The backfill computes, roughly:
#       normalized_time = raw_time * (5000 / distance)^exp * geometry * era
# For XC, geometry ~= 1 (no track banking) and era is within a few percent, so:
#       raw_time / normalized_time ~= (distance / 5000)^exp
# With the XC exponent near 1, the EFFECTIVE distance the backfill used is:
#       d_eff ~= 5000 * median(raw_time / normalized_time)
#
# So per override division we compare three numbers:
#   override_d   what corrections.py SAYS the distance is
#   d_eff        what the data BEHAVES as if the distance was
#   stored_d     the old (wrong) meets.distance, for anet
#
#   d_eff ~= override_d   -> the override APPLIED. A residual flag here is NOT a
#                           non-application; it is the distance model extrapolating
#                           at an unusual distance, or a wrong override value.
#   d_eff ~= stored_d     -> the override did NOT apply. The overnight backfill
#                           ran without it (stale corrections, or a match bug).
#
# This is an APPROXIMATION (exp=1, geometry=era=1). It cannot tell 3000 from 3200
# (7% apart), but it cleanly separates "applied 1200" from "still 5000" (4x).
#
# USAGE
#   python scripts/diag_override_check.py
#   python scripts/diag_override_check.py --corrections engine/corrections.py
#   python scripts/diag_override_check.py --only-mismatch   # hide the healthy ones
# ============================================================================

import argparse
import importlib.util
import os
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool

_CORRECTIONS_CANDIDATES = ("engine/corrections.py", "scripts/corrections.py",
                           "corrections.py", "backfill/corrections.py")


# ================================================================== #
# CHUNK 1 -- LOAD THE OVERRIDE TABLE
# ================================================================== #

# _loadOverrides
# Purpose : read _DISTANCE_OVERRIDES out of corrections.py by path.
# Output  : {(meet,div): distance}.
def _loadOverrides(path):
    if path is None:
        for cand in _CORRECTIONS_CANDIDATES:
            if os.path.exists(cand):
                path = cand
                break
    if path is None or not os.path.exists(path):
        sys.exit("corrections.py not found -- pass --corrections <path>")
    spec = importlib.util.spec_from_file_location("corrections", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # ⚠ THE NAME WAS SPLIT PER SPORT AND THIS WAS NOT UPDATED. corrections.py
    #   has held _DISTANCE_OVERRIDES_XC / _TF for a long time; the unsuffixed
    #   name has not existed, so this script died on an AttributeError before
    #   reading a single row. Falls back to the old name so a corrections.py
    #   predating the split still loads.
    table = getattr(mod, "_DISTANCE_OVERRIDES_XC", None)
    if table is None:
        table = getattr(mod, "_DISTANCE_OVERRIDES", None)
    if table is None:
        sys.exit(f"{path} has neither _DISTANCE_OVERRIDES_XC nor "
                 f"_DISTANCE_OVERRIDES")
    print(f"  [ok] {len(table)} overrides loaded from {path}")
    return dict(table)


# ================================================================== #
# CHUNK 2 -- BACK OUT THE EFFECTIVE DISTANCE
# ================================================================== #

# _effectiveDistances
# Purpose : per override division, the median raw/norm ratio -> d_eff, the field
#           size, the source, and the stored anet distance for comparison.
# Syntax  : the override keys go in as a VALUES list joined to results. We guard
#           the 999999 DNF sentinel and non-positive norms before dividing.
#           percentile_cont(0.5) WITHIN GROUP is the ordered-set median (legal
#           with GROUP BY, unlike a window).
# Output  : {(meet,div): (source, n, d_eff, stored_d)}.
def _effectiveDistances(cur, overrides):
    keys = ", ".join(f"({m},{d})" for (m, d) in overrides)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, r.source,
               count(*) AS n,
               5000.0 * percentile_cont(0.5) WITHIN GROUP
                        (ORDER BY r.time_seconds / r.normalized_time) AS d_eff,
               min(m.distance) AS stored_d
        FROM results r
        JOIN (VALUES {keys}) AS k(meet_id, div_id)
          ON k.meet_id = r.meet_id AND k.div_id = r.div_id
        LEFT JOIN meets m ON m.div_id = r.div_id AND r.source = 'anet'
        WHERE r.normalized_time IS NOT NULL
          AND r.normalized_time > 0
          AND r.time_seconds IS NOT NULL
          AND r.time_seconds < 999999
        GROUP BY r.meet_id, r.div_id, r.source
    """)
    out = {}
    for meet, div, source, n, d_eff, stored in cur.fetchall():
        out[(meet, div)] = (source, n, d_eff, stored)
    return out


# ================================================================== #
# CHUNK 3 -- CLASSIFY
# ================================================================== #

# _near
# Purpose : within 8% -- loose enough to absorb the exp/era approximation,
#           tight enough to separate real distances that differ by 4x.
def _near(a, b):
    if a is None or b is None or b == 0:
        return False
    return abs(a - b) / b < 0.08


# _verdict
# Purpose : one word per override division.
#   APPLIED       d_eff matches the override -> correction reached the data
#   NOT-APPLIED   d_eff matches the old stored distance -> it did not
#   MISSING       no rows survive (dropped elsewhere, or override is a drop-ee)
#   UNCLEAR       d_eff matches neither -- worth a look (wrong override? era?)
def _verdict(override_d, source, n, d_eff, stored_d):
    if n == 0 or d_eff is None:
        return "MISSING"
    if _near(d_eff, override_d):
        return "APPLIED"
    if stored_d is not None and _near(d_eff, stored_d):
        return "NOT-APPLIED"
    return "UNCLEAR"


# ================================================================== #
# CHUNK 4 -- DRIVER
# ================================================================== #

def _run(corr_path, only_mismatch):
    overrides = _loadOverrides(corr_path)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '1GB'")
        eff = _effectiveDistances(cur, overrides)
        conn.rollback()

    counts = {"APPLIED": 0, "NOT-APPLIED": 0, "MISSING": 0, "UNCLEAR": 0}
    lines = []
    for (meet, div), override_d in sorted(overrides.items()):
        source, n, d_eff, stored_d = eff.get((meet, div), (None, 0, None, None))
        v = _verdict(override_d, source, n, d_eff, stored_d)
        counts[v] += 1
        if only_mismatch and v == "APPLIED":
            continue
        d_eff_s = f"{d_eff:7.0f}" if d_eff is not None else "      -"
        stored_s = f"{stored_d:7.0f}" if stored_d is not None else "      -"
        lines.append(f"  {v:<12} meet={meet:<8} div={div:<8} "
                     f"{source or '-':<6} n={n:<5} "
                     f"override={override_d:7.0f}  d_eff={d_eff_s}  "
                     f"stored={stored_s}")

    print("\n".join(lines))
    print("\n" + "=" * 60)
    print(f"  APPLIED     {counts['APPLIED']:>4}   (override reached the data)")
    print(f"  NOT-APPLIED {counts['NOT-APPLIED']:>4}   (data still on old distance)")
    print(f"  UNCLEAR     {counts['UNCLEAR']:>4}   (matches neither -- inspect)")
    print(f"  MISSING     {counts['MISSING']:>4}   (no surviving rows)")
    print("=" * 60)
    if counts["NOT-APPLIED"]:
        print("  -> NOT-APPLIED means the overnight backfill did not honour these.")
        print("     The corrections did not reach the pool. Triage is blocked until")
        print("     you know why (stale corrections.py? source-match bug?).")
    elif counts["APPLIED"] > counts["NOT-APPLIED"]:
        print("  -> Overrides mostly APPLIED. A residual flag on an APPLIED division")
        print("     is the distance model extrapolating at an unusual distance, not")
        print("     a non-application -- exclude those distances in the detector.")


def main():
    ap = argparse.ArgumentParser(
        description="Did the distance overrides actually reach the data? READ-ONLY.")
    ap.add_argument("--corrections", default=None)
    ap.add_argument("--only-mismatch", action="store_true",
                    help="hide APPLIED rows; show only the problems")
    args = ap.parse_args()
    initPool()
    _run(args.corrections, args.only_mismatch)


if __name__ == "__main__":
    main()