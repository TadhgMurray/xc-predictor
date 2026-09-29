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

⚠⚠ UNKNOWN LENGTH IS THE 400m REFERENCE, AND THAT REVERSES A DECISION MADE
   ON 2026-09-19 (owner, 2026-09-20: "track difficulty is not set to 0 for all
   outdoor 400m tracks"). It was not: the predicate demanded a positively-known
   length, a missing track_length is -- by this file's own words -- "the
   commonest case in the corpus", so most outdoor ovals were never in the
   reference class and were never pinned. The owner asked for ALL flat outdoor
   400m tracks at 0.0 and got a small minority of them.

   The old argument was "a cell admitted to the anchor on an assumption is
   pinned to 0.0 on an assumption". The answer is that THE ASSUMPTION IS
   ALREADY MADE, upstream, by the code that produces the very numbers being
   pinned: normalize_distance._resolveTrackLength returns
   REFERENCE_TRACK_LENGTH (400.0) for an unknown length, and its own diagram
   says "a row we have no geometry for -- passes through unchanged", i.e. it is
   normalised AS a flat 400m oval. So an unknown-length outdoor cell already
   carries a normalized_time on the 400m reference scale. Refusing it a pinned
   difficulty does not avoid the assumption; it just applies the assumption to
   the numerator and not the denominator, which is the inconsistency, not the
   protection.

   What is still refused is a STATED fact to the contrary: a length that is
   positively known and is not 400 (±3m), and a positively-known banked oval.
   Unknown means "the standard oval", exactly as it does one module upstream.

 ! AND IT IS A POLICY, NOT A REWRITE. XCP_GAUGE_UNKNOWN_LENGTH=strict restores
   the 2026-09-19 behaviour for a run that wants to price the difference, and
   the census prints BOTH counts so the cost is a number rather than an
   argument.
"""

import os

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


UNKNOWN_LENGTH_MODES = ("assume400", "strict")
UNKNOWN_LENGTH_DEFAULT = "assume400"


def unknownLengthMode():
    """"assume400" (an unrecorded length is the standard oval, the default and
    what normalize_distance already assumes) or "strict" (the fact or nothing,
    the 2026-09-19 behaviour)."""
    v = str(os.environ.get("XCP_GAUGE_UNKNOWN_LENGTH", "") or "").strip().lower()
    return v if v in UNKNOWN_LENGTH_MODES else UNKNOWN_LENGTH_DEFAULT


def _unknownCounts(unknown_is_400):
    if unknown_is_400 is None:
        return unknownLengthMode() == "assume400"
    return bool(unknown_is_400)


def isFlatOutdoor400(track_length, track_type=None, is_indoor=None,
                     unknown_is_400=None):
    """The reference class: a flat, outdoor, 400m oval.

    An unrecorded length counts as the reference oval (see the header);
    pass unknown_is_400=False for the strict reading. A length that is
    positively known and not 400 never counts. `is_indoor` may be None, 0/1,
    or a bool."""
    known = True
    if track_length is None:
        known = False
        length = FLAT_400
    else:
        try:
            length = float(track_length)
        except (TypeError, ValueError):
            known = False
            length = FLAT_400
    # ⚠ NaN IS UNKNOWN, AND abs(nan - 400) > 3 IS FALSE -- so without this
    #   line a NaN length PASSES every remaining test and joins the anchor.
    #   The pack stores unknown length as NaN, which is the commonest value in
    #   the corpus, so this was not a corner case: a test caught it before the
    #   pin ever ran. It is still handled explicitly here: NaN is UNKNOWN and
    #   takes the unknown branch, never the arithmetic one.
    if length != length:
        known = False
        length = FLAT_400
    # ★ SILENCE AND CORRUPTION ARE NOT THE SAME THING. None and NaN are the
    #   corpus saying nothing, and the policy above covers them. A length of 0
    #   or a negative one is a STATED value that cannot be a track, so it is
    #   refused in both modes -- reading it as "the standard oval" would let a
    #   broken row into the anchor on the strength of its brokenness.
    elif known and length <= 0:
        return False
    if not known:
        # ! THE ONLY PLACE THE POLICY APPLIES. Everything below is about a
        #   length the corpus actually states.
        if not _unknownCounts(unknown_is_400):
            return False
    elif abs(length - FLAT_400) > LENGTH_TOLERANCE_M:
        # ★ A STATED LENGTH THAT IS NOT 400 IS NEVER THE REFERENCE, whatever
        #   the policy. The policy is about silence, not about contradiction.
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
# INDOOR OVALS BY GEOMETRY
# ------------------------------------------------------------------ #

# ★★ ONE DEFINITION FOR THE MEASUREMENT AND THE ENGINE (owner, 2026-09-29:
#    "indoor difficulty is too easy generally"). scripts/indoor_outdoor_check.py
#    --by geometry measured indoor minus outdoor per class of oval and found
#    them far apart -- small flat ovals and the unrecorded (mostly high-school)
#    ones ~0.7-0.9% slower than a flat outdoor 400, flat 200s ~+0.2%, banked
#    200s ~-0.6% and oversized 300m+ ~-0.5%, i.e. FASTER. The engine now pins
#    each class on its own measured level (bracket_engine.INDOOR_MODES
#    "geometry"), and a level measured on one split of the ovals and applied
#    on another would be a number about nothing. So the split lives here, once,
#    and both import it.
#
# ! UNKNOWN LENGTH IS ITS OWN CLASS, NOT THE STANDARD OVAL. That is the
#   opposite of isFlatOutdoor400's assume400, and deliberately: there the
#   question is "is this the reference?" and normalize_distance has already
#   answered it for the numerator. Here the class IS the measurement, and an
#   assumption would put the answer into the question -- the unknowns measured
#   +0.5..+0.9%, nothing like a 200.
GEO_CLASSES = ("under 200m", "flat 200m", "banked 200m", "300m+", "unknown")
# ! THE BOUNDARIES ARE THE ONES THE 2026-09-29 MEASUREMENT WAS TAKEN WITH
#   (indoor_outdoor_check.geometryClass, moved here unchanged): below 190 m is
#   a sub-200 oval, 290 m and up the 300 m-and-longer family. Moving either
#   re-classes ovals, and then the levels file describes classes that no
#   longer exist -- re-measure (--write-levels) after any change here.
GEO_SMALL_BELOW_M = 190.0
GEO_LARGE_FROM_M = 290.0


def geometryClass(length, ttype):
    """One of GEO_CLASSES for an indoor track.

    ! A STATED LENGTH OF 0 OR LESS IS "unknown", not "under 200m": the same
      "silence and corruption" line isFlatOutdoor400 draws -- a length that
      cannot be a track says nothing about the track."""
    try:
        v = float(length)
    except (TypeError, ValueError):
        return "unknown"
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:
        return "unknown"
    if v >= GEO_LARGE_FROM_M:
        return "300m+"
    if v < GEO_SMALL_BELOW_M:
        return "under 200m"
    return "banked 200m" if isBanked(ttype) else "flat 200m"


def geometryClassIndex(track_length, track_type):
    """Per course key, the index into GEO_CLASSES (an int array). Like
    flatOutdoor400Mask: the scalar rule applied elementwise, never a second
    implementation."""
    import numpy as np
    n = len(track_length)
    out = np.zeros(n, dtype=np.int64)
    for i in range(n):
        tt = track_type[i] if track_type is not None else None
        out[i] = GEO_CLASSES.index(geometryClass(track_length[i], tt))
    return out


# ★ WHERE THE MEASURED LEVELS LIVE. Written by
#   scripts/indoor_outdoor_check.py --by geometry --write-levels, read by the
#   bracket engine under indoor_mode="geometry" and by run_joint for the
#   joint solve's single indoor level. The file is the measurement; nothing
#   in code carries a copy of the numbers.
INDOOR_LEVELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "indoor_geometry_levels.json")


def loadIndoorLevels(path=None):
    """The measured levels file as a dict, with every class present and every
    level a finite number. RAISES when it is missing or incomplete: the
    geometry mode has no default to fall back on, and a run that silently
    used one would be a different model reporting itself as this one (the
    gauge=flat400 lesson, 2026-09-20)."""
    import json
    if isinstance(path, dict):
        doc = path
        where = "<dict>"
    else:
        where = path or INDOOR_LEVELS_FILE
        if not os.path.exists(where):
            raise FileNotFoundError(
                f"indoor geometry levels: no file at {where}. Measure them "
                f"first: scripts/indoor_outdoor_check.py --by geometry "
                f"--write-levels")
        with open(where) as fh:
            doc = json.load(fh)
    classes = doc.get("classes") or {}
    missing = [c for c in GEO_CLASSES if c not in classes]
    if missing:
        raise ValueError(f"indoor geometry levels ({where}) lack "
                         f"{', '.join(missing)}; rerun --write-levels")
    for c in GEO_CLASSES:
        lv = classes[c].get("level") if isinstance(classes[c], dict) else None
        try:
            lv = float(lv)
        except (TypeError, ValueError):
            lv = float("nan")
        if lv != lv:
            raise ValueError(f"indoor geometry levels ({where}): class {c!r} "
                             f"has no finite level")
    doc = dict(doc)
    doc["_path"] = where
    return doc


def classLevels(doc):
    """(level per GEO_CLASSES index), as floats, from loadIndoorLevels."""
    return [float(doc["classes"][c]["level"]) for c in GEO_CLASSES]


def jointIndoorLevel(doc):
    """★ THE JOINT SOLVE'S ONE INDOOR NUMBER UNDER THE GEOMETRY MODE: the
    class levels weighted by the indoor ROWS each class holds in the pack
    (n_rows, counted by the writer over the whole pack). The joint solve's
    indoor term is one coefficient over every indoor row, so a row-weighted
    mean is the number that term would have to be for the two engines to
    agree on average. Falls back to n_pairs weights for a file written
    before n_rows existed, and says so in the returned tuple."""
    lv = classLevels(doc)
    w = [float(doc["classes"][c].get("n_rows") or 0) for c in GEO_CLASSES]
    how = "rows"
    if sum(w) <= 0:
        w = [float(doc["classes"][c].get("n_pairs") or 0) for c in GEO_CLASSES]
        how = "pairs"
    if sum(w) <= 0:
        raise ValueError("indoor geometry levels carry no n_rows or n_pairs "
                         "to weight the joint level by; rerun --write-levels")
    return sum(a * b for a, b in zip(lv, w)) / sum(w), how


# ------------------------------------------------------------------ #
# THE SAME QUESTION, ON WHOLE ARRAYS
# ------------------------------------------------------------------ #

# ! A SECOND IMPLEMENTATION IS THE FAILURE THIS MODULE EXISTS TO PREVENT, so
#   the vector form is the scalar one applied elementwise rather than a
#   re-derivation in numpy. The arrays here are per COURSE KEY -- tens of
#   thousands of entries, not per row -- so the loop costs nothing, and the
#   test can assert the two agree.
def flatOutdoor400Mask(track_length, track_type, is_indoor,
                       unknown_is_400=None):
    """A bool array, one entry per course key."""
    import numpy as np
    # ! RESOLVED ONCE, not per key: the policy cannot change mid-array, and
    #   reading the environment tens of thousands of times would be the only
    #   cost this loop has.
    unknown_is_400 = _unknownCounts(unknown_is_400)
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
        out[i] = isFlatOutdoor400(ln, tt, ind, unknown_is_400)
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
    # ★ BOTH READINGS, ALWAYS. The policy (unknown length = the standard oval)
    #   is the one number the pin uses; the strict one is what 2026-09-19
    #   would have anchored on. Printing them together is what makes the
    #   choice a measurement instead of an argument.
    ref_strict = flatOutdoor400Mask(track_length, track_type, is_indoor,
                                    unknown_is_400=False)
    have = np.array([(ln is not None and ln == ln) for ln in track_length])
    w = (np.ones(len(keys)) if rows_per_key is None
         else np.asarray(rows_per_key, dtype=np.float64))
    out = {
        "n_keys": len(keys),
        "n_tf_outdoor": int(tf_out.sum()),
        "n_reference": int((ref & tf_out).sum()),
        "n_reference_strict": int((ref_strict & tf_out).sum()),
        "rows_reference_strict": float(w[ref_strict & tf_out].sum()),
        "mode": unknownLengthMode(),
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
    print(f"    {c['n_unknown_length']:,} carry no track_length at all")
    # ★★ WHAT THE POLICY IS WORTH, IN CELLS. The gap between these two lines
    #    is the answer to "track difficulty is not set to 0 for all outdoor
    #    400m tracks": every cell in it is an outdoor oval that IS normalised
    #    as a 400m track and, under strict, was not pinned as one.
    print(f"    unknown-length policy: {c.get('mode', '?')}  "
          f"(XCP_GAUGE_UNKNOWN_LENGTH=strict for the fact-only reading)")
    print(f"      assume400 -> {c['n_reference']:,} reference keys")
    print(f"      strict    -> {c.get('n_reference_strict', 0):,} reference keys")
    gained = c['n_reference'] - c.get('n_reference_strict', 0)
    if gained:
        print(f"      the policy pins {gained:,} more outdoor "
              f"{'cell' if gained == 1 else 'cells'} at 0.0")
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
