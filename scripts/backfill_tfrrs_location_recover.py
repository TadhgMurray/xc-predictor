# Project: xc-predictor
# File:    scripts/backfill_tfrrs_location_recover.py
# Purpose: The last squeeze before geocoding. Two operations on meets_tfrrs XC,
#          both dry-run by default:
#            RECOVER  a bailed tie whose top-2 anet candidates AGREE on location
#                     (<agree_km apart) is not ambiguous about WHERE -- only about
#                     which redundant location_id to use. Stamp the higher-scoring
#                     one; the gps is identical either way, and gps is what matters.
#            REVERSE  un-stamp specific meet_ids that the fuzzy pass matched to the
#                     WRONG venue, so they fall back to the geocode pile.
#          Nothing in anet `meets` is ever modified.
#
# ============================================================================
# WHY RECOVER IS SAFE
# ============================================================================
# A tie means two anet venues scored within the margin for one tfrrs raw. The
# fuzzy pass bailed because it could not tell them apart. But many ties are the
# SAME physical venue carrying two anet location_ids (the known collision). When
# the two candidates' coordinates agree to within agree_km, the meet's LOCATION
# is unambiguous -- we just pick the higher-scoring candidate's location_id and
# its (identical) gps. We NEVER recover a tie whose candidates disagree on
# location; those stay in the geocode pile.
#
# ============================================================================
# USAGE
#   python scripts/backfill_tfrrs_location_recover.py                 # dry run
#   python scripts/backfill_tfrrs_location_recover.py --apply
#   python scripts/backfill_tfrrs_location_recover.py --agree-km 3 --apply
#   # false matches to reverse are listed in _REVERSE below; edit and re-run
# ============================================================================

import argparse
import collections
import re
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- WHAT TO REVERSE
# ================================================================== #
# tfrrs meet_ids the fuzzy pass matched to the WRONG anet venue (confirmed by
# eye in the review). Un-stamping sends them back to the geocode pile.
#   16707: 'Air Academy High School' <= 'Air Force USAF Academy' (different place)
#    9477: 'Moorhead Soccer Complex'  <= 'Moorview Soccer Complex' (different name)
_REVERSE = {16707, 9477}


# ================================================================== #
# CHUNK 2 -- STOPWORDS / TOKENIZER  (mirror of the matcher)
# ================================================================== #
_STOP = {
    "university", "college", "school", "high", "park", "state", "the", "of",
    "invitational", "invite", "classic", "course", "cross", "country", "xc",
    "golf", "club", "community", "center", "centre", "complex", "trail",
    "trails", "hs", "ms", "middle", "elementary", "city", "county", "north",
    "south", "east", "west", "at", "and", "championships", "championship",
    "meet", "run", "open", "campus",
}


def _tokens(s):
    return set(t for t in re.split(r"[^\w]+", (s or "").lower()) if t)


def _distinctive(tok):
    return {t for t in tok if t not in _STOP and not t.isdigit()}


def _haversineKm(a, b):
    from math import radians, sin, cos, asin, sqrt
    if None in a or None in b:
        return None
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


# ================================================================== #
# CHUNK 3 -- LOAD anet SOURCE (mirror of the matcher, valid coords only)
# ================================================================== #

def _validCoord(lat, lon):
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return False
    return True


# _anetByState : {state: [(dist_tokens, loc, lat, lon)]} -- the same source the
#                fuzzy matcher scored against, so recovered top-2 == bailed top-2.
def _anetByState(cur):
    cur.execute("""
        SELECT DISTINCT course_name, upper(coalesce(state,'')) AS st,
               location_id, gps_lat, gps_long
        FROM meets
        WHERE location_id IS NOT NULL AND course_name IS NOT NULL
          AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
    """)
    by_state = collections.defaultdict(list)
    for name, st, loc, lat, lon in cur.fetchall():
        if not _validCoord(lat, lon):
            continue
        dt = _distinctive(_tokens(name))
        if dt:
            by_state[st].append((dt, loc, lat, lon))
    return by_state


# ================================================================== #
# CHUNK 4 -- FIND RECOVERABLE TIES
# ================================================================== #

