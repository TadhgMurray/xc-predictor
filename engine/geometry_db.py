# Project: xc-predictor
# File:    engine/geometry_db.py   (v2.1 — streamed, prefiltered, date-parsed,
#                                   event_distance resolved)
# Purpose: Read-side loader for the geometry matched-pair diagnostic. Pulls anet
#          TF *running* results with their track geometry, one dict per usable
#          finishing performance.
#
# THE SEAM RULE (learned the hard way, twice): this file's output contract —
#   the field list in loadTFGeometryResults — must cover EVERY field the
#   diagnostic reads. Two bugs lived at this seam: dates shipped as strings
#   (the diagnostic subtracts date objects) and event_distance was never
#   shipped at all (the diagnostic reads it in both clouds). Synthetic tests
#   built row dicts by hand, so the seam was never exercised. If the
#   diagnostic ever grows a new row["..."], it gets added HERE, same commit.
#
# WHY anet-ONLY (a data fact, not a style choice): geometry lives only in
#   meets_tf (anet). meets_tfrrs has no geometry columns, so a tfrrs row could
#   never form a matched pair. This also means canon-linked duplicate meets
#   enter ONCE (the anet copy) — dedup-safe by construction.
#   [v3 NOTE] once propagate_tfrrs_geometry.py is applied, this filter widens:
#   tfrrs rows join tfrrs_meet_geometry, pairing keys on person_id, and a
#   --sources knob keeps the anet-only baseline one flag away.
#
# WHY the SQL filters NULL geometry: a row whose track_type or track_length is
#   NULL fits NEITHER cloud (_isFlat and _isBanked both reject None) — a
#   USABILITY filter, removing rows that could never be a side of any pair.
#   VALUE filters (indoor-only, one event, one length) must stay OUT of SQL,
#   or the in-memory pairing loses one side of real contrasts.
#
# WHY dates are parsed HERE: the diagnostic subtracts dates ((a - b).days),
#   which needs datetime.date objects — the raw column is text. Parsing at the
#   load boundary also drops corrupt years (0023-style strings) before they
#   reach any pairing logic.
#
# WHY event_distance is resolved HERE: it's a property of the event, and this
#   is the file that talks to the event tables. Two sources, in order:
#     1. meets_tf.distance_meters — the DB column (primary; already joined)
#     2. parse event_short        — '5000m' / 'Mile' / '440y' (fallback)
#   Neither -> the row drops, counted and its event string tallied, so the
#   parser is extended from MEASURED gaps, never guessed at.

import sys
sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")   # venue_geometry_overrides lives here

import re                             # regexes for the event_short parser
from datetime import date            # date.fromisoformat parses 'YYYY-MM-DD'
from collections import Counter      # a dict that counts key occurrences —
                                     # tallies which event strings failed

from database import getConn
# Date-aware curated venue facts (renovated venues, the Iowa chimera).
# Applied at the LOAD BOUNDARY — same place dates are parsed and for the
# same reason: nothing downstream should ever see the uncorrected value.
from venue_geometry_overrides import applyVenueOverride


# ------------------------------------------------------------------ #
# CONSTANTS / KNOBS
# ------------------------------------------------------------------ #

# anet's no-result sentinel: DNF/DNS stored as a huge time. Anything at or
# above it is not a performance.
_SENTINEL_TIME = 999999

# Sane-year gate for parsed dates. Outside this range = corrupt string
# (the 0023/0025 family) -> the row is dropped and counted, not loaded.
_MIN_SANE_YEAR = 1980
_MAX_SANE_YEAR = 2035

# Sanity window for ANY event distance, from EITHER source (the DB column is
# scraped data too — it gets the same distrust as the parser). Below 40m no
# race exists; above 45km nothing on a track does.
_MIN_SANE_DIST = 40.0
_MAX_SANE_DIST = 45_000.0

# How many rows the server-side cursor ships per round trip. Bigger = fewer
# trips but a larger in-flight batch; 50k is a comfortable middle.
_FETCH_BATCH = 50_000

# How many distinct unparseable event strings to print at the end — the
# shopping list for extending the parser, most-common first.
_TOP_UNPARSED = 10

# Unit conversions for the parser's non-metric branches.
_METERS_PER_MILE = 1609.34
_METERS_PER_YARD = 0.9144

