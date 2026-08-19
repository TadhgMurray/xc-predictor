# ======================================================================
# census_transitions.py -- how much of this merge did the pages cover?
# ======================================================================
#
# THE IDEA
# --------
# 1,082 REFUTED verdicts. Three pages were pulled by hand, and all three
# were 5000 -> 6000. So the question that decides whether this merge is
# evidence or extrapolation is:
#
#     how much of the 1,082 IS 5000 -> 6000?
#
#   ~800 of one transition  -> the pages cover the bulk. Ship it.
#   ~130 and a long tail    -> we page-checked 12% and applied 100%.
#
# A transition repeated across dozens of unrelated meets is ONE ERROR
# MADE MANY TIMES -- and one page settles all of them. A transition
# appearing 3 times is an unverified guess wearing the same clothes.
#
# Read-only. No DB. Parses the file adjudicate just wrote.
#
# ======================================================================

import argparse
import os
import re

# _NOTE_RE -- pull (proposed, stored) out of a generated line:
#   (140140, 582873): 6000,  # REFUTED: field (UNIFORM_NEG, iqr=3.6 < 10.4)
#                              moved as ONE factor against stored 5000; ...
# The value is before the comment; the stored number is inside it. Both in
# one pass so a line that half-matches contributes nothing.
_LINE_RE = re.compile(
    r"^\s*\((\d+),\s*(\d+)\):\s*([\d.]+),\s*#\s*(\w+):.*?against stored (\d+)")

# ln(6000/5000) = 18.2%; the three pages pulled 7/17 all said 6K.
_VERIFIED = {(5000, 6000)}


def _readTransitions(path):
    """
    Purpose : the generated overrides file -> [(stored, proposed, verdict)]
    Output  : list of tuples. Lines that do not match are skipped silently --
              AGREE notes carry no "against stored", and that is correct,
              not an error.
    """
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = _LINE_RE.match(line)
            if m:
                out.append((int(m.group(5)), round(float(m.group(3))),
                            m.group(4)))
    return out


def _countBy(rows, keyFn):
    """Tally. keyFn is a parameter so one counter serves several questions."""
    counts = {}
    for r in rows:
        k = keyFn(r)
        counts[k] = counts.get(k, 0) + 1
    return counts


def _report(counts, total):
    """Biggest first -- the clusters ARE the finding; the tail is the risk."""
    print("\nTRANSITIONS (stored -> proposed), biggest cluster first:")
    covered = 0
    for (a, b), n in sorted(counts.items(), key=lambda x: -x[1]):
        if n < 3:
            continue                       # the tail gets summarised below
        tag = "  <- PAGE-VERIFIED 7/17" if (a, b) in _VERIFIED else ""
        if (a, b) in _VERIFIED:
            covered += n
        print("  {:>8} -> {:<8} {:>5,}  ({:4.1f}%){}".format(
            a, b, n, n / total * 100.0, tag))

    tail = sum(n for k, n in counts.items() if n < 3)
    print("\n  singletons/pairs (<3 meets): {:,}  ({:.1f}%)  "
          "<- no cluster, no corroboration".format(tail, tail / total * 100.0))
    print("\n  PAGE-VERIFIED share: {:,}/{:,} = {:.1f}%".format(
        covered, total, covered / total * 100.0))


def main():
    ap = argparse.ArgumentParser(description="Price the REFUTED merge.")
    ap.add_argument("--file", default=os.path.join(
        "scripts", "adjudicated_overrides_xc.py"))
    args = ap.parse_args()

    rows = _readTransitions(args.file)
    if not rows:
        print("nothing parsed -- check the note format in _writeRepairs")
        return

    print("read {:,} verdicts carrying a stored value from {}".format(
        len(rows), args.file))
    _report(_countBy(rows, lambda r: (r[0], r[1])), len(rows))
    print("")


if __name__ == "__main__":
    main()