#!/usr/bin/env python3
"""
xc_reference.py -- the cross-country courses that ARE the zero.

    python engine/xc_reference.py --suggest          # pick from this list
    python engine/xc_reference.py                    # what is pinned now

★★ WHY THIS EXISTS, AND WHY IT IS A HAND-PICKED LIST (owner, 2026-09-20).

   Track has an absolute anchor: a flat outdoor 400m oval IS 0.0, by physical
   definition, and indoor sits +0.3% off it by assertion. Cross country had
   none. The bracket engine groups cells by (sport, era) and pins each group's
   VOTE-WEIGHTED MEAN to zero, so XC's zero is its own mean -- a see-saw. If
   California's courses are heavily raced and genuinely fast, holding the mean
   at zero MATHEMATICALLY REQUIRES everything else to be negative. That is the
   owner's standing complaint, and it is a property of the constraint rather
   than of the courses.

⚠ AND THE TRACK ANCHOR CANNOT REACH XC. That was measured, not assumed
  (engine/diag_xc_track_bridge.py, run 2026-09-20 on a 5% athlete sample):

      window   bridged XC rows   share   via INDOOR   share
          21             7,350    0.4%        3,607    0.2%
          30            14,152    0.8%        8,560    0.5%
          45            33,695    1.9%       24,416    1.4%
          60            62,129    3.5%       48,906    2.7%

  At the bracket window the indoor bridge reaches 0.2-0.5% of XC rows, 2.7% at
  60 days. That is THIN, and thin is a number rather than a verdict: a small
  set of athletes can still carry a level if they are well spread and race a
  lot, which is why merging the (sport, era) groups is available as
  --gauge-scope merge and is SCORED rather than argued about
  (scripts/bracket_holdout.py --gauge-scope merge --compare). What settles it
  is held-out error on the same rows, not the size of the bridge.

  What the number DOES say is that merge cannot be the default on its own
  evidence, and that this file is the route that does not depend on the bridge
  at all.

★ SO THE ANCHOR HAS TO BE INSIDE XC, AND IT CANNOT BE DERIVED. There is no
  terrain data: course_canonical holds a name and a GPS point, venue_elevation
  holds ONE elevation for the altitude term, and nothing anywhere records
  elevation gain, surface or footing. A predicate like track_geometry's
  "flat, outdoor, 400m" cannot be written for a golf course. So the reference
  class is NAMED, the way venue_geometry_overrides names its five venues --
  by a human who knows the sport.

! WHAT PINNING ONE OF THESE MEANS, EXACTLY. `z` reaches the bracket engine
  with mu already applied, so the XC-vs-track LEVEL is already set by
  definition (0.0583 = ln(1.06)). Pinning a course at 0.0 therefore does NOT
  claim it is as fast as a track. It claims it sits exactly where mu says the
  average cross-country race sits -- i.e. this course is the standard one, and
  every other course is measured against IT instead of against the mean of all
  of them. That is the whole of the change: the constraint stops being "the
  average XC course is zero".

⚠ SO THE CHOICE IS A JUDGEMENT AND IT MOVES EVERY OTHER COURSE. Pick courses
  that are genuinely ordinary -- well-raced, unremarkable footing, honest
  distance -- not the fastest and not the hardest. A fast set pushes the whole
  corpus positive; a hard set pushes it negative. Pick several, in different
  states, so one venue's bad year cannot move the zero.

! EMPTY IS SAFE AND IS THE DEFAULT. With nothing named, the engine falls back
  to the whole-group mean exactly as before and SAYS SO on every solve, so a
  half-applied state is impossible and nobody is left believing XC is anchored
  when it is not.

  To fill it: run --suggest, pick from the list, and add entries here. The key
  is the canonical_id (stable); the name is a comment because names move.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ★ {canonical_id: asserted difficulty in log-time}. 0.0 means "this course IS
#   the zero". A non-zero value says "this course is N% off the standard", for
#   a venue the owner wants in the anchor but does not call ordinary.
#
# ⚠ EMPTY UNTIL THE OWNER NAMES THEM. See the header: an unnamed reference
#   class is not a safe default to guess at, because whatever is picked moves
#   every other course in the corpus.
#
#   Example of the shape, once there are entries:
#       12345: 0.0,      # Some Park, OH -- 41k rows, 380 races, 5000m
XC_REFERENCE = {}


def _venuePart(key):
    """'XC:12345:d5000@e3' -> '12345'. None for a non-XC key."""
    k = str(key).partition("@e")[0]
    if not k.startswith("XC:"):
        return None
    rest = k[3:]
    venue, tag, _dist = rest.rpartition(":d")
    return venue if tag else rest


def canonicalId(key):
    """The canonical_id an XC course key names, or None.

    ! ONLY THE NUMERIC FORM IS AN IDENTITY. A key can also carry 'name:<x>' or
      a raw string when course_canonical could not place it
      (speed_ratings_db.venueKeyParts); those are not stable across rebuilds,
      so they can never be the anchor.
    """
    v = _venuePart(key)
    if v is None or not v.isdigit():
        return None
    return int(v)


def referenceMask(course_keys, table=None):
    """(bool mask, value array) over course keys -- the XC cells that ARE the
    zero, and what each is pinned at."""
    import numpy as np
    ref = XC_REFERENCE if table is None else table
    n = len(course_keys)
    mask = np.zeros(n, dtype=bool)
    val = np.zeros(n, dtype=np.float64)
    if not ref:
        return mask, val
    for i, k in enumerate(course_keys):
        cid = canonicalId(k)
        if cid is not None and cid in ref:
            mask[i] = True
            val[i] = float(ref[cid])
    return mask, val


def suggest(cols, show=40, min_races=0):
    """★ THE LIST THE OWNER PICKS FROM. Ranked by ROWS, because the gauge is
    vote-weighted and a reference nobody races cannot hold a zero.

    ! NAMES ARE NOT IN THE PACK, so this prints ids and asks the database for
      names only if it can. An id alone is enough to pin; the name is for the
      human doing the picking.
    """
    import numpy as np
    keys = [str(k) for k in cols["course_keys"]]
    course = np.asarray(cols["course"])
    n_key = len(keys)
    rows = np.bincount(course[course >= 0], minlength=n_key)
    cid = np.array([canonicalId(k) or -1 for k in keys])

    # ★ PER VENUE, NOT PER CELL. One course is several keys (a 5000 and a
    #   5-mile at the same park), and the owner is naming the COURSE.
    by_venue = {}
    for i, k in enumerate(keys):
        if cid[i] < 0:
            continue
        slot = by_venue.setdefault(int(cid[i]), {"rows": 0, "keys": []})
        slot["rows"] += int(rows[i])
        slot["keys"].append(k)

    names = {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT canonical_id, min(canonical_name), sum(n_rows)
                           FROM course_canonical GROUP BY 1""")
            for c, nm, _nr in cur.fetchall():
                names[int(c)] = str(nm)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  (no course names: {type(exc).__name__}; ids only)")

    order = sorted(by_venue.items(), key=lambda kv: -kv[1]["rows"])
    print(f"\n[xc-ref] the {min(show, len(order))} most-raced XC courses "
          f"({len(order):,} with a canonical id)\n")
    print(f"  {'canonical_id':>13} {'rows':>12} {'keys':>5}  name")
    for c, slot in order[:show]:
        print(f"  {c:>13} {slot['rows']:>12,} {len(slot['keys']):>5}  "
              f"{names.get(c, '(unnamed)')}")
    print("\n  Pick SEVERAL, in different states, that you would call "
          "ORDINARY -- not the\n  fastest and not the hardest. Then add them "
          "to XC_REFERENCE in this file:\n")
    for c, slot in order[:3]:
        print(f"      {c}: 0.0,    # {names.get(c, '(unnamed)')} "
              f"-- {slot['rows']:,} rows")
    print("\n  ⚠ whatever you pick becomes the zero, so every other course "
          "moves relative\n    to it. That is the point, and it is why this "
          "is a judgement and not a fit.")


def describe():
    """What is pinned right now, for a solve log and for a human."""
    if not XC_REFERENCE:
        print("[xc-ref] NOTHING PINNED. Cross country has no absolute anchor: "
              "its zero is\n         the vote-weighted mean of its own cells, "
              "which is the see-saw that\n         forces courses negative "
              "when the heavily-raced ones read high.\n         Run "
              "engine/xc_reference.py --suggest and name some courses.")
        return
    print(f"[xc-ref] {len(XC_REFERENCE)} cross-country courses ARE the zero:")
    for c, v in sorted(XC_REFERENCE.items()):
        print(f"           canonical_id {c:>8}  pinned at {100.0 * v:+.2f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suggest", action="store_true",
                    help="rank XC courses by how much they are raced, to pick from")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--pack", default=None)
    args = ap.parse_args()

    describe()
    if not args.suggest:
        return
    import bracket as bk
    import run_joint as rj
    pack = args.pack or rj.buildParser().get_default("pack")
    cols, _npz = bk.loadInputs(pack, None)
    suggest(cols, args.show)


if __name__ == "__main__":
    main()
