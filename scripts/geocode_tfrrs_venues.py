# Project: xc-predictor
# File:    scripts/geocode_tfrrs_venues.py
# Purpose: Place leftover tfrrs XC meets by geocoding their DISTINCT venues,
#          minting a location_id per venue, stamping gps + id onto every meet
#          there. Rewritten after profiling the real data:
#            ~923 rows carry a state           -> Census (US street addresses)
#              74 null-state rows hide a state -> recover from raw, then Census
#               6 name-only                    -> Nominatim
#           2,908 empty location_raw           -> UNPLACEABLE, cached null
#
# ============================================================================
# WHAT CHANGED FROM THE FIRST VERSION (all lessons from the data)
# ============================================================================
#   * Census is fed PARSED street/city/state/zip, not the whole raw crammed in
#     one field (that placed 75/968). We also RECOVER a state+zip hiding inside
#     a null-state raw ("...langhorne, pa 19047"), incl. full-name states
#     ("Ohio") and malformed zips ("1641l7").
#   * Empty raws are detected and cached null immediately -- never sent anywhere.
#   * Nominatim is a CASCADE (raw+state -> raw -> bare name) with RETRY+BACKOFF,
#     so a 429/timeout sleeps and retries instead of crashing a long run.
#
# THREE STAGES, network isolated to `geocode`:
#   collect : leftovers -> distinct venues (with parsed address parts) -> CSV
#   geocode : Census batch (stated+recovered) then Nominatim cascade (residue)
#   apply   : mint location_id per resolved venue (900M base), stamp meets
#
# USAGE
#   python scripts/geocode_tfrrs_venues.py collect
#   python scripts/geocode_tfrrs_venues.py geocode
#   python scripts/geocode_tfrrs_venues.py apply          # dry run
#   python scripts/geocode_tfrrs_venues.py apply --apply
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
# CHUNK 1 -- CONSTANTS
# ================================================================== #
_VENUES_CSV = "geocode_venues.csv"
_CACHE = "geocode_cache.json"
_MINT_BASE = 900_000_000
_NOMINATIM_UA = "xc-predictor-venue-backfill (contact: tadhg)"
_NOMINATIM_SLEEP = 1.1
_NOMINATIM_RETRIES = 3

# full-name -> 2-letter, so a raw ending "..., Ohio 45177" recovers OH.
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}
_STATE_CODES = set(_STATE_NAMES.values())


# ================================================================== #
# CHUNK 2 -- VENUE KEY
# ================================================================== #

def _normRaw(raw):
    if not raw:
        return None
    s = re.sub(r"[^\w\s]", " ", raw.strip().lower())
    return re.sub(r"\s+", " ", s).strip() or None


def _venueKey(raw, state):
    n = _normRaw(raw)
    if n is None:
        return None
    return f"{n}|{(state or '').strip().upper()}"


def _isEmpty(raw):
    return not raw or not raw.strip()


# ================================================================== #
# CHUNK 3 -- ADDRESS PARSING (for Census)
# ================================================================== #

