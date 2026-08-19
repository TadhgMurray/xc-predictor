# ======================================================================
# census_worklist.py -- how big is the REFUTED prize, actually?
# ======================================================================
#
# THE IDEA
# --------
# adjudicate sent 389 of 500 divisions to the human queue. "Inconclusive"
# is not one thing. Reading _verdict backwards, a division reaches
# WORKLIST through exactly two doors:
#
#   A. stored FAILS physics        -> rules 1,2,4 all require s_ok
#   B. stored ~= effective         -> rule 4 needs them to DISAGREE,
#      and the draft disagrees        rule 1 needs the draft to AGREE
#
# B is the REFUTED case: the ruler and the metadata tell the same story,
# and the field calls both of them liars. Nobody promotes the draft, so
# it dies in a tsv. That is the population 11.1 was built for and the
# one the 7/16 stale-parse repair does NOT reach.
#
# This script counts A vs B. It decides whether a REFUTED rule is worth
# building or whether the prize is 12 divisions and we should move on.
#
# ★ IT IS ALSO A FALSIFICATION TEST. If my reading of _verdict is right,
#   one bucket below is IMPOSSIBLE and must print 0. If it does not, the
#   model is wrong and nothing here should be trusted. An instrument that
#   cannot come back "you are wrong" is not an instrument.
#
# Read-only. No DB. Reads one tsv.
#
# ======================================================================

import argparse
import os


# ----------------------------------------------------------------------
# TUNABLES -- copied from adjudicate_regressions.py
# ----------------------------------------------------------------------
# ⚠ HAND-COPIED, and that is a known drift hazard (14: event_parse's
#   _MIN/_MAX are the same sin). Kept as a copy on purpose: this script is
#   an AUDIT of adjudicate's behaviour, and a verifier that imports the
#   thing it audits certifies nothing. Mirror, not import.
#   !! KEEP IN SYNC with adjudicate_regressions._SAME_TOL / _AGREE_TOL !!
_SAME_TOL = 0.02        # two candidates this close are "the same answer"
_AGREE_TOL = 0.08       # stored-vs-draft agreement gate


# ----------------------------------------------------------------------
# 1. READING THE FILE
# ----------------------------------------------------------------------
# Purpose : the worklist tsv -> a list of dicts, one per division
# Arguments:
#   path -- str, scripts/adjudication_worklist_xc.tsv
# Output  : list of dict, keyed by the header's column names
#
# Written by hand rather than with csv.DictReader because the file has
# free-text columns (meet_name, reason) that can contain anything except
# a tab. split("\t") is exactly right and cannot be confused by quotes
# or commas the way a csv dialect can.
# ----------------------------------------------------------------------
def _readTsv(path):
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        # zip() stops at the shorter of the two, so a short/ragged line
        # yields a partial dict rather than raising. _toFloat then reads
        # the missing key as None, which is the honest answer.
        rows.append(dict(zip(header, parts)))
    return rows


