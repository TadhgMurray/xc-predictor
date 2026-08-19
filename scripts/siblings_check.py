# Project: xc-predictor
# File:    scripts/sibling_check.py
# Purpose: READ-ONLY. Separate the two cases the gap test CANNOT tell apart.
#
# THE PROBLEM
#   A field rated uniformly -17% with a narrow spread means every runner was
#   displaced by the same factor. That is consistent with a wrong distance
#   label. It is EQUALLY consistent with the whole field running slow that day
#   (mud, heat, a re-routed course). Both produce an identical signature in
#   triage_regressions._gaps -- median shifts, spread unchanged. No threshold
#   on that data can separate them, because the information is not in it.
#
# THE DISCRIMINATOR
#   A distance label is per-(meet, div). Race-day conditions are per-MEET.
#
#     div 1 = -17%,  divs 2,3,4 = ~0%    -> div 1's LABEL is wrong
#     div 1 = -17%,  divs 2,3,4 = -17%   -> the MEET ran slow. Labels are fine.
#
#   No invented constants: each division is compared to its own meet, so the
#   scale of the effect cancels.
#
# WHY IT MATTERS
#   A div-wide distance edit on a meet-wide effect writes the WEATHER into
#   _DISTANCE_OVERRIDES, permanently, as a fake course length. Already visible
#   in triage_c9c.log:
#       7474/3 -23.2%  and  7474/4 -23.4%   <- same meet, same shift
#       250207/995070 +56/56 and 995071 +57/58
#
# USAGE (~6 min, writes one TSV, touches no tables)
#   python scripts\sibling_check.py --sport XC --log triage_c9c.log

import argparse
import os
import re
import statistics
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool
# Same gap query triage classifies on, so this measures what it decided.
from triage_regressions import _gaps, _TABLE

# A sibling needs enough runners to have a trustworthy median.
_MIN_FIELD = 8
# A division whose |median gap| is under this is "not displaced".
_FLAT = 5.0


# ================================================================== #
# CHUNK 1 -- WHICH DIVISIONS TRIAGE ACTUALLY DRAFTED
# ================================================================== #

# The UNIFORM verdict lines from triage's console table:
#   "    9333         1  ov=    8000  UNIFORM_NEG  n= 238 ..."
_VERDICT_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+ov=\s*([\d.]+)\s+(UNIFORM_NEG|UNIFORM_POS)\s")


def _readUniform(path):
    """
    Purpose : the divisions triage called UNIFORM -- the only ones it drafts a
              div-wide distance for, hence the only ones at risk here.
    Output  : dict (meet, div) -> {"ov": float, "verdict": str}
    """
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("utf-8", "replace")

    out = {}
    for line in text.splitlines():
        m = _VERDICT_RE.match(line)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = {
                "ov": float(m.group(3)), "verdict": m.group(4)}
    return out


# ================================================================== #
# CHUNK 2 -- THE SIBLINGS
# ================================================================== #

def _siblingsOf(cur, table, meets):
    """
    Purpose : every division of every meet under test, flagged or not. The
              unflagged ones are the point -- they are the control.
    Output  : dict meet_id -> [div_id, ...]
    """
    cur.execute(f"""
        SELECT meet_id, div_id, count(*) AS n
        FROM {table}
        WHERE speed_rating > 0 AND meet_id = ANY(%s)
        GROUP BY meet_id, div_id
        HAVING count(*) >= %s
    """, (sorted(meets), _MIN_FIELD))
    out = {}
    for meet, div, _n in cur.fetchall():
        out.setdefault(meet, []).append(div)
    return out


def _medianGap(cur, table, meet, div):
    """Purpose : one division's median per-runner gap%, or None if unusable."""
    gaps = _gaps(cur, table, meet, div)
    return statistics.median(gaps) if len(gaps) >= _MIN_FIELD else None


# ================================================================== #
# CHUNK 3 -- THE VERDICT
# ================================================================== #