# Relay/multi-leg detector, v3. Broadened again after the ledger showed
# 'swedish1234' / 'mixed-swedish1234' leaking into no_distance: a Swedish
# relay is a medley (legs of 100/200/300/400 — the '1234'), spelled with
# neither 'x' nor 'med', so no existing alternative caught it.
#   \d\s*x     a digit followed by 'x' — any 4x/2x/6x form
#   med\d      'med' immediately followed by a digit — the medley codes
#   swedish    the Swedish relay family, whatever surrounds the word
_RELAY_RX = re.compile(r"\d\s*x|shuttle|relay|medley|med\d|swedish", re.I)

# Metric steeplechase written as meters: '1609msteeple' -> 1609. The plain
# meters pattern can't take it (\b fails between 'm' and 's'); this one
# REQUIRES the steeple suffix, so it can never collide with 'mile'.
#   (?=steeple)   lookahead: assert 'steeple' comes next WITHOUT consuming
#                 it — the match is still just the digits + m.
_MSTEEPLE_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m(?=steeple)", re.I)

# Yard hurdles: '60yh' -> 60 yards. Same anatomy as _HURDLES_RX, yard unit.
_YARDH_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*yh\b", re.I)

# Kilometres: '3ksteeple' -> 3000, '5k' -> 5000. NO \b after the 'k' ON
# PURPOSE — the point is matching when a suffix follows ('3ksteeple' has
# no word boundary after the k, so \b would refuse exactly the string we
# want). Safe from '4x160m': the relay check runs first, and .match
# anchors at the start so a 'k' mid-string can't trigger it.
_KILO_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*k", re.I)

# Hurdles: '60mh', '300mh', '110MH'. Same anatomy as _METERS_RX but the 'm'
# is followed by 'h' instead of a word boundary — which is exactly why
# _METERS_RX could never match these (no \b between two word characters).
_HURDLES_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*mh\b", re.I)

# Race walk: a real timed event, but a different LOCOMOTION class — its
# cornering speeds break the shared shape-over-distance the geometry fit
# assumes. Excluded BY RULE (own ledger line), not by the string-gluing
# accident that first dropped it. rw\b: 'rw' at a word end ('1500mrw').
_WALK_RX = re.compile(r"rw\b|race\s*walk", re.I)

# The assumed-400 stamp. Census C: 100.0% of annotated outdoor tracks are
# ~400m (404,932/404,958) -> assuming unannotated outdoor = 400 flat has a
# measured error bound of ~0.006%.
_ASSUMED_LENGTH = 400.0
_ASSUMED_TYPE   = "Flat"

# Months in which the assumed-400 stamp is admitted. The assumption
# "is_indoor=0 & unannotated => real outdoor 400" is near-certain ONLY when
# indoor season is over: the winter pool carries mislabeled-indoor rows
# (measured: 55m/60m/300m/1000m in the Dec-Feb slice), and the bridge's own
# selection rules CONCENTRATE them (28d-from-indoor window + closest-pair
# preference for same-venue repeats). Apr-Jun keeps 76.6% of the pool
# (census C) while restoring the label's certainty.
_ASSUMED_MONTHS = ("04", "05", "06")

# The IN-list built from the constant: "'04','05','06'". One source of
# truth — change _ASSUMED_MONTHS, the SQL follows. Safe as an f-string
# ONLY because these are our own literals; anything user-shaped still goes
# in as %s bind parameters, always.
_MONTHS_IN = ",".join(f"'{m}'" for m in _ASSUMED_MONTHS)

# Indoor-tell events: a meet hosting ANY of these is an indoor meet
# regardless of its is_indoor flag — sub-80m dashes, their hurdle forms,
# and short-oval relays essentially don't exist outdoors. Every entry is
# MEASURED (from our own shopping lists / the winter-slice census), not
# guessed. Extendable: a new tell is one string here. Deliberately NOT
# included: 300m/600m/1000m — indoor-leaning but real at youth outdoor
# meets; add only if the leak detectors below ever fire.
_INDOOR_TELL_EVENTS = ("50m", "55m", "60m", "55mh", "60mh", "60yh",
                       "1000m", "55shuttleh", "50shuttleh", "4x160m", "4x50y")

# The IN-list built from the constant — one source of truth, same pattern
# as before. Our own literals, so the f-string is safe; anything
# user-shaped still binds via %s.
_TELLS_IN = ",".join(f"'{e}'" for e in _INDOOR_TELL_EVENTS)

