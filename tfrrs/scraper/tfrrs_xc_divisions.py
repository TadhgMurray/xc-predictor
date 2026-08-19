# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/tfrrs_xc_divisions.py
# Purpose: The logic that gives tfrrs XC results a distance. The page parser
#          already attaches event_id, event_name, and distance_meters to every
#          finisher row — but the save path drops them. This module turns those
#          into: (1) a per-meet LOCAL div_id stamped onto each result row, and
#          (2) a {local_div_id -> {div_name, distance}} blob for meets_tfrrs.
#          PURE FUNCTIONS — no DB — so the whole thing is unit-testable and the
#          save path just calls these and writes the results.
#
#   THE MAIN IDEA
#   -------------
#   A meet's DIVISIONS are the distinct races in it. Each parsed row already
#   knows its race (event_name) and that race's distance (distance_meters). So:
#     * collect the meet's distinct divisions,
#     * ORDER them (distance ascending, ties broken by event_name lexicographically),
#     * number them 0,1,2,... -> that number is the LOCAL div_id,
#     * stamp each result row's div_id with its division's number,
#     * build a blob mapping number -> {div_name, distance}.
#   The result row's div_id + the meet's blob together resolve distance at
#   normalize time: blob[str(div_id)]["distance"].
#
#   WHY ORDER BY DISTANCE (the correctness contract)
#   ------------------------------------------------
#   The local div_id must be a DETERMINISTIC function of the division set, so a
#   re-scrape reproduces the SAME numbering. We sort by (distance, event_name),
#   both intrinsic properties of the division, so the same set of divisions
#   always yields the same numbering. Because a meet is scraped ATOMICALLY (all
#   divisions + all result rows together), a re-scrape rewrites BOTH the row
#   div_ids and the blob with this same ordering — so they can never drift apart.
#   (If a division is ever added, both sides are rebuilt together in that same
#   atomic scrape, so they stay consistent.)
#
#   THE HIERARCHY
#   -------------
#     parsed rows (each with event_name, distance_meters)
#        |
#        |-- STEP 1  _collectDivisions   -> distinct (event_name, distance) set
#        |-- STEP 2  _orderDivisions     -> sorted list; index = local div_id
#        |-- STEP 3  _buildDivIndex      -> {event_name -> local_div_id}
#        |-- STEP 4  stampDivIds         -> write div_id onto each result row
#        `-- STEP 5  buildDivisionBlob   -> {local_div_id -> {div_name, distance}}
#
#   This module is DB-free and import-safe; the save path calls stampDivIds +
#   buildDivisionBlob and persists their outputs.

# The keys we read off each parsed row. Named once so a rename is one-line.
_NAME_KEY = "event_name"
_DIST_KEY = "distance_meters"


# ================================================================== #
# STEP 1 — COLLECT the meet's distinct divisions from its rows.
#   A division is identified by its event_name; we also capture the
#   distance that goes with it.
# ================================================================== #

def _collectDivisions(rows):
    """
    Purpose : reduce a meet's result rows to its distinct divisions.
    Argument: rows — list of parsed row dicts, each with event_name + distance_meters.
    Output  : a dict { event_name : distance }  (one entry per division).

    Mechanics:
      - Iterate rows; for each, read its event_name and distance. First time we
        see an event_name, record its distance. We assume all rows of one race
        share one distance (they do — distance is a race-level fact the parser
        derived from the race title), so later rows of the same race don't change it.
      - Rows missing event_name are skipped (can't place them in a division);
        they'll simply have no div_id, which is the honest outcome.
      - distance may be None (a race whose title had no parseable distance) — we
        keep the entry so the division still exists; its distance is just None.
    """
    divisions = {}
    for row in rows:
        name = row.get(_NAME_KEY)
        if name is None:
            continue                          # can't assign a division without a name
        if name not in divisions:
            divisions[name] = row.get(_DIST_KEY)
    return divisions


# ================================================================== #
# STEP 2 — ORDER the divisions: distance ascending, ties broken by
#   event_name lexicographically. The position in this order IS the
#   local div_id.
# ================================================================== #

def _orderDivisions(divisions):
    """
    Purpose : produce the deterministic ordering that assigns local div_ids.
    Argument: divisions — { event_name : distance } from _collectDivisions.
    Output  : a list of (event_name, distance) in the canonical order.

    Mechanics / syntax:
      - sorted(..., key=...) sorts the (name, distance) pairs.
      - The key is (_distanceSortKey(dist), name):
          * primary: distance ascending. We route distance through a helper so a
            None distance sorts LAST (float('inf')) instead of crashing the sort
            (Python can't compare None to a number).
          * secondary: event_name — the lexicographic tie-break you specified,
            applied to the FULL event_name string.
      - The resulting list order is the numbering: index 0 = local div_id 0, etc.
    """
    return sorted(
        divisions.items(),
        key=lambda item: (_distanceSortKey(item[1]), item[0]),
    )


