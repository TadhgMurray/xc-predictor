# Project: xc-predictor
# File:    scripts/probe_census.py
# Purpose: 3 real venues, printed loud. Shows the EXACT query string sent to
#          Census + the raw HTTP status + whether it matched, then the Nominatim
#          fallback. No loop, no DB writes, no sleeps. Tells us in 5 seconds
#          whether Census is being fed wrong input or is throttling.
# USAGE:  python scripts\probe_census.py
# ============================================================================
import sys, json
import requests
sys.path.insert(0, "scripts")
from database import getConn, initPool

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMIN  = "https://nominatim.openstreetmap.org/search"
UA = "xc-predictor-probe (contact: tadhg)"


def probe(venue, city, state, raw):
    census_q = ", ".join(p for p in (city, state) if p) or None
    nomin_q  = ", ".join(p for p in (venue, city, state) if p) or raw
    print(f"\n  VENUE: {venue!r}  city={city!r} state={state!r}")
    print(f"    census query -> {census_q!r}")
    if census_q:
        try:
            r = requests.get(CENSUS, params={"address":census_q,
                             "benchmark":"Public_AR_Current","format":"json"}, timeout=30)
            print(f"    census HTTP {r.status_code}")
            body = r.json()
            matches = body.get("result",{}).get("addressMatches",[])
            print(f"    census matches: {len(matches)}")
            if matches:
                c = matches[0]["coordinates"]
                print(f"    census -> lat={c['y']} lon={c['x']}  MATCH")
            else:
                # show a snippet so we see if it's an error page / weird body
                print(f"    census body[:200]: {json.dumps(body)[:200]}")
        except Exception as e:
            print(f"    census EXCEPTION: {e!r}")
    print(f"    nominatim query -> {nomin_q!r}")
    try:
        r = requests.get(NOMIN, params={"q":nomin_q,"format":"json","limit":1},
                         headers={"User-Agent":UA}, timeout=30)
        print(f"    nominatim HTTP {r.status_code}  results: {len(r.json())}")
    except Exception as e:
        print(f"    nominatim EXCEPTION: {e!r}")


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT venue_name, city, state, location_raw
            FROM meets_tfrrs
            WHERE gps_lat IS NULL AND city IS NOT NULL AND state IS NOT NULL
            ORDER BY random() LIMIT 3
        """)
        rows = cur.fetchall(); conn.rollback()
    for v, c, st, raw in rows:
        probe(v, c, st, raw)


if __name__ == "__main__":
    main()