# Candidates: clean outdoor rows with NO geometry annotation, gated to the
# months where the assumption holds, semi-joined so we only ship rows that
# could possibly bridge (same athlete AND event as some annotated row).
# Candidates: clean, fully-unannotated, outdoor-flagged rows whose MEET
# shows no indoor tell, semi-joined to rows that could actually bridge.
# The tell filter targets the MECHANISM (mislabeled-indoor meets betray
# themselves by hosting indoor events) instead of the old calendar proxy —
# which discarded legitimate warm-state winter racing and all of March.
_ASSUMED_SQL = f"""
    WITH suspect_meets AS (
        -- Every anet meet that hosted at least one indoor-tell event.
        -- Computed ONCE (one scan), then anti-joined below — never
        -- re-probed per candidate. DISTINCT collapses a meet's many
        -- tell results to one id. lower(): tolerate case slop; there's
        -- no index on event_short, so nothing is lost by wrapping it.
        SELECT DISTINCT meet_id
        FROM results_tf
        WHERE source = 'anet'                -- same id space as r below (§0)
          AND lower(event_short) IN ({_TELLS_IN})
    )
    SELECT r.athlete_id, r.event_short, r.date, r.time_seconds,
           m.distance_meters
    FROM results_tf r
    JOIN meets_tf m ON m.meet_id = r.meet_id
                   AND m.div_id = r.div_id AND m.event_id = r.event_id
    WHERE r.source = 'anet' AND m.source = 'anet'
      AND r.is_relay = 0 AND r.is_field = 0
      AND r.athlete_id IS NOT NULL
      AND r.time_seconds > 0 AND r.time_seconds < %s
      AND r.date IS NOT NULL AND r.date != ''
      AND m.is_indoor = 0                    -- outdoor-flagged...
      AND m.track_length IS NULL             -- ...fully unannotated =
      AND m.track_type   IS NULL             -- the assumed class
      AND NOT EXISTS (                       -- ...whose MEET is clean:
          SELECT 1 FROM suspect_meets s      -- anti-join against the tell
          WHERE s.meet_id = r.meet_id        -- set (both sides anet — the
      )                                      -- §0 discipline holds)
      AND EXISTS (                           -- partner semi-join, unchanged
          SELECT 1 FROM results_tf r2
          JOIN meets_tf m2 ON m2.meet_id = r2.meet_id
                          AND m2.div_id = r2.div_id
                          AND m2.event_id = r2.event_id
          WHERE r2.source = 'anet' AND m2.source = 'anet'
            AND r2.athlete_id  = r.athlete_id
            AND r2.event_short = r.event_short
            AND m2.track_length IS NOT NULL
            AND m2.track_type   IS NOT NULL
      )
"""

# ------------------------------------------------------------------ #
# TFRRS GEOMETRY LEG  (meet-grain; parallels the assumed-400 leg)
# ------------------------------------------------------------------ #
# WHY a separate query + converter, not a WHERE relaxation on _GEOMETRY_SQL:
#   _GEOMETRY_SQL JOINs meets_tf on (meet_id, div_id, event_id). tfrrs rows
#   carry NULL div_id/event_id (their geometry never lived at the event
#   grain), so that JOIN drops 100% of them. tfrrs geometry lives at the
#   MEET grain in tfrrs_meet_geometry, so its leg joins on meet_id alone —
#   with source pinned, honouring the (meet_id, sport, source) invariant.

_TFRRS_GEOMETRY_SQL = """
    SELECT
        r.athlete_id,              -- may be NULL on tfrrs; the diagnostic
                                   -- pairs on person_id, so the caller must
                                   -- decide identity (see the [PAIRING] note)
        r.event_short,
        r.date,
        r.time_seconds,
        g.track_type,              -- geometry comes from the STAMP table,
        g.track_length,            -- not from any meets_tf row
        g.is_indoor,
        g.location_id
    FROM results_tf r
    -- meet-grain join: the ONLY key tfrrs has. sport='TF' pins the stamp
    -- partition; the table is tfrrs-only, so (meet_id, sport) already
    -- respects (meet_id, sport, source) — no bare-meet_id join.
    JOIN tfrrs_meet_geometry g ON g.meet_id = r.meet_id
                              AND g.sport   = 'TF'
    WHERE r.source = 'tfrrs'             -- §0: pin the source, always
      AND r.time_seconds IS NOT NULL
      AND r.time_seconds > 0             -- a zero time would divide a ratio
      AND COALESCE(r.place, -1) <> 0     -- tfrrs non-finisher sentinel
      AND r.date IS NOT NULL
      AND r.date != ''
      AND g.track_type   IS NOT NULL     -- usability: NULL fits neither cloud
      AND g.track_length IS NOT NULL
"""
# NOTE: no distance_meters column — tfrrs meet geometry has no event distance,
# so event_distance is resolved from event_short ALONE (the parser path). That
# is fine: the parser is already the workhorse (6.89M of anet's rows use it).