def _distanceSortKey(distance):
    """
    Purpose : make distance safely sortable, sending None to the end.
    Argument: distance — a number or None.
    Output  : the distance as a float, or +infinity if None.

    Why: sorted() can't compare None with a number; mapping None -> inf puts
    distance-less divisions last without raising, and keeps real distances in
    ascending order.
    """
    return float("inf") if distance is None else float(distance)


# ================================================================== #
# STEP 3 — BUILD the {event_name -> local_div_id} index from the order.
#   This single map is used to stamp rows AND to build the blob, so both
#   sides are guaranteed consistent (same map -> same numbering).
# ================================================================== #

def _buildDivIndex(ordered_divisions):
    """
    Purpose : map each division's event_name to its local div_id (its position).
    Argument: ordered_divisions — the (event_name, distance) list from _orderDivisions.
    Output  : { event_name : local_div_id }  (local_div_id is an int 0,1,2,...).

    Syntax: enumerate(...) yields (index, (name, distance)); we key by name and
    store the index as the local div_id. This index map is the SINGLE SOURCE OF
    TRUTH the two downstream steps share.
    """
    return {name: idx for idx, (name, _distance) in enumerate(ordered_divisions)}


# ================================================================== #
# STEP 4 — STAMP each result row's div_id from the index. Mutates the
#   rows in place (they're ours, mid-build) — the save path then reads
#   row["div_id"] into the results tuple.
# ================================================================== #

def stampDivIds(rows):
    """
    Purpose : assign each result row a LOCAL div_id (0,1,2,...) for its meet.
    Argument: rows — the meet's parsed row dicts (each with event_name).
    Output  : the same rows list, each row gaining row["div_id"] (int or None).

    This is a PUBLIC entry point (the save path calls it). It runs steps 1-3 to
    build the index, then writes div_id onto each row:
      - a row whose event_name is in the index gets that division's number.
      - a row with no event_name (or an unknown one) gets div_id = None — honest:
        we couldn't place it, so it has no division (and no distance).
    Returns the rows for convenient chaining, though it mutates in place.
    """
    divisions = _collectDivisions(rows)
    ordered = _orderDivisions(divisions)
    index = _buildDivIndex(ordered)

    for row in rows:
        name = row.get(_NAME_KEY)
        row["div_id"] = index.get(name)       # None if name missing/unknown
    return rows


# ================================================================== #
# STEP 5 — BUILD the blob {local_div_id -> {div_name, distance}} for the
#   meet's meets_tfrrs row. Built from the SAME index, so its keys match
#   the div_ids stamped on the rows.
# ================================================================== #

def buildDivisionBlob(rows):
    """
    Purpose : build the per-meet division map to store in meets_tfrrs.division_distances.
    Argument: rows — the meet's parsed row dicts (each with event_name + distance_meters).
    Output  : a dict { "0": {"div_name": ..., "distance": ...}, "1": {...}, ... }.

    Why string keys: JSON object keys are strings, and jsonb will store them as
    strings, so at normalize time you look up blob[str(div_id)]. Building the keys
    as strings here means what we store matches what we query with (no int/str
    mismatch surprises).

    Mechanics:
      - rebuild the SAME ordered index (collect -> order -> index) so the blob's
        numbering is identical to what stampDivIds wrote onto the rows. (Both call
        the same three helpers, so they cannot disagree.)
      - for each division, emit { str(local_div_id): {div_name, distance} }.
    """
    divisions = _collectDivisions(rows)
    ordered = _orderDivisions(divisions)
    index = _buildDivIndex(ordered)

    blob = {}
    for name, distance in divisions.items():
        local_id = index[name]
        blob[str(local_id)] = {
            "div_name": name,
            "distance": distance,             # metres, or None if unparseable
        }
    return blob


# ================================================================== #
# CONVENIENCE — do both in one call, returning (rows, blob). The save
#   path can call this once instead of stampDivIds + buildDivisionBlob
#   separately. Both derive from the same helpers, so they're consistent.
# ================================================================== #

def applyDivisions(rows):
    """
    Purpose : stamp div_ids onto rows AND build the blob, in one call.
    Argument: rows — the meet's parsed row dicts.
    Output  : (rows, blob) — rows with div_id stamped, and the blob to store.

    This is the single entry point the save path should use. Order doesn't matter
    (both rebuild the same index), but doing them together documents that they
    belong to the same meet and must be persisted together (atomic scrape).
    """
    stampDivIds(rows)
    blob = buildDivisionBlob(rows)
    return rows, blob