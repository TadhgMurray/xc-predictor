# Project: xc-predictor
# File:    engine/geometry_db.py
# Purpose: Read-side loader for the geometry matched-pair diagnostic, in the same
#          style as speed_ratings_db.loadResults. Pulls TF *running* results with
#          their track geometry (track_type/track_length/is_indoor) so the
#          diagnostic can compare the SAME athlete across geometries.
#
# WHY anet-ONLY (not a stylistic choice -- a data fact):
#   Geometry lives only in meets_tf, which is anet-sourced. The locate-geometry
#   probe confirmed meets_tfrrs has NONE of the geometry columns. A TFRRS result
#   therefore has no track_type/length to join to -- it could never form a
#   matched pair, so loading it would only add rows that are NULL on every
#   geometry field. We filter to source='anet' to avoid pulling rows that cannot
#   contribute. When TFRRS geometry is later propagated via venue-matching, this
#   filter widens; until then, anet is the entire usable set.

import sys
sys.path.insert(0, "scripts")

import psycopg2.extras
from database import getConn

# The XC no-result sentinel: a DNF/DNS stored as a huge time (999999). It is NOT
# a real performance; if it slipped into a ratio it would dwarf every real time.
# We filter strictly below it so only genuine finishing times survive.
_SENTINEL_TIME = 999999


# loadTFGeometryResults
# Purpose: Load anet TF running results joined to their meet's track geometry --
#          one row per finishing performance, carrying exactly the fields the
#          matched-pair diagnostic needs (who/what/when/how-fast/where-geometry).
#          Field events and relays are excluded at the SQL level because they have
#          no single comparable clock time; the DNF sentinel and null/blank dates
#          are excluded so every returned row is a usable data point.
# Arguments:
#           (none) -- this deliberately loads the WHOLE anet TF running set rather
#           than taking filters, because the diagnostic does its narrowing (to
#           matched pairs) in memory. Pushing event/geometry filters into SQL here
#           would prevent the in-memory pairing from seeing both sides of a
#           contrast for the same athlete.
# Output:
#           A list of dicts (RealDictCursor rows), one per result, each with:
#             athlete_id   (int)   -- the anet athlete; the pairing key. Never NULL
#                                     (filtered), so two rows with the same value
#                                     are the same person.
#             event_short  (str)   -- the event code, e.g. '200m'. Pairs are only
#                                     ever formed within one event_short, so this
#                                     is half the grouping key.
#             gender       (str)   -- 'M'/'F', from the athlete. The other half of
#                                     the grouping key (a 200m M pair never mixes
#                                     with 200m F).
#             date         (str)   -- 'YYYY-MM-DD'. Drives the close-in-time test;
#                                     guaranteed non-null/non-empty by the WHERE.
#             time_seconds (float) -- the finishing time, < _SENTINEL_TIME. This
#                                     is the value whose ratio across geometries
#                                     IS the measured effect.
#             track_type   (str|None) -- 'Flat'/'Banked'/'Oversized'/'Undersized'
#                                     or None when the venue's type is unrecorded.
#                                     None rows simply can't be placed on the
#                                     banking contrast (handled downstream).
#             track_length (float|None) -- track length in metres (e.g. 200.0,
#                                     400.0) or None when unrecorded. None indoor
#                                     rows can't be placed on the length contrast.
#             is_indoor    (int)   -- 1 indoor / 0 outdoor. 100% populated, so it's
#                                     the most reliable geometry signal and anchors
#                                     both contrasts.
#           The list is unsorted; the diagnostic indexes it itself. Empty list if
#           nothing matches (e.g. run before any geometry is populated).
def loadTFGeometryResults() -> list:
    with getConn() as conn:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT
                r.athlete_id,
                r.event_short,
                a.gender,
                r.date,
                r.time_seconds,
                m.track_type,
                m.track_length,
                m.is_indoor
            FROM results_tf r
            JOIN athletes  a ON a.athlete_id = r.athlete_id
            -- meets_tf is keyed (meet_id, div_id, event_id); join on ALL THREE.
            -- Joining on div_id/event_id alone hits the historical collision
            -- (div_id is a per-meet local index), pulling a DIFFERENT meet's
            -- geometry onto this result. meet_id closes that hole.
            JOIN meets_tf  m ON m.meet_id  = r.meet_id
                            AND m.div_id   = r.div_id
                            AND m.event_id = r.event_id
            WHERE r.source = 'anet'        -- the only source with geometry (see top)
              AND r.is_relay = 0           -- relays have no single athlete time
              AND r.is_field = 0           -- field events have a mark, not a clock time
              AND r.athlete_id IS NOT NULL -- need a stable person to pair on
              AND r.time_seconds IS NOT NULL
              AND r.time_seconds < %s       -- drop the DNF sentinel
              AND r.date IS NOT NULL
              AND r.date != ''              -- need a real date for the time window
        """, (_SENTINEL_TIME,))
        return cursor.fetchall()