# ------------------------------------------------------------------ #
# THE QUERY  (every filter here is a USABILITY filter — see header)
# ------------------------------------------------------------------ #

_GEOMETRY_SQL = """
    SELECT
        r.athlete_id,
        r.event_short,
        r.date,
        r.time_seconds,
        m.track_type,
        m.track_length,
        m.is_indoor,
        m.distance_meters,
        -- the override key. meets_tf's own column first; meets_tf_meta's
        -- as the fallback (its population is the verified one — the
        -- renovation census ran against it). The loader's override ledger
        -- is the tripwire if neither reaches: zero hits at Clemson/Iowa
        -- on a rerun means this join isn't delivering, stop and say so.
        COALESCE(m.location_id, mm.location_id) AS location_id
    FROM results_tf r
    -- meets_tf is keyed (meet_id, div_id, event_id); join on ALL THREE.
    JOIN meets_tf m ON m.meet_id  = r.meet_id
                   AND m.div_id   = r.div_id
                   AND m.event_id = r.event_id
    -- meet-grain meta, ONLY for the location fallback. meets_tf_meta is
    -- anet-only with PK meet_id; r.source='anet' below keeps §0 intact.
    LEFT JOIN meets_tf_meta mm ON mm.meet_id = r.meet_id
                              AND mm.source  = 'anet'
    WHERE r.source = 'anet'
      AND m.source = 'anet'              -- §0 discipline: pin BOTH sides, always
      AND r.is_relay = 0                 -- relays: no single-athlete time
      AND r.is_field = 0                 -- field events: a mark, not a clock
      AND r.athlete_id IS NOT NULL       -- need a person to pair on
      AND r.time_seconds IS NOT NULL
      AND r.time_seconds > 0             -- a zero time would divide a ratio later
      AND r.time_seconds < %s            -- drop the DNF sentinel
      AND r.date IS NOT NULL
      AND r.date != ''                   -- need a date for the time window
      AND m.track_type   IS NOT NULL     -- usability: NULL type fits neither cloud
      AND m.track_length IS NOT NULL     -- usability: NULL length fits neither cloud
"""


# ------------------------------------------------------------------ #
# DATE PARSING
# ------------------------------------------------------------------ #

# _parseDate
# Purpose:   Turn the raw text date into a datetime.date, or reject it.
# Arguments: raw — the results_tf.date string, expected 'YYYY-MM-DD'.
# Output:    a datetime.date, or None when the string doesn't parse OR parses
#            to an insane year (the corrupt 0023-style rows). None means
#            "drop this row" — the caller counts how often that happens.
def _parseDate(raw):
    try:
        d = date.fromisoformat(raw)      # strict 'YYYY-MM-DD' parser, fast
    except ValueError:                   # malformed string -> unusable
        return None
    if not (_MIN_SANE_YEAR <= d.year <= _MAX_SANE_YEAR):
        return None                      # parsed fine but the year is garbage
    return d


# ------------------------------------------------------------------ #
# EVENT-DISTANCE PARSING  (fallback when distance_meters is absent/insane)
# ------------------------------------------------------------------ #
# Three unit patterns, tried metric-first (by far the most common).
# re.compile builds each once at import; re.I = case-insensitive.
# Regex anatomy, once:
#   ^\s*            anchored at the string's start (leading spaces allowed)
#   (\d+(?:\.\d+)?) capture digits with an optional decimal part; (?: ... )
#                   groups WITHOUT capturing, so group(1) stays just the number
#   \s*m\b          optional space then 'm' ending at a word boundary — so
#                   '200m Hurdles' matches but '2 Mile' does NOT (\b fails
#                   between 'm' and 'i')

_METERS_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m\b", re.I)

# Miles: the count is OPTIONAL (the ? after the capture group), so a bare
# 'Mile' parses as 1 mile while '2 Mile' captures the 2.
_MILES_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*mile", re.I)

# Yards: 'y', 'yd', 'yard', 'yards' all accepted by y(?:d|ards?)? .
_YARDS_RX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*y(?:d|ards?)?\b", re.I)


