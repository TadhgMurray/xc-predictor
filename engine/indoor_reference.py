#!/usr/bin/env python3
"""
indoor_reference.py -- indoor ovals asserted onto the absolute scale.

    python engine/indoor_reference.py --find "boston university"
    python engine/indoor_reference.py                 # what is pinned now

★★ WHY (owner, 2026-09-20): "I honestly want you to do it based on the
   xc -> indoor (specifically BU which is as fast as a flat 400m so it works)
   conversions".

   Track has an absolute anchor -- a flat outdoor 400m oval IS 0.0. Cross
   country has none, and the measurement said the generic XC-to-indoor bridge
   is thin: 0.2-0.5% of XC rows at the bracket window
   (diag_xc_track_bridge.py).

   The owner's route is different and better. It does not need the bridge to
   be WIDE, it needs one end of it to be KNOWN. Boston University's oval is
   the fastest banked 200 in the country and races there run at flat-400
   pace -- so BU is asserted onto the reference class directly, at 0.0. Every
   athlete who races BU is then standing on the absolute scale, and cross
   country is one athlete-season away from it instead of floating.

! SO THIS IS AN ASSERTION, EXACTLY LIKE indoor's +0.3% CENTRE. It is not
  measured and cannot be: the fit is what said indoor was -1.68%, and was
  overruled. What makes it usable is that it is stated in one place, printed
  on every solve, and priced by the holdout like anything else.

⚠ IT ONLY REACHES XC WITH --gauge-scope merge. Pinning BU anchors the INDOOR
  group. Cross country is a different (sport, era) group and keeps its own
  zero unless the groups are merged, in which case the pin propagates through
  athletes who raced both. Pinning BU alone changes track and leaves XC
  exactly where it was.

⚠ AND A BANKED 200 IS NOT IN THE flat400 CLASS BY PREDICATE -- track_geometry
  refuses banked ovals, correctly, because banking is a real measured speed
  effect. This file OVERRIDES that for named venues only, on the owner's
  judgement that this particular oval runs at flat-400 pace. It is a curated
  exception, in the shape venue_geometry_overrides already uses, not a change
  to the predicate.

! NAMES ARE RESOLVED AT RUN TIME AND PRINTED. A location_id is stable and a
  name is not, so the pin keys on ids -- but nobody knows BU's location_id by
  heart, so a name pattern is resolved against meets_tf.venue_name and every
  match is printed. Verify that list the first time: one careless pattern
  could pin a venue nobody meant.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ★ {location_id: asserted difficulty}. Exact and stable; preferred once the
#   id is known. Fill this from --find and the patterns below become
#   unnecessary.
INDOOR_REFERENCE = {
    # ★★ BOSTON UNIVERSITY (owner, 2026-09-20: "BU which is as fast as a flat
    #    400m so it works"; id supplied as 111135/in). Held at 0.0 -- the same
    #    value a flat outdoor 400m oval is held at, which is the claim.
    #
    # ⚠ IT IS A BANKED 200, so track_geometry's predicate refuses it and is
    #   right to: banking is a real, measured speed effect. This is the
    #   curated exception the module header describes -- the owner's judgement
    #   that THIS oval runs at flat-400 pace, stated once, in one place.
    111135: 0.0,
}

# ★ {name pattern (ILIKE, case-insensitive): asserted difficulty}. Resolved
#   against meets_tf.venue_name for INDOOR meets only, and every match is
#   printed before it is used.
#
# ⚠ 0.0 MEANS "AS FAST AS A FLAT OUTDOOR 400", which is the owner's claim
#   about this oval specifically. It is not a claim about indoor tracks in
#   general -- those sit at the asserted +0.3% centre.
# ! EMPTY, BECAUSE THE ID IS KNOWN. '%boston univ%' matched NOTHING on the
#   real database -- the oval carries no venue_name and is identified only by
#   the meets held on it -- which is exactly why an id is the key and a name
#   is a convenience. The resolver and --find still search venue_name OR
#   meet_name, for the next venue somebody has to locate.
INDOOR_REFERENCE_NAMES = {}

_RESOLVED = None


def resolve(verbose=True):
    """{location_id: value}, from the explicit ids plus the resolved names."""
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED
    out = {int(k): float(v) for k, v in INDOOR_REFERENCE.items()}
    if INDOOR_REFERENCE_NAMES:
        try:
            from database import getConn
            with getConn() as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('meets_tf')")
                if cur.fetchone()[0] is None:
                    raise RuntimeError("meets_tf is missing")
                for pat, val in INDOOR_REFERENCE_NAMES.items():
                    # ! venue_name OR meet_name, THE SAME AS --find. A
                    #   track in this feed is often identified only by the
                    #   meets held on it; matching one field silently pinned
                    #   nothing and said so in a line nobody reads.
                    cur.execute("""
                        SELECT location_id,
                               COALESCE(min(venue_name), min(meet_name)),
                               count(*)
                        FROM   meets_tf
                        WHERE  location_id IS NOT NULL
                          AND  COALESCE(is_indoor::int, 0) <> 0
                          AND  (venue_name ILIKE %(p)s OR meet_name ILIKE %(p)s)
                        GROUP  BY location_id
                        ORDER  BY count(*) DESC
                    """, {"p": pat})
                    rows = cur.fetchall()
                    if verbose:
                        print(f"[indoor-ref] {pat!r} -> {len(rows)} indoor "
                              f"venue(s):", flush=True)
                    for loc, nm, n in rows:
                        out.setdefault(int(loc), float(val))
                        if verbose:
                            print(f"              loc {int(loc):>8}  {n:>6,} "
                                  f"meets  {nm}", flush=True)
                    if verbose and not rows:
                        print("              (no match — the pin does nothing)",
                              flush=True)
        except Exception as exc:                              # noqa: BLE001
            if verbose:
                print(f"[indoor-ref] names unresolved ({exc}); explicit ids "
                      f"only", flush=True)
    _RESOLVED = out
    return out


def locationId(key):
    """'TF:loc:501:in@e2' -> 501, but ONLY for an indoor key. None otherwise.

    ! INDOOR ONLY. The same location has an outdoor cell too, and the owner's
      claim is about the oval inside the building.
    """
    k = str(key).partition("@e")[0]
    if not k.startswith("TF:loc:"):
        return None
    body = k[7:]
    loc, _, surface = body.partition(":")
    if surface != "in" or not loc.isdigit():
        return None
    return int(loc)


def referenceMask(cell_keys, table=None, verbose=False):
    """(bool mask, value array) over cell keys."""
    import numpy as np
    ref = resolve(verbose) if table is None else table
    n = len(cell_keys)
    mask = np.zeros(n, dtype=bool)
    val = np.zeros(n, dtype=np.float64)
    if not ref:
        return mask, val
    for i, k in enumerate(cell_keys):
        loc = locationId(k)
        if loc is not None and loc in ref:
            mask[i] = True
            val[i] = float(ref[loc])
    return mask, val


def find(pattern, indoor_only=True, limit=40):
    """Search venue_name AND meet_name for a pattern.

    ⚠ venue_name ALONE WAS NOT ENOUGH (owner, 2026-09-20: '%boston univ%'
      matched nothing). A venue's NAME in this feed is whatever the meet
      listing carried, and a track is very often identified only by the meets
      held on it -- BU's oval is "BU Terrier Classic", "Valentine
      Invitational", "John Thomas Terrier Classic". Searching one field and
      reporting "nothing matched" was a false negative.

    ! THE DATE COLUMN IS PROBED, NOT ASSUMED -- see loadCourseGeometry, where
      assuming it cost the pack its geometry entirely.
    """
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = 'meets_tf'
                         AND column_name IN ('meet_date', 'date')""")
        have = {r[0] for r in cur.fetchall()}
        dcol = "meet_date" if "meet_date" in have else ("date" if "date" in have else None)
        dsel = f"min({dcol}), max({dcol})" if dcol else "NULL::text, NULL::text"
        gate = "AND COALESCE(is_indoor::int, 0) <> 0" if indoor_only else ""
        cur.execute(f"""
            SELECT location_id,
                   min(venue_name) FILTER (WHERE venue_name IS NOT NULL),
                   count(*), {dsel},
                   min(meet_name) FILTER (WHERE meet_name IS NOT NULL),
                   min(state) FILTER (WHERE state IS NOT NULL)
            FROM   meets_tf
            WHERE  location_id IS NOT NULL
              AND  (venue_name ILIKE %(p)s OR meet_name ILIKE %(p)s)
              {gate}
            GROUP  BY location_id ORDER BY count(*) DESC LIMIT %(lim)s
        """, {"p": pattern, "lim": limit})
        rows = cur.fetchall()
    print(f"\n[indoor-ref] venue_name OR meet_name matching {pattern!r}"
          f"{' (indoor meets only)' if indoor_only else ''}\n")
    if not rows:
        print("  (nothing matched)\n")
        print("  Try --top to list the biggest indoor venues and pick by eye,")
        print("  or --all-surfaces if the meets are not flagged indoor.")
        return
    print(f"  {'location_id':>12} {'meets':>7} {'st':>3}  {'venue':<34} "
          f"example meet")
    for loc, venue, n, _d0, _d1, meet, st in rows:
        print(f"  {int(loc):>12} {n:>7,} {str(st or '--'):>3}  "
              f"{str(venue or '(no venue name)')[:34]:<34} "
              f"{str(meet or '')[:40]}")
    print("\n  Put the id you want in INDOOR_REFERENCE in this file:\n")
    for loc, venue, n, _a, meet, _st in rows[:2]:
        print(f"      {int(loc)}: 0.0,    # {venue or meet} -- {n:,} indoor meets")


