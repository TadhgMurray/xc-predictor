# Project: xc-predictor
# File:    scripts/propagate_tfrrs_geometry.py
# Purpose: Stamp track geometry onto tfrrs TF meets, three routes in
#          descending evidence rank:
#            canon (3)      inherit the canon-linked anet twin's geometry
#            venue-dict (2) apply a venue_name -> location dictionary that was
#                           LEARNED at canon meets (proven-by-proxy)
#            title-parse(1) parse (Banked) / '192m' / 'Indoor' annotations out
#                           of the venue/meet strings (direct but thin)
#          Every proposed stamp is cross-checked against the parsed annotations;
#          contradictions are resolved by evidence rank (see _resolveStamp).
#          Writes tfrrs_meet_geometry: a DERIVED table, delete-then-insert, so
#          every run is a fresh function of the evidence (four-layer model).
#          DRY-RUN BY DEFAULT.  Apply:  python scripts/propagate_tfrrs_geometry.py --apply

import sys
sys.path.insert(0, "scripts")

import re
import argparse
import psycopg2.extras
from database import getConn

# ------------------------------------------------------------------ #
# CONSTANTS / KNOBS
# ------------------------------------------------------------------ #

SPORT = "TF"                 # this pass is TF-only; XC has no track geometry
MIN_MEET_CONF = 2            # match the merge's MEET threshold — the dict must
                             # not learn from links the merge itself didn't trust
MIN_DICT_SUPPORT = 1         # canon meets required to admit a dict entry.
                             # 1 = trust any proven sighting; raise to 2 if the
                             # dry-run's low-support sample looks dirty.
LENGTH_TOLERANCE_M = 5.0     # parsed-vs-stamped lengths within this agree
SANE_LENGTH = (150.0, 440.0) # a parsed length outside this is a false hit
GENERIC_VENUE_TOKENS = ["tbd"]   # names containing these can never enter the
                                 # dict ('**Venue TBD**' is live in the data)

# Route names -> confidence, one place. Higher wins when routes collide.
ROUTE_CONF = {"canon": 3, "venue-dict": 2, "title-parse": 1}

# ------------------------------------------------------------------ #
# THE PARSER (route 4' — three tiny single-signal helpers + one assembler)
# ------------------------------------------------------------------ #
# re.compile builds the pattern once at import; re.I = case-insensitive.

_BANKED_RX = re.compile(r"banked", re.I)          # matches '(Banked)' too
_FLAT_RX   = re.compile(r"\(\s*flat\s*\)", re.I)  # PARENTHESIZED only — bare
                                                  # 'flat' collides with names
                                                  # like 'Flatirons'
_LENGTH_RX = re.compile(r"\b(\d{3})\s*m\b", re.I) # 3 digits + optional space
                                                  # + 'm' at a word boundary,
                                                  # e.g. '192m', '300 M'
_INDOOR_RX = re.compile(r"indoor", re.I)


# _parseType
# Purpose:   Read a track_type claim out of one venue string.
# Arguments: venue — the venue_name text (may be None).
# Output:    'Banked' / 'Flat' / None. Banked wins if both somehow match.
#            None means "the string makes no claim" — never a denial.
def _parseType(venue):
    if not venue:                      # None and '' both fail this truth test
        return None
    if _BANKED_RX.search(venue):       # .search scans anywhere in the string
        return "Banked"
    if _FLAT_RX.search(venue):
        return "Flat"
    return None


# _parseLength
# Purpose:   Read an explicit track length out of one venue string.
# Arguments: venue — the venue_name text (may be None).
# Output:    the length as float, or None (no figure, or an insane one —
#            '100m' in a name is likelier an event than a track).
def _parseLength(venue):
    if not venue:
        return None
    m = _LENGTH_RX.search(venue)
    if not m:
        return None
    length = float(m.group(1))         # group(1) = the (\d{3}) capture only
    lo, hi = SANE_LENGTH
    return length if lo <= length <= hi else None


# _parseIndoor
# Purpose:   Read an indoor claim from venue OR meet name.
# Arguments: venue, meet_name — either may be None.
# Output:    1 or None — NEVER 0: absence of 'indoor' is not evidence of
#            outdoor, and this asymmetry is load-bearing downstream.
def _parseIndoor(venue, meet_name):
    for s in (venue, meet_name):
        if s and _INDOOR_RX.search(s):
            return 1
    return None