# _isRelayString
# Purpose:   Admissibility check: is this event a relay/multi-leg race? Relay
#            times are 3-4 people's combined time — the same-athlete
#            cancellation the clouds rely on does NOT hold, so these rows are
#            rejected BY RULE (not left to fail parsing by accident).
# Arguments: event_short — the raw event code text (may be None).
# Output:    True = relay, reject; False = single-athlete, proceed.
def _isRelayString(event_short) -> bool:
    # .search (not .match): a relay marker ANYWHERE poisons the string.
    return bool(event_short and _RELAY_RX.search(event_short))


# _parseEventDistance
# Purpose:   Read a race distance in meters out of one event_short string.
#            The FALLBACK path — used only when distance_meters was absent.
# Arguments: event_short — the raw event code text (may be None).
# Output:    meters as float, or None when the string names no distance we
#            recognize ('DMR', 'Pentathlon', ...). None = the caller decides
#            (here: drop the row and tally the string).
def _parseEventDistance(event_short):
    if not event_short:
        return None
    # Commas out ('5,000m'), hyphens out ('10-km'), edges trimmed.
    s = event_short.replace(",", "").replace("-", "").strip()
    if _RELAY_RX.search(s):               # backstop: reject relays even if
        return None                       # a caller skipped _isRelayString
    m = _METERS_RX.match(s)
    if m:
        return float(m.group(1))
    m = _HURDLES_RX.match(s)              # '60mh' -> 60.0
    if m:
        return float(m.group(1))
    m = _MSTEEPLE_RX.match(s)             # '1609msteeple' -> 1609.0
    if m:
        return float(m.group(1))
    m = _KILO_RX.match(s)                 # '3ksteeple' -> 3000, '10km' -> 10000
    if m:
        return float(m.group(1)) * 1000.0
    m = _MILES_RX.match(s)
    if m:
        count = float(m.group(1)) if m.group(1) else 1.0
        return count * _METERS_PER_MILE
    m = _YARDH_RX.match(s)                # '60yh' -> 60 yards, BEFORE plain
    if m:                                 # yards (which can't see the h)
        return float(m.group(1)) * _METERS_PER_YARD
    m = _YARDS_RX.match(s)
    if m:
        return float(m.group(1)) * _METERS_PER_YARD
    return None


# ------------------------------------------------------------------ #
# EVENT-DISTANCE RESOLUTION  (the precedence rule + memo cache)
# ------------------------------------------------------------------ #

# The memo cache: event_short string -> parsed meters (or None). Only a few
# hundred DISTINCT event strings exist across 9.5M rows, so caching turns
# 9.5M regex runs into a few hundred, then dict lookups. Failures (None) are
# cached too — an unparseable string is unparseable every time; re-trying it
# per row would throw the cache's benefit away exactly where it's needed most.
_distance_cache = {}


# _saneDist
# Purpose:   One place for the sanity-window test, applied to BOTH sources.
# Arguments: d — a candidate distance (float or None).
# Output:    True only for a real number inside [_MIN_SANE_DIST, _MAX_SANE_DIST].
def _saneDist(d) -> bool:
    return d is not None and _MIN_SANE_DIST <= d <= _MAX_SANE_DIST


# _resolveEventDistance
# Purpose:   The precedence rule, in one auditable spot: the DB column wins
#            when present and sane; the parser fills its gaps; neither -> None.
# Arguments: distance_meters — meets_tf.distance_meters (float or None).
#            event_short     — the event code text, for the fallback.
# Output:    (meters, path) — path is 'column' / 'parsed' / None; the tag is
#            what lets the loader COUNT which source fed each row.
def _resolveEventDistance(distance_meters, event_short):
    if _saneDist(distance_meters):
        return float(distance_meters), "column"
    if event_short not in _distance_cache:            # miss -> parse once
        _distance_cache[event_short] = _parseEventDistance(event_short)
    parsed = _distance_cache[event_short]              # hit -> free lookup
    if _saneDist(parsed):
        return parsed, "parsed"
    return None, None


# ------------------------------------------------------------------ #
# ROW CONVERSION  (raw tuple -> the diagnostic's row dict, or a reason)
# ------------------------------------------------------------------ #