# _recoverState
# Purpose : pull a 2-letter state out of a raw string when the state column is
#           null. Handles ", pa 19047", ", Ohio 45177", ", OK, Oklahoma 73170".
# Output  : "PA" | None.
def _recoverState(raw):
    if not raw:
        return None
    low = raw.lower()
    # full state name anywhere (longest first so "new york" beats "york")
    for name in sorted(_STATE_NAMES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return _STATE_NAMES[name]
    # 2-letter code after a comma
    m = re.search(r",\s*([A-Za-z]{2})\b", raw)
    if m and m.group(1).upper() in _STATE_CODES:
        return m.group(1).upper()
    return None


# _parseAddress
# Purpose : best-effort (street, city, state, zip) from a tfrrs raw + its known
#           state. street = a "123 Word Word" run if present; city = the token
#           run before the state; zip = a clean 5-digit (malformed like "1641l7"
#           is dropped rather than sent wrong).
# Output  : dict for the Census CSV, plus the resolved state (recovered if the
#           column was null).
def _parseAddress(raw, state):
    st = (state or "").strip().upper() or _recoverState(raw)
    zc = ""
    zm = re.search(r"\b(\d{5})\b", raw or "")
    if zm:
        zc = zm.group(1)
    sm = re.search(r"\d+\s+[A-Za-z][A-Za-z .]+", raw or "")
    street = sm.group(0).strip() if sm else ""
    # city: text just before the state token, best-effort
    city = ""
    if st and raw:
        cm = re.search(r"([A-Za-z .'-]+),\s*" + st + r"\b", raw, re.I)
        if cm:
            city = cm.group(1).strip().split("  ")[-1][:40]
    return {"street": street, "city": city, "state": st or "", "zip": zc}, st


# ================================================================== #
# CHUNK 4 -- STAGE collect
# ================================================================== #

def _collect(out_dir):
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, location_raw, venue_name, city, state
            FROM meets_tfrrs
            WHERE sport = 'XC' AND location_id IS NULL
        """)
        rows = cur.fetchall()
        conn.rollback()

    venues, empties = {}, 0
    for meet_id, raw, venue, city, state in rows:
        if _isEmpty(raw):
            empties += 1
            key = f"__empty__{meet_id}"      # unique -> counted, never queried
            venues.setdefault(key, {"key": key, "raw": "", "empty": True,
                                    "meets": []})["meets"].append(meet_id)
            continue
        key = _venueKey(raw, state)
        v = venues.setdefault(key, {"key": key, "raw": raw, "state": state or "",
                                    "empty": False, "meets": []})
        v["meets"].append(meet_id)

    path = os.path.join(out_dir, _VENUES_CSV)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "empty", "street", "city", "state", "zip", "raw",
                    "n_meets"])
        for v in venues.values():
            if v["empty"]:
                w.writerow([v["key"], "1", "", "", "", "", "", len(v["meets"])])
            else:
                parts, _ = _parseAddress(v["raw"], v["state"])
                w.writerow([v["key"], "0", parts["street"], parts["city"],
                            parts["state"], parts["zip"], v["raw"],
                            len(v["meets"])])
    real = sum(1 for v in venues.values() if not v["empty"])
    print(f"  collect: {len(rows)} leftover meets")
    print(f"    {real} distinct GEOCODABLE venues")
    print(f"    {empties} meets with empty location_raw (unplaceable, will be null)")
    print(f"  wrote {path}\n  next: geocode")


# ================================================================== #
# CHUNK 5 -- STAGE geocode
# ================================================================== #

def _loadCache(out_dir):
    p = os.path.join(out_dir, _CACHE)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _saveCache(out_dir, cache):
    p = os.path.join(out_dir, _CACHE)
    tmp = p + ".tmp"
    json.dump(cache, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, p)


def _readVenues(out_dir):
    p = os.path.join(out_dir, _VENUES_CSV)
    if not os.path.exists(p):
        sys.exit(f"  run 'collect' first -- {p} missing")
    return list(csv.DictReader(open(p, encoding="utf-8")))


def _censusBatch(batch):
    """One POST, up to 10k rows: id, street, city, state, zip. Returns
    {key:(lat,lon)} for matches. Used for venues we parsed into clean parts."""
    import requests
    buf = io.StringIO()
    w = csv.writer(buf)
    for i, v in enumerate(batch):
        w.writerow([i, v.get("street", ""), v.get("city", ""),
                    v.get("state", ""), v.get("zip", "")])
    r = requests.post(
        "https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
        files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")},
        data={"benchmark": "Public_AR_Current"}, timeout=300)
    r.raise_for_status()
    out = {}
    for row in csv.reader(io.StringIO(r.text)):
        if len(row) >= 6 and row[2] == "Match" and row[5]:
            lon, lat = row[5].split(",")
            out[batch[int(row[0])]["key"]] = (float(lat), float(lon))
    return out


# _censusOneLine
# Purpose : hand Census the WHOLE raw string and let ITS parser split street/
#           city/state/zip. This beats our regex on messy strings like
#           "4150 Patricks Point Dr Trinidad CA 95570" where our split mangled
#           street vs city. One address per GET (Census one-line is not batched),
#           so we only use it for the Census-batch MISSES, not everything.
# Output  : (lat, lon) or None.
def _censusOneLine(raw):
    import requests
    try:
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": raw, "benchmark": "Public_AR_Current",
                    "format": "json"}, timeout=30)
        r.raise_for_status()
        matches = r.json().get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            return float(c["y"]), float(c["x"])       # y=lat, x=lon
    except Exception:
        pass
    return None


def _nominatimCascade(raw, state):
    """Try richest -> barest query; first hit wins. Now also tries the venue NAME
    alone (the leading words before any street number), which Nominatim often
    knows for parks/universities/golf courses even when the full address string
    confuses it. Retry+backoff per attempt."""
    import requests
    queries = []
    if state:
        queries.append(f"{raw}, {state}, USA")
    queries.append(raw)
    # drop trailing zip and everything after it
    bare = re.sub(r"\d{5}.*$", "", raw).strip(" ,")
    if bare and bare not in queries:
        queries.append(bare)
    # the venue NAME: the words BEFORE the first street number. "Avery Park 100
    # Avery Park Road..." -> "Avery Park". Named places resolve on this alone.
    name = re.split(r"\s+\d", raw)[0].strip(" ,")
    if name and len(name) >= 4 and name not in queries:
        queries.append(f"{name}, {state}, USA" if state else name)
    # last resort: name with no qualifier
    if name and name not in queries:
        queries.append(name)

    for q in queries:
        for attempt in range(_NOMINATIM_RETRIES):
            try:
                r = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": 1},
                    headers={"User-Agent": _NOMINATIM_UA}, timeout=30)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                j = r.json()
                if j:
                    return float(j[0]["lat"]), float(j[0]["lon"])
                break                       # valid empty answer -> next query
            except Exception:
                time.sleep(3 * (attempt + 1))
        time.sleep(_NOMINATIM_SLEEP)
    return None


def _geocode(out_dir, retry_misses=False):
    venues = _readVenues(out_dir)
    cache = _loadCache(out_dir)

    # empties: cache null immediately, never query
    for v in venues:
        if v["empty"] == "1" and v["key"] not in cache:
            cache[v["key"]] = None
    _saveCache(out_dir, cache)

    # retry mode: drop cached NULLS for non-empty venues so they get another pass
    # with the improved Census one-line + Nominatim cascade. Successes (truthy)
    # and empties stay cached, so we never re-hit the 515 already found.
    if retry_misses:
        empty_keys = {v["key"] for v in venues if v["empty"] == "1"}
        dropped = 0
        for k in [k for k, val in cache.items()
                  if val is None and k not in empty_keys]:
            del cache[k]
            dropped += 1
        print(f"  retry-misses: cleared {dropped} cached misses for another pass")

    todo = [v for v in venues if v["empty"] != "1" and v["key"] not in cache]
    print(f"  geocode: {len(todo)} geocodable venues to do "
          f"({sum(1 for v in venues if v['empty']=='1')} empties -> null)")

    # PASS 1: Census batch for anything with a state (parsed street/city/zip).
    batch = [v for v in todo if v["state"]]
    if batch:
        print(f"  Census batch: {len(batch)} stated venues ...")
        try:
            hits = _censusBatch(batch)
            for v in batch:
                if v["key"] in hits:
                    lat, lon = hits[v["key"]]
                    cache[v["key"]] = {"lat": lat, "lon": lon, "src": "census"}
            _saveCache(out_dir, cache)
            print(f"  Census batch placed {len(hits)}")
        except Exception as e:
            print(f"  Census batch failed ({e}); those fall through")

    # PASS 2: Census ONE-LINE on whatever the batch missed. Let Census parse the
    # raw string itself -- recovers clean addresses our regex mangled. One GET
    # each, so only on the residue (small), with a courtesy sleep.
    census_residue = [v for v in todo if v["key"] not in cache and v["raw"]]
    if census_residue:
        print(f"  Census one-line: retrying {len(census_residue)} on raw string ...")
        placed_ol = 0
        for i, v in enumerate(census_residue):
            res = _censusOneLine(v["raw"])
            if res:
                cache[v["key"]] = {"lat": res[0], "lon": res[1], "src": "census1"}
                placed_ol += 1
            if i % 25 == 0:
                _saveCache(out_dir, cache)
            time.sleep(0.2)
        _saveCache(out_dir, cache)
        print(f"  Census one-line placed {placed_ol}")

    # PASS 3: Nominatim cascade for everything still unplaced.
    residue = [v for v in venues if v["empty"] != "1" and v["key"] not in cache]
    print(f"  Nominatim cascade: {len(residue)} residue "
          f"(~{len(residue)*_NOMINATIM_SLEEP*2/60:.0f} min)")
    for i, v in enumerate(residue):
        res = _nominatimCascade(v["raw"], v["state"])
        cache[v["key"]] = ({"lat": res[0], "lon": res[1], "src": "osm"}
                           if res else None)
        if i % 20 == 0:
            _saveCache(out_dir, cache)
        time.sleep(_NOMINATIM_SLEEP)
    _saveCache(out_dir, cache)

    placed = sum(1 for x in cache.values() if x)
    total_real = sum(1 for v in venues if v["empty"] != "1")
    print(f"  done: {placed}/{total_real} geocodable venues placed\n  next: apply")


# ================================================================== #
# CHUNK 6 -- STAGE apply
# ================================================================== #

def _apply(out_dir, do_write):
    cache = _loadCache(out_dir)
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT meet_id, location_raw, state
                FROM meets_tfrrs
                WHERE sport = 'XC' AND location_id IS NULL
            """)
            rows = cur.fetchall()
            conn.rollback()

        key_to_meets = {}
        for meet_id, raw, state in rows:
            key = f"__empty__{meet_id}" if _isEmpty(raw) else _venueKey(raw, state)
            key_to_meets.setdefault(key, []).append(meet_id)

        resolved = sorted(k for k in key_to_meets if cache.get(k))
        mint = {k: _MINT_BASE + i for i, k in enumerate(resolved)}
        n_meets = sum(len(key_to_meets[k]) for k in resolved)
        unresolved = sum(len(key_to_meets[k]) for k in key_to_meets
                         if not cache.get(k))
        print(f"  apply: {len(resolved)} resolved venues -> {n_meets} meets; "
              f"mint {_MINT_BASE}..{_MINT_BASE+len(resolved)-1}")
        print(f"  {unresolved} meets stay NULL (empty raw or geocoder miss)")
        if not do_write:
            print("  [dry-run] nothing written")
            return

        with conn.cursor() as cur:
            for k in resolved:
                c = cache[k]
                cur.execute("""
                    UPDATE meets_tfrrs
                    SET location_id = %s, gps_lat = %s, gps_long = %s
                    WHERE meet_id = ANY(%s) AND sport='XC' AND location_id IS NULL
                """, (mint[k], c["lat"], c["lon"], key_to_meets[k]))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) FILTER (WHERE location_id IS NOT NULL),
                                  count(*) FROM meets_tfrrs WHERE sport='XC'""")
            have, tot = cur.fetchone()
        print(f"  meets_tfrrs XC now located: {have:,} / {tot:,}")


# ================================================================== #
# CHUNK 8 -- STAGE deadends (hand-filled dead-ends; no network, no mint)
# ------------------------------------------------------------------ #
# tf_deadend_venues.final.tsv was filled offline by fill_deadend_venues.py.
# There is nothing to geocode or mint here: the coords are already done and
# every dead-end location_id ALREADY EXISTS (that is why it was a dead-end --
# the id was real, only the name/gps were missing). This stage only STAMPS
# gps onto the venues' rows, in BOTH gps-bearing tf tables because
# geometry_db resolves location_id via COALESCE(meets_tf, meets_tf_meta);
# writing both means the weather backfill finds the coord whichever side it
# reads. Only null-gps rows are touched, so it is idempotent and re-runnable.
# ================================================================== #

_DEADEND_TSV = "tf_deadend_venues.final.tsv"     # sits in --out dir (scripts/)
_DEADEND_TABLES = ("meets_tf", "meets_tf_meta")  # both hold gps + location_id


# _readDeadendCoords
# Purpose : load location_id -> (lat, lon) from the hand-filled TSV.
# Arguments: path -- the final dead-end tsv (str). Has a '#' comment header,
#            7 columns; still-null rows have empty gps cells.
# Output  : dict[str, (float, float)] for rows that actually carry coords;
#           comment lines, blanks, and un-filled rows are skipped.
def _readDeadendCoords(path):
    coords = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            c = line.rstrip("\n").split("\t")
            if len(c) < 7 or not c[5] or not c[6]:   # need BOTH lat and lon
                continue
            coords[c[0].strip()] = (float(c[5]), float(c[6]))
    return coords


# _deadendCensus
# Purpose : dry-run count -- how many ids match and how many null-gps rows
#           one table would fill. Read-only (rolls back).
# Arguments: conn -- open connection; table -- whitelisted table name (str);
#            ids -- list[int] of location_ids.
# Output  : (n_ids_matched, n_rows) ints.
def _deadendCensus(conn, table, ids):
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT count(DISTINCT location_id), count(*)
            FROM {table}
            WHERE location_id = ANY(%s) AND gps_lat IS NULL
        """, (ids,))
        n_ids, n_rows = cur.fetchone()
        conn.rollback()
    return n_ids, n_rows


