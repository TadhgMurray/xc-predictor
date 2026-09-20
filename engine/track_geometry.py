#!/usr/bin/env python3
"""
track_geometry.py -- the reference class, defined once.

    from track_geometry import isFlatOutdoor400, FLAT_400

★ WHY THIS FILE EXISTS (owner, 2026-09-19: "we're gonna make all falt outdoor
  400m tracks 0.0"). The bracket cell key is TF:loc:<id>:in|:out -- location and
  surface, NO track length and NO banking -- so "all flat outdoor 400m tracks"
  could not be addressed at all, and what shipped on 2026-09-18 was a mean-pin
  over every outdoor cell instead of the owner's design.

  The data was never missing. meets_tf carries track_type, track_length and
  is_indoor per meet; geometry_resolver reads them and venue_geometry_overrides
  corrects the venues that are known-wrong. What was missing was a way to ask
  the question on a COURSE KEY, which is the grain the engine pins.

★ ONE PREDICATE, ONE PLACE. The pin, the census and every diagnostic have to
  agree on what a flat outdoor 400 is, or the anchor means something different
  in each of them. This module is pure -- no DB, no numpy import at module
  level -- so anything can import it for free, exactly like
  venue_geometry_overrides.

! WHAT COUNTS, AND WHY EACH CLAUSE IS THERE:
    length   rounds to 400m. A 400m oval is the reference distance every other
             geometry correction in normalize_distance is measured against, so
             the class that anchors difficulty has to be the same one.
    flat     NOT banked. Banking is a real, measured speed effect and a banked
             oval is not the reference surface. normalize_distance keys the
             banking correction off the literal string "Banked", so this does
             too -- one spelling, one place.
    outdoor  is_indoor false. Indoor gets a CENTRE of its own (+0.3%), not a
             place in the reference class.

⚠ UNKNOWN IS NOT FLAT. A missing track_length is the commonest case in the
  corpus and normalize_distance treats it as the 400m reference so it can
  normalise the time at all. That is right for normalising and wrong here: a
  cell admitted to the anchor on an assumption is pinned to 0.0 on an
  assumption. So this predicate demands the fact and returns False without it,
  and the census prints how many cells that costs.
"""

# The 400m oval, with the tolerance the corpus actually needs: anet and tfrrs
# both carry 400, 400.0 and the odd 402 (a quarter-mile, 402.34m, labelled as
# the oval it is). A metric mile track at 402m IS the reference oval.
FLAT_400 = 400.0
LENGTH_TOLERANCE_M = 3.0

# ! ONLY THE POSITIVE CLAIM, the same rule normalize_distance applies at
#   line 1201 ("only positively-known banked"). Anything else -- "Flat",
#   "Oversized Flat", None, "" -- is not a banked oval.
BANKED = "Banked"


def isBanked(track_type):
    """True only for the positive claim, matching normalize_distance."""
    return str(track_type or "").strip() == BANKED


def isFlatOutdoor400(track_length, track_type=None, is_indoor=None):
    """The reference class: a flat, outdoor, 400m oval.

    Returns False for anything whose geometry is unknown -- see the note in
    the header. `is_indoor` may be None, 0/1, or a bool."""
    if track_length is None:
        return False
    try:
        length = float(track_length)
    except (TypeError, ValueError):
        return False
    # ⚠ NaN IS UNKNOWN, AND abs(nan - 400) > 3 IS FALSE -- so without this
    #   line a NaN length PASSES every remaining test and joins the anchor.
    #   The pack stores unknown length as NaN, which is the commonest value in
    #   the corpus, so this was not a corner case: a test caught it before the
    #   pin ever ran.
    if length != length:
        return False
    if length <= 0:
        return False
    if abs(length - FLAT_400) > LENGTH_TOLERANCE_M:
        return False
    if isBanked(track_type):
        return False
    if is_indoor is None:
        # ! NOT ASSUMED OUTDOOR. An unstated surface is unknown geometry, and
        #   an indoor 400 (they exist, and they are fast) must not be admitted
        #   to the outdoor anchor by omission.
        return False
    try:
        if int(is_indoor) != 0:
            return False
    except (TypeError, ValueError):
        return False
    return True


# ------------------------------------------------------------------ #
# THE SAME QUESTION, ON WHOLE ARRAYS
# ------------------------------------------------------------------ #