# _rowToRecord
# Purpose:   Convert one raw SQL tuple into the diagnostic's row dict, parsing
#            the date and resolving the event distance on the way.
# Arguments: raw — an 8-tuple in _GEOMETRY_SQL's column order:
#            (athlete_id, event_short, date_str, time_seconds,
#             track_type, track_length, is_indoor, distance_meters).
# Output:    (record, drop_reason, path) — exactly ONE of record/drop_reason
#            is None:
#              record      — the row dict, or None if the row is unusable
#              drop_reason — 'bad_date' / 'no_distance', or None if kept
#              path        — 'column' / 'parsed' when kept (which source fed
#                            event_distance), else None
#            The (value, reason) pair is the drop-bookkeeping pattern: the
#            caller keeps separate books per failure kind instead of one
#            mushy count — rows, not intentions.
def _rowToRecord(raw, override_hits=None):
    # Tuple unpacking: 9 positions to 9 names; order MUST match the SELECT.
    (athlete_id, event_short, date_str, time_s,
     ttype, tlen, indoor, dist_col, location_id) = raw

    d = _parseDate(date_str)
    if d is None:
        return None, "bad_date", None

    # VENUE OVERRIDE — before anything reads the geometry fields. The date
    # is already a real date object here, so .isoformat() gives the ISO
    # string the override's range checks compare (no US-format hazard in
    # this loader: _parseDate is strict-ISO). is_indoor is never touched —
    # the override corrects type/length only.
    geo, rule = applyVenueOverride(
        {"track_type": ttype, "track_length": tlen, "is_indoor": indoor},
        location_id, d.isoformat())
    if rule is not None:
        ttype, tlen = geo["track_type"], geo["track_length"]
        if override_hits is not None:            # per-venue ledger
            override_hits[location_id] += 1

    if _isRelayString(event_short):       # admissibility BEFORE parsing:
        return None, "relay", None        # its own ledger line
    
    if _WALK_RX.search(event_short or ""):    # admissibility, own ledger line
        return None, "racewalk", None

    dist, path = _resolveEventDistance(dist_col, event_short)
    if dist is None:
        return None, "no_distance", None

    return {
        "athlete_id":     athlete_id,
        "event_short":    event_short,
        "date":           d,
        "time_seconds":   time_s,
        "track_type":     ttype,
        "track_length":   tlen,
        "is_indoor":      indoor,
        "event_distance": dist,
        "assumed":        False,             # the flag: this row is real, not assumed
    }, None, path

# _assumedRowToRecord
# Purpose:   Convert one assumed-candidate tuple into a row dict, stamping
#            the assumed geometry and FLAGGING it. The flag is the whole
#            discipline: an assumption must stay distinguishable forever
#            downstream, or the annotated-vs-assumed agreement check
#            becomes impossible (exclusion/inclusion by rule, again).
# Arguments: raw — a 5-tuple (athlete_id, event_short, date_str,
#            time_seconds, distance_meters).
# Output:    (record, drop_reason, path) — same triple contract as
#            _rowToRecord, so the caller's bookkeeping is shared.
def _assumedRowToRecord(raw):
    athlete_id, event_short, date_str, time_s, dist_col = raw
    d = _parseDate(date_str)
    if d is None:
        return None, "bad_date", None
    if _isRelayString(event_short):
        return None, "relay", None
    if _WALK_RX.search(event_short or ""):
        return None, "racewalk", None
    dist, path = _resolveEventDistance(dist_col, event_short)
    if dist is None:
        return None, "no_distance", None
    return {
        "athlete_id":     athlete_id,
        "event_short":    event_short,
        "date":           d,
        "time_seconds":   time_s,
        "track_type":     _ASSUMED_TYPE,     # the stamp...
        "track_length":   _ASSUMED_LENGTH,
        "is_indoor":      0,
        "event_distance": dist,
        "assumed":        True,              # ...and its flag
    }, None, path

# _tfrrsRowToRecord
# Purpose:   Convert one tfrrs geometry tuple into the shared row dict. Same
#            (record, drop_reason, path) contract as _rowToRecord and
#            _assumedRowToRecord, so the caller's Counters are reused verbatim.
# Arguments: raw — an 8-tuple in _TFRRS_GEOMETRY_SQL's column order:
#            (athlete_id, event_short, date_str, time_seconds,
#             track_type, track_length, is_indoor, location_id).
# Output:    (record, drop_reason, path) — exactly one of record/drop_reason
#            is None; path is 'parsed' when kept (tfrrs never uses 'column').
# NOTE:      NO applyVenueOverride here — overrides are anet-venue facts keyed
#            to anet location_ids; applying them to tfrrs rows would be a
#            cross-source category error.
def _tfrrsRowToRecord(raw):
    (athlete_id, event_short, date_str, time_s,
     ttype, tlen, indoor, location_id) = raw

    d = _parseDate(date_str)                    # reuse: strict-ISO + sane-year
    if d is None:
        return None, "bad_date", None

    if _isRelayString(event_short):             # reuse: relay admissibility
        return None, "relay", None
    if _WALK_RX.search(event_short or ""):      # reuse: racewalk admissibility
        return None, "racewalk", None

    dist, path = _resolveEventDistance(None, event_short)   # None => parser only
    if dist is None:
        return None, "no_distance", None

    return {
        "athlete_id":     athlete_id,
        "event_short":    event_short,
        "date":           d,
        "time_seconds":   time_s,
        "track_type":     ttype,
        "track_length":   tlen,
        "is_indoor":      indoor,
        "event_distance": dist,
        "assumed":        False,
    }, None, path


