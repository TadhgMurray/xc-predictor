#!/usr/bin/env python3
# ======================================================================
# flag_slowrace_overrides.py -- which live overrides are slow races, not
#                               distance errors?
# ======================================================================
#
# THE FINDING (163697/678411, worked by hand 7/18)
# ------------------------------------------------
# A wrong DISTANCE moves every runner by ONE multiplicative factor, so
# the per-runner residuals (e = ln(sr) - ln(athlete_rating)) land in a
# TIGHT cluster -- low IQR. The MEDIAN shifts; the SPREAD does not.
#
# A SLOW RACE moves each runner by their own amount -- some had an off
# day, some didn't. The residuals FAN OUT -- high IQR -- even though the
# median is just as negative as a real distance error.
#
# The detector reduces each division to its MEDIAN (c) and the inversion
# converts that median to a distance. It never asks whether the IQR is
# tight enough for "one factor" to be a believable explanation. So a slow
# race and a mislabelled course produce the SAME c and get the SAME
# distance draft. 678411 (per-runner gaps -33 to +0.7, IQR ~16%) was
# drafted 1609 -> 3000 on exactly this confusion.
#
# THE GOOD NEWS: the discriminating number ALREADY EXISTS. score_suspects
# writes iqr_pct per division into scored_div_*.tsv (the IQR of e). We do
# not need to compute anything -- we need to READ it and cross it against
# the overrides that were applied.
#
# This script joins the two files already on disk and flags every applied
# override whose division has an IQR too wide to be a distance error.
#
# Read-only. No DB. Two file reads.
#
# ======================================================================

import argparse
import os
import re


# ----------------------------------------------------------------------
# THE THRESHOLD -- provisional, and the script PRINTS THE EVIDENCE to set it
# ----------------------------------------------------------------------
# A real distance error's residual IQR is bounded by ordinary within-race
# noise (pacing, place-jockeying): a few percent. A slow race adds
# per-runner form variation ON TOP, widening it. 678411 sits at ~16%.
#
# This default is a STARTING LINE, not a calibrated constant. The script's
# job is to show the IQR distribution of confirmed-good overrides (the 6K
# cluster) beside the suspects, so the real cut is set from data. It is a
# NAMED TUNABLE precisely so it does not hide as a magic number.
_IQR_SLOWRACE_PCT = 9.0


# ----------------------------------------------------------------------
# 1. THE OVERRIDES THAT WENT IN
# ----------------------------------------------------------------------
# Purpose : (meet, div) -> proposed distance, from the generated file.
# Arguments: path -- adjudicated_overrides_xc.py (or distance_override_xc.py).
# Output   : dict {(meet, div): distance}.
# Note     : parse the DATA (key: value), not the comment -- same lesson as
#            spot_check. A writer changing its prose must not blind this.
# ----------------------------------------------------------------------
_OV_RE = re.compile(r"\((\d+),\s*(\d+)\):\s*([\d.]+)\s*,")