# parseVenueSignals
# Purpose:   The whole route-4' read for one meet, assembled from the three
#            single-signal helpers above.
# Arguments: venue, meet_name — the two title strings (either may be None).
# Output:    dict {track_type, track_length, is_indoor}, each None when the
#            strings make no claim about it.
def parseVenueSignals(venue, meet_name) -> dict:
    return {
        "track_type":   _parseType(venue),
        "track_length": _parseLength(venue),
        "is_indoor":    _parseIndoor(venue, meet_name),
    }

# ------------------------------------------------------------------ #
# EVIDENCE LOADERS (small queries, plain tuples)
# ------------------------------------------------------------------ #

# _loadCanonLinks
# Purpose:   The meet-level canon evidence: which tfrrs meet IS which anet meet.
# Arguments: conn — open connection.
# Output:    list of (tfrrs_id, anet_id), confidence >= MIN_MEET_CONF only.
def _loadCanonLinks(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tfrrs_id, anet_id FROM entity_links
            WHERE entity_type = 'meet' AND sport = %s AND confidence >= %s
        """, (SPORT, MIN_MEET_CONF))
        return cur.fetchall()


# _loadAnetGeometry
# Purpose:   anet's meet-level geometry — what route 1 inherits and the dict
#            learns from.
# Arguments: conn — open connection.
# Output:    dict anet_meet_id -> (track_type, track_length, is_indoor,
#            location_id). Meets with NOTHING useful are skipped.
def _loadAnetGeometry(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, track_type, track_length, is_indoor, location_id
            FROM meets_tf_meta
            WHERE source = 'anet'
              AND (track_type IS NOT NULL OR track_length IS NOT NULL)
        """)
        return {mid: (tt, tl, ind, loc) for mid, tt, tl, ind, loc in cur}