# loadTFRRSGeometryResults
# Purpose:   The tfrrs analogue of loadTFGeometryResults — every usable tfrrs
#            TF running result with propagated geometry, one dict per row, same
#            contract. Kept SEPARATE so the anet-only baseline is untouched and
#            a caller opts in explicitly (the header's --sources discipline).
# Arguments: (none) — value-level narrowing stays with the caller, as before.
# Output:    list of row dicts identical in shape to loadTFGeometryResults.
def loadTFRRSGeometryResults() -> list:
    records = []
    drops = Counter()
    paths = Counter()
    unparsed = Counter()

    with getConn() as conn:
        # meet-grain join over ~20M tfrrs rows: same scale-safe planner guard
        # the assumed leg uses, for the same reason (avoid a nested-loop blowup).
        _tuneConnectionForBulkScan(conn)
        cursor = conn.cursor("tfrrs_geometry_stream")   # server-side, batched
        cursor.itersize = _FETCH_BATCH
        cursor.execute(_TFRRS_GEOMETRY_SQL)
        for raw in cursor:
            rec, reason, path = _tfrrsRowToRecord(raw)
            if rec is None:
                drops[reason] += 1
                if reason == "no_distance":
                    unparsed[raw[1]] += 1               # raw[1] = event_short
            else:
                paths[path] += 1
                records.append(rec)

    print(f"  loaded {len(records):,} tfrrs geometry rows")
    print(f"    event_distance from parser: {paths['parsed']:,}"
          f"  (tfrrs has no distance column — parser-only by design)")
    for reason, n in drops.most_common():
        print(f"    dropped [{reason}]: {n:,}")
    if unparsed:
        print(f"    top unparseable event strings (extend the parser from THIS list):")
        for name, n in unparsed.most_common(_TOP_UNPARSED):
            print(f"      {n:>8,}  {name!r}")
    return records

# ---------------------------------------------------------------------------
# _tuneConnectionForBulkScan
# Purpose:   Make the planner favour SCALE-SAFE hash/merge joins for a full-
#            drain streaming query, on THIS connection's current transaction.
#            The assumed-400 partner EXISTS, left at defaults, is planned as a
#            per-row nested loop that fires once per survivor (~11.6M). A nested
#            loop is only cheap when few rows survive; if a row estimate ever
#            drifts again, it re-explodes to hours. Disabling nested-loop joins
#            forces the hash plan, whose cost grows LINEARLY and cannot explode
#            on a bad estimate.
# Arguments: conn -- an open pooled connection, at the START of the transaction
#            the streaming cursor will run in. MUST be called before the cursor
#            is executed (the plan is chosen at execute time).
# Output:    None. Mutates only this transaction's planner settings.
# ---------------------------------------------------------------------------
def _tuneConnectionForBulkScan(conn):
    with conn.cursor() as cur:                 # a throwaway client cursor to issue the SET
        # SET LOCAL = scoped to the CURRENT TRANSACTION only. It reverts when the
        # transaction ends (getConn rolls back on block exit), so it never leaks
        # to the next borrower of the pooled connection. A plain
        # SET (no LOCAL) WOULD leak to the next borrower -- never use it here.
        cur.execute("SET LOCAL enable_nestloop = off")
        # If a later EXPLAIN ANALYZE shows "Batches: N" (N > 1) on the hash nodes,
        # the hashes are spilling to disk. Give THIS one transaction more memory:
        #     cur.execute("SET LOCAL work_mem = '256MB'")
        # (SET LOCAL again -- affects only this txn. Size it to your box's RAM.)


