# Project: xc-predictor
# File:    scripts/geocode_anet_venues.py
# Purpose: Fill gps for anet XC venues that have a course_name but no
#          coordinates. Separate from the tfrrs geocoder (which is running) so
#          the two never share a cache or collide on a write.
#
# ============================================================================
# THE POPULATION (measured)
# ============================================================================
#   905 anet rows have a course_name and NULL gps. Two kinds:
#     821 have a location_id  -> ~47 DISTINCT venues. Geocode by course_name,
#          write gps onto the EXISTING location_id (no minting).
#      84 have NO location_id -> distinct venues get a NEW minted id, so they
#          become deduped courses instead of orphans. Mint ONE id per distinct
#          (course_name, state), not per row.
#   0 were self-fillable (no venue had a sibling row with gps), so every one
#   needs the geocoder.
#
# ============================================================================
# ID MINTING -- a DIFFERENT base from the tfrrs geocoder
# ============================================================================
# tfrrs-geocoded venues mint at 900,000,000. anet-geocoded no-loc venues mint at
# _MINT_BASE = 800,000,000 -- a distinct high range, so:
#   2..49,452     native anet/tfrrs location_ids
#   800,000,000+  anet-geocoded (this script)
#   900,000,000+  tfrrs-geocoded (the other script)
# No range can collide with another, and the prefix says who minted it.
#
# ============================================================================
# THREE STAGES (network isolated to `geocode`), same as the tfrrs tool
#   collect : anet no-gps rows -> distinct venues -> CSV
#   geocode : Census batch + Nominatim fallback, cached (OWN cache file)
#   apply   : write gps to the 821 (by location_id); mint + write for the 84
#
# USAGE
#   python scripts/geocode_anet_venues.py collect
#   python scripts/geocode_anet_venues.py geocode
#   python scripts/geocode_anet_venues.py apply            # dry run
#   python scripts/geocode_anet_venues.py apply --apply
# ============================================================================

import argparse
import csv
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- CONSTANTS (own files, own mint base)
# ================================================================== #
_VENUES_CSV = "geocode_anet_venues.csv"
_CACHE = "geocode_anet_cache.json"
_MINT_BASE = 800_000_000                      # anet-geocoded; tfrrs uses 900M
_NOMINATIM_UA = "xc-predictor-anet-venue-backfill (contact: tadhg)"
_NOMINATIM_SLEEP = 1.1


# ================================================================== #
# CHUNK 2 -- VENUE KEY (course_name is clean here, not location_raw)
# ================================================================== #

def _norm(name):
    if not name:
        return None
    s = re.sub(r"[^\w\s]", " ", name.strip().lower())
    return re.sub(r"\s+", " ", s).strip() or None


def _venueKey(name, state):
    n = _norm(name)
    if n is None:
        return None
    return f"{n}|{(state or '').strip().upper()}"


# ================================================================== #
# CHUNK 3 -- STAGE collect
# ================================================================== #

