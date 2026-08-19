# Project: xc-predictor
# File:    scripts/diag_location_audit.py
# Purpose: READ-ONLY. Two questions the location backfill left open:
#          (1) the 1,257 (norm_name, state) collisions -- are they real distinct
#              venues that got a neighbour's coordinates, or harmless dupes?
#          (2) the Tier-2 propagation split -- how much came from tfrrs->tfrrs
#              (same scraper, strings agree) vs anet->tfrrs (different scrapers),
#              which tells us whether fuzzy matching would recover more.
#          Writes nothing. Mirrors the backfill's own key logic so the audit sees
#          exactly what the backfill saw.
#
# USAGE
#   python scripts/diag_location_audit.py                 # both audits
#   python scripts/diag_location_audit.py --collisions    # just the collisions
#   python scripts/diag_location_audit.py --split         # just the tier-2 split
#   python scripts/diag_location_audit.py --show 40       # more collision detail
# ============================================================================

import argparse
import collections
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- KEY NORMALIZATION  (must match the backfill EXACTLY)
# ================================================================== #
# If this drifts from backfill_tfrrs_location._normRaw/_key, the audit is auditing
# a different grouping than the one that ran. Kept byte-identical on purpose.

def _normRaw(raw):
    if not raw:
        return None
    s = raw.strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _key(raw, state):
    n = _normRaw(raw)
    if n is None:
        return None
    return (n, (state or "").strip().upper() or None)


# ================================================================== #
# CHUNK 2 -- HAVERSINE (how far apart are colliding coordinates?)
# ================================================================== #

# _haversineKm
# Purpose : great-circle distance between two lat/long, in km. A collision whose
#           location_ids sit <1 km apart is effectively the same place (mapping
#           noise); one spanning tens of km is two real venues sharing a name.
# Syntax  : standard haversine. math in radians; 6371 km = Earth radius.
def _haversineKm(a, b):
    from math import radians, sin, cos, asin, sqrt
    if None in a or None in b:
        return None
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


# ================================================================== #
# CHUNK 3 -- BUILD THE SAME KEY -> location_id MAP THE BACKFILL BUILT
# ================================================================== #

# _knownVariants
# Purpose : reproduce the backfill's index but KEEP every variant per key (the
#           backfill collapsed to most-common; we need the full set to judge the
#           collisions). Same two sources: anet meets keyed on course_name, and
#           the tfrrs meets already stamped, keyed on location_raw.
# Output  : {key: {location_id: {"n": count, "gps": (lat,lon)}}}.
def _knownVariants(cur):
    variants = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"n": 0, "gps": (None, None)}))

    cur.execute("""
        SELECT course_name, state, location_id, gps_lat, gps_long
        FROM meets
        WHERE location_id IS NOT NULL
    """)
    for name, state, loc, lat, lon in cur.fetchall():
        k = _key(name, state)
        if k is None:
            continue
        v = variants[k][loc]
        v["n"] += 1
        v["gps"] = (lat, lon)

    # tfrrs meets that now carry a location_id (post-backfill) keyed on their raw
    cur.execute("""
        SELECT location_raw, state, location_id, gps_lat, gps_long
        FROM meets_tfrrs
        WHERE sport = 'XC' AND location_id IS NOT NULL
    """)
    for raw, state, loc, lat, lon in cur.fetchall():
        k = _key(raw, state)
        if k is None:
            continue
        v = variants[k][loc]
        v["n"] += 1
        v["gps"] = (lat, lon)

    return variants


# ================================================================== #
# CHUNK 4 -- COLLISION AUDIT
# ================================================================== #

