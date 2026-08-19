# Project: xc-predictor
# File:    scripts/audit_snapper.py
# Purpose: SETTLE STEP 2 (the snapper root cause) by measurement, not theory.
#
#          triage_regressions._impliedDistance turns a division's median gap
#          into a drafted distance in two steps, and BOTH steps are suspect:
#
#            step 1 INVERSION -- gap% -> implied metres
#              A (current) : implied = ov * (1 + |g|/100)      [linear]
#              B (alt)     : implied = ov / (1 - |g|/100)      [reciprocal]
#              At g=-20%: A gives ov*1.20, B gives ov*1.25. For ov=6437
#              that is 7724 vs 8046.25 -- and 8046.7 (5 miles) is in _SNAPS.
#
#            step 2 SNAP -- implied metres -> nearest standard distance
#              abs  (current) : min |s - implied|   -> boundary = (a+b)/2
#              geom (alt)     : min |ln(s/implied)| -> boundary = sqrt(a*b)
#              sqrt(a*b) <= (a+b)/2 ALWAYS, so `abs` snaps DOWN inside that
#              band. That is the "picks the next-lower candidate" hypothesis.
#
#          This script scores all FOUR combinations against the source's own
#          stored distance -- evidence that does not depend on brackets, the
#          engine, or any override. Whichever combination reproduces stored
#          most often is the correct one. READ-ONLY: writes one TSV, touches
#          no tables and no corrections.
#
# USAGE
#   python scripts\audit_snapper.py --sport XC --log triage_c6.log
#
# READ: the win-rate table. A clear winner is a ONE-FUNCTION fix to
# triage_regressions._impliedDistance, not 400 hand edits.

import argparse
import math
import os
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool
# Reuse adjudicate's evidence fetchers so "stored" cannot drift between the
# two tools. Same source, same coercion, same NULL handling.
from adjudicate_regressions import _storedAnet, _storedTfrrs

# The snap table, imported (never copied) from the tool under audit.
from triage_regressions import _SNAPS

# A draft and stored within this are "the same answer" -- the snap table has
# near-duplicate entries (1600/1609, 8000/8046.7) and metadata rounds.
_HIT_TOL = 0.02


# ================================================================== #
# CHUNK 1 -- PARSE THE DRAFT LINES (they carry everything but stored)
# ================================================================== #

# triage_regressions writes each draft as:
#   (meet, div): 8000,  # was 6437; field med -20.1% -> implied ~7731
#                       # (next snap 8046.7). PAGE-VERIFY before pasting.
# So ov and med% are recoverable WITHOUT re-running triage.
_DRAFT_RE = re.compile(
    r"^\s*\((\d+),\s*(\d+)\):\s*([\d.]+),\s*#\s*was\s*([\d.]+);"
    r"\s*field\s*med\s*([-+][\d.]+)%")


def _readDrafts(path):
    """
    Purpose : pull (meet, div) -> {drafted, ov, med} from a triage Tee log.
    Arguments: path -- triage_c<N>.log.
    Output  : dict. Tee-Object writes UTF-16 on PS 5.1, so sniff the BOM
              rather than assume utf-8 (this is why findstr chokes on them).
    """
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else raw.decode("utf-8", "replace")

    out = {}
    for line in text.splitlines():
        m = _DRAFT_RE.match(line)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = {
                "drafted": float(m.group(3)),
                "ov": float(m.group(4)),
                "med": float(m.group(5)),
            }
    return out


# ================================================================== #
# CHUNK 2 -- THE FOUR CANDIDATE IMPLEMENTATIONS
# ================================================================== #

def _invertLinear(ov, med):
    """
    Purpose : the CURRENT inversion. A field rated |g|% slow is assumed to have
              run |g|% further.
    Arguments: ov -- current override metres; med -- median gap %, signed.
    Output  : implied metres.
    """
    return ov * (1.0 + abs(med) / 100.0) if med < 0 \
        else ov / (1.0 + med / 100.0)


def _invertReciprocal(ov, med):
    """
    Purpose : the ALTERNATIVE inversion. If the gap is a ratio of speed-like
              quantities, then rating_here/rating_own = (1 + g), and the
              distance ratio is the RECIPROCAL of the speed ratio -- so a -20%
              field ran 1/0.80 = 1.25x further, not 1.20x.
    Arguments: ov, med (as above).
    Output  : implied metres. Guarded against |med| >= 100 (division by zero).
    """
    g = med / 100.0
    if med < 0:
        denom = 1.0 + g          # med<0 -> 1-|g|
        return ov / denom if denom > 0.01 else ov * 100.0
    return ov * (1.0 + g)


def _snapAbs(implied):
    """
    Purpose : the CURRENT snap. Nearest by absolute metres.
    Arguments: implied metres.
    Output  : the chosen standard distance.
    Why wrong: the boundary between candidates a<b falls at (a+b)/2.
    """
    return min(_SNAPS, key=lambda s: abs(s - implied))


def _snapGeom(implied):
    """
    Purpose : the ALTERNATIVE snap. Nearest in LOG space -- i.e. by ratio.
    Arguments: implied metres.
    Output  : the chosen standard distance.
    Why right: a distance error is multiplicative (a 5k mislabelled 3k is off
              by a FACTOR, not by 2000 metres), so "nearest" must be measured
              as a ratio. The boundary lands at sqrt(a*b), which is BELOW
              (a+b)/2 -- exactly the band where `abs` snaps one step short.
    """
    return min(_SNAPS, key=lambda s: abs(math.log(s / implied)))


