#!/usr/bin/env python
"""
engine/geometry_resolver.py
    Resolve track geometry for a result row REGARDLESS of source, by reading it
    at the grain each source actually carries it.

================================================================================
THE MAIN IDEA
================================================================================
Geometry (track_type, track_length, is_indoor, location_id) is a MEET fact.
  - anet  stores it at the EVENT grain, in meets_tf (div_id, meet_id, event_id).
  - tfrrs stores it (via propagation) at the MEET grain, in tfrrs_meet_geometry
    (meet_id, sport) — and its results_tf rows have NULL div_id/event_id, so the
    event-keyed lookup is IMPOSSIBLE for tfrrs.

So we DON'T force tfrrs into an event key. We resolve each source at its own
grain and hand back the SAME little geometry record either way. The caller
(normalize_distance's loader) never learns which leg fired.

THE INVARIANT (why every lookup pins source):
    meet_id ALONE is never a key. Identity is (meet_id, sport, source). anet
    meet 12345 and tfrrs meet 12345 are unrelated. Every query below carries
    source explicitly — no bare-meet_id joins, ever.

================================================================================
THE HIERARCHY (one decision, two legs, one shape out)
================================================================================
    resolveGeometry(row)                      <- the only public entry point
        │
        ├─ source == 'anet'  -> _anetGeometry(...)   event-level meets_tf lookup
        └─ source == 'tfrrs' -> _tfrrsGeometry(...)  meet-level  stamp lookup
                                     │
                                     ▼
                             _emptyGeometry() when neither has a row
                             (all fields None = the 400m-flat reference = no-op)

Both legs return the SAME dict shape, so the caller is source-blind.
================================================================================
"""

# ------------------------------------------------------------------ #
# THE GEOMETRY RECORD  —  one shape, so callers never branch on source
# ------------------------------------------------------------------ #

# The exact fields normalize_distance reads. Kept in ONE place so the two legs
# and the empty case can never drift apart in what they return.
GEOMETRY_FIELDS = ("track_type", "track_length", "is_indoor", "location_id")


def _emptyGeometry() -> dict:
    """
    Purpose : the 'no basis to correct' record — every field None.
    Meaning : normalize_distance treats all-None as the 400m-flat REFERENCE
              (g(400)=0, banking=1), i.e. a clean no-op. This is honest:
              "we have no geometry" is NOT "we believe it was flat".
    Output  : {track_type: None, track_length: None, is_indoor: None,
               location_id: None}.
    """
    return {field: None for field in GEOMETRY_FIELDS}


def _rowToGeometry(row) -> dict:
    """
    Purpose : turn a DB result tuple (track_type, track_length, is_indoor,
              location_id) into the standard geometry dict.
    Argument: row — a 4-tuple in GEOMETRY_FIELDS order, or None (no match).
    Output  : the geometry dict; _emptyGeometry() when row is None.
    """
    if row is None:                       # LEFT-join miss / .fetchone() empty
        return _emptyGeometry()
    # zip pairs field-name -> value positionally; dict() builds the record.
    return dict(zip(GEOMETRY_FIELDS, row))


# ------------------------------------------------------------------ #
# LEG 1 — anet: EVENT-LEVEL lookup in meets_tf (unchanged behaviour)
# ------------------------------------------------------------------ #

# _anetGeometry
# Purpose : read geometry for an anet result at the event grain it lives at.
# Arguments:
#   cur      — an open cursor (caller owns its lifecycle).
#   meet_id  — the anet meet_id from the result row.
#   div_id   — the anet div_id  (part of the event key; present for anet).
#   event_id — the anet event_id (part of the event key; present for anet).
# Output  : geometry dict (empty if no meets_tf row matches).
# Note    : source is pinned to 'anet' so we never cross the id-space boundary.
def _anetGeometry(cur, meet_id, div_id, event_id) -> dict:
    cur.execute("""
        SELECT track_type, track_length, is_indoor, location_id
        FROM meets_tf
        WHERE meet_id  = %s
          AND div_id   = %s
          AND event_id = %s
          AND source   = 'anet'
    """, (meet_id, div_id, event_id))
    return _rowToGeometry(cur.fetchone())


# ------------------------------------------------------------------ #
# LEG 2 — tfrrs: MEET-LEVEL lookup in the propagation stamp table
# ------------------------------------------------------------------ #