# _auditCollisions
# Purpose : for every key with >1 location_id, measure how far apart the two most
#           common coordinates are. Bucket by distance so you can see whether the
#           1,257 are mostly mapping-noise (<1 km) or genuine distinct venues.
def _auditCollisions(variants, show):
    colliding = {k: v for k, v in variants.items() if len(v) > 1}
    buckets = collections.Counter()
    detail = []
    for key, locs in colliding.items():
        top = sorted(locs.items(), key=lambda kv: -kv[1]["n"])[:2]
        (loc_a, a), (loc_b, b) = top[0], top[1]
        km = _haversineKm(a["gps"], b["gps"])
        if km is None:
            bucket = "no-gps"
        elif km < 1:
            bucket = "<1km (noise)"
        elif km < 25:
            bucket = "1-25km"
        elif km < 200:
            bucket = "25-200km"
        else:
            bucket = ">200km (clearly different)"
        buckets[bucket] += 1
        detail.append((km if km is not None else -1, key, loc_a, a["n"],
                       loc_b, b["n"]))

    print(f"\nCOLLISIONS: {len(colliding)} keys map to >1 location_id")
    print("  distance between the two most-common coordinates:")
    for b in ("<1km (noise)", "1-25km", "25-200km", ">200km (clearly different)",
              "no-gps"):
        if buckets[b]:
            print(f"    {b:<28} {buckets[b]:>5}")
    real = sum(buckets[b] for b in ("25-200km", ">200km (clearly different)"))
    print(f"  -> ~{real} look like genuinely DIFFERENT venues sharing a "
          f"(name,state). These are the ones a city-in-key would fix.")

    detail.sort(reverse=True)          # farthest apart first
    print(f"\n  worst {min(show, len(detail))} (farthest-apart collisions):")
    print(f"    {'km':>7}  {'n_a':>5} {'n_b':>5}  name / state")
    for km, key, la, na, lb, nb in detail[:show]:
        name, state = key
        km_s = f"{km:.0f}" if km >= 0 else "n/a"
        print(f"    {km_s:>7}  {na:>5} {nb:>5}  {name[:44]!r} / {state}")


# ================================================================== #
# CHUNK 5 -- TIER-2 SPLIT (anet->tfrrs vs tfrrs->tfrrs)
# ================================================================== #

# _auditTier2Split
# Purpose : of the propagated tfrrs meets, how many matched a key that ALSO
#           exists among anet venues (so the match COULD be anet-sourced) vs a
#           key seen only among tfrrs. Tells us whether anet->tfrrs recall is the
#           bottleneck (worth fuzzy matching) or already saturated.
# Detail  : a propagated meet is one whose location_id is NOT NULL but which has
#           NO canon_meet_id on its results (so tier 1 could not have placed it).
#           We recompute the anet-key set and the tfrrs-only-key set and see which
#           bucket each propagated meet's key falls in.
def _auditTier2Split(cur):
    # keys that exist among anet venues
    cur.execute("SELECT DISTINCT course_name, state FROM meets "
                "WHERE location_id IS NOT NULL")
    anet_keys = set()
    for name, state in cur.fetchall():
        k = _key(name, state)
        if k:
            anet_keys.add(k)

    # tfrrs meets that were PROPAGATED: located, but no canon link of their own
    cur.execute("""
        SELECT mt.meet_id, mt.location_raw, mt.state
        FROM meets_tfrrs mt
        WHERE mt.sport = 'XC' AND mt.location_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM results r
              WHERE r.source = 'tfrrs' AND r.meet_id = mt.meet_id
                AND r.canon_meet_id IS NOT NULL
          )
    """)
    both, tfrrs_only, no_key = 0, 0, 0
    for meet_id, raw, state in cur.fetchall():
        k = _key(raw, state)
        if k is None:
            no_key += 1
        elif k in anet_keys:
            both += 1
        else:
            tfrrs_only += 1

    total = both + tfrrs_only + no_key
    print(f"\nTIER-2 SPLIT: {total} propagated tfrrs meets (no canon of their own)")
    print(f"  key also seen in anet venues (anet->tfrrs possible): {both:>5}")
    print(f"  key seen only among tfrrs (tfrrs->tfrrs)           : {tfrrs_only:>5}")
    print(f"  no usable key                                      : {no_key:>5}")
    if both < tfrrs_only * 0.25:
        print("  -> anet->tfrrs recall is LOW. The anet course_name vs tfrrs "
              "location_raw\n     string mismatch is starving it. Fuzzy matching "
              "would recover venues.")
    else:
        print("  -> anet->tfrrs is contributing. Fuzzy matching would add less.")


# ================================================================== #
# CHUNK 6 -- DRIVER
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Audit the location backfill: collisions + tier-2 split. "
                    "READ-ONLY.")
    ap.add_argument("--collisions", action="store_true")
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--show", type=int, default=25,
                    help="how many worst collisions to list")
    args = ap.parse_args()
    both = not (args.collisions or args.split)

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '512MB'")
        if args.collisions or both:
            variants = _knownVariants(cur)
            _auditCollisions(variants, args.show)
        if args.split or both:
            _auditTier2Split(cur)
        conn.rollback()


if __name__ == "__main__":
    main()