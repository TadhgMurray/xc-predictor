# Project: xc-predictor
# Subset:  Speed Rating Engine -- §6.1 fix #2, the XC region lookup
#
# THE BUG THIS FIXES
# ------------------
# speed_ratings_db._xcQuery builds the XC venue key in one of TWO shapes:
#
#     '<canonical_id>:d<distance>'        when course_canonical matched
#     'name:<course_name>:d<distance>'    when it did not
#
# packResults prefixes the sport, so cols["course_keys"] holds 'XC:13433:d4700'
# or 'XC:name:Holmdel Park:d5000'. loadCourseRegions looked those up with
# `xc_map.get(key[3:])` against a map keyed on a BARE course_name. Neither shape
# is ever a bare name, so every XC lookup missed and the region prior has never
# applied to a single cross-country cell.
#
# ★ WHY THE PARSING LIVES HERE AND NOT IN A str.split(":")
#   Course names contain colons. 'XC:name:Nike Cross: Southwest:d5000' splits
#   into five pieces, not four. The distance suffix is parsed with rpartition,
#   which scans from the RIGHT and is therefore immune -- the same technique
#   speed_ratings_db._splitVenueKey already uses on these keys. One shape of
#   parsing for one shape of key.
#
# ★ WHY THE ID MAP IS BUILT BY REPEATING _xcQuery'S JOIN
#   The id in the key exists only because that join matched on (course_name,
#   gps_lat, gps_long) rounded to 5dp. Any other route to a state risks mapping
#   ids the engine never minted, or missing ones it did. Same join, same keys,
#   cannot disagree.

import numpy as np


# ------------------------------------------------------------------ #
# CHUNK 1 -- key parsing
# ------------------------------------------------------------------ #

# splitXcVenue
# Purpose:   take an engine XC course key apart into the piece that identifies
#            the VENUE, ignoring the distance.
# Arguments: key -- e.g. 'XC:13433:d4700' or 'XC:name:Holmdel Park:d5000'.
# Output:    ("id", "13433") | ("name", "Holmdel Park") | (None, None).
#
# Syntax: rpartition(":d") returns (head, separator, tail) splitting at the LAST
# occurrence. When the separator is absent the head is empty and the tail is the
# whole string, which is why the `if not tag` branch reassigns rather than
# trusting the head.
def splitXcVenue(key):
    if not key or not key.startswith("XC:"):
        return (None, None)

    rest = key[3:]                       # drop the 'XC:' namespace
    venue, tag, _dist = rest.rpartition(":d")
    if not tag:                          # no ':d' suffix at all
        venue = rest

    if not venue:
        return (None, None)
    if venue.startswith("name:"):
        return ("name", venue[5:])
    if venue.isdigit():
        return ("id", venue)
    return (None, None)                  # unrecognised shape; caller leaves it -1


# ------------------------------------------------------------------ #
# CHUNK 2 -- schema probing
# ------------------------------------------------------------------ #