# _tfrrsGeometry
# Purpose : read geometry for a tfrrs result at the MEET grain — the only grain
#           tfrrs has (its results carry NULL div_id/event_id).
# Arguments:
#   cur     — open cursor.
#   meet_id — the tfrrs meet_id from the result row.
# Output  : geometry dict (empty if the meet was never stamped).
# Note    : sport='TF' pins the stamp partition; the table is tfrrs-only by
#           construction, so (meet_id, sport) already respects the invariant.
def _tfrrsGeometry(cur, meet_id) -> dict:
    cur.execute("""
        SELECT track_type, track_length, is_indoor, location_id
        FROM tfrrs_meet_geometry
        WHERE meet_id = %s
          AND sport   = 'TF'
    """, (meet_id,))
    return _rowToGeometry(cur.fetchone())


# ------------------------------------------------------------------ #
# PUBLIC ENTRY — dispatch on source, return the uniform record
# ------------------------------------------------------------------ #

# resolveGeometry
# Purpose : the ONE function the loader calls. Picks the correct grain for the
#           row's source and returns the standard geometry dict.
# Arguments:
#   cur    — an open cursor.
#   row    — a mapping with at least: source, meet_id, div_id, event_id.
#            (div_id/event_id may be None for tfrrs; leg 2 ignores them.)
# Output  : geometry dict in GEOMETRY_FIELDS shape — source-blind to the caller.
def resolveGeometry(cur, row) -> dict:
    source = row["source"]
    if source == "anet":
        return _anetGeometry(cur, row["meet_id"], row["div_id"], row["event_id"])
    if source == "tfrrs":
        return _tfrrsGeometry(cur, row["meet_id"])
    return _emptyGeometry()               # unknown source -> honest no-op


# ------------------------------------------------------------------ #
# BATCH VARIANT — one round trip per source instead of one per row
# ------------------------------------------------------------------ #
# Per-row queries are fine for small loads but murder on millions of rows.
# These build an in-memory dict once per source; the loader then does dict
# lookups (O(1)) instead of a query per row. Same records, same invariant.

# _loadAnetGeometryMap
# Purpose : bulk-load anet event geometry into a dict keyed by the event triple.
# Argument: cur — open cursor.
# Output  : { (meet_id, div_id, event_id) : geometry-dict }.
# Note    : only rows WITH some geometry are loaded (a missing key -> empty later).
def _loadAnetGeometryMap(cur) -> dict:
    cur.execute("""
        SELECT meet_id, div_id, event_id, track_type, track_length,
               is_indoor, location_id
        FROM meets_tf
        WHERE source = 'anet'
          AND (track_type IS NOT NULL OR track_length IS NOT NULL)
    """)
    out = {}
    for meet_id, div_id, event_id, tt, tl, ind, loc in cur:
        out[(meet_id, div_id, event_id)] = dict(zip(GEOMETRY_FIELDS, (tt, tl, ind, loc)))
    return out


# _loadTfrrsGeometryMap
# Purpose : bulk-load tfrrs MEET geometry into a dict keyed by meet_id only.
# Argument: cur — open cursor.
# Output  : { meet_id : geometry-dict }.
def _loadTfrrsGeometryMap(cur) -> dict:
    cur.execute("""
        SELECT meet_id, track_type, track_length, is_indoor, location_id
        FROM tfrrs_meet_geometry
        WHERE sport = 'TF'
    """)
    out = {}
    for meet_id, tt, tl, ind, loc in cur:
        out[meet_id] = dict(zip(GEOMETRY_FIELDS, (tt, tl, ind, loc)))
    return out


class GeometryMaps:
    """
    Purpose : hold both prebuilt maps so the loader resolves geometry with pure
              dict lookups (no per-row SQL). Build ONCE, reuse for every row.
    Usage   :
        maps = GeometryMaps.build(cur)
        geom = maps.resolve(row)      # row = mapping with source/meet_id/div_id/event_id
    """

    def __init__(self, anet_map, tfrrs_map):
        # Two dicts, two grains — the class just routes between them by source.
        self._anet = anet_map
        self._tfrrs = tfrrs_map

    @classmethod
    def build(cls, cur):
        """Run the two bulk loads and wrap them. One round trip per source."""
        return cls(_loadAnetGeometryMap(cur), _loadTfrrsGeometryMap(cur))

    def resolve(self, row) -> dict:
        """
        Purpose : the batch analogue of resolveGeometry — same dispatch, but the
                  lookups are in-memory. Same output shape and same invariant.
        """
        source = row["source"]
        if source == "anet":
            key = (row["meet_id"], row["div_id"], row["event_id"])
            return self._anet.get(key, _emptyGeometry())     # .get -> empty on miss
        if source == "tfrrs":
            return self._tfrrs.get(row["meet_id"], _emptyGeometry())
        return _emptyGeometry()