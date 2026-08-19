# Project: xc-predictor
# File:    scripts/calibrate_shape.py
# Purpose: READ-ONLY. Answer "what should the PARTIAL fraction be?" by
#          measuring instead of guessing -- and test whether the fraction is
#          the right question at all.
#
# THE IDEA
#   A distance error is a property of the COURSE. Every runner in the division
#   ran the same course. So a wrong label multiplies EVERY runner's gap by the
#   same factor: the gap distribution SLIDES, it does not change shape.
#
#       wrong label        -> median shifts, IQR stays NORMAL   (translation)
#       half the field slow -> median shifts, IQR INFLATES      (mixture)
#
#   triage_regressions._classify cannot see this. _SPIKE=15.0 binarizes every
#   gap to fast/slow/clean, discarding magnitudes; the 0.15/0.60/0.30/0.20
#   thresholds then try to reconstruct meaning from counts. This script keeps
#   the magnitudes and asks whether IQR separates the two cases.
#
# WHAT "NORMAL" MEANS
#   Measured, not chosen: sample divisions the detector does NOT flag. Their
#   IQR distribution IS the null. If flagged divisions split into a normal-IQR
#   group and an inflated-IQR group, the test works and the cut reads straight
#   off the null. If they overlap, the test is dead -- and this script says so
#   rather than shipping a constant.
#
# USAGE (~3 min, writes one TSV, touches no tables)
#   python scripts\calibrate_shape.py --sport XC

import argparse
import os
import statistics
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool
# Import the SAME gap query triage uses, so this measures what it classifies.
from triage_regressions import _gaps, _TABLE, _SPIKE


# ================================================================== #
# CHUNK 1 -- THE TWO NUMBERS PER DIVISION
# ================================================================== #

def _pct(values, p):
    """Percentile by position. No interpolation -- we only need the shape."""
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


def _shape(gaps):
    """
    Purpose : the whole description of a division's gap distribution.
    Output  : dict, or None if the field is too small to have a shape.
      med        -- the SHIFT. how far off the field is.
      iqr        -- the SPREAD. did the field move TOGETHER?
      clean_frac -- what _classify uses today, kept so we can compare.
    """
    n = len(gaps)
    if n < 12:
        return None
    s = sorted(gaps)
    return {
        "n": n,
        "med": statistics.median(s),
        "iqr": s[(3 * n) // 4] - s[n // 4],
        "clean_frac": sum(abs(g) <= _SPIKE for g in s) / n,
    }


# ================================================================== #
# CHUNK 2 -- THE TWO POPULATIONS
# ================================================================== #

def _flaggedPairs(path):
    """
    Purpose : the divisions diag flagged. Positional parse matching diag's
              writer: p[2]=meet p[3]=div (same as _pairsFromWorksheet).
    """
    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) > 3:
                pairs.append((int(p[2]), int(p[3])))
    return pairs


def _cleanPairs(cur, table, flagged, want):
    """
    Purpose : the NULL -- random divisions the detector did not flag. Their
              spread is the definition of "normal"; nothing is chosen here.
    """
    cur.execute(f"""
        SELECT meet_id, div_id
        FROM {table}
        WHERE speed_rating > 0
        GROUP BY meet_id, div_id
        HAVING count(*) >= 12
        ORDER BY random()
        LIMIT %s
    """, (want * 2,))
    hits = [p for p in cur.fetchall() if tuple(p) not in flagged]
    return hits[:want]


def _shapesFor(cur, table, pairs, label):
    """Purpose : walk a list of divisions -> their shapes. Prints progress
    because each division costs one correlated subquery."""
    out = {}
    for i, (meet, div) in enumerate(pairs, 1):
        sh = _shape(_gaps(cur, table, meet, div))
        if sh:
            out[(meet, div)] = sh
        if i % 100 == 0:
            print(f"    {label}: {i}/{len(pairs)}")
    return out


# ================================================================== #
# CHUNK 3 -- REPORTING (the answer is a comparison, not a number)
# ================================================================== #