# _loadTfrrsMeets
# Purpose:   Every tfrrs TF meet with its two title strings — the population
#            being stamped.
# Arguments: conn — open connection.
# Output:    list of (meet_id, venue_name, meet_name).
def _loadTfrrsMeets(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, venue_name, meet_name
            FROM meets_tfrrs WHERE sport = %s
        """, (SPORT,))
        return cur.fetchall()

# ------------------------------------------------------------------ #
# ROUTE BUILDERS (each returns {tfrrs_meet_id: stamp-dict})
# ------------------------------------------------------------------ #

# _makeStamp
# Purpose:   One stamp record, one shape, every route — so the writer and the
#            cross-check never care which route made a stamp.
# Arguments: geometry fields (any may be None), method — a ROUTE_CONF key.
# Output:    the stamp dict.
def _makeStamp(track_type, track_length, is_indoor, location_id, method) -> dict:
    return {"track_type": track_type, "track_length": track_length,
            "is_indoor": is_indoor, "location_id": location_id,
            "method": method, "confidence": ROUTE_CONF[method]}


# buildRoute1
# Purpose:   Canon inheritance: a canon-linked tfrrs meet IS its anet twin
#            (proven at result level), so it gets the twin's geometry verbatim.
# Arguments: links — (tfrrs_id, anet_id) pairs; anet_geo — _loadAnetGeometry.
# Output:    {tfrrs_meet_id: stamp} for twins that HAVE geometry.
def buildRoute1(links, anet_geo) -> dict:
    stamps = {}
    for tfrrs_id, anet_id in links:
        geo = anet_geo.get(anet_id)    # .get -> None when twin has no geometry
        if geo:
            tt, tl, ind, loc = geo
            stamps[tfrrs_id] = _makeStamp(tt, tl, ind, loc, "canon")
    return stamps


# _isGenericName
# Purpose:   Gate names that can never safely key a dictionary entry.
# Arguments: name — a venue_name string.
# Output:    True for None/blank or any GENERIC_VENUE_TOKENS hit.
def _isGenericName(name) -> bool:
    if not name or not name.strip():
        return True
    low = name.lower()
    return any(tok in low for tok in GENERIC_VENUE_TOKENS)


# buildVenueDict
# Purpose:   Learn venue_name -> geometry from route-1 meets ONLY (a name match
#            is trusted because it was PROVEN by result-level evidence somewhere
#            — the dedup two-stage gate, transplanted). An entry must be
#            UNANIMOUS: every canon sighting of the name agrees on location AND
#            geometry, or the name is ambiguous and learns nothing.
# Arguments: links, anet_geo — as above; venue_of — {tfrrs_meet_id: venue_name}.
# Output:    (dict {venue_name: stamp-fields-tuple}, rejects {reason: count}).
def buildVenueDict(links, anet_geo, venue_of):
    seen = {}                          # name -> set of geometry tuples observed
    for tfrrs_id, anet_id in links:
        geo = anet_geo.get(anet_id)
        name = venue_of.get(tfrrs_id)
        if geo and not _isGenericName(name):
            seen.setdefault(name, set()).add(geo)   # set: dupes collapse free

    dictionary, rejects = {}, {"ambiguous": 0, "low_support": 0}
    for name, geos in seen.items():
        if len(geos) > 1:              # canon sightings disagree -> unusable
            rejects["ambiguous"] += 1
        elif len(geos) < MIN_DICT_SUPPORT:
            rejects["low_support"] += 1
        else:
            dictionary[name] = next(iter(geos))     # the one agreed tuple
    return dictionary, rejects


# buildRoute2
# Purpose:   Apply the learned dictionary to meets route 1 didn't reach.
# Arguments: tfrrs_meets — (meet_id, venue, meet_name) rows;
#            dictionary — from buildVenueDict; already — route-1 stamp keys.
# Output:    {tfrrs_meet_id: stamp} for dictionary hits.
def buildRoute2(tfrrs_meets, dictionary, already) -> dict:
    stamps = {}
    for meet_id, venue, _mn in tfrrs_meets:
        if meet_id not in already and venue in dictionary:
            tt, tl, ind, loc = dictionary[venue]
            stamps[meet_id] = _makeStamp(tt, tl, ind, loc, "venue-dict")
    return stamps


# buildRoute3
# Purpose:   Title-parse stamps for meets NO other route reached — thin but
#            direct evidence (may carry only is_indoor; that feeds nothing
#            today and future work later, and NULLs are honest).
# Arguments: tfrrs_meets, already — as above (already = routes 1+2 keys).
# Output:    {tfrrs_meet_id: stamp} where the parser found ANY signal.
def buildRoute3(tfrrs_meets, already) -> dict:
    stamps = {}
    for meet_id, venue, meet_name in tfrrs_meets:
        if meet_id in already:
            continue
        p = parseVenueSignals(venue, meet_name)
        if any(v is not None for v in p.values()):  # at least one real claim
            stamps[meet_id] = _makeStamp(p["track_type"], p["track_length"],
                                         p["is_indoor"], None, "title-parse")
    return stamps

# ------------------------------------------------------------------ #
# CROSS-CHECK (route 4' as validator) + ASSEMBLY
# ------------------------------------------------------------------ #

# _contradicts
# Purpose:   Does a parsed annotation CONTRADICT a proposed stamp? Only a
#            positive claim can contradict — parsed None never disputes.
# Arguments: stamp — a stamp dict; parsed — parseVenueSignals output.
# Output:    list of human-readable contradiction strings (empty = agrees).
def _contradicts(stamp, parsed) -> list:
    probs = []
    if (parsed["track_type"] and stamp["track_type"]
            and parsed["track_type"] != stamp["track_type"]):
        probs.append(f"type: title={parsed['track_type']} stamp={stamp['track_type']}")
    if (parsed["track_length"] and stamp["track_length"]
            and abs(parsed["track_length"] - stamp["track_length"]) > LENGTH_TOLERANCE_M):
        probs.append(f"length: title={parsed['track_length']:g} stamp={stamp['track_length']:g}")
    return probs


# _resolveStamp
# Purpose:   The evidence-rank rule for one contradicted stamp:
#              canon      -> KEEP + flag  (result-level proof outranks a title)
#              venue-dict -> WITHHOLD     (a name-proxy loses to direct
#                                          evidence about THIS meet)
# Arguments: stamp, probs — the stamp and its contradiction strings.
# Output:    (keep: bool, note: str).
def _resolveStamp(stamp, probs):
    if stamp["method"] == "canon":
        return True, f"FLAG canon: {'; '.join(probs)}"
    return False, f"WITHHELD {stamp['method']}: {'; '.join(probs)}"


# assembleStamps
# Purpose:   Merge the three routes (priority already enforced by build order),
#            run every stamp through the cross-check, apply _resolveStamp.
# Arguments: routes — [route1, route2, route3] stamp dicts, priority order;
#            titles — {meet_id: (venue, meet_name)} for parsing.
# Output:    (final {meet_id: stamp}, flags [str], withheld [str]).
def assembleStamps(routes, titles):
    merged = {}
    for route in routes:               # dicts arrive highest-priority first;
        for mid, stamp in route.items():   # first writer wins, later skipped
            merged.setdefault(mid, stamp)

    final, flags, withheld = {}, [], []
    for mid, stamp in merged.items():
        venue, meet_name = titles.get(mid, (None, None))
        probs = _contradicts(stamp, parseVenueSignals(venue, meet_name))
        if not probs:
            final[mid] = stamp
            continue
        keep, note = _resolveStamp(stamp, probs)
        (flags if keep else withheld).append(f"meet {mid}: {note}")
        if keep:
            final[mid] = stamp
    return final, flags, withheld

# ------------------------------------------------------------------ #
# REPORT + APPLY
# ------------------------------------------------------------------ #

# _report
# Purpose:   The dry-run's whole output: per-route counts, dict health,
#            contradiction lists, and a few sample stamps per route.
def _report(routes, dictionary, rejects, final, flags, withheld) -> None:
    names = ["canon", "venue-dict", "title-parse"]
    print("== proposed stamps ==")
    for name, route in zip(names, routes):
        print(f"  {name:<12}: {len(route):,}")
    print(f"  final (after cross-check): {len(final):,}")
    print(f"\n== venue dictionary ==  entries={len(dictionary):,}  "
          f"rejected: ambiguous={rejects['ambiguous']:,} "
          f"low_support={rejects['low_support']:,}")
    print(f"\n== cross-check ==  canon flags={len(flags):,}  withheld={len(withheld):,}")
    for line in (flags + withheld)[:15]:
        print(f"  {line}")
    print("\n== samples ==")
    for name, route in zip(names, routes):
        for mid in list(route)[:3]:
            if mid in final:
                s = final[mid]
                print(f"  [{name}] meet {mid}: type={s['track_type']} "
                      f"len={s['track_length']} indoor={s['is_indoor']}")


# applyStamps
# Purpose:   The write path: ensure the derived table, DELETE this sport's old
#            rows, bulk-insert the new ones, read back the count. Rows, not
#            intentions — printed at every step.
# Arguments: conn — open connection; final — {meet_id: stamp}.
def applyStamps(conn, final) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tfrrs_meet_geometry (
                meet_id      BIGINT  NOT NULL,
                sport        TEXT    NOT NULL,
                track_type   TEXT,
                track_length REAL,
                is_indoor    INTEGER,
                location_id  BIGINT,
                method       TEXT    NOT NULL,
                confidence   INTEGER NOT NULL,
                PRIMARY KEY (meet_id, sport)   -- mirrors meets_tfrrs's key;
            )                                  -- the table name pins source
        """)
        cur.execute("DELETE FROM tfrrs_meet_geometry WHERE sport = %s", (SPORT,))
        print(f"  deleted {cur.rowcount:,} old rows")
        rows = [(mid, SPORT, s["track_type"], s["track_length"], s["is_indoor"],
                 s["location_id"], s["method"], s["confidence"])
                for mid, s in final.items()]
        # execute_values batches many rows per INSERT round trip.
        psycopg2.extras.execute_values(cur, """
            INSERT INTO tfrrs_meet_geometry
                (meet_id, sport, track_type, track_length, is_indoor,
                 location_id, method, confidence) VALUES %s
        """, rows)
        print(f"  inserted {cur.rowcount:,} rows")
        conn.commit()
        cur.execute("SELECT count(*) FROM tfrrs_meet_geometry WHERE sport = %s",
                    (SPORT,))
        print(f"  read-back: {cur.fetchone()[0]:,} rows now stamped")


def main():
    parser = argparse.ArgumentParser(description="Propagate geometry onto tfrrs TF meets")
    parser.add_argument("--apply", action="store_true",
                        help="write stamps (default: dry-run report only)")
    args = parser.parse_args()

    with getConn() as conn:
        links = _loadCanonLinks(conn)
        anet_geo = _loadAnetGeometry(conn)
        tfrrs_meets = _loadTfrrsMeets(conn)
        titles = {mid: (v, mn) for mid, v, mn in tfrrs_meets}
        venue_of = {mid: v for mid, v, _ in tfrrs_meets}

        r1 = buildRoute1(links, anet_geo)
        dictionary, rejects = buildVenueDict(links, anet_geo, venue_of)
        r2 = buildRoute2(tfrrs_meets, dictionary, set(r1))
        r3 = buildRoute3(tfrrs_meets, set(r1) | set(r2))
        final, flags, withheld = assembleStamps([r1, r2, r3], titles)

        _report([r1, r2, r3], dictionary, rejects, final, flags, withheld)
        if args.apply:
            print("\n== applying ==")
            applyStamps(conn, final)
        else:
            print("\n(dry run — nothing written; --apply to stamp)")


if __name__ == "__main__":
    main()