# streamAssumed400Records
# Purpose:   PASS 2's public generator: yield assumed-400 row dicts one at a
#            time, never holding the set in memory (census A: 70.5M
#            candidates — a list would be ~40GB). Prints its own ledger when
#            the stream is exhausted.
# Arguments: (none) — same no-filter contract as loadTFGeometryResults.
# Output:    a generator of row dicts (each with assumed=True).
def streamAssumed400Records():
    drops = Counter()
    kept = 0
    with getConn() as conn:
        _tuneConnectionForBulkScan(conn)             # <-- NEW LINE (must precede the cursor)
        cursor = conn.cursor("assumed400_stream")    # server-side, batched
        cursor.itersize = _FETCH_BATCH
        cursor.execute(_ASSUMED_SQL, (_SENTINEL_TIME,))
        for raw in cursor:
            rec, reason, _path = _assumedRowToRecord(raw)
            if rec is None:
                drops[reason] += 1
            else:
                kept += 1
                yield rec                            # hand over, retain nothing
    print(f"  assumed-400 stream: {kept:,} yielded", end="")
    for reason, n in drops.most_common():
        print(f"  [{reason}] {n:,}", end="")
    print()


# ------------------------------------------------------------------ #
# STREAMING
# ------------------------------------------------------------------ #

# _streamGeometryRows
# Purpose:   Execute the query on a SERVER-SIDE cursor and yield raw tuples in
#            batches, so the full result set is never resident twice.
# Arguments: conn — an open connection (must stay open while consuming).
# Output:    a generator of raw row tuples.
def _streamGeometryRows(conn):
    # Passing a NAME to cursor() makes it server-side: Postgres keeps the
    # result as a portal and ships itersize rows per round trip, instead of
    # psycopg2 buffering everything client-side before fetchall().
    cursor = conn.cursor("geometry_stream")
    cursor.itersize = _FETCH_BATCH
    cursor.execute(_GEOMETRY_SQL, (_SENTINEL_TIME,))
    # 'yield from' forwards every row the cursor iterator produces, one at a
    # time, without us writing the fetch loop by hand.
    yield from cursor


# ------------------------------------------------------------------ #
# PUBLIC ENTRY POINT
# ------------------------------------------------------------------ #

# loadTFGeometryResults
# Purpose:   Load every usable anet TF running result with known geometry.
# Arguments: (none) — value-level narrowing stays in the diagnostic, on purpose.
# Output:    list of row dicts, each carrying EXACTLY the fields the diagnostic
#            reads (the seam contract):
#              athlete_id     (int)        — pairing key; never NULL (filtered)
#              event_short    (str)        — grouping key half
#              date           (date)       — real date OBJECT; drives the window
#              time_seconds   (float)      — the value whose ratio IS the effect
#              track_type     (str)        — 'Flat'/'Banked'/'Oversized'/
#                                            'Undersized'; never None (filtered)
#              track_length   (float)      — meters; never None (filtered)
#              is_indoor      (int)        — 1/0
#              event_distance (float)      — race distance in meters; never
#                                            None (rows without one drop)
#            Prints kept/dropped per reason, the column-vs-parsed split, and
#            the top unparseable event strings (the parser's shopping list).
def loadTFGeometryResults() -> list:
    records = []
    drops = Counter()                    # reason -> count
    paths = Counter()                    # 'column'/'parsed' -> count
    unparsed = Counter()                 # event_short -> count (failed rows)
    override_hits = Counter()            # location_id -> corrected rows

    with getConn() as conn:
        for raw in _streamGeometryRows(conn):
            rec, reason, path = _rowToRecord(raw, override_hits)
            if rec is None:
                drops[reason] += 1
                if reason == "no_distance":
                    unparsed[raw[1]] += 1     # raw[1] = event_short position
            else:
                paths[path] += 1
                records.append(rec)

    print(f"  loaded {len(records):,} geometry rows")
    if override_hits:
        total_ov = sum(override_hits.values())
        per_venue = "  ".join(f"loc{k}:{v:,}"
                              for k, v in override_hits.most_common())
        print(f"    venue overrides applied: {total_ov:,}  ({per_venue})")
    else:
        print(f"    venue overrides applied: 0"
              f"  <- if OVERRIDES is non-empty this means the location"
              f" join is not delivering — investigate before trusting"
              f" the clouds")
    print(f"    event_distance from column: {paths['column']:,}   "
          f"from parser: {paths['parsed']:,}")
    for reason, n in drops.most_common():     # most_common() = sorted big-first
        print(f"    dropped [{reason}]: {n:,}")
    if unparsed:
        print(f"    top unparseable event strings (extend the parser from THIS list):")
        for name, n in unparsed.most_common(_TOP_UNPARSED):
            print(f"      {n:>8,}  {name!r}")   # !r shows quotes/whitespace

    return records