def _loadOverrides(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _OV_RE.search(line)
            if m:
                out[(int(m.group(1)), int(m.group(2)))] = float(m.group(3))
    return out


# ----------------------------------------------------------------------
# 2. THE PER-DIVISION DISPERSION
# ----------------------------------------------------------------------
# Purpose : (meet, div) -> {c_pct, iqr_pct, n}, from the scored tsv.
# Arguments: path -- scored_div_xc.tsv.
# Output   : dict.
# Format (header line 391 of score_suspects):
#   source  meet_id  div_id  n  c_pct  iqr_pct  z  already
# Split on tab; the free-text `already` column can hold anything, so a
# fixed-width parse would break -- tab split is exact.
# ----------------------------------------------------------------------
def _loadScored(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        header = fh.readline()                      # discard the # header row
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 7:
                continue
            try:
                meet, div, n = int(p[1]), int(p[2]), int(p[3])
                c_pct, iqr_pct = float(p[4]), float(p[5])
            except ValueError:
                continue                            # a malformed row informs nothing
            out[(meet, div)] = {"c_pct": c_pct, "iqr_pct": iqr_pct, "n": n}
    return out


# ----------------------------------------------------------------------
# 3. CLASSIFY EACH OVERRIDE
# ----------------------------------------------------------------------
# Purpose : for one applied override, is its division tight (distance) or
#           wide (slow race)?
# Arguments: ov_dist -- the proposed distance; stat -- the scored record.
# Output   : dict with a verdict.
#
# A division with NO scored record is UNKNOWN, not innocent -- it means the
# override's division did not survive to the scored file (dropped n<2, or
# the tsv predates it). Flag it as unverifiable rather than passing it.
# ----------------------------------------------------------------------
def _classify(ov_dist, stat):
    if stat is None:
        return {"verdict": "UNSCORED", "iqr_pct": None, "c_pct": None,
                "n": None, "dist": ov_dist}
    wide = stat["iqr_pct"] >= _IQR_SLOWRACE_PCT
    return {"verdict": "SLOW_RACE?" if wide else "distance-ok",
            "iqr_pct": stat["iqr_pct"], "c_pct": stat["c_pct"],
            "n": stat["n"], "dist": ov_dist}


# ----------------------------------------------------------------------
# 4. THE IQR DISTRIBUTION -- so the threshold is set from data, not decree
# ----------------------------------------------------------------------
# Purpose : histogram the IQR of all overridden divisions, so you can SEE
#           where the tight cluster (real fixes) ends and the wide tail
#           (slow races) begins, and set _IQR_SLOWRACE_PCT on evidence.
# ----------------------------------------------------------------------
def _histogram(records):
    buckets = {}
    for r in records:
        if r["iqr_pct"] is None:
            continue
        b = int(r["iqr_pct"] // 2) * 2               # 2-percent bins
        buckets[b] = buckets.get(b, 0) + 1
    print("\n  IQR distribution of overridden divisions (2% bins):")
    for b in sorted(buckets):
        bar = "#" * buckets[b]
        mark = "  <- _IQR_SLOWRACE_PCT" if b <= _IQR_SLOWRACE_PCT < b + 2 else ""
        print(f"    {b:>3}-{b+2:<3}%  {buckets[b]:>4}  {bar}{mark}")


# ----------------------------------------------------------------------
# 5. MAIN
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Flag applied overrides that are slow races, not distance errors.")
    ap.add_argument("--overrides",
                    default=os.path.join("scripts", "adjudicated_overrides_xc.py"))
    ap.add_argument("--scored",
                    default=os.path.join("scripts", "scored_div_xc.tsv"))
    ap.add_argument("--iqr", type=float, default=None,
                    help="override _IQR_SLOWRACE_PCT for this run")
    args = ap.parse_args()

    global _IQR_SLOWRACE_PCT
    if args.iqr is not None:
        _IQR_SLOWRACE_PCT = args.iqr

    overrides = _loadOverrides(args.overrides)
    scored = _loadScored(args.scored)
    print(f"loaded {len(overrides):,} overrides, {len(scored):,} scored divisions")

    records = [dict(_classify(d, scored.get(k)), meet=k[0], div=k[1])
               for k, d in overrides.items()]

    _histogram(records)

    suspects = sorted((r for r in records if r["verdict"] == "SLOW_RACE?"),
                      key=lambda r: -r["iqr_pct"])
    unscored = [r for r in records if r["verdict"] == "UNSCORED"]

    print(f"\n  {len(suspects):,} overrides sit on WIDE divisions "
          f"(IQR >= {_IQR_SLOWRACE_PCT}%) -- likely SLOW RACES, not distance:")
    print(f"    {'meet/div':>16} {'dist':>6} {'c%':>7} {'iqr%':>6} {'n':>4}")
    for r in suspects[:40]:
        print(f"    {r['meet']}/{r['div']:<9} {r['dist']:>6.0f} "
              f"{r['c_pct']:>+7.1f} {r['iqr_pct']:>6.1f} {r['n']:>4}")
    if len(suspects) > 40:
        print(f"    ... and {len(suspects) - 40:,} more")

    if unscored:
        print(f"\n  {len(unscored):,} overrides have NO scored division "
              f"(unverifiable from this tsv).")

    print(f"\n  -> the SLOW_RACE? list is the pardon candidate set. Spot-check a\n"
          f"     few against their meet pages, set --iqr from the histogram gap,\n"
          f"     then feed confirmed pardons to the pardon path.\n")


if __name__ == "__main__":
    main()