# The matrix under test. Order matters only for printing.
_COMBOS = (
    ("linear + abs   (CURRENT)", _invertLinear, _snapAbs),
    ("linear + geom", _invertLinear, _snapGeom),
    ("recip  + abs", _invertReciprocal, _snapAbs),
    ("recip  + geom", _invertReciprocal, _snapGeom),
)


# ================================================================== #
# CHUNK 3 -- SCORING AGAINST STORED
# ================================================================== #

def _isHit(candidate, stored_m):
    """
    Purpose : did this combination reproduce the source's own distance?
    Arguments: candidate metres; stored_m metres.
    Output  : bool. _HIT_TOL absorbs 8000-vs-8046.7 style near-duplicates.
    """
    if not stored_m or stored_m <= 0:
        return False
    return abs(candidate - stored_m) / stored_m <= _HIT_TOL


def _scoreCombo(drafts, stored, invert, snap):
    """
    Purpose : run one (inversion, snapper) pair over every draft that has a
              stored distance to check against.
    Arguments: drafts, stored dicts; invert/snap functions.
    Output  : (hits, total, list of per-division records).
    """
    hits, total, recs = 0, 0, []
    for key, d in drafts.items():
        s = stored.get(key)
        s_dist = s[0] if s else None
        if not s_dist or s_dist <= 0:
            continue
        implied = invert(d["ov"], d["med"])
        chosen = snap(implied)
        ok = _isHit(chosen, s_dist)
        hits += ok
        total += 1
        recs.append((key, d["ov"], d["med"], implied, chosen, s_dist, ok))
    return hits, total, recs


def _ratioHistogram(recs, width=12):
    """
    Purpose : where do this combination's answers sit relative to stored?
              A SPIKE at one ratio is a systematic bug; a spread is noise.
    Arguments: recs from _scoreCombo; width -- max bar characters.
    Output  : None (prints).
    """
    bins = {}
    for _, _, _, _, chosen, s_dist, _ in recs:
        pct = 100.0 * (chosen / s_dist - 1.0)
        bins[5 * round(pct / 5)] = bins.get(5 * round(pct / 5), 0) + 1
    if not bins:
        return
    peak = max(bins.values())
    print(f"      chosen vs stored (5% bins, {len(recs):,} divisions):")
    for b in sorted(bins):
        bar = "#" * max(1, round(width * bins[b] / peak))
        print(f"        {b:>+4}% {bins[b]:>6,} {bar}")


# ================================================================== #
# CHUNK 4 -- ORCHESTRATION
# ================================================================== #

def _fetchStored(sport, drafts):
    """
    Purpose : the ground truth -- the source's own recorded distance. Does not
              depend on brackets, the engine, or any override, which is the
              whole reason it can adjudicate them.
    Arguments: sport; drafts (for the key list).
    Output  : dict (meet, div) -> (distance, name, division).
    """
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        stored = _storedAnet(cur, [d for _, d in drafts])
        stored.update(_storedTfrrs(cur, [m for m, _ in drafts]))
        conn.rollback()                      # read-only, always
    return stored


def _writeDetail(path, recs):
    """
    Purpose : per-division evidence for the winning combination, so the verdict
              is auditable instead of a single win-rate to be taken on faith.
    Arguments: path; recs.
    Output  : None.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write("meet\tdiv\tov\tmed_pct\timplied\tchosen\tstored\thit\n")
        for (m, d), ov, med, implied, chosen, s_dist, ok in sorted(recs):
            f.write(f"{m}\t{d}\t{ov}\t{med:+.1f}\t{implied:.0f}\t{chosen}\t"
                    f"{s_dist}\t{int(ok)}\n")


def main():
    ap = argparse.ArgumentParser(
        description="Score all four snapper implementations against stored "
                    "metadata. READ-ONLY.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--log", required=True, help="a triage_c<N>.log with drafts")
    ap.add_argument("--dir", default="scripts")
    args = ap.parse_args()

    drafts = _readDrafts(args.log)
    print(f"parsed {len(drafts):,} drafted divisions from {args.log}")
    if not drafts:
        sys.exit("no drafts parsed -- wrong log, or triage drafted nothing.")

    stored = _fetchStored(args.sport, drafts)
    print(f"stored metadata for {sum(1 for k in drafts if k in stored):,}"
          f"/{len(drafts):,}\n")

    results = []
    for name, invert, snap in _COMBOS:
        hits, total, recs = _scoreCombo(drafts, stored, invert, snap)
        rate = hits / total if total else 0.0
        results.append((rate, name, hits, total, recs))
        print(f"  {name:<26} {hits:>5,}/{total:<5,} = {rate:>6.1%}")
        _ratioHistogram(recs)
        print()

    results.sort(key=lambda r: -r[0])
    best_rate, best_name, best_hits, best_total, best_recs = results[0]
    cur_rate = next(r[0] for r in results if "CURRENT" in r[1])

    print("=" * 62)
    print(f"  WINNER: {best_name}  ({best_rate:.1%} vs current {cur_rate:.1%})")
    if best_rate - cur_rate > 0.05:
        gained = round((best_rate - cur_rate) * best_total)
        print(f"  -> ONE FUNCTION. Fixing _impliedDistance recovers ~{gained:,}\n"
              f"     divisions that the current snapper drafts wrong.")
    else:
        print("  -> No combination clearly beats the current one. The drafts\n"
              "     are not the bottleneck; the snapper hypothesis is DEAD.\n"
              "     Do not spend more time here -- go to the z-cut.")
    print("=" * 62)

    out = os.path.join(args.dir, f"snapper_audit_{args.sport.lower()}.tsv")
    _writeDetail(out, best_recs)
    print(f"\nwrote {out}  (per-division detail for the winner)")


if __name__ == "__main__":
    main()