# ! A SECOND IMPLEMENTATION IS THE FAILURE THIS MODULE EXISTS TO PREVENT, so
#   the vector form is the scalar one applied elementwise rather than a
#   re-derivation in numpy. The arrays here are per COURSE KEY -- tens of
#   thousands of entries, not per row -- so the loop costs nothing, and the
#   test can assert the two agree.
def flatOutdoor400Mask(track_length, track_type, is_indoor):
    """A bool array, one entry per course key."""
    import numpy as np
    n = len(track_length)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        tt = track_type[i] if track_type is not None else None
        ind = is_indoor[i] if is_indoor is not None else None
        ln = track_length[i]
        # NaN is unknown, and float('nan') is not None
        if ln is not None and ln != ln:
            ln = None
        if ind is not None and isinstance(ind, float) and ind != ind:
            ind = None
        out[i] = isFlatOutdoor400(ln, tt, ind)
    return out


def geometryCensus(course_keys, track_length, track_type, is_indoor,
                   rows_per_key=None):
    """★ THE CENSUS THE PIN IS NOT ALLOWED TO SKIP. A reference class covering
    few cells is a worse anchor than the mean it replaces, and that has to be
    known BEFORE the pin is written rather than after a solve."""
    import numpy as np
    keys = [str(k) for k in course_keys]
    tf_out = np.array([k.startswith("TF:loc:") and not k.endswith(":in")
                       and ":in@" not in k for k in keys])
    ref = flatOutdoor400Mask(track_length, track_type, is_indoor)
    have = np.array([(ln is not None and ln == ln) for ln in track_length])
    w = (np.ones(len(keys)) if rows_per_key is None
         else np.asarray(rows_per_key, dtype=np.float64))
    out = {
        "n_keys": len(keys),
        "n_tf_outdoor": int(tf_out.sum()),
        "n_reference": int((ref & tf_out).sum()),
        "n_unknown_length": int((tf_out & ~have).sum()),
        "rows_tf_outdoor": float(w[tf_out].sum()),
        "rows_reference": float(w[ref & tf_out].sum()),
    }
    out["share_cells"] = (out["n_reference"] / out["n_tf_outdoor"]
                          if out["n_tf_outdoor"] else 0.0)
    out["share_rows"] = (out["rows_reference"] / out["rows_tf_outdoor"]
                         if out["rows_tf_outdoor"] else 0.0)
    return out


def printCensus(c):
    print("\n[geometry] the reference class: flat, outdoor, 400m")
    print(f"    {c['n_tf_outdoor']:,} outdoor TF course keys")
    print(f"    {c['n_reference']:,} of them qualify "
          f"({c['share_cells'] * 100:.1f}% of keys, "
          f"{c['share_rows'] * 100:.1f}% of rows)")
    print(f"    {c['n_unknown_length']:,} carry no track_length at all "
          f"-- unknown is NOT flat, see the header")
    if c["share_rows"] < 0.10:
        print("    ⚠ THIN. Under a tenth of outdoor rows sit in the reference "
              "class, so pinning it\n      anchors the corpus on a small "
              "subset. Read this before trusting the pin.")


def main():
    """The census, from the pack. Read-only."""
    import argparse
    import os
    import sys
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import numpy as np
    import bracket as bk
    import run_joint as rj

    ap = argparse.ArgumentParser(description="census the reference class")
    ap.add_argument("--pack", default=rj.buildParser().get_default("pack"))
    ap.add_argument("--show", type=int, default=15,
                    help="print this many qualifying keys")
    args = ap.parse_args()

    cols, _npz = bk.loadInputs(args.pack, None)
    if "track_length" not in cols:
        raise SystemExit(
            "the pack carries no track geometry. Rebuild it: the columns come "
            "from speed_ratings.attachCourseGeometry, which runs beside "
            "attachCourseCoords at pack time.")
    course = np.asarray(cols["course"])
    n_key = len(cols["course_keys"])
    rows_per_key = np.bincount(course[course >= 0], minlength=n_key)
    c = geometryCensus(cols["course_keys"], cols["track_length"],
                       cols["track_type"], cols["track_indoor"], rows_per_key)
    printCensus(c)
    ref = flatOutdoor400Mask(cols["track_length"], cols["track_type"],
                             cols["track_indoor"])
    idx = np.argsort(-rows_per_key)
    shown = 0
    print(f"\n    the {args.show} biggest qualifying keys:")
    for i in idx:
        if not ref[i]:
            continue
        print(f"      {str(cols['course_keys'][i]):<34} "
              f"{rows_per_key[i]:>9,} rows  "
              f"len={cols['track_length'][i]} type={cols['track_type'][i]!r}")
        shown += 1
        if shown >= args.show:
            break


if __name__ == "__main__":
    main()