# hasColumn
# Purpose:   does <table>.<column> exist?
# Arguments: cur -- an open cursor; table, column -- unqualified names.
# Output:    bool.
#
# ★ House rule: DON'T GUESS SCHEMA -- READ information_schema. meets_tfrrs may
#   or may not carry a state column, and the answer decides whether the
#   tfrrs-only venues can be mapped at all. Probing costs one cheap query and
#   removes the guess.
def hasColumn(cur, table, column):
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        LIMIT 1
    """, (table, column))
    return cur.fetchone() is not None


# ------------------------------------------------------------------ #
# CHUNK 3 -- the state maps
# ------------------------------------------------------------------ #

# _modalStateSql
# Purpose:   canonical_id -> the state it most often sits in, by repeating
#            _xcQuery's course_canonical join against one meets-shaped table.
# Arguments: table -- 'meets' or 'meets_tfrrs';
#            name_col -- 'course_name' or 'venue_name'.
# Output:    SQL string.
#
# Syntax: DISTINCT ON (canonical_id) with ORDER BY canonical_id, n DESC keeps
# exactly one row per id -- the most frequent state. This is Postgres's cheapest
# argmax and the same pattern the existing name map already uses.
def _modalStateSql(table, name_col):
    return f"""
        SELECT DISTINCT ON (canonical_id) canonical_id, state
        FROM (
            SELECT cc.canonical_id::text AS canonical_id,
                   upper(btrim(t.state))  AS state,
                   count(*)               AS n
            FROM {table} t
            JOIN course_canonical cc
              ON cc.course_name = t.{name_col}
             AND round(cc.gps_lat::numeric,  5) = round(t.gps_lat::numeric,  5)
             AND round(cc.gps_long::numeric, 5) = round(t.gps_long::numeric, 5)
            WHERE COALESCE(btrim(t.state), '') <> ''
            GROUP BY 1, 2
        ) q
        ORDER BY canonical_id, n DESC
    """


# loadIdStates
# Purpose:   {canonical_id_text: state} covering both XC sources.
# Arguments: cur -- an open cursor.
# Output:    dict.
#
# The anet arm always runs. The tfrrs arm runs ONLY if meets_tfrrs actually has
# a state column; when it does not, tfrrs-only venues stay unmapped, which is
# honest -- an unknown region must not be invented.
#
# ★ ANET WINS TIES. It is the source the difficulty solve is anchored on and its
#   state column is the better populated of the two, so the tfrrs arm is loaded
#   FIRST and anet overwrites it.
def loadIdStates(cur):
    id_map = {}

    if hasColumn(cur, "meets_tfrrs", "state"):
        cur.execute(_modalStateSql("meets_tfrrs", "venue_name"))
        id_map.update({k: v for k, v in cur.fetchall()})
    else:
        print("[engine] meets_tfrrs has no state column -- "
              "tfrrs-only XC venues stay unmapped")

    cur.execute(_modalStateSql("meets", "course_name"))
    id_map.update({k: v for k, v in cur.fetchall()})
    return id_map


# ------------------------------------------------------------------ #
# CHUNK 4 -- resolution
# ------------------------------------------------------------------ #

# xcState
# Purpose:   the state for one engine XC course key, or None.
# Arguments: key; name_map -- the EXISTING course_name -> state dict;
#            id_map -- from loadIdStates.
# Output:    state string or None.
#
# The 'name:' shape resolves through the map loadCourseRegions already builds --
# that lookup was always correct, it was simply never reached because the key
# was passed in with its prefix and suffix still attached.
def xcState(key, name_map, id_map):
    kind, value = splitXcVenue(key)
    if kind == "id":
        return id_map.get(value)
    if kind == "name":
        return name_map.get(value)
    return None


# ------------------------------------------------------------------ #
# CHUNK 5 -- state whitelist
# ------------------------------------------------------------------ #

# ★ §6.1 records 219 "regions" where there are 50 states. The tail is junk
#   labels holding a handful of cells each, with near-zero cross-links, which
#   the prior then flattens outright. Collapsing the tail into one bucket stops
#   those cells being shrunk as if they were a coherent geography.
US_STATES = frozenset("""
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO
MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
DC PR GU VI AS MP
""".split())


# normalizeState
# Purpose:   fold a raw state label to a real code, or None.
# Arguments: st -- raw value; strict -- when False, non-whitelist codes pass
#            through unchanged (today's behaviour).
# Output:    upper-cased code, or None.
#
# Kept OFF by default. Turning it on changes which cells get a region, so it is
# a separate experiment from the lookup fix -- do not confound the two.
def normalizeState(st, strict=False):
    if st is None:
        return None
    st = st.strip().upper()
    if not st:
        return None
    if strict and st not in US_STATES:
        return None
    return st


# ------------------------------------------------------------------ #
# CHUNK 6 -- code -> state, for downstream reporting
# ------------------------------------------------------------------ #

# Populated by loadCourseRegions once the codes are assigned. The runaway
# reporter needs it to print 'CA' rather than 'r17'; nothing in the solve reads
# it, so an empty dict is harmless.
REGION_NAMES = {}