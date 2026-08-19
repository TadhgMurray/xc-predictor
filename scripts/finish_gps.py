# Project: xc-predictor
# File:    scripts/finish_gps.py
# Purpose: Close out ALL remaining gps. One script, one run. Steps:
#   1. GEOCODE  the meets_tf_meta rows that have an address but no gps
#      (Census one-line -> Nominatim), cached.
#   2. PROPAGATE gps by location_id into every table that still has nulls and
#      shares that location_id: meets_tf, meets_tf_meta, meets (anet XC).
#      A location_id's gps is a venue fact, identical across tables/sports.
#   Dry-run by default; --apply commits. Idempotent (only null rows written).
#
# USAGE
#   python scripts/finish_gps.py            # dry run: counts only
#   python scripts/finish_gps.py --apply    # geocode + propagate + commit
# ============================================================================

import argparse
import json
import os
import sys
import time
sys.path.insert(0, "scripts")

from database import getConn, initPool

_CACHE = "finish_gps_cache.json"
_UA = "xc-predictor-finish-gps (contact: tadhg)"
_SLEEP = 1.1
_GPS_TABLES = ("meets_tf", "meets_tf_meta", "meets")   # all key on location_id


# ================================================================== #
# CHUNK 1 -- GEOCODE the 36 meta rows (only ones with an address)
# ================================================================== #

def _cachePath(out_dir):
    return os.path.join(out_dir, _CACHE)


def _loadCache(out_dir):
    p = _cachePath(out_dir)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _saveCache(out_dir, cache):
    tmp = _cachePath(out_dir) + ".tmp"
    json.dump(cache, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, _cachePath(out_dir))


# _oneLine : hand Census the whole "venue, city, state" string; it parses.
def _censusOneLine(q):
    import requests
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": q, "benchmark": "Public_AR_Current",
                    "format": "json"}, timeout=30)
        r.raise_for_status()
        m = r.json().get("result", {}).get("addressMatches", [])
        if m:
            c = m[0]["coordinates"]
            return float(c["y"]), float(c["x"])       # y=lat, x=lon
    except Exception:
        pass
    return None


def _nominatim(q):
    import requests
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": q, "format": "json", "limit": 1},
                         headers={"User-Agent": _UA}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j:
            return float(j[0]["lat"]), float(j[0]["lon"])
    except Exception:
        pass
    return None


# _query : build the richest address string the meta row supports.
def _query(venue, address, city, state):
    parts = [p for p in (venue, address, city, state) if p]
    return ", ".join(parts)


# _geocodeMeta
# Purpose : resolve gps for the meets_tf_meta rows that have an address but no
#           gps, and WRITE it onto those rows (by meet_id). Returns how many
#           were placed. Cached so a re-run is free.
def _geocodeMeta(conn, out_dir, do_write):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, location_id, venue_name, address, city, state
            FROM meets_tf_meta
            WHERE gps_lat IS NULL
              AND (venue_name IS NOT NULL OR address IS NOT NULL
                   OR city IS NOT NULL OR state IS NOT NULL)
        """)
        rows = cur.fetchall()
        conn.rollback()

    cache = _loadCache(out_dir)
    todo = [r for r in rows if str(r[0]) not in cache]
    print(f"  geocode: {len(rows)} meta rows need gps, {len(todo)} to look up")
    for i, (meet_id, loc, venue, addr, city, state) in enumerate(todo):
        q = _query(venue, addr, city, state)
        res = _censusOneLine(q) or _nominatim(q)
        if res is None:
            res = _nominatim(_query(venue, None, city, state))
            time.sleep(_SLEEP)
        if res is None:
            print(f"    MISS: {q!r}")     # <-- add this
        cache[str(meet_id)] = {"lat": res[0], "lon": res[1]} if res else None
        if i % 10 == 0:
            _saveCache(out_dir, cache)
        time.sleep(0.2)
    _saveCache(out_dir, cache)
    placed = sum(1 for v in cache.values() if v)
    print(f"  geocode: {placed}/{len(rows)} placed")

    if do_write:
        with conn.cursor() as cur:
            for meet_id, *_ in rows:
                hit = cache.get(str(meet_id))
                if hit:
                    cur.execute("""UPDATE meets_tf_meta
                                   SET gps_lat=%s, gps_long=%s
                                   WHERE meet_id=%s AND gps_lat IS NULL""",
                                (hit["lat"], hit["lon"], meet_id))
        conn.commit()
    return sum(1 for v in cache.values() if v)


# ================================================================== #
# CHUNK 2 -- PROPAGATE gps by location_id into every table
# ================================================================== #

# _sourceGps
# Purpose : a location_id -> (lat, lon) map built from EVERY table that has a
#           known gps for that venue, so any table can borrow from any other.
def _sourceGps(cur):
    coords = {}
    for table in _GPS_TABLES:
        cur.execute(f"""SELECT location_id, gps_lat, gps_long FROM {table}
                        WHERE gps_lat IS NOT NULL AND location_id IS NOT NULL""")
        for loc, lat, lon in cur.fetchall():
            coords.setdefault(loc, (lat, lon))     # first known wins
    return coords


# _propagate
# Purpose : fill null-gps rows in one table from the shared coords map.
#           Dry-run counts; write does the UPDATE. Returns (would_fill, ids).
def _propagate(conn, table, coords, do_write):
    with conn.cursor() as cur:
        cur.execute(f"""SELECT DISTINCT location_id FROM {table}
                        WHERE gps_lat IS NULL AND location_id IS NOT NULL""")
        null_ids = [r[0] for r in cur.fetchall()]
        conn.rollback()
    fillable = [i for i in null_ids if i in coords]
    if do_write and fillable:
        with conn.cursor() as cur:
            for loc in fillable:
                lat, lon = coords[loc]
                cur.execute(f"""UPDATE {table} SET gps_lat=%s, gps_long=%s
                                WHERE location_id=%s AND gps_lat IS NULL""",
                            (lat, lon, loc))
        conn.commit()
    return len(fillable), len(null_ids)


# ================================================================== #
# CHUNK 3 -- DRIVER
# ================================================================== #

def _run(out_dir, do_write):
    with getConn() as conn:
        # 1) geocode the meta rows first, so their new gps can propagate below
        _geocodeMeta(conn, out_dir, do_write)

        # 2) propagate across all three tables
        with conn.cursor() as cur:
            coords = _sourceGps(cur)
            conn.rollback()
        print(f"\n  propagate: {len(coords):,} venues have a known gps to lend")
        for table in _GPS_TABLES:
            fillable, null_ids = _propagate(conn, table, coords, do_write)
            stuck = null_ids - fillable
            verb = "filled" if do_write else "would fill"
            print(f"    {table}: {verb} {fillable} of {null_ids} null venues "
                  f"({stuck} have no known gps anywhere)")

        if not do_write:
            print("\n  [dry-run] nothing written  (add --apply to commit)")


def main():
    ap = argparse.ArgumentParser(description="Finish all gps: geocode meta, "
                                             "propagate by location_id.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    initPool()
    _run(args.out, args.apply)


if __name__ == "__main__":
    main()