# _deadendStamp
# Purpose : write coords into one table (only null-gps rows). Commit is the
#           CALLER's job.
# Arguments: conn -- connection; table -- whitelisted name; coords -- the
#            id->(lat,lon) map.
# Output  : None. One small UPDATE per id (a few hundred total).
def _deadendStamp(conn, table, coords):
    with conn.cursor() as cur:
        for locId, (lat, lon) in coords.items():
            cur.execute(f"""
                UPDATE {table}
                SET gps_lat = %s, gps_long = %s
                WHERE location_id = %s AND gps_lat IS NULL
            """, (lat, lon, int(locId)))


# _deadends
# Purpose : stage entry point -- read tsv, dry-run census, then (with --apply)
#           stamp both tf tables and re-report the remaining nulls.
# Arguments: out_dir -- dir holding the tsv (str); do_write -- commit flag.
# Output  : None.
def _deadends(out_dir, do_write):
    path = os.path.join(out_dir, _DEADEND_TSV)
    if not os.path.exists(path):
        sys.exit(f"  missing {path} -- put the filled dead-end file there")
    coords = _readDeadendCoords(path)
    ids = [int(k) for k in coords]
    print(f"  deadends: {len(ids)} filled location_ids from {path}")

    with getConn() as conn:
        for table in _DEADEND_TABLES:                # always show the census
            n_ids, n_rows = _deadendCensus(conn, table, ids)
            print(f"    {table}: {n_ids} ids matched, "
                  f"{n_rows} null-gps rows to fill")

        if not do_write:
            print("  [dry-run] nothing written  (add --apply to commit)")
            return

        for table in _DEADEND_TABLES:
            _deadendStamp(conn, table, coords)
        conn.commit()

        for table in _DEADEND_TABLES:                # confirm what's left
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT count(*) FILTER (WHERE gps_lat IS NULL), count(*)
                    FROM {table} WHERE location_id = ANY(%s)
                """, (ids,))
                miss, tot = cur.fetchone()
                conn.rollback()
            print(f"    {table}: {miss} of {tot} dead-end-id rows still null")


# ================================================================== #
# CHUNK 7 -- CLI
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Geocode leftover tfrrs venues; mint ids; stamp meets.")
    ap.add_argument("stage", choices=["collect", "geocode", "apply", "deadends"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--retry-misses", action="store_true",
                    help="(geocode) clear cached misses and retry them with the "
                         "improved Census one-line + Nominatim cascade")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.stage == "geocode":
        _geocode(args.out, retry_misses=args.retry_misses)
        return
    initPool()
    if args.stage == "collect":
        _collect(args.out)
    elif args.stage == "deadends":
        _deadends(args.out, args.apply)
    else:
        _apply(args.out, args.apply)


if __name__ == "__main__":
    main()