# _collect
# Purpose : anet no-gps rows -> one row per distinct venue, tracking BOTH the
#           div_ids to update and whether the venue already has a location_id
#           (821 case) or needs one minted (84 case). meets PK is div_id, so the
#           write target is div_id, not meet_id.
def _collect(out_dir):
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT div_id, course_name, state, location_id
            FROM meets
            WHERE gps_lat IS NULL AND course_name IS NOT NULL
        """)
        rows = cur.fetchall()
        conn.rollback()

    venues = {}
    for div_id, name, state, loc in rows:
        key = _venueKey(name, state)
        if key is None:
            key = f"__noname__{div_id}"
        v = venues.setdefault(key, {"key": key, "name": name, "state": state,
                                    "div_ids": [], "location_id": None})
        v["div_ids"].append(div_id)
        # if ANY row of this venue already carries a location_id, remember it so
        # apply writes to the existing id rather than minting a new one.
        if loc is not None and v["location_id"] is None:
            v["location_id"] = loc

    path = os.path.join(out_dir, _VENUES_CSV)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "course_name", "state", "existing_loc", "n_divs"])
        for v in venues.values():
            w.writerow([v["key"], v["name"] or "", v["state"] or "",
                        v["location_id"] if v["location_id"] is not None else "",
                        len(v["div_ids"])])
    has_loc = sum(1 for v in venues.values() if v["location_id"] is not None)
    print(f"  collect: {len(rows)} no-gps rows -> {len(venues)} distinct venues")
    print(f"    {has_loc} have a location_id (write to it), "
          f"{len(venues)-has_loc} need a minted id")
    print(f"  wrote {path}\n  next: geocode")


# ================================================================== #
# CHUNK 4 -- STAGE geocode (network; own cache)
# ================================================================== #

def _loadCache(out_dir):
    path = os.path.join(out_dir, _CACHE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _saveCache(out_dir, cache):
    path = os.path.join(out_dir, _CACHE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, path)


def _readVenues(out_dir):
    path = os.path.join(out_dir, _VENUES_CSV)
    if not os.path.exists(path):
        sys.exit(f"  run 'collect' first -- {path} missing")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# _censusBatch : anet has a clean course_name (no street), so we send it as the
#                street field -- Census often resolves a named place + city/state.
#                Misses fall to Nominatim.
def _censusBatch(batch):
    import requests
    buf = io.StringIO()
    w = csv.writer(buf)
    for i, v in enumerate(batch):
        w.writerow([i, v["course_name"], "", v["state"], ""])
    files = {"addressFile": ("addr.csv", buf.getvalue(), "text/csv")}
    data = {"benchmark": "Public_AR_Current"}
    r = requests.post(
        "https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
        files=files, data=data, timeout=300)
    r.raise_for_status()
    out = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) >= 6 and row[2] == "Match" and row[5]:
            lon, lat = row[5].split(",")
            out[batch[int(row[0])]["key"]] = (float(lat), float(lon))
    return out


def _nominatimOne(name, state):
    import requests
    q = f"{name}, {state}" if state else name
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": q, "format": "json", "limit": 1},
                     headers={"User-Agent": _NOMINATIM_UA}, timeout=30)
    r.raise_for_status()
    j = r.json()
    return (float(j[0]["lat"]), float(j[0]["lon"])) if j else None


def _geocode(out_dir):
    venues = _readVenues(out_dir)
    cache = _loadCache(out_dir)
    todo = [v for v in venues if v["key"] not in cache]
    print(f"  geocode: {len(venues)} venues, {len(cache)} cached, {len(todo)} to do")

    batch = [v for v in todo if v["course_name"]]
    if batch:
        print(f"  Census batch: {len(batch)} ...")
        try:
            hits = _censusBatch(batch)
            for v in batch:
                if v["key"] in hits:
                    lat, lon = hits[v["key"]]
                    cache[v["key"]] = {"lat": lat, "lon": lon, "src": "census"}
            _saveCache(out_dir, cache)
            print(f"  Census placed {len(hits)}")
        except Exception as e:
            print(f"  Census failed ({e}); all to Nominatim")

    residue = [v for v in venues if v["key"] not in cache]
    print(f"  Nominatim: {len(residue)} at ~1/s "
          f"(~{len(residue)*_NOMINATIM_SLEEP/60:.0f} min)")
    for i, v in enumerate(residue):
        try:
            res = _nominatimOne(v["course_name"], v["state"])
            cache[v["key"]] = ({"lat": res[0], "lon": res[1], "src": "osm"}
                               if res else None)
        except Exception as e:
            print(f"    [{v['key']}] error {e}")
        if i % 25 == 0:
            _saveCache(out_dir, cache)
        time.sleep(_NOMINATIM_SLEEP)
    _saveCache(out_dir, cache)

    placed = sum(1 for x in cache.values() if x)
    print(f"  done: {placed}/{len(venues)} placed\n  next: apply")


# ================================================================== #
# CHUNK 5 -- STAGE apply
# ================================================================== #

# _apply
# Purpose : write gps for resolved anet venues. Two write modes:
#   existing_loc present -> UPDATE all this venue's div_ids: set gps only
#                           (location_id already correct).
#   existing_loc absent  -> mint ONE id (deterministic, from _MINT_BASE + index
#                           over the sorted no-loc keys) and set BOTH gps and
#                           location_id on all its div_ids.
# Idempotent: only rows still missing gps are written; minted ids are stable
# across reruns because the index is over a sorted key list.
def _apply(out_dir, do_write):
    venues = _readVenues(out_dir)
    cache = _loadCache(out_dir)

    # authoritative current div lists from the DB (the CSV could be stale)
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT div_id, course_name, state, location_id
                FROM meets
                WHERE gps_lat IS NULL AND course_name IS NOT NULL
            """)
            rows = cur.fetchall()
            conn.rollback()

        by_key = {}
        for div_id, name, state, loc in rows:
            key = _venueKey(name, state) or f"__noname__{div_id}"
            e = by_key.setdefault(key, {"divs": [], "loc": None})
            e["divs"].append(div_id)
            if loc is not None and e["loc"] is None:
                e["loc"] = loc

        resolved = [k for k in by_key if cache.get(k)]
        with_loc = sorted(k for k in resolved if by_key[k]["loc"] is not None)
        no_loc = sorted(k for k in resolved if by_key[k]["loc"] is None)
        mint = {k: _MINT_BASE + i for i, k in enumerate(no_loc)}

        div_with = sum(len(by_key[k]["divs"]) for k in with_loc)
        div_no = sum(len(by_key[k]["divs"]) for k in no_loc)
        print(f"  apply: {len(with_loc)} venues -> {div_with} divs (existing id, "
              f"gps only)")
        print(f"         {len(no_loc)} venues -> {div_no} divs (mint "
              f"{_MINT_BASE}..{_MINT_BASE+len(no_loc)-1})")
        unresolved = sum(len(by_key[k]["divs"]) for k in by_key
                         if not cache.get(k))
        print(f"         {unresolved} divs unresolved (venue not geocoded)")

        if not do_write:
            print("  [dry-run] nothing written")
            return

        with conn.cursor() as cur:
            for k in with_loc:
                c = cache[k]
                cur.execute("""
                    UPDATE meets SET gps_lat = %s, gps_long = %s
                    WHERE div_id = ANY(%s) AND gps_lat IS NULL
                """, (c["lat"], c["lon"], by_key[k]["divs"]))
            for k in no_loc:
                c = cache[k]
                cur.execute("""
                    UPDATE meets
                    SET gps_lat = %s, gps_long = %s, location_id = %s
                    WHERE div_id = ANY(%s) AND gps_lat IS NULL
                """, (c["lat"], c["lon"], mint[k], by_key[k]["divs"]))
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FILTER (WHERE gps_lat IS NULL), count(*) "
                        "FROM meets WHERE course_name IS NOT NULL")
            missing, tot = cur.fetchone()
        print(f"  anet venues still missing gps: {missing:,} (of {tot:,} named)")


# ================================================================== #
# CHUNK 6 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Geocode anet XC venues missing gps; write to existing "
                    "location_id or mint a new one for no-loc venues.")
    ap.add_argument("stage", choices=["collect", "geocode", "apply"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if args.stage == "geocode":
        _geocode(args.out)
        return
    initPool()
    if args.stage == "collect":
        _collect(args.out)
    else:
        _apply(args.out, args.apply)


if __name__ == "__main__":
    main()