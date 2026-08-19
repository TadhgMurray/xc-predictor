# Project: xc-predictor
# File:    scripts/recover_nulls.py
# Purpose: Recover the venues refix_gps.py nulled from a BAD query format. It
#          stuffed venue_name+address+state into one string and dropped city;
#          Census/Nominatim choke on that. This rebuilds clean queries using the
#          right columns per table (Census likes "street, city, state"; Nominatim
#          likes "venue, city, state"), geocodes NULL venues that still have a
#          clue, verifies in the buffered state box, writes, and mirrors to
#          meets_tf. Misses are NOT cached, so re-runs retry (fixes the poisoned-
#          miss bug). Dry-run prints the target count only; --apply does network.
# USAGE:
#   python scripts/recover_nulls.py                 # instant: how many to retry
#   python scripts/recover_nulls.py --apply         # geocode + write + mirror
# ============================================================================
import argparse, json, math, os, re, sys, time
sys.path.insert(0, "scripts")
from database import getConn, initPool
from audit_gps_sanity import _BBOX

_CACHE = "recover_cache.json"          # hits only
_UA = "xc-predictor-recover (contact: tadhg)"
_SLEEP = 1.1
_MARGIN = 0.4

# columns per table: (street-ish, venue-ish, city, state) -- NULL where absent
_COLS = {
    "meets_tf_meta": ("address", "venue_name", "city", "state"),
    "meets":         ("NULL::text", "course_name", "NULL::text", "state"),
    "meets_tfrrs":   ("location_raw", "venue_name", "city", "state"),
}


# ---- geometry -------------------------------------------------------
def _inBox(state, lat, lon):
    b = _BBOX.get((state or "").strip().upper())
    if not b:
        return True
    a, c, d, e = b
    return (a-_MARGIN) <= lat <= (c+_MARGIN) and (d-_MARGIN) <= lon <= (e+_MARGIN)


# ---- clean query construction --------------------------------------
def _clean(*parts):
    """Join non-empty parts, drop consecutive duplicates, add USA."""
    seen, out = None, []
    for p in parts:
        p = (p or "").strip().strip(",")
        if p and p.lower() != (seen or "").lower():
            out.append(p); seen = p
    out.append("USA")
    return ", ".join(out)

def _hasNumber(s):
    return bool(s and re.search(r"\d", s))


# ---- geocoders ------------------------------------------------------
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


# ---- resolve one venue with several clean query forms --------------
def _resolve(street, venue, city, state, cache):
    """
    Try, in order: Census(street,city,state), Nominatim(street,city,state),
    Nominatim(venue,city,state). Accept first hit inside the buffered box.
    Cache HITS only (so misses retry next run).
    """
    tries = []
    if _hasNumber(street):
        tries.append(("census", _clean(street, city, state)))
        tries.append(("osm",    _clean(street, city, state)))
    tries.append(("osm", _clean(venue, city, state)))

    for engine, q in tries:
        if q in cache:                            # cached hit only
            lat, lon = cache[q]
            if _inBox(state, lat, lon):
                return (lat, lon)
            continue
        res = _census(q) if engine == "census" else _nominatim(q)
        if engine == "osm":
            time.sleep(_SLEEP)
        if res and _inBox(state, res[0], res[1]):
            cache[q] = list(res)
            return res
    return None


# ---- driver ---------------------------------------------------------
def _loadCache(d):
    p = os.path.join(d, _CACHE)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}

def _saveCache(d, c):
    tmp = os.path.join(d, _CACHE) + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8")); os.replace(tmp, os.path.join(d, _CACHE))

def _targets(cur, table):
    street, venue, city, state = _COLS[table]
    cur.execute(f"""
        SELECT location_id, max({street}), max({venue}), max({city}), max({state})
        FROM {table}
        WHERE gps_lat IS NULL
          AND ({venue} IS NOT NULL OR {street} IS NOT NULL)
        GROUP BY location_id
    """)
    return cur.fetchall()

def _fixTable(conn, table, cache, out_dir, do_write):
    with conn.cursor() as cur:
        rows = _targets(cur, table)
        conn.rollback()
    print(f"  {table:<13}: {len(rows)} null venues with a clue to retry", flush=True)
    if not do_write:
        return []
    placed = []; hit = 0
    for i, (loc, street, venue, city, state) in enumerate(rows, 1):
        res = _resolve(street, venue, city, state, cache)
        if res:
            hit += 1; placed.append((loc, res[0], res[1]))
        if i % 25 == 0:
            print(f"      {i}/{len(rows)}  (placed {hit})", flush=True); _saveCache(out_dir, cache)
    _saveCache(out_dir, cache)
    print(f"    -> recovered {hit}/{len(rows)}", flush=True)
    with conn.cursor() as cur:
        for loc, la, lo in placed:
            cur.execute(f"UPDATE {table} SET gps_lat=%s, gps_long=%s "
                        f"WHERE location_id=%s AND gps_lat IS NULL", (la, lo, loc))
    conn.commit()
    return placed

def _mirror(conn, placed):
    by = {loc: (la, lo) for loc, la, lo in placed}
    print(f"  meets_tf     : mirroring {len(by)} recovered venues", flush=True)
    with conn.cursor() as cur:
        for loc, (la, lo) in by.items():
            cur.execute("UPDATE meets_tf SET gps_lat=%s, gps_long=%s "
                        "WHERE location_id=%s AND gps_lat IS NULL", (la, lo, loc))
    conn.commit()

def _run(out_dir, do_write):
    cache = _loadCache(out_dir); placed = []
    with getConn() as conn:
        for table in ("meets_tf_meta", "meets", "meets_tfrrs"):
            placed += _fixTable(conn, table, cache, out_dir, do_write)
        if do_write:
            _mirror(conn, placed)
    if not do_write:
        print("\n  [dry-run] counts only. add --apply to recover.", flush=True)
    else:
        print("\n  done. re-run show_nulled.py / audit_gps_sanity.py to confirm.", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()
    initPool()
    _run(args.out, args.apply)

if __name__ == "__main__":
    main()