# _recoverable
# Purpose : the still-unresolved tfrrs XC meets whose top-2 anet candidates (by
#           the matcher's score) agree on location within agree_km. Returns the
#           stamp to write: the higher-scoring candidate's location_id + gps.
# Detail  : we re-derive top-2 from the DB rather than trust the ties TSV, so this
#           is self-contained and reflects the CURRENT unresolved set (idempotent
#           if run twice -- already-stamped meets are gone from the pool).
# Output  : [(meet_id, loc, lat, lon)].
def _recoverable(cur, anet_by_state, agree_km):
    cur.execute("""
        SELECT meet_id, upper(coalesce(state,'')) AS st, location_raw
        FROM meets_tfrrs
        WHERE sport = 'XC' AND location_id IS NULL
    """)
    stamps = []
    for meet_id, st, raw in cur.fetchall():
        raw_tok = _tokens(raw)
        scored = []
        for dt, loc, lat, lon in anet_by_state.get(st, ()):
            hit = sum(1 for x in dt if x in raw_tok)
            s = hit / len(dt) if dt else 0
            if s > 0:
                scored.append((s, loc, lat, lon))
        if len(scored) < 2:
            continue
        scored.sort(reverse=True, key=lambda x: x[0])
        s1, loc1, lat1, lon1 = scored[0]
        s2, loc2, lat2, lon2 = scored[1]
        # only a genuine tie is a recovery candidate (clear winners were already
        # placed by the fuzzy pass; we do not re-adjudicate those)
        if s1 - s2 >= 0.2:
            continue
        km = _haversineKm((lat1, lon1), (lat2, lon2))
        if km is not None and km <= agree_km:
            stamps.append((meet_id, loc1, lat1, lon1))   # higher-scoring id
    return stamps


# ================================================================== #
# CHUNK 5 -- WRITES
# ================================================================== #

def _applyRecover(conn, stamps):
    """Stamp location_id + gps on recovered ties; scoped XC + still-NULL."""
    if not stamps:
        return 0
    with conn.cursor() as cur:
        for meet_id, loc, lat, lon in stamps:
            cur.execute("""
                UPDATE meets_tfrrs
                SET location_id = %s,
                    gps_lat  = COALESCE(gps_lat,  %s),
                    gps_long = COALESCE(gps_long, %s)
                WHERE meet_id = %s AND sport = 'XC' AND location_id IS NULL
            """, (loc, lat, lon, meet_id))
    conn.commit()
    return len(stamps)


def _applyReverse(conn, meet_ids):
    """Un-stamp wrong fuzzy matches: null location_id + gps so they re-geocode.
    We null gps too because the fuzzy pass wrote it from the wrong venue."""
    if not meet_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE meets_tfrrs
            SET location_id = NULL, gps_lat = NULL, gps_long = NULL
            WHERE meet_id = ANY(%s) AND sport = 'XC'
        """, (list(meet_ids),))
        n = cur.rowcount
    conn.commit()
    return n


# ================================================================== #
# CHUNK 6 -- DRIVER
# ================================================================== #

def _run(apply, agree_km):
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '512MB'")

        # reverse first, so a reversed meet can then be RE-CONSIDERED for recovery
        # in the same run if it happens to be a coord-agreeing tie (it will not be,
        # since we reversed it for being a wrong CLEAR match, but order it safely).
        if apply:
            n_rev = _applyReverse(conn, _REVERSE)
            print(f"  REVERSE: un-stamped {n_rev} wrong matches {sorted(_REVERSE)}")
        else:
            print(f"  REVERSE: would un-stamp {len(_REVERSE)} "
                  f"wrong matches {sorted(_REVERSE)}")

        anet = _anetByState(cur)
        stamps = _recoverable(cur, anet, agree_km)
        print(f"  RECOVER: {len(stamps)} ties whose top-2 anet coords agree "
              f"(<= {agree_km}km) -> placeable")

        if apply:
            n = _applyRecover(conn, stamps)
            print(f"  RECOVER: stamped {n}")
            cur.execute("""SELECT count(*) FILTER (WHERE location_id IS NOT NULL),
                                  count(*) FROM meets_tfrrs WHERE sport='XC'""")
            have, tot = cur.fetchone()
            print(f"\n  meets_tfrrs XC now located: {have:,} / {tot:,}")
            cur.execute("""SELECT count(*) FROM meets_tfrrs
                           WHERE sport='XC' AND location_id IS NULL""")
            print(f"  still to geocode: {cur.fetchone()[0]:,}")
        else:
            print("  [dry-run] nothing written")
            conn.rollback()


def main():
    ap = argparse.ArgumentParser(
        description="Recover coord-agreeing ties + reverse wrong fuzzy matches. "
                    "Dry-run default.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--agree-km", type=float, default=5.0,
                    help="tied candidates within this distance count as one venue")
    args = ap.parse_args()

    initPool()
    print(f"recover ties + reverse wrong matches  "
          f"({'APPLY' if args.apply else 'DRY RUN'})")
    _run(args.apply, args.agree_km)


if __name__ == "__main__":
    main()