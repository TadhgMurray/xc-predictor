# Project: xc-predictor
# File:    scripts/finish_gps_overseas.py
# Purpose: The last step. The stuck meets_tf_meta rows are overseas (OS/os/CS)
#          venues Census/Nominatim can't parse (APO/FPO, ROK, "various Japan").
#          Place them from a keyword table (same idea as the dead-end filler),
#          write onto meta, then propagate by location_id into meets_tf/meets.
#          Dry-run by default; --apply commits. Idempotent (null rows only).
#
# USAGE
#   python scripts/finish_gps_overseas.py            # dry run
#   python scripts/finish_gps_overseas.py --apply    # write + propagate
# ============================================================================

import argparse
import sys
sys.path.insert(0, "scripts")

from database import getConn, initPool

_GPS_TABLES = ("meets_tf", "meets_tf_meta", "meets")


# ================================================================== #
# CHUNK 1 -- KEYWORD -> CITY (first token that matches wins)
# ================================================================== #
# (lat, lon) is city-level; overseas weather grids are coarse so this is fine.
# Tokens are matched case-insensitively as substrings of the full meta string
# (venue + address + city + state), so "Pyongtaek", "Humphreys", "APO AP 96..."
# all route to the right base.
_RULES = [
    # Korea
    ("humphreys",              36.97, 127.03), ("pyongtaek",  36.97, 127.03),
    ("pyeongtaek",             36.97, 127.03), ("osan",       37.09, 127.03),
    ("daegu",                  35.87, 128.60),
    # Japan - Saitama cluster (Ageo/Kawagoe/Kumagaya/Kamiyugi) + Tokyo/Okayama...
    ("ageo",                   35.97, 139.59), ("kawagoe",    35.92, 139.48),
    ("kumagaya",               36.15, 139.39), ("kamiyugi",   35.63, 139.36),
    ("hachioji",               35.63, 139.32), ("higashi kurume", 35.76, 139.53),
    ("saitama",                35.86, 139.65), ("kawagoe undo",  35.92, 139.48),
    ("okayama",                34.66, 133.93), ("tendo",      38.36, 140.38),
    ("yamagata",               38.24, 140.34), ("oita",       33.24, 131.61),
    ("nagoya",                 35.18, 136.91), ("gooding",    42.94, -114.71),
    ("koza",                   26.33, 127.80), ("zion",       26.33, 127.80),
    ("okinawa",                26.34, 127.80), ("zama",       35.49, 139.41),
    # Guam
    ("mangilao",               13.44, 144.80), ("tumon",      13.51, 144.80),
    ("ramsey field",           13.51, 144.80), ("jfk",        13.51, 144.80),
    # China
    ("puxi",                   31.18, 121.30), ("shanghai american", 31.18, 121.30),
    ("shanghai",               31.23, 121.47),
    # UK / Egypt / UAE / Caribbean
    ("eton",                   51.49,  -0.61), ("thames valley", 51.49, -0.61),
    ("maadi",                  29.96,  31.26), ("cairo",      30.04, 31.24),
    ("dubai",                  25.20,  55.27), ("daa/uas",    25.20, 55.27),
    ("vieux fort",             13.72, -60.95), ("george odlum", 13.72, -60.95),
    ("bridgetown",             13.10, -59.62),
    # Germany DoDEA (Blackhawk/Blawkhawk Stadium = Baumholder area, APO AE)
    ("blackhawk",              49.63,   7.33), ("blawkhawk",  49.63,  7.33),
]


def _placeMeta(text):
    """
    Purpose : first keyword rule whose token is in the venue/address blob.
    Arguments: text -- concatenated meta fields, lowercased (str).
    Output  : (lat, lon) or None. None => genuinely dead (TBD/virtual/blob).
    """
    for token, lat, lon in _RULES:
        if token in text:
            return (lat, lon)
    return None


# ================================================================== #
# CHUNK 2 -- GEOCODE the stuck meta rows from the keyword table
# ================================================================== #

def _fillMeta(conn, do_write):
    """
    Purpose : place stuck meets_tf_meta rows from _RULES, write onto meta.
    Output  : (placed, total) ints. Prints each placement and each dead row.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, venue_name, address, city, state
            FROM meets_tf_meta
            WHERE gps_lat IS NULL
              AND (venue_name IS NOT NULL OR address IS NOT NULL
                   OR city IS NOT NULL OR state IS NOT NULL)
        """)
        rows = cur.fetchall()
        conn.rollback()

    placed = 0
    hits = []                                     # (meet_id, lat, lon)
    for meet_id, venue, addr, city, state in rows:
        blob = " ".join(x for x in (venue, addr, city, state) if x).lower()
        res = _placeMeta(blob)
        if res:
            placed += 1
            hits.append((meet_id, res[0], res[1]))
            print(f"    PLACE {meet_id}: {res[0]:.2f},{res[1]:.2f}  <- {blob[:60]}")
        else:
            print(f"    DEAD  {meet_id}: {blob[:60]}")
    print(f"  meta: {placed}/{len(rows)} placed by keyword")

    if do_write and hits:
        with conn.cursor() as cur:
            for meet_id, lat, lon in hits:
                cur.execute("""UPDATE meets_tf_meta SET gps_lat=%s, gps_long=%s
                               WHERE meet_id=%s AND gps_lat IS NULL""",
                            (lat, lon, meet_id))
        conn.commit()
    return placed, len(rows)


# ================================================================== #
# CHUNK 3 -- PROPAGATE by location_id into all three tables
# ================================================================== #

def _sourceGps(cur):
    """location_id -> (lat, lon) from every table that already knows one."""
    coords = {}
    for table in _GPS_TABLES:
        cur.execute(f"""SELECT location_id, gps_lat, gps_long FROM {table}
                        WHERE gps_lat IS NOT NULL AND location_id IS NOT NULL""")
        for loc, lat, lon in cur.fetchall():
            coords.setdefault(loc, (lat, lon))
    return coords


def _propagate(conn, table, coords, do_write):
    """Fill null-gps rows in one table from coords. Returns (fillable, null)."""
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
# CHUNK 4 -- DRIVER
# ================================================================== #

def _run(do_write):
    with getConn() as conn:
        _fillMeta(conn, do_write)                 # place overseas meta first
        with conn.cursor() as cur:                # then rebuild the lend-map
            coords = _sourceGps(cur)
            conn.rollback()
        print(f"\n  propagate: {len(coords):,} venues have a known gps to lend")
        for table in _GPS_TABLES:
            fillable, null_ids = _propagate(conn, table, coords, do_write)
            verb = "filled" if do_write else "would fill"
            print(f"    {table}: {verb} {fillable} of {null_ids} null venues "
                  f"({null_ids - fillable} still stuck)")
        if not do_write:
            print("\n  [dry-run] nothing written  (add --apply to commit)")


def main():
    ap = argparse.ArgumentParser(description="Last step: keyword-place overseas "
                                             "meta venues, then propagate.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="scripts")
    args = ap.parse_args()
    initPool()
    _run(args.apply)


if __name__ == "__main__":
    main()