def top(limit=40, indoor_only=True):
    """★ THE BIGGEST INDOOR VENUES, to pick by eye when no pattern matches.
    A famous oval is a heavily-raced one, so BU cannot be far down this list."""
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        gate = "AND COALESCE(is_indoor::int, 0) <> 0" if indoor_only else ""
        cur.execute(f"""
            SELECT location_id,
                   min(venue_name) FILTER (WHERE venue_name IS NOT NULL),
                   count(*),
                   min(meet_name) FILTER (WHERE meet_name IS NOT NULL),
                   min(state) FILTER (WHERE state IS NOT NULL),
                   min(track_length), max(track_length),
                   min(track_type) FILTER (WHERE track_type IS NOT NULL)
            FROM   meets_tf
            WHERE  location_id IS NOT NULL {gate}
            GROUP  BY location_id ORDER BY count(*) DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    print(f"\n[indoor-ref] the {len(rows)} most-raced "
          f"{'indoor ' if indoor_only else ''}venues\n")
    print(f"  {'location_id':>12} {'meets':>7} {'st':>3} {'len':>6} {'type':<8} "
          f"{'venue':<30} example meet")
    for loc, venue, n, meet, st, l0, l1, tt in rows:
        ln = (f"{l0:.0f}" if l0 is not None and l0 == l1
              else (f"{l0:.0f}-{l1:.0f}" if l0 is not None else "--"))
        print(f"  {int(loc):>12} {n:>7,} {str(st or '--'):>3} {ln:>6} "
              f"{str(tt or '--')[:8]:<8} "
              f"{str(venue or '(no venue name)')[:30]:<30} "
              f"{str(meet or '')[:34]}")
    print("\n  ! BU is a 200m BANKED oval in MA. Match on that shape, then "
          "confirm the\n    example meet name before pinning it.")


def describe():
    ref = resolve(verbose=False)
    if not ref:
        print("[indoor-ref] NOTHING PINNED. Indoor sits at its asserted "
              "centre and nothing is\n             on the absolute scale.")
        return
    print(f"[indoor-ref] {len(ref)} indoor venue(s) asserted onto the "
          f"reference class:")
    for loc, v in sorted(ref.items()):
        print(f"               loc {loc:>8}  pinned at {100.0 * v:+.2f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", default=None, metavar="PATTERN",
                    help="search meets_tf.venue_name (ILIKE, use %% wildcards)")
    ap.add_argument("--all-surfaces", action="store_true",
                    help="with --find/--top, include outdoor meets too")
    ap.add_argument("--top", type=int, nargs="?", const=40, default=None,
                    metavar="N",
                    help="list the N most-raced indoor venues, to pick by eye "
                         "when no name pattern matches")
    args = ap.parse_args()
    if args.top is not None:
        top(args.top, indoor_only=not args.all_surfaces)
    elif args.find:
        find(args.find, indoor_only=not args.all_surfaces)
    else:
        describe()


if __name__ == "__main__":
    main()