def _verdict(own, sibs):
    """
    Purpose : is this displacement the division's, or the whole meet's?
    Arguments: own -- this division's median gap%; sibs -- the other divisions'
               median gaps at the same meet.
    Output  : (verdict, sibling median, note)

    The rule is a ratio, not a threshold on metres: how much of this
    division's shift do its meet-mates SHARE? Scale cancels, so nothing is
    invented except the two ratio bands, and those are reported per row so the
    distribution can be inspected rather than trusted.
    """
    if not sibs:
        return "ALONE", None, "no sibling divisions -- cannot separate"

    sib_med = statistics.median(sibs)

    if abs(own) < _FLAT:
        return "FLAT", sib_med, "this division is barely displaced"

    share = sib_med / own          # 1.0 = siblings moved exactly as much

    if share >= 0.5:
        return "MEET_WIDE", sib_med, (
            f"siblings shifted {sib_med:+.1f}% vs this {own:+.1f}% -- the MEET "
            f"moved. DO NOT write a div-wide distance.")
    if share <= 0.25:
        return "DIV_SPECIFIC", sib_med, (
            f"siblings flat ({sib_med:+.1f}%) while this is {own:+.1f}% -- the "
            f"LABEL is the difference. Draft is safe to page-verify.")
    return "PARTIAL_SHARE", sib_med, (
        f"siblings shifted {sib_med:+.1f}% vs this {own:+.1f}% -- ambiguous")


# ================================================================== #
# CHUNK 4 -- ORCHESTRATION
# ================================================================== #

def _measure(cur, table, uniform, sibmap):
    """Purpose : walk every UNIFORM division -> its verdict against its meet."""
    recs = []
    for i, ((meet, div), info) in enumerate(sorted(uniform.items()), 1):
        own = _medianGap(cur, table, meet, div)
        if own is None:
            continue

        sibs = []
        for other in sibmap.get(meet, []):
            if other == div:
                continue
            m = _medianGap(cur, table, meet, other)
            if m is not None:
                sibs.append(m)

        v, sib_med, note = _verdict(own, sibs)
        recs.append({"meet": meet, "div": div, "ov": info["ov"],
                     "verdict": info["verdict"], "own": own,
                     "sib_med": sib_med, "n_sibs": len(sibs),
                     "call": v, "note": note})
        if i % 25 == 0:
            print(f"    {i}/{len(uniform)}")
    return recs


def _report(recs):
    """Purpose : the census. This is the number that decides step 2."""
    census = {}
    for r in recs:
        census[r["call"]] = census.get(r["call"], 0) + 1

    print("\n" + "=" * 66)
    print(f"  {len(recs):,} UNIFORM divisions checked against their meet-mates\n")
    for k in sorted(census, key=lambda x: -census[x]):
        print(f"    {k:<16} {census[k]:>5,}")

    safe = census.get("DIV_SPECIFIC", 0)
    bad = census.get("MEET_WIDE", 0)
    print(f"\n  SAFE to draft a div-wide distance : {safe:,}")
    print(f"  MUST NOT (the meet moved)         : {bad:,}")
    if bad:
        print(f"\n  !! {bad:,} drafts would have written race-day conditions into\n"
              f"     _DISTANCE_OVERRIDES as a permanent fake course length.")


def _write(path, recs):
    with open(path, "w", encoding="utf-8") as f:
        f.write("meet\tdiv\tov\ttriage_verdict\town_med\tsibling_med\t"
                "n_siblings\tcall\tnote\n")
        for r in recs:
            sm = f"{r['sib_med']:+.1f}" if r["sib_med"] is not None else ""
            f.write(f"{r['meet']}\t{r['div']}\t{r['ov']}\t{r['verdict']}\t"
                    f"{r['own']:+.1f}\t{sm}\t{r['n_sibs']}\t{r['call']}\t"
                    f"{r['note']}\n")


def main():
    ap = argparse.ArgumentParser(
        description="Is the field displaced, or the whole meet? READ-ONLY.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--log", required=True, help="a triage_c<N>.log")
    ap.add_argument("--dir", default="scripts")
    args = ap.parse_args()

    uniform = _readUniform(args.log)
    print(f"UNIFORM divisions in {args.log}: {len(uniform):,}")
    if not uniform:
        sys.exit("none parsed -- wrong log?")

    meets = {m for m, _ in uniform}
    print(f"fetching sibling divisions for {len(meets):,} meets ...")

    table = _TABLE[args.sport]
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        sibmap = _siblingsOf(cur, table, meets)
        recs = _measure(cur, table, uniform, sibmap)
        conn.rollback()                       # read-only, always

    _report(recs)
    out = os.path.join(args.dir, f"siblings_{args.sport.lower()}.tsv")
    _write(out, recs)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()