# ----------------------------------------------------------------------
# 2. PARSING ONE CELL
# ----------------------------------------------------------------------
# Purpose : a tsv cell -> float, or None
# Arguments:
#   text -- str or None. May be "", "None", or "5000.0".
# Output  : float | None
#
# The writer f-strings Python values straight in, so a genuine None
# arrives as the four characters N-o-n-e. Treating that as a float would
# raise; treating it as 0.0 would be worse -- it would silently become a
# distance. NULL IS BETTER THAN STALE.
# ----------------------------------------------------------------------
def _toFloat(text):
    if text is None:
        return None
    text = text.strip()
    if text in ("", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# 3. RELATIVE COMPARISON
# ----------------------------------------------------------------------
# Purpose : are two distances the same, within a tolerance?
# Arguments:
#   a, b -- float | None
#   tol  -- float, a FRACTION (0.02 = 2%), matching adjudicate's constants
# Output  : bool. Unknown compares as "not the same" -- absence is not
#           agreement.
#
# Relative, not absolute: 50m is 6% at 800m and 0.6% at 8000m. Absolute
# metres is the bug that pinned the snapper at 25.7%.
# ----------------------------------------------------------------------
def _same(a, b, tol):
    if a is None or b is None or not b:
        return False
    return abs(a - b) / abs(b) <= tol


# ----------------------------------------------------------------------
# 4. THE BUCKETS -- one row, one door
# ----------------------------------------------------------------------
# Purpose : which WORKLIST door did this division come through?
# Arguments:
#   row -- dict from _readTsv
# Output  : str, the bucket name
#
# Order matters and encodes the claim. Read it as a decision list:
#
#   no draft                          -> the inverter had nothing to say
#   stored disagrees with effective   -> rule 4 (AMBIGUOUS) should have
#                                        caught this... unless stored
#                                        FAILED PHYSICS. So this bucket
#                                        IS the stored-fails-physics set.
#   stored ~= effective, draft far    -> ★ REFUTED CANDIDATE
#   stored ~= effective, draft close  -> ★ IMPOSSIBLE: rule 1 would have
#                                        fired AGREE. Must print 0.
# ----------------------------------------------------------------------
def _bucket(row):
    eff = _toFloat(row.get("effective"))
    stored = _toFloat(row.get("stored"))
    draft = _toFloat(row.get("draft"))

    if draft is None:
        return "NO_DRAFT (inverter silent)"

    if not _same(stored, eff, _SAME_TOL):
        return "STORED != EFFECTIVE (stored likely failed physics)"

    if not _same(draft, stored, _AGREE_TOL):
        return "REFUTED CANDIDATE (stored==ruler, field+draft disagree)"

    return "!! IMPOSSIBLE -- rule 1 should have fired AGREE"


# ----------------------------------------------------------------------
# 5. COUNTING
# ----------------------------------------------------------------------
# Purpose : tally rows into a {key: count} dict
# Arguments:
#   rows  -- list of dict
#   keyFn -- a function row -> str, the bucket name
# Output  : dict str -> int
#
# keyFn is a parameter so the same counter serves both the bucket tally
# and the by-category tally. One counter, two questions.
# ----------------------------------------------------------------------
def _countBy(rows, keyFn):
    counts = {}
    for row in rows:
        k = keyFn(row)
        counts[k] = counts.get(k, 0) + 1
    return counts


# ----------------------------------------------------------------------
# 6. REPORTING
# ----------------------------------------------------------------------
def _printTable(title, counts, total):
    print("\n" + title)
    for k in sorted(counts, key=lambda x: -counts[x]):
        pct = counts[k] / total * 100.0 if total else 0.0
        print("  {:<52} {:>5,}  ({:4.1f}%)".format(k, counts[k], pct))


# ----------------------------------------------------------------------
# 7. THE SHAPE GATE -- of the refuted, how many are actionable?
# ----------------------------------------------------------------------
# Purpose : a REFUTED candidate is only auto-fixable if the field moved
#           as ONE factor. Split them by triage's category so we can see
#           how many would survive a shape gate before we build one.
# Arguments:
#   rows -- list of dict, ALREADY filtered to refuted candidates
# Output  : None (prints)
#
# UNIFORM_*  -> the whole field slid. A label's signature.
# PARTIAL /
# BIMODAL    -> more than one population in the bucket. A division-wide
#               edit is FORBIDDEN here (0.10, the 26785 rule) no matter
#               how convincing the median looks.
# ----------------------------------------------------------------------
def _reportShapes(rows):
    counts = _countBy(rows, lambda r: r.get("category", "?"))
    total = len(rows)
    _printTable("  of those, by field SHAPE:", counts, total)

    uniform = sum(v for k, v in counts.items() if k.startswith("UNIFORM"))
    print("\n  -> {:,} are UNIFORM: the field moved as one factor.".format(uniform))
    print("     THAT is the auto-lane's ceiling. The rest need the splitter\n"
          "     or a human -- their buckets hold more than one race.")


# ----------------------------------------------------------------------
# 8. MAIN
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Size the REFUTED population.")
    ap.add_argument("--tsv",
                    default=os.path.join("scripts",
                                         "adjudication_worklist_xc.tsv"))
    args = ap.parse_args()

    rows = _readTsv(args.tsv)
    print("read {:,} worklist rows from {}".format(len(rows), args.tsv))

    buckets = _countBy(rows, _bucket)
    _printTable("WHY EACH DIVISION LANDED IN THE QUEUE:", buckets, len(rows))

    # THE FALSIFICATION CHECK. Loud, and it gates.
    impossible = sum(v for k, v in buckets.items() if k.startswith("!!"))
    if impossible:
        print("\n  !!! {:,} rows are in a bucket that CANNOT exist if my "
              "reading of\n      _verdict is correct. The model is wrong. "
              "Do not act on this.".format(impossible))
        return

    refuted = [r for r in rows
               if _bucket(r).startswith("REFUTED")]
    if refuted:
        _reportShapes(refuted)

    print("")


if __name__ == "__main__":
    main()