def _summary(label, shapes):
    """Purpose : one population's IQR and clean_frac distribution."""
    iqrs = [s["iqr"] for s in shapes.values()]
    cfs = [s["clean_frac"] for s in shapes.values()]
    if not iqrs:
        print(f"\n  {label}: EMPTY")
        return None
    print(f"\n  {label}  ({len(iqrs):,} divisions)")
    print(f"    IQR of gap%    p50={_pct(iqrs, .5):7.1f}  p90={_pct(iqrs, .9):7.1f}  "
          f"p99={_pct(iqrs, .99):7.1f}")
    print(f"    clean frac     p50={_pct(cfs, .5):7.2f}  p10={_pct(cfs, .1):7.2f}  "
          f"p01={_pct(cfs, .01):7.2f}")
    return _pct(iqrs, .9)          # the null's p90 -- candidate cut


def _crossTab(shapes, null_p90):
    """
    Purpose : THE DECISIVE VIEW. Bin flagged divisions by clean_frac and show
              their median IQR. If the two measures agree, IQR should climb
              with clean_frac -- more clean runners == more of a mixture.
              If IQR is flat across the bins, clean_frac and IQR are measuring
              different things and one of them is wrong.
    """
    bins = {}
    for s in shapes.values():
        b = round(s["clean_frac"] * 10) / 10
        bins.setdefault(b, []).append(s["iqr"])
    print(f"\n  FLAGGED: does IQR climb with clean fraction?")
    print(f"    (null p90 IQR = {null_p90:.1f} -- above this is a MIXTURE, "
          f"below is a TRANSLATION)")
    print(f"    {'clean_frac':>10} {'divs':>6} {'median IQR':>11}   verdict")
    for b in sorted(bins):
        med = statistics.median(bins[b])
        verdict = "MIXTURE (do not edit div-wide)" if med > null_p90 \
            else "translation (label may be wrong)"
        print(f"    {b:>10.1f} {len(bins[b]):>6,} {med:>11.1f}   {verdict}")


def _write(path, shapes, null_p90):
    with open(path, "w", encoding="utf-8") as f:
        f.write("meet\tdiv\tn\tmed_gap\tiqr\tclean_frac\tshape\n")
        for (m, d), s in sorted(shapes.items()):
            kind = "MIXTURE" if s["iqr"] > null_p90 else "TRANSLATION"
            f.write(f"{m}\t{d}\t{s['n']}\t{s['med']:+.1f}\t{s['iqr']:.1f}\t"
                    f"{s['clean_frac']:.2f}\t{kind}\n")


# ================================================================== #
# CHUNK 4 -- ORCHESTRATION
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(description="Measure the gap-distribution "
                                 "shape of flagged vs unflagged divisions.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--null-sample", type=int, default=400,
                    help="how many unflagged divisions define 'normal'")
    args = ap.parse_args()

    ws = os.path.join(args.dir, f"suspects_div_{args.sport.lower()}.tsv")
    flagged = _flaggedPairs(ws)
    print(f"flagged divisions in {ws}: {len(flagged):,}")

    table = _TABLE[args.sport]
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print(f"sampling {args.null_sample} unflagged divisions (the null) ...")
        clean_pairs = _cleanPairs(cur, table, set(flagged), args.null_sample)
        null = _shapesFor(cur, table, clean_pairs, "null")

        # Cap the flagged pass so this stays a ~3 min diagnostic, not a cycle.
        hot_pairs = flagged[:args.null_sample]
        hot = _shapesFor(cur, table, hot_pairs, "flagged")
        conn.rollback()                        # read-only, always

    print("\n" + "=" * 66)
    null_p90 = _summary("NULL -- divisions the detector did NOT flag", null)
    _summary("FLAGGED", hot)
    if null_p90 is None:
        sys.exit("no null sample -- cannot calibrate")

    _crossTab(hot, null_p90)

    out = os.path.join(args.dir, f"shape_{args.sport.lower()}.tsv")
    _write(out, hot, null_p90)
    print(f"\nwrote {out}")
    print("\n  READ THIS: if the FLAGGED p50 IQR is close to the NULL p50 IQR,\n"
          "  spread does NOT separate the two cases and this whole approach is\n"
          "  dead -- keep the fraction. If FLAGGED is clearly wider, IQR is the\n"
          "  test and the cut is the null's p90 printed above.")


if __name__ == "__main__":
    main()