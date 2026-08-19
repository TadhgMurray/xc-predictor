# Project: xc-predictor
# File:    scripts/refix_gps.py
# Purpose: Fix out-of-state-box gps for real. DRY RUN is instant (counts only,
#          NO network). --apply re-geocodes ONE lookup per distinct venue (not
#          per row), with live progress, and arbitrates each:
#            fresh coord ~= stored (<=25km) -> keep (rescues border venues)
#            fresh coord in buffered box     -> re-pin (fixes wrong-name pins)
#            neither                         -> null (honest > wrong)
#          Then mirrors corrected venues into meets_tf by location_id.
# USAGE:
#   python scripts/refix_gps.py                 # instant dry run: suspect counts
#   python scripts/refix_gps.py --apply         # fix (Census->Nominatim, paced)
#   python scripts/refix_gps.py --apply --census-only   # faster, skip Nominatim
# ============================================================================
import argparse, json, math, os, sys, time
sys.path.insert(0, "scripts")
from database import getConn, initPool
from audit_gps_sanity import _BBOX

_CACHE = "refix_gps_cache.json"
_UA = "xc-predictor-refix-gps (contact: tadhg)"
_SLEEP = 1.1
_MARGIN = 0.4          # ~44km box buffer so correct border venues don't flag
_AGREE_KM = 25.0

# per table: the venue-name column and a secondary clue column
_CLUE = {
    "meets":         ("course_name", "NULL::text"),
    "meets_tf_meta": ("venue_name",  "address"),
    "meets_tfrrs":   ("venue_name",  "location_raw"),
}


# ---- geometry -------------------------------------------------------
def _haversineKm(la1, lo1, la2, lo2):
    r = 6371.0
    dp = math.radians(la2 - la1); dl = math.radians(lo2 - lo1)
    a = (math.sin(dp/2)**2
         + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dl/2)**2)
    return 2*r*math.asin(math.sqrt(a))

def _inBox(state, lat, lon, margin):
    b = _BBOX.get((state or "").strip().upper())
    if not b:
        return True
    a, c, d, e = b
    return (a-margin) <= lat <= (c+margin) and (d-margin) <= lon <= (e+margin)

def _isSuspect(state, lat, lon):
    st = (state or "").strip().upper()
    return st in _BBOX and lat is not None and not _inBox(st, lat, lon, _MARGIN)


# ---- collect suspects (fast, ONE row per distinct venue+coord) ------
def _suspects(cur, table):
    name, extra = _CLUE[table]
    cur.execute(f"""
        SELECT location_id, max(state), gps_lat, gps_long,
               max({name}), max({extra})
        FROM {table}
        WHERE gps_lat IS NOT NULL
        GROUP BY location_id, gps_lat, gps_long
    """)
    rows = cur.fetchall()
    return [r for r in rows if _isSuspect(r[1], r[2], r[3])], len(rows)


# ---- geocode (cached; --census-only skips the paced Nominatim leg) --
def _loadCache(d):
    p = os.path.join(d, _CACHE)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

def _saveCache(d, c):
    tmp = os.path.join(d, _CACHE) + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8")); os.replace(tmp, os.path.join(d, _CACHE))

def _census(q):
    import requests
    try:
        r = requests.get("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                         params={"address": q, "benchmark": "Public_AR_Current", "format": "json"},
                         timeout=30); r.raise_for_status()
        m = r.json().get("result", {}).get("addressMatches", [])
        if m:
            c = m[0]["coordinates"]; return float(c["y"]), float(c["x"])
    except Exception:
        pass
    return None

def _nominatim(q):
    import requests
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": q, "format": "json", "limit": 1},
                         headers={"User-Agent": _UA}, timeout=30); r.raise_for_status()
        j = r.json()
        if j:
            return float(j[0]["lat"]), float(j[0]["lon"])
    except Exception:
        pass
    return None

def _geocode(q, cache, census_only):
    if q in cache:
        v = cache[q]; return tuple(v) if v else None
    res = _census(q)
    if res is None and not census_only:
        res = _nominatim(q); time.sleep(_SLEEP)
    cache[q] = list(res) if res else None
    return res

def _query(name, extra, state):
    return ", ".join(p for p in (name, extra, state, "USA") if p)


# ---- decision -------------------------------------------------------
def _decide(ola, olo, fresh, state):
    if fresh is not None:
        if _haversineKm(ola, olo, fresh[0], fresh[1]) <= _AGREE_KM:
            return ("keep", None)
        if _inBox(state, fresh[0], fresh[1], _MARGIN):
            return ("write", fresh)
    return ("null", None)


# ---- per-table fix --------------------------------------------------
def _fixTable(conn, table, cache, out_dir, do_write, census_only):
    with conn.cursor() as cur:
        suspects, total = _suspects(cur, table)
        conn.rollback()
    print(f"  {table:<13}: {total:,} venues, {len(suspects)} suspect", flush=True)
    if not do_write:
        return []                                  # DRY RUN stops here: no network

    keep = write = null = 0; actions = []
    for i, (loc, st, la, lo, nm, ex) in enumerate(suspects, 1):
        fresh = _geocode(_query(nm, ex, st), cache, census_only)
        verb, coord = _decide(la, lo, fresh, st)
        if verb == "keep":
            keep += 1
        elif verb == "write":
            write += 1; actions.append((loc, coord[0], coord[1]))
        else:
            null += 1; actions.append((loc, None, None))
        if i % 25 == 0:
            print(f"      {i}/{len(suspects)} ...", flush=True); _saveCache(out_dir, cache)
    _saveCache(out_dir, cache)
    print(f"    -> {keep} kept, {write} re-pinned, {null} nulled", flush=True)

    with conn.cursor() as cur:
        for loc, la, lo in actions:
            cur.execute(f"UPDATE {table} SET gps_lat=%s, gps_long=%s WHERE location_id=%s",
                        (la, lo, loc))
    conn.commit()
    return actions


def _mirrorGeometry(conn, corrected):
    by_loc = {loc: (la, lo) for loc, la, lo in corrected}
    print(f"  meets_tf     : mirroring {len(by_loc)} corrected venues", flush=True)
    with conn.cursor() as cur:
        for loc, (la, lo) in by_loc.items():
            cur.execute("UPDATE meets_tf SET gps_lat=%s, gps_long=%s WHERE location_id=%s",
                        (la, lo, loc))
    conn.commit()


def _run(out_dir, do_write, census_only):
    cache = _loadCache(out_dir)
    corrected = []
    with getConn() as conn:
        for table in ("meets_tf_meta", "meets", "meets_tfrrs"):
            corrected += _fixTable(conn, table, cache, out_dir, do_write, census_only)
        if do_write:
            _mirrorGeometry(conn, corrected)
    if not do_write:
        print("\n  [dry-run] counts only, no network. add --apply to fix.", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--census-only", action="store_true", dest="census_only")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    initPool()
    _run(args.out, args.apply, args.census_only)


if __name__ == "__main__":
    main()