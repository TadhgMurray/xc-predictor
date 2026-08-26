# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Model
# Date: 6/11/2026
# File Title: feature_extraction.py
# Purpose: Builds the training dataset for the transformer model.
#          For every result in the DB where the athlete has prior results,
#          creates one training example:
#            - sequence: all prior results as feature vectors
#            - context:  features about the target race
#            - target:   normalized_time of the target result
#
# Output files (all saved to model/data/):
#            - sequences.pt   — padded sequence tensors
#            - masks.pt       — attention masks (True = real, False = padding)
#            - context.pt     — context feature tensors
#            - targets.pt     — target normalized_time values
#            - encoders.pkl   — saved label encoders for use at inference time
#
# System design:
#   1. Load all results from DB in one big query
#   2. Group by athlete_id
#   3. For each athlete, sort results by date
#   4. For each result, build (sequence, context, target) from prior results
#   5. Encode categorical features (grade, pool, school) to integers
#   6. Pad sequences to uniform length within each batch
#   7. Save everything to disk as PyTorch tensors

import os
import sys
import heapq
import pickle
import zlib
import torch
import psycopg2.extras
from collections import defaultdict
from datetime import datetime, date, timedelta
from sklearn.preprocessing import LabelEncoder
import re
import random
import statistics

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import initPool, closePool, getConn

# ★ THE ENGINE'S POOLING DECISION, IMPORTED. buildPool below explains
#   what it replaces and why a fifth reimplementation was the bug.
from pool_resolve import resolvePool

# ★ THE HAND-VERIFIED DISTANCES, SAME AS THE FITTER AND THE BACKFILL. The
#   target (normalized_time) is computed at the OVERRIDDEN distance, so the
#   distance feature must be the overridden distance too -- otherwise the
#   12,000+ corrected divisions teach the model a 5000m label against a time
#   normalized at 2735m, wrong physics at exactly the rows that were fixed.
#   (TF's dict is empty today; the helper then returns "" and the query is
#   byte-for-byte what it was.)
from corrections import distanceOverrideSQL
# stored_expr = each query's own fallback column, so the override is clamped
# to never RAISE the stored distance (downward-only policy; see
# corrections._DISTANCE_OVERRIDES_XC header).
_OV_JOIN_XC, _OV_COALESCE_XC = distanceOverrideSQL(
    "r", "XC", stored_expr="m.distance")
_OV_JOIN_TF, _OV_COALESCE_TF = distanceOverrideSQL(
    "r", "TF", stored_expr="m.distance_meters")

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Where to save the output tensors and encoders.
OUTPUT_DIR = "model/data"
 
# Default race hours for weather lookup.
# We don't store actual race times, so we use typical start times
# per sport. At inference time the user can specify the exact hour.
# XC races almost always start 9-10am.
# TF distance events typically run in the afternoon session.
XC_DEFAULT_HOUR  = 9
TF_DEFAULT_HOUR  = 15
 
# Minimum normalized_time to be considered a valid result.
# Matches the filter in the speed ratings engine.
MIN_NORMALIZED_TIME = 600

# Number of examples per chunk file.
CHUNK_SIZE = 10_000

# How many examples to hold before emitting a chunk, so that a chunk is a
# MIX of athletes rather than a contiguous run of them.
#
# ★ WHY THIS EXISTS. train.py batches WITHIN a chunk -- it has to, because a
#   plain shuffle across chunk files means one 10,000-example file read per
#   example and training never finishes. That is only equivalent to true
#   shuffling if a chunk is already a random sample. Written straight out of
#   groupByAthlete, a chunk is ~one athlete after another, so every batch
#   would be 64 examples from a handful of athletes -- correlated targets,
#   and biased gradients if athlete order tracks anything (school, state,
#   era).
#
#   So: fill a buffer SHUFFLE_FACTOR chunks deep, shuffle it, emit ONE
#   chunk, keep the rest. The standard streaming-shuffle trick. RAM holds
#   ~SHUFFLE_FACTOR * CHUNK_SIZE examples instead of the whole corpus, and
#   an athlete's examples end up spread across many chunks.
#
# ⚠ IT IS NOT A FULL SHUFFLE. Two examples more than ~SHUFFLE_FACTOR chunks
#   apart in athlete order can never land in the same chunk. Raise the
#   factor if RAM allows; 10 (100k examples) already breaks up any
#   single-athlete run by orders of magnitude.
SHUFFLE_FACTOR = 10

# Fixed seed so the shuffle is reproducible -- a rerun of extraction gives
# byte-identical chunk files, which makes "did the data change or the model"
# answerable.
SHUFFLE_SEED = 42

# Width of one race's feature vector, and of the target-race context
# vector. Both are 18; they are separate constants because they describe
# different things and could diverge.
#
# ⚠ THESE MUST MATCH transformer.SEQUENCE_FEATURES / CONTEXT_FEATURES. A
#   mismatch is not caught here -- it surfaces as a shape error deep inside
#   the first nn.Linear, long after the extraction has finished writing
#   gigabytes of chunk files. The padding row below used a hardcoded 18,
#   which silently desyncs the moment a feature is added.
SEQUENCE_FEATURES = 21
# 21, not 20: is_forecast sits at index 0 -- count _buildContextVector's
# return, and keep transformer.CONTEXT_FEATURES equal to it.
CONTEXT_FEATURES  = 21

# ★ ATHLETE-DISJOINT VALIDATION, DECIDED HERE. train.py used to random_split
#   over EXAMPLES, which puts the same athlete's examples -- and a forecast
#   twin of the very same target -- on both sides of the split. Validation
#   loss then rewards memorising athletes, and save-on-best keeps an overfit
#   model on purpose. The split has to be by ATHLETE, and extraction is the
#   only place that still knows which athlete an example came from -- so each
#   athlete is hashed to a side here, every one of their examples carries the
#   flag through the shuffle, and val_mask.pt records it in global order.
#   crc32, not hash(): Python's hash is salted per process, and the split must
#   be identical between runs.
VAL_FRACTION_PERMILLE = 100          # 10.0% of ATHLETES -> validation


def _isValAthlete(identity) -> bool:
    return (zlib.crc32(repr(identity).encode())
            % 1000) < VAL_FRACTION_PERMILLE

# ★ THE TWO WIDTHS DIFFER ON PURPOSE, AND THE DIFFERENCE IS `place`.
#   A prior race's finishing position is field context the model cannot get
#   from a time alone: a 16:00 that won by 40 seconds and a 16:00 that
#   finished 40th say different things about how hard the athlete was
#   pushed. Safe in the sequence -- those races are over and their times are
#   already in the vector.
#
# ⚠ AND IT IS LEAKAGE IN THE CONTEXT. Telling the model "this athlete
#   finished 3rd" and asking it to predict their time hands it a fact
#   DETERMINED BY the outcome being predicted. It would learn to lean on a
#   feature that does not exist at inference -- which shows up as good
#   validation loss and bad real predictions, the worst failure to diagnose.
#   Same reasoning excludes `score`, more so since it is team-derived.

# Latitude/longitude are centred and scaled before they reach the network.
#
# ★ RAW DEGREES WOULD DOMINATE THE FIRST LINEAR. Every other feature sits
#   near 0-1 or is a small count; -122.4 and 47.6 are one to two orders of
#   magnitude larger, so their weights start by drowning everything else and
#   the net wastes early epochs learning to scale them down. Centring on the
#   continental US and dividing by ~a state's width puts them in the same
#   range as the rest.
GEO_LAT_CENTER, GEO_LAT_SCALE = 39.0, 10.0
GEO_LON_CENTER, GEO_LON_SCALE = -98.0, 20.0

# ★ A SAFETY RAIL, NOT A TUNING KNOB -- AND THAT IS A CHANGE. This was 500
#   as a placeholder, then 64 as a real cap, and is now high enough to
#   truncate nobody. Two changes made truncating pointless:
#
#     ragged chunks       the cap no longer costs disk. A three-race athlete
#                         stores three rows whether this says 64 or 512.
#     length-sorted       the cap no longer costs a shuffled batch. Long
#     batching            athletes now batch WITH each other, so their L is
#                         paid by the few batches that hold them instead of
#                         by every batch one of them lands in.
#
#   What removing the cap actually costs, measured against the fitted step
#   time (33 + 7.51L + 0.0190L^2 ms at batch 64) over the real length
#   distribution, with batches length-sorted:
#
#       cap    truncated   mean padded L   epoch
#        64        4.80%            18.0    1.00x
#       128        0.95%            19.3    1.08x
#       256        0.10%            19.9    1.13x
#       331        0.00%            19.9    1.13x
#
#   13% of an epoch to stop throwing away the longest careers in the corpus,
#   which are the multi-year athletes with XC and TF merged -- exactly the
#   histories a sequence model has the most to learn from.
#
# ⚠ IT IS STILL A BOUND, AND ON PURPose. The longest athlete_id had 331
#   races; person_id merging concatenates careers scraped from two sources,
#   so the real maximum is higher and unknown until saveAll prints it. 512
#   sits above any plausible career and still stops one corrupt row -- an
#   identity collision merging thousands of results onto one person -- from
#   allocating a batch nobody can hold.
#
# ! _padSequence STILL TRUNCATES TO THE LAST max_len when it does bite: the
#   most recent races are the ones that predict the next one.
MAX_SEQ_LEN = 512

# ------------------------------------------------------------------ #
# CHUNK 1 — DATABASE QUERIES
# ------------------------------------------------------------------ #
#
# We load everything in two queries:
#   1. All XC results with meet, athlete, course difficulty, weather
#   2. All TF results with meet, athlete, course difficulty, weather
#
# Then we merge them into one list in Python.
#
# Why two queries instead of one?
# XC and TF results live in different tables (results vs results_tf)
# and join to different meet tables (meets vs meets_tf). It's cleaner
# to query them separately and merge in Python than to write one
# massive UNION query.
#
# Why LEFT JOIN on weather and course_difficulties?
# Not every meet has weather data yet (backfill not complete) and not
# every course has a difficulty rating (thin courses get 0.0). LEFT JOIN
# means we still get the result row even if weather/difficulty is missing
# — the missing columns just come back as NULL, which we handle in Python.


# ⚠ STREAMED, NEVER fetchall(). The corpus is ~54M rated rows across the two
#   tables; materialised as RealDict rows that is >100 GB of Python objects
#   and the old loadXCResults() died at the first query on any real machine.
#   A named (server-side) cursor holds a portal in Postgres and hands rows
#   over in itersize batches, so RAM holds one batch, not the corpus.
#
# ⚠ ORDERED BY IDENTITY, NOT BY DATE. Streaming only works if an athlete's
#   rows arrive TOGETHER: the two sorted streams are heap-merged on the same
#   key and grouped as they pass. (person_id IS NULL) sorts person-keyed rows
#   apart from athlete-keyed ones, matching _identity's tag; date last keeps
#   each career chronological.
_STREAM_BATCH = 50_000

_ORDER_BY = """
        ORDER BY (r.person_id IS NULL) ASC,
                 COALESCE(r.person_id, r.athlete_id) ASC,
                 r.date ASC
"""

_XC_SQL = f"""
            SELECT
                -- Result identifiers.
                r.result_id,
                r.athlete_id,
                r.date,
                       
                -- The value we're trying to predict.
                r.normalized_time,
                -- Finishing position. Sequence vector only -- in the
                -- target's context it would be leakage, since place is
                -- determined by the outcome being predicted.
                r.place,
                       
                -- Per-race features that go into the sequence.
                r.grade,
                r.time_seconds,
                a.gender,
                
                -- Meet features.
                m.meet_id,
                m.course_name,
                -- AS renames the column in the query result,
                -- from distance to distance_meters.
                -- ★ THE OVERRIDDEN DISTANCE, when one exists. The target was
                --   normalized at it; the feature must agree with the target.
                COALESCE({_OV_COALESCE_XC} m.distance) AS distance_meters,
                m.gps_lat,
                m.gps_long,
                
                -- Course difficulty - how much harder/easier than flat.
                -- LEFT JOIN means NULL if course not yet rated, which COALESCE
                -- makes 0.0.
                COALESCE(cd.difficulty, 0.0) AS course_difficulty,
                -- The venue's identity, for the embedding. Already
                -- resolved by the difficulty join above, so this
                -- costs nothing extra.
                cc.canonical_id,
                
                -- Weather features - NULL if backfill not yet run for this meet.
                -- COALESCE won't help here since we wanted to know if it's missing.
                -- We handle NULLs in Python by substituting 0.0.)
                w.temp_c,
                w.dew_point_c,
                w.humidity,
                w.apparent_temp_c,
                w.precipitation_mm,
                w.pressure_hpa,
                w.cloud_cover,
                w.wind_speed_kmh AS wind_speed_km,
                w.wind_dir,
                       
                -- Altitude of the meet location.
                -- NULL until elevation backfill runs.
                m.altitude_meters,
 
                -- School name for school encoding.
                -- ★ FROM `results`, NOT `athletes`. athletes.school is one row
                --   per school an athlete ever had; results.school is the
                --   school they raced for ON THIS DAY, which is what the
                --   encoding should see. Taking it from the LATERAL would pin
                --   one alphabetically-chosen school across a whole career.
                --   Aliased so the downstream dict key is unchanged.
                r.school AS school,
 
                -- Flag so the model knows this is an XC result.
                -- Everything resolvePool reads. person_id, not athlete_id:
                -- the gate tables are keyed on the person.
                r.person_id,
                r.source,
                COALESCE(asl_s.level, asl_a.level) AS season_level,
                (gu.person_id IS NOT NULL)  AS grade_untrusted,
                   gu.grade                    AS fixed_grade,
                   gu.level                    AS fixed_level,
                (pas.person_id IS NOT NULL) AS is_pro,
                cfs.first_date              AS college_first,
                ufs.first_date              AS upperclass_first,
                TRUE AS is_xc,
                FALSE AS is_indoor
        
        FROM results r
        -- ★ LATERAL, NOT A PLAIN JOIN. `athletes` PK is (athlete_id, school),
        --   so an athlete with rows for three schools produced THREE IDENTICAL
        --   COPIES of every one of their results: a measured 1.070x fan-out,
        --   concentrated on transfer athletes -- exactly the multi-level
        --   careers the model most needs to see once each.
        --
        --   Duplicates are not free: the athlete is weighted 3x per epoch, and
        --   if copies land either side of the train/val split the val loss is
        --   measuring memorisation. That is the number gating every other
        --   decision, including the W0 weather test.
        --
        --   ORDER BY school LIMIT 1 is deterministic, so reruns pick the same
        --   row. Same shape speed_ratings_db._xcQuery uses.
        LEFT JOIN LATERAL (
            SELECT a2.gender
            FROM   athletes a2
            WHERE  a2.athlete_id = r.athlete_id
            ORDER  BY a2.school
            LIMIT  1
        ) a ON TRUE
        -- All THREE key parts. `meets` is disambiguated by source, and tfrrs
        -- XC div_ids are a per-meet counter starting at 0 -- so joining on
        -- div_id alone matches a tfrrs row against whatever anet meet happens
        -- to hold that div_id: wrong course, wrong distance, wrong gps,
        -- silently. speed_ratings_db._xcQuery joins on all three.
        JOIN meets     m  ON r.div_id       = m.div_id
                         AND r.meet_id      = m.meet_id
                         AND r.source       = m.source
        -- The hand-verified distance overrides (source, meet_id, div_id) ->
        -- corrected distance; referenced by the SELECT and the difficulty
        -- join below, so it must appear before them.
{_OV_JOIN_XC}
        -- LEFT JOIN: keeps result even if no course difficulty yet.
        --
        -- The old join was `m.course_name = cd.course_name` and NEVER MATCHED:
        -- course_difficulties.course_name carries the sport prefix
        -- ('XC:Idaho State Cross Country Course') and meets.course_name does
        -- not. Every XC row therefore fell through to COALESCE(...,0.0) and
        -- the model has been training on a constant zero difficulty.
        --
        -- Fixing the prefix alone would not be enough: a name matches every
        -- distance cell AND every other venue sharing the name (there are 21
        -- Woodward Parks), which fans out and DUPLICATES training examples.
        -- The real key is (canonical_id, distance_m), resolved the same way
        -- the website and the engine resolve it.
        LEFT JOIN course_canonical cc
               ON cc.course_name = m.course_name
              AND round(cc.gps_lat::numeric,  5) = round(m.gps_lat::numeric,  5)
              AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              -- The overridden distance again: the difficulty cell is keyed
              -- by the distance actually raced, not the scraped label.
              AND cd.distance_m   =
                  (round(COALESCE({_OV_COALESCE_XC} m.distance) / 100.0)
                   * 100)::int
        
        -- LEFT JOIN weather at the default XC race hour (9am local time).
        -- meet_id matches, hour matches our XC default.
        LEFT JOIN weather w
            ON  m.meet_id = w.meet_id
            AND w.source  = m.source
            AND w.hour    = %s
                       
        -- ★ THE FIVE FACTS resolvePool NEEDS. Byte-for-byte the joins in
        --   panels.py and build_ranking_results.py: the decision is shared, so
        --   its inputs have to be identical or the model pools differently from
        --   the ratings it is trained on.
        LEFT JOIN athlete_season_level asl_s
               ON asl_s.person_id = r.person_id
              AND asl_s.sport = 'XC'
              AND asl_s.ay = -- ! '08', matching season_year.ACADEMIC_START_MONTH. This was '07'
              --   while athlete_season_level moved to the August seam; a
              --   mismatched seam mis-joins every July race silently.
              CASE WHEN substring(r.date, 6, 2) >= '08'
                                  THEN substring(r.date, 1, 4)::int
                                  ELSE substring(r.date, 1, 4)::int - 1 END
        LEFT JOIN athlete_season_level asl_a
               ON asl_a.person_id = r.person_id
              AND asl_a.sport = 'ALL'
              AND asl_a.ay = -- ! '08', matching season_year.ACADEMIC_START_MONTH. This was '07'
              --   while athlete_season_level moved to the August seam; a
              --   mismatched seam mis-joins every July race silently.
              CASE WHEN substring(r.date, 6, 2) >= '08'
                                  THEN substring(r.date, 1, 4)::int
                                  ELSE substring(r.date, 1, 4)::int - 1 END
        -- * ONE JOIN, BOTH FACTS. grade_fix carries the resolved grade or
        --   level AND, by its existence, the fact that the recorded grade is
        --   not the one to use. grade_untrusted holds the same keys, so
        --   joining it as well would be a second thing to keep in step.
        LEFT JOIN grade_fix gu
               ON gu.person_id = r.person_id
              -- ! THE ACADEMIC SEASON, NOT THE CALENDAR YEAR, AND THE SPORT
              --   DECIDES WHICH. grade_fix is keyed on the school year: a
              --   calendar year holds two of them for anyone who graduates.
              --   Dylan Weniger ran fifteen races as grade 12 through May
              --   2025, then 2025-12-13 as Fr -- one calendar year, and the
              --   majority handed his first collegiate race grade 12.
              --
              -- ! THE XC RULE, BAKED IN, because this query reads
              --   `results`. The TF query below uses the TF rule: they roll on
              --   different months and a shared literal would be wrong for one
              --   of them, silently.
              AND gu.season = ((CASE WHEN substring(r.date, 6, 2) <= '02' THEN (substring(r.date, 1, 4)::int - 1)::text ELSE substring(r.date, 1, 4) END))::int
        LEFT JOIN pro_athlete_season pas
               ON pas.person_id = r.person_id
              AND pas.season = substring(r.date, 1, 4)::int
        LEFT JOIN college_first_season cfs ON cfs.person_id = r.person_id
        LEFT JOIN upperclass_first_season ufs ON ufs.person_id = r.person_id
        WHERE r.normalized_time IS NOT NULL
        -- Makes normalized time be a reasonable value.
        AND   r.normalized_time > %s
        AND   r.date IS NOT NULL
        AND   r.date != ''
        -- Drop profile-less meet entries (AAU/junior/user-uploaded) saved
        -- with NULL athlete_id — they have no stable identity, can't be
        -- tracked across races, and are useless/polluting to a per-athlete
        -- sequence model. (See 6/25 diagnostics: ~2.5M such TF rows.)
        AND   r.athlete_id IS NOT NULL
                       
{_ORDER_BY}
"""


def _streamRows(conn, sql, params, name):
    """Server-side cursor stream: yields plain dicts, one per row.

    The named cursor is a Postgres portal -- rows come over in
    _STREAM_BATCH gulps and RAM holds one gulp, never the corpus.
    withhold=True keeps the portal alive across the commits psycopg2's
    context managers may issue elsewhere on this connection.
    """
    cursor = conn.cursor(name=name, withhold=True,
                         cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.itersize = _STREAM_BATCH
    cursor.execute(sql, params)
    for row in cursor:
        yield dict(row)
    cursor.close()


# A weather-shaped nothing, for a database where the table was never
# created: the LEFT JOIN produces the same NULLs an unfetched meet does,
# so the run survives instead of dying six hours in with UndefinedTable
# (2026-08-25: it did, and the night's summary lied about it).
_WEATHER_STUB = """(SELECT NULL::bigint AS meet_id, NULL::text AS source,
            NULL::int AS hour, NULL::real AS temp_c,
            NULL::real AS dew_point_c, NULL::real AS humidity,
            NULL::real AS apparent_temp_c, NULL::real AS precipitation_mm,
            NULL::real AS pressure_hpa, NULL::int AS cloud_cover,
            NULL::real AS wind_speed_kmh, NULL::real AS wind_dir
        WHERE FALSE)"""


def weatherlessSql(sql):
    """The same query against a database with no weather table."""
    return sql.replace("LEFT JOIN weather w",
                       f"LEFT JOIN {_WEATHER_STUB} w")


def hasWeatherTable(conn):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.weather')")
    row = cur.fetchone()
    v = row[0] if not isinstance(row, dict) else row.get("to_regclass")
    return v is not None


def _corpusSql(conn, sql):
    if hasWeatherTable(conn):
        return sql
    print("WARNING: no weather table in this database -- weather "
          "features will be all zero. backfill/weather_backfill.py "
          "creates and fills it.")
    return weatherlessSql(sql)


def streamXCResults(conn):
    """All rated XC rows, identity-ordered, streamed."""
    return _streamRows(conn, _corpusSql(conn, _XC_SQL),
                       (XC_DEFAULT_HOUR, MIN_NORMALIZED_TIME), "xc_stream")


# The TF stream: same shape and same identity ordering as the XC one.
_TF_SQL = f"""
            SELECT
                r.result_id,
                r.athlete_id,
                r.date,
                r.normalized_time,
                -- Finishing position. Sequence vector only -- in the
                -- target's context it would be leakage, since place is
                -- determined by the outcome being predicted.
                r.place,
                r.grade,
                r.time_seconds,
                a.gender,
 
                -- TF meets table has event-level info
                m.meet_id,
                m.event_short    AS course_name,
                m.location_id,
                -- _DISTANCE_OVERRIDES_TF is empty today, so the helper
                -- returns "" and this is byte-for-byte m.distance_meters --
                -- but the day a TF override lands, this picks it up.
                COALESCE({_OV_COALESCE_TF} m.distance_meters)
                                 AS distance_meters,
                m.gps_lat,
                m.gps_long,
                m.is_indoor,
 
                -- TF DOES have course difficulties. The earlier 0.0 assumed
                -- "a track is a standard course", which is wrong: a track is
                -- standard in SHAPE only. Altitude, banking, surface and
                -- 200m-vs-400m are per-venue constants, which is exactly what
                -- delta is for -- and the engine already fits them. Real TF
                -- cells run about -0.016 to -0.045, a ~3x spread across
                -- venues that a constant can never recover.
                COALESCE(cd.difficulty, 0.0) AS course_difficulty,
 
                -- Weather at default TF hour (3pm local time).
                w.temp_c,
                w.dew_point_c,
                w.humidity,
                w.apparent_temp_c,
                w.precipitation_mm,
                w.pressure_hpa,
                w.cloud_cover,
                w.wind_speed_kmh AS wind_speed_km,
                w.wind_dir,
 
                -- Altitude
                m.altitude_meters,
 
                -- Per-race school; see the XC query's note.
                r.school AS school,

                -- Creates a new column in the query result not in any table.
                -- Hardcodes the values FALSE for every TF row.
                -- Everything resolvePool reads. person_id, not athlete_id:
                -- the gate tables are keyed on the person.
                r.person_id,
                r.source,
                COALESCE(asl_s.level, asl_a.level) AS season_level,
                (gu.person_id IS NOT NULL)  AS grade_untrusted,
                   gu.grade                    AS fixed_grade,
                   gu.level                    AS fixed_level,
                (pas.person_id IS NOT NULL) AS is_pro,
                cfs.first_date              AS college_first,
                ufs.first_date              AS upperclass_first,
                FALSE AS is_xc
 
            FROM results_tf r
            -- LATERAL for the same reason as the XC query above: `athletes`
            -- PK is (athlete_id, school) and a plain join duplicates a
            -- transfer athlete's every result once per school.
            LEFT JOIN LATERAL (
                SELECT a2.gender
                FROM   athletes a2
                WHERE  a2.athlete_id = r.athlete_id
                ORDER  BY a2.school
                LIMIT  1
            ) a ON TRUE
            JOIN meets_tf  m  ON r.meet_id    = m.meet_id
                             AND r.div_id     = m.div_id
                             AND r.event_id   = m.event_id
{_OV_JOIN_TF}
            -- TF cells are keyed by LOCATION and the indoor flag, not by a
            -- course name and not by distance:
            --     TF:loc:<location_id>:<in|out>
            -- canonical_id and distance_m are NULL on every TF row, so the
            -- XC-style canonical join does not apply here. The indoor flag is
            -- part of the key because one facility gets separate cells for
            -- its indoor and outdoor tracks, and they are different courses.
            -- ⚠ SENTINEL GUARD. location_id 0 (and NULL) is "no location
            --   known", not a place. Without this every such meet joins to one
            --   'TF:loc:0:in' cell whose delta is an average over unrelated
            --   venues nationwide -- a confident number that means nothing.
            --   Excluded here so those rows fall through to COALESCE(...,0.0),
            --   i.e. HONESTLY unknown rather than wrongly specific.
            LEFT JOIN course_difficulties cd
                   ON m.location_id IS NOT NULL
                  AND m.location_id <> 0
                  AND cd.course_name = 'TF:loc:' || m.location_id || ':'
                                    -- is_indoor is INTEGER, not boolean, so it
                                    -- needs an explicit comparison: a bare
                                    -- `CASE WHEN m.is_indoor` is a type error
                                    -- in Postgres. COALESCE so an unknown flag
                                    -- reads as outdoor rather than dropping the
                                    -- whole CASE to NULL.
                                    || CASE WHEN COALESCE(m.is_indoor, 0) = 1
                                            THEN 'in' ELSE 'out' END
 
            LEFT JOIN weather w
                ON  m.meet_id = w.meet_id
            AND w.source  = m.source
                AND w.hour    = %s
 
            -- ★ THE FIVE FACTS resolvePool NEEDS. Byte-for-byte the joins in
            --   panels.py and build_ranking_results.py: the decision is shared, so
            --   its inputs have to be identical or the model pools differently from
            --   the ratings it is trained on.
            LEFT JOIN athlete_season_level asl_s
                   ON asl_s.person_id = r.person_id
                  AND asl_s.sport = 'TF'
                  AND asl_s.ay = -- ! '08', matching season_year.ACADEMIC_START_MONTH. This was '07'
              --   while athlete_season_level moved to the August seam; a
              --   mismatched seam mis-joins every July race silently.
              CASE WHEN substring(r.date, 6, 2) >= '08'
                                      THEN substring(r.date, 1, 4)::int
                                      ELSE substring(r.date, 1, 4)::int - 1 END
            LEFT JOIN athlete_season_level asl_a
                   ON asl_a.person_id = r.person_id
                  AND asl_a.sport = 'ALL'
                  AND asl_a.ay = -- ! '08', matching season_year.ACADEMIC_START_MONTH. This was '07'
              --   while athlete_season_level moved to the August seam; a
              --   mismatched seam mis-joins every July race silently.
              CASE WHEN substring(r.date, 6, 2) >= '08'
                                      THEN substring(r.date, 1, 4)::int
                                      ELSE substring(r.date, 1, 4)::int - 1 END
            -- * ONE JOIN, BOTH FACTS. grade_fix carries the resolved grade or
        --   level AND, by its existence, the fact that the recorded grade is
        --   not the one to use. grade_untrusted holds the same keys, so
        --   joining it as well would be a second thing to keep in step.
        LEFT JOIN grade_fix gu
                   ON gu.person_id = r.person_id
                  -- ! THE ACADEMIC SEASON, NOT THE CALENDAR YEAR, AND THE SPORT
                  --   DECIDES WHICH. grade_fix is keyed on the school year: a
                  --   calendar year holds two of them for anyone who graduates.
                  --   Dylan Weniger ran fifteen races as grade 12 through May
                  --   2025, then 2025-12-13 as Fr -- one calendar year, and the
                  --   majority handed his first collegiate race grade 12.
                  --
                  -- ! THE TF RULE, BAKED IN, because this query reads
                  --   `results_tf`. TF rolls at October and XC at March.
                  AND gu.season = ((CASE WHEN substring(r.date, 6, 2) >= '10' THEN (substring(r.date, 1, 4)::int + 1)::text ELSE substring(r.date, 1, 4) END))::int
            LEFT JOIN pro_athlete_season pas
                   ON pas.person_id = r.person_id
                  AND pas.season = substring(r.date, 1, 4)::int
            LEFT JOIN college_first_season cfs ON cfs.person_id = r.person_id
            LEFT JOIN upperclass_first_season ufs ON ufs.person_id = r.person_id
            WHERE r.normalized_time IS NOT NULL
            AND   r.normalized_time > %s
            AND   r.date IS NOT NULL
            AND   r.date != ''
            AND   r.is_relay = 0
            -- Drop NULL-athlete_id profile-less entries (see XC note above).
            AND   r.athlete_id IS NOT NULL
{_ORDER_BY}
"""


def streamTFResults(conn):
    """All rated TF distance rows, identity-ordered, streamed."""
    return _streamRows(conn, _corpusSql(conn, _TF_SQL),
                       (TF_DEFAULT_HOUR, MIN_NORMALIZED_TIME), "tf_stream")


def personResultsSql(sport, has_weather=True):
    """The SAME corpus row SQL, filtered to a handful of athletes -- the
    inference path (racecast/predict.py) must build its sequences from
    byte-for-byte the rows training saw, or the model reads a different
    world at predict time than it learned from. Params: the stream's own
    (hour, min_normalized_time) plus the id array."""
    base = _XC_SQL if sport == "XC" else _TF_SQL
    if not has_weather:
        base = weatherlessSql(base)
    return base.replace(
        _ORDER_BY,
        "        AND COALESCE(r.person_id, r.athlete_id) = ANY(%s)\n"
        + _ORDER_BY)


# ------------------------------------------------------------------ #
# The streaming merge: two identity-sorted streams -> one athlete at a time
# ------------------------------------------------------------------ #

def _mergeKey(row):
    """The Python mirror of _ORDER_BY, so heapq.merge preserves it.

    ⚠ MUST SORT IDENTICALLY TO THE SQL: (person-keyed first, id, date).
      False < True matches Postgres's (r.person_id IS NULL) ASC.
    """
    pid = row.get("person_id")
    return (pid is None,
            pid if pid is not None else row["athlete_id"],
            row["date"])


def streamAthletes(conn):
    """Yields one athlete's complete, date-sorted career at a time.

    ★ THIS IS WHAT REPLACES loadAllResults + groupByAthlete. Both of those
      needed the corpus in RAM; this holds exactly one career (a few hundred
      rows at the extreme) plus one stream batch per source.
    """
    merged = heapq.merge(streamXCResults(conn), streamTFResults(conn),
                         key=_mergeKey)
    current_key, rows = None, []
    for row in merged:
        k = _identity(row)
        if k != current_key and rows:
            rows.sort(key=lambda r: r["date"])
            yield current_key, rows
            rows = []
        current_key = k
        rows.append(row)
    if rows:
        rows.sort(key=lambda r: r["date"])
        yield current_key, rows


# groupByAthlete
# Purpose: Groups the flat list of results into a dict keyed by athlete_id.
#          Each value is the athlete's results in chronological order.
#          This is the structure we iterate over to build training examples.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: {athlete_id: [result, result, ...]} sorted by date.
# _identity
# Purpose: the key a sequence is grouped under -- one runner, one history.
#
# ⚠ athlete_id IS NOT A PERSON. It is a per-SOURCE id: the same runner scraped
#   from anet and from tfrrs carries two of them, and grouping on it hands the
#   model two people with half a career each. Every other consumer in this
#   project resolves identity as COALESCE(person_id, athlete_id) -- the
#   linkage pass exists precisely so that person_id ties those copies
#   together -- and the extractor was the one place that did not.
#
#   The damage is worst exactly where the model needs history most: a senior
#   with four years of racing split across two sources looks like two
#   two-year athletes, so every example built from them sees half the
#   sequence it should.
#
# ! TAGGED, NOT COALESCED. person_id and athlete_id are separate id spaces, so
#   a bare COALESCE can collide a person_id with an unrelated athlete_id. The
#   tag keeps them apart; the key is opaque to every consumer, which only ever
#   iterates .items() for the value.
def _identity(row):
    pid = row.get("person_id")
    return ("p", pid) if pid is not None else ("a", row["athlete_id"])


def groupByAthlete(results: list) -> dict:
 
    # defaultdict(list) creates an empty list for any new key automatically.
    by_athlete = defaultdict(list)

    for r in results:
        by_athlete[_identity(r)].append(r)

    print(f"Grouped into {len(by_athlete):,} athletes")
    return by_athlete

# ------------------------------------------------------------------ #
# CHUNK 2 — ENCODERS
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   The model only understands numbers. Every categorical field
#   (grade, pool, school) needs to become an integer before it can
#   be turned into a tensor.
#
#   We use sklearn's LabelEncoder for this. It's a simple lookup table:
#       fit()       — scans all the data, assigns each unique string
#                      an integer (e.g. "Amherst" -> 47, "Tufts" -> 112)
#       transform() — converts strings to their assigned integers
#
#   We fit these encoders ONCE on the full training set, then save
#   them to disk (encoders.pkl). At inference time we LOAD the same
#   encoders and call transform() — this guarantees "Amherst" always
#   maps to 47, whether it's training or a live prediction.
#
# WHAT THIS CHUNK ADDS:
#   1. normalizeGrade — cleans messy grade strings into canonical labels
#   2. buildPool — combines normalized grade + gender into a pool string,
#      e.g. "FR-M", "11-F", "Unknown-M"
#   3. buildEncoders — fits LabelEncoders for grade, pool, and school
#      across the whole dataset and saves them to disk
# ------------------------------------------------------------------ #
# Grade level classification
# ------------------------------------------------------------------ #

# College grades are letter codes, not numbers.
# Stored as a set for fast "in" checks.
COLLEGE_GRADES = {"FR", "SO", "JR", "SR"}

# MS/HS grades that pass through unchanged when seen alone (no range).
SCHOOL_GRADES = {"6", "7", "8", "9", "10", "11", "12"}

# _extractFirstNumber
# Purpose: Pulls the first integer out of a grade string. Handles
#          plain numbers ("11"), ranges ("11-12"), and "19+".
#          Returns None if no number is found (e.g. "-", "", "FR").
# Arguments:
#           grade_str: the raw grade string from the DB.
# Output: An int, or None if no digits are present.
def _extractFirstNumber(grade_str: str) -> int | None:

    # re.match looks for a pattern at the START of the string.
    # \d+ means "one or more digits". We capture them with ().
    # For "11-12" this matches "11". For "19+" this matches "19".
    # For "FR" or "-" this matches nothing -> returns None.
    # This is a Regex, a pattern-matching language for strings.
    # It's a way to describe "shapes" of text so code can find/extract
    # them. Here out "shape" is one or more digits in a row.
    match = re.match(r"(\d+)", grade_str)

    if match is None:
        return None
 
    # group(1) is the captured digits as a string; int() converts it.
    return int(match.group(1))

# normalizeGrade
# Purpose: Cleans a raw grade string into a canonical label used for
#          the pool feature. Preserves the FR/SO/JR/SR and 9-12
#          distinctions (unlike a broad MS/HS/College bucket), while
#          collapsing ranges and junk values down to something usable.
# Arguments:
#           grade_str: raw grade value from the DB (e.g. "11", "FR",
#                      "11-12", "-", "", "19+", None).
# Output: A canonical grade label:
#         "6".."12", "FR","SO","JR","SR", or "Unknown".
def normalizeGrade(grade_str: str) -> str:

    # Handle None / NULL from the DB.
    if grade_str is None:
        return "Unknown"
 
    # Strip whitespace so " 11 " doesn't break comparisons.
    grade_str = grade_str.strip()
 
    # College grades pass through unchanged — check this first since
    # they're letter codes with no leading digit.
    if grade_str in COLLEGE_GRADES:
        return grade_str
 
    # Plain MS/HS grades pass through unchanged.
    if grade_str in SCHOOL_GRADES:
        return grade_str
 
    # Everything else (ranges like "11-12", "19+", "-", "") — try to
    # pull a leading number and re-check it against SCHOOL_GRADES.
    num = _extractFirstNumber(grade_str)
    
    # Rechecks against MS and HS grades to see if the leading
    # num is there.
    if num is not None and 6 <= num <= 12:
        # e.g. "11-12" -> 11 -> "11"
        return str(num)
 
    # No usable number (ages 13+, "-", "", anything unexpected).
    return "Unknown"

# ------------------------------------------------------------------ #
# Pool derivation
# ------------------------------------------------------------------ #


# buildPool
# Purpose: Combines normalized grade + gender into a single pool string.
#          This is the "pool_encoded" context feature from MODEL.md —
#          it groups athletes into comparable cohorts, e.g. "FR-M"
#          (college freshman men) vs "SR-M" (college senior men), which
#          the model can learn have different improvement curves.
# Arguments:
#           grade_str: raw grade value from the DB.
#           gender: "M" or "F" (or whatever athletes.gender contains).
# Output: A pool string like "FR-M", "11-F", "Unknown-M".
# Ordered, because this is the whole reason the feature can be a single float.
# elem < ms < hs < college < pro is a REAL ordering -- unlike a LabelEncoder's
# alphabetical accident, which the network would read as a magnitude anyway.
_LEVEL_RANK = {"elem": 0.0, "ms": 1.0, "hs": 2.0, "college": 3.0, "pro": 4.0}

# What an unpoolable row gets. -1 rather than 0, so "we could not decide" is
# distinguishable from "elementary" -- 0 would silently merge the two.
_LEVEL_UNKNOWN = -1.0


def buildPool(row: dict) -> float:
    """The athlete's LEVEL for this race, as an ordinal float.

    ★ THE ENGINE'S DECISION, NOT A FIFTH IMPLEMENTATION OF IT. The previous
      version was `f"{normalizeGrade(grade)}-{gender}"` -- grade x gender,
      which is not a pool at all. Nothing computed on the engine side reached
      the model: not athlete_season_level's per-sport verdict, not poolFor's
      grade-vs-race arbitration, not the pro/college/upperclass gates, and not
      grade_sanity's 3.47M untrusted grades.

    ★ LEVEL ONLY, NOT LEVEL x GENDER. Gender is already its own context
      feature. Folding it in here encoded it twice and gave the network two
      partially-redundant channels to reconcile.

    ⚠ RETURNS A NUMBER, NOT A LABEL. The caller no longer passes this through
      an encoder: a LabelEncoder over pool strings produces an arbitrary
      integer, and handing that to a Linear layer as a float claims an ordering
      that does not exist. The rank above is an ordering that does.
    """
    pool = resolvePool(
        row.get("grade"),
        row.get("gender"),
        row.get("source"),
        row.get("school"),
        # ★ SPORT FROM THE ROW, NOT AN ARGUMENT. Both queries already stamp
        #   is_xc, so deriving it here removes the one way a caller could pass
        #   the wrong sport and silently read the wrong season verdict.
        "XC" if row.get("is_xc") else "TF",
        season_level=row.get("season_level"),
        grade_untrusted=bool(row.get("grade_untrusted")),
                       fixed_grade=row.get("fixed_grade"),
                       fixed_level=row.get("fixed_level"),
        is_pro=bool(row.get("is_pro")),
        college_first=row.get("college_first"),
        upperclass_first=row.get("upperclass_first"),
        race_date=_asDate(row.get("date")),
        merge=True,
    )
    if not pool:
        return _LEVEL_UNKNOWN
    for level, rank in _LEVEL_RANK.items():
        if pool.startswith(level + "_"):
            return rank
    return _LEVEL_UNKNOWN


def _asDate(value):
    """'YYYY-MM-DD' -> date, for the college and upperclass gates.

    ⚠ THOSE GATES COMPARE BY DATE, NOT YEAR. A senior's spring high-school
      track season and their first autumn of college share a calendar year;
      comparing years promoted 288,264 genuine high-school athlete-seasons.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        # `date`, not `datetime.date`: this module does
        # `from datetime import datetime, date`, so `datetime` here is the
        # CLASS and datetime.date(...) would raise.
        return date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------------ #
# FORECAST TWINS
# ------------------------------------------------------------------ #

# ★ HOW OFTEN A TARGET ALSO GETS A TRUNCATED TWIN.
#
#   Not 1.0. Both shapes are real at inference -- "what happens this weekend",
#   where the history does run up to the target, and "what happens at
#   nationals", where it does not. Training only on twins would trade one blind
#   spot for the other.
#
#   0.5 keeps the dataset balanced between them without doubling it: a twin is
#   emitted for about half the targets, so the corpus grows ~50%, not 100%.
FORECAST_TWIN_RATE = 0.5

# An athlete needs this many prior races before truncating leaves a usable
# history. Below it, hiding races leaves a sequence too short to say anything.
MIN_PRIOR_FOR_TWIN = 4

# ...and this many must survive the cut, or the twin is skipped.
MIN_KEPT_RACES = 2

# ★ THE CUT IS BY DATE, NOT BY RACE COUNT, AND THAT IS THE WHOLE DESIGN.
#
#   Nobody asks "predict this race with the last four hidden". They ask "it is
#   October 1st, what happens at nationals in November". A date cut IS that
#   question. A count cut is a proxy that gets the gap wrong in both
#   directions -- hiding three races is six weeks for a championship runner and
#   four months for someone who races twice a year.
#
#   It also keeps the twin coherent with days_since_last_race, which the model
#   reads: under a count cut that feature took an arbitrary value, under a date
#   cut it is the gap we chose.
#
#   And it removes the truncation cap. A count cap silently limited how deep a
#   forecast could go -- so the championship case, the one this exists for, was
#   the one that could not be generated.
FORECAST_GAP_MIN_WEEKS = 2.0
FORECAST_GAP_MAX_WEEKS = 40.0

# ★ SKEWED, NOT UNIFORM, AND ONE KNOB RATHER THAN NAMED REGIMES.
#
#   Uniform over 2-40 weeks puts half its mass beyond 20 weeks and only ~16% in
#   the 4-10 week championship band. Weighting by u**SKEW pulls it back.
#   Measured over 200k draws:
#
#       skew   2-4w    4-10w   10-20w  20-40w   median
#       1.0     5.3%   15.9%   26.3%   52.5%    21.0w
#       2.0    23.1%   22.9%   22.8%   31.2%    11.5w
#       3.0    37.6%   22.0%   18.4%   22.1%     6.7w
#
#   2.0 gives roughly even coverage of all four bands while staying denser
#   per-week at short gaps. Named regimes with tuned weights were considered
#   and rejected: they encode a guess about query mix AS DATA, leave holes
#   between the regimes, and their only advantage -- targeting -- pays off only
#   if the guess is right. Revisit once the site can COUNT what people ask.
FORECAST_GAP_SKEW = 2.0

# ------------------------------------------------------------------ #
# VENUE VOCABULARY
# ------------------------------------------------------------------ #

# ★ A VENUE NEEDS THIS MANY RACES BEFORE IT GETS ITS OWN EMBEDDING.
#
#   Below it, the venue shares index 0 with every other thin one. The reason is
#   the same failure the difficulty solver had: a one-day course with its own
#   free parameter memorises its residual and is confidently wrong. Mission
#   Concepcion carried delta +0.7585 and produced ratings of 176 on exactly
#   that mechanism. The solver got cell_days to fix it; the model has neither
#   shrinkage nor an anchor, so the only defence is refusing to give a thin
#   venue a parameter at all.
#
#   50 races is roughly one full field, or several small ones. Generous on
#   purpose -- an embedding that memorises is worse than one that is absent.
MIN_VENUE_RACES = 50

# Index 0 is reserved. Every venue below the threshold, and every row whose
# venue could not be resolved, lands here and shares one embedding.
UNKNOWN_VENUE = 0


def buildVenueVocab(results: list[dict]) -> dict:
    """{venue_key: index}, with thin venues left out so they fall to 0.

    The key is whatever the query resolved: canonical_id for XC, location_id
    for TF, both already namespaced by sport in _venueKey so the two id spaces
    cannot collide.
    """
    from collections import Counter

    counts = Counter(_venueKey(r) for r in results if _venueKey(r) is not None)
    kept = sorted(k for k, n in counts.items() if n >= MIN_VENUE_RACES)

    thin = len(counts) - len(kept)
    print(f"  venues: {len(kept):,} with >= {MIN_VENUE_RACES} races, "
          f"{thin:,} thin ones share index {UNKNOWN_VENUE}")

    # Indices start at 1; 0 is reserved for UNKNOWN_VENUE.
    return {key: i for i, key in enumerate(kept, start=1)}


def _venueKey(row: dict):
    """A venue's identity, namespaced by sport.

    ⚠ NAMESPACED BECAUSE THE TWO ID SPACES OVERLAP. canonical_id 7 and
      location_id 7 are different places, and an unnamespaced key would merge
      a cross country course with a track.
    """
    if row.get("is_xc"):
        cid = row.get("canonical_id")
        return None if cid is None else ("XC", int(cid))
    loc = row.get("location_id")
    # location_id 0 is tfrrs' "not known", not a place -- the difficulty join
    # already excludes it for the same reason.
    if loc is None or int(loc) == 0:
        return None
    return ("TF", int(loc))


def venueIndex(row: dict, vocab: dict) -> int:
    """The embedding row for this race's venue, or UNKNOWN_VENUE."""
    key = _venueKey(row)
    return vocab.get(key, UNKNOWN_VENUE) if key is not None else UNKNOWN_VENUE


# ------------------------------------------------------------------ #
# Encoder fitting
# ------------------------------------------------------------------ #


# _collectValues
# Purpose: Walks every result and builds three parallel lists —
#          one value per result for grade, pool, and school.
#          Separated from buildEncoders so each piece is testable
#          and buildEncoders itself stays short.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: A tuple of three lists (grades, pools, schools), all the
#         same length as results.
def _collectValues(results: list[dict]) -> tuple[list, list, list]:

    grades  = []
    pools   = []
    schools = []
 
    for r in results:
        # raw grade string, e.g. "11", "FR", "-"
        # NOTE: kept raw (not normalized) for the sequence feature —
        # LabelEncoder just needs unique strings, doesn't need them clean.
        grades.append(r["grade"])
 
        # pool combines normalized grade + gender, e.g. "FR-M"
        # ⚠ NOT COLLECTED ANY MORE. buildPool returns an ordinal, so there is
        #   no encoder to fit -- see buildEncoders.
 
        # school name, e.g. "Amherst Regional"
        schools.append(r["school"])
 
    return grades, pools, schools

# _fitEncoder
# Purpose: Fits a single LabelEncoder on a list of values.
#          Tiny helper so buildEncoders doesn't repeat this 3x.
# Arguments:
#           values: list of strings (or None) to fit on.
# Output: A fitted LabelEncoder.
def _fitEncoder(values: list) -> LabelEncoder:

    # LabelEncoder.fit_transform doesn't like None - replace with
    # the string "None" so it's treated as it's own category.
    cleaned = [v if v is not None else "None" for v in values]

    encoder = LabelEncoder()

    # fit() scans all values and builds the string -> int mapping.
    # We don't need the transformed output here, just the fitted
    # encoder itself (used in Chunk 3 to transform).
    encoder.fit(cleaned)

    return encoder

# buildEncoders
# Purpose: Fits LabelEncoders for grade, pool, and school across the
#          entire dataset, prints how many categories each found,
#          and returns them as a dict ready to be saved to disk.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: dict with keys "grade", "pool", "school", each mapping to
#         a fitted LabelEncoder.
def buildEncoders(results: list[dict]) -> dict:

    grades, pools, schools = _collectValues(results)

    # For each list of string values creates an string -> int
    # mapping for later use in Chunk 3 to transform.
    encoders = {
        "grade":  _fitEncoder(grades),
        # ⚠ NO POOL ENCODER ANY MORE. buildPool returns an ORDINAL now, so
        #   there is nothing to fit -- and fitting one would reintroduce the
        #   arbitrary integer the ordinal exists to replace. Nothing outside
        #   this file reads it: train.py takes only max_len, total_examples
        #   and num_chunks from metadata.pkl.
        "school": _fitEncoder(schools),
    }

    # encoder.classes_ is the array of unique values the encoder learned.
    # len() of that tells us how many categories each field has.
    for name, encoder in encoders.items():
        print(f"  {name}: {len(encoder.classes_)} categories")

    return encoders


# ------------------------------------------------------------------ #
# Vocabulary straight from the DB -- the streaming pipeline's versions
# ------------------------------------------------------------------ #
#
# ★ AGGREGATE QUERIES, NOT A CORPUS PASS. buildEncoders/buildVenueVocab take
#   the full results list, which the streaming pipeline never materialises.
#   A LabelEncoder only needs the set of distinct values, and the venue vocab
#   only needs per-venue counts -- both of which Postgres computes in one
#   aggregate each, returning kilobytes instead of streaming 54M rows twice.
#
# ⚠ THE ENCODER CLASSES ARE A SUPERSET of what the filtered rows contain
#   (DISTINCT is taken with the row filters that matter, but edge rows can
#   differ). Harmless: an extra class shifts nothing at training time, and the
#   fitted encoder is saved beside the chunks either way.

def buildEncodersFromDB(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT grade FROM results
            WHERE  normalized_time IS NOT NULL
            UNION
            SELECT DISTINCT grade FROM results_tf
            WHERE  normalized_time IS NOT NULL
        """)
        grades = [row[0] for row in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT school FROM results
            WHERE  normalized_time IS NOT NULL
            UNION
            SELECT DISTINCT school FROM results_tf
            WHERE  normalized_time IS NOT NULL
        """)
        schools = [row[0] for row in cur.fetchall()]
    encoders = {"grade": _fitEncoder(grades), "school": _fitEncoder(schools)}
    for name, encoder in encoders.items():
        print(f"  {name}: {len(encoder.classes_)} categories")
    return encoders


def buildVenueVocabFromDB(conn) -> dict:
    """{venue_key: index} for venues with >= MIN_VENUE_RACES rated rows.

    Same keys _venueKey produces -- ("XC", canonical_id) / ("TF",
    location_id) -- with the same exclusions (no id, location_id 0).
    """
    keys = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT cc.canonical_id, count(*)
            FROM   results r
            JOIN   meets m  ON r.div_id  = m.div_id
                           AND r.meet_id = m.meet_id
                           AND r.source  = m.source
            JOIN   course_canonical cc
                   ON cc.course_name = m.course_name
                  AND round(cc.gps_lat::numeric,  5) = round(m.gps_lat::numeric,  5)
                  AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
            WHERE  r.normalized_time IS NOT NULL
              AND  r.normalized_time > %s
              AND  r.athlete_id IS NOT NULL
            GROUP  BY cc.canonical_id
            HAVING count(*) >= %s
        """, (MIN_NORMALIZED_TIME, MIN_VENUE_RACES))
        keys += [("XC", int(cid)) for cid, _ in cur.fetchall()]
        cur.execute("""
            SELECT m.location_id, count(*)
            FROM   results_tf r
            JOIN   meets_tf m ON r.meet_id  = m.meet_id
                             AND r.div_id   = m.div_id
                             AND r.event_id = m.event_id
            WHERE  r.normalized_time IS NOT NULL
              AND  r.normalized_time > %s
              AND  r.is_relay = 0
              AND  r.athlete_id IS NOT NULL
              AND  m.location_id IS NOT NULL
              AND  m.location_id <> 0
            GROUP  BY m.location_id
            HAVING count(*) >= %s
        """, (MIN_NORMALIZED_TIME, MIN_VENUE_RACES))
        keys += [("TF", int(loc)) for loc, _ in cur.fetchall()]
    kept = sorted(keys)
    print(f"  venues: {len(kept):,} with >= {MIN_VENUE_RACES} races "
          f"(thin ones share index {UNKNOWN_VENUE})")
    return {key: i for i, key in enumerate(kept, start=1)}

# ------------------------------------------------------------------ #
# CHUNK 3 — PER-ATHLETE SEQUENCE BUILDER
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   For each athlete, walk through their races in chronological order.
#   Every race AFTER the first becomes one training example:
#     - "sequence" = feature vectors for every race BEFORE it
#     - "target_result" = the race itself (Chunk 4 turns this into
#                          the context vector)
#     - "target" = that race's normalized_time (what we're predict)
#
#   This is a "growing window" — race 2's history is just race 1,
#   race 4's history is races 1-3, etc. The first race has no history,
#   so it can't be a training example itself — but it still appears
#   INSIDE every later example's sequence


# _parseDate
# Purpose: Converts a "YYYY-MM-DD" string from the DB into a Python
#          date object so we can subtract dates to get day counts.
# Arguments:
#           date_str: date string, e.g. "2025-10-04"
# Output: a datetime.date object
def _parseDate(date_str: str) -> date:
    # strptime parses a string into a datetime according to the format
    # string. .date() drops the time-of-day part, leaving just the date.
    return datetime.strptime(date_str, "%Y-%m-%d").date()

# _daysAgo
# Purpose: Computes how many days before the target race a prior race
#          happened. This becomes the "days_ago" sequence feature —
#          it tells the model how recent each prior race is relative
#          to the race it's predicting.
# Arguments:
#           prior_date_str: date string of the prior race
#           target_date_str: date string of the target race
# Output: int number of days (>= 0) between the two dates
def _daysAgo(prior_date_str: str, target_date_str: str) -> int:
    prior_date  = _parseDate(prior_date_str)
    target_date = _parseDate(target_date_str)
 
    # Subtracting two date objects gives a timedelta object.
    # .days extracts the whole number of days as an int.
    return (target_date - prior_date).days

# _encodeGrade
# Purpose: Converts a raw grade string ("11", "FR", None, ...) into
#          the integer assigned by the Chunk 2 grade LabelEncoder.
# Arguments:
#           grade_raw: the raw "grade" value from a result dict
#           grade_encoder: the fitted LabelEncoder for "grade"
#                          (encoders["grade"] from Chunk 2)
# Output: float — the encoded grade, ready to go straight into a
#         feature vector
def _encoderMap(encoder: LabelEncoder) -> dict:
    """{class: float(code)}, cached on the encoder.

    ★ A DICT LOOKUP, NOT encoder.transform(). transform() is a numpy
      searchsorted with ~50µs of sklearn overhead PER CALL, and the sequence
      builder calls it once per (example, prior-race) pair -- hundreds of
      millions of times over the corpus. That overhead alone was hours. The
      mapping is identical (LabelEncoder codes classes in sorted order).
    """
    m = getattr(encoder, "_code_map", None)
    if m is None:
        m = {c: float(i) for i, c in enumerate(encoder.classes_)}
        encoder._code_map = m
    return m


def _encodeGrade(grade_raw, grade_encoder: LabelEncoder) -> float:

    # buildEncoders fit on "None" (the string) wherever grade was None,
    # so we have to match that exact substitution here at transform time.
    if grade_raw is None:
        grade_raw = "None"

    m = _encoderMap(grade_encoder)
    # A grade string the vocabulary pass never saw (possible only through
    # the DISTINCT-superset edge) falls to the "None" class rather than
    # crashing a run hours in.
    return m.get(grade_raw, m.get("None", 0.0))

# _orZero
# Purpose: Converts a possibly-NULL numeric value into a float,
#          substituting 0.0 for NULL — per MODEL.md's "missing weather
#          -> NULL -> 0.0" rule. Defined here (Chunk 3) since sequence
#          vectors now carry weather; Chunk 4 reuses this same helper
#          for the context vector's weather fields — only define it
#          once in your combined file.
# Arguments:
#           value: a number, or None (if RealDictCursor returned NULL)
# Output: float — value as a float, or 0.0 if value was None
def _orZero(value) -> float:
 
    # Postgres NULL comes back as Python None via RealDictCursor.
    # "is None" specifically checks for that — 0 or 0.0 from the DB
    # would NOT match this and would pass through unchanged.
    if value is None:
        return 0.0
 
    return float(value)

# _altitudeDelta
# Purpose: Computes altitude_delta = this race's altitude minus the
#          MEDIAN altitude of every race the athlete ran BEFORE it.
#          Captures sea-level vs altitude effects on performance —
#          same idea as weather: a slow time at 8,000ft is a different
#          signal than the same time at sea level.
#          Returns 0.0 (neutral) if either the race's own altitude is
#          unknown, or there are no prior races with known altitude to
#          compare against — both common until the elevation backfill
#          (MODEL.md, V2) has run.
# Arguments:
#           altitude: this race's altitude_meters (float or None)
#           races_before: list of result dicts for every race the
#                          athlete ran BEFORE this one (chronological,
#                          may be empty for the athlete's first race)
# Output: float — altitude_delta, or 0.0 if not computable
def _altitudeDelta(altitude, races_before: list[dict]) -> float:

    # Collect altitudes from prior races, skipping any that are still
    # NULL (None).
    prior_altitudes = [
        r["altitude_meters"] for r in races_before
        if r["altitude_meters"] is not None
    ]

    # Nothing to compare against, in either direction -> neutral 0.0.
    if altitude is None or len(prior_altitudes) == 0:
        return 0.0

    # This returns the difference in altitude between the current result
    # and the median result of the athlete's previous races.
    # statistics.median sorts the values and returns the middle one
    # (or the average of the two middle values for an even-length
    # list) — robust to a single outlier altitude in a small history.
    return float(altitude) - statistics.median(prior_altitudes)

# _buildSequenceVector
# Purpose: Converts ONE prior race into the 17-number feature vector
#          for the sequence — the athlete's race AND the conditions
#          they ran it in (weather + altitude). Included here (not
#          just in the target's context) so the model can learn that,
#          e.g., a slow time on a hot/humid day or at high altitude is
#          a noisier signal of true ability than the same time under
#          neutral conditions — it can "de-weight" that data point if
#          it learns to.
# Arguments:
#           prior_result: a result dict for a race the athlete ran
#                          BEFORE the target race
#           target_date_str: date string of the target race, used to
#                          compute days_ago
#           races_before_prior: list of result dicts for every race
#                          the athlete ran BEFORE prior_result — used
#                          to compute prior_result's OWN altitude_delta
#                          (may be empty if prior_result was the
#                          athlete's first-ever race)
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of SEQUENCE_FEATURES floats, in this fixed order:
#         [normalized_time, course_difficulty, days_ago,
#          distance_meters, grade_encoded, is_xc, is_indoor,
#          temp_c, dew_point_c, humidity, apparent_temp_c,
#          precipitation_mm, pressure_hpa, cloud_cover,
#          wind_speed_km, wind_dir, altitude_delta, altitude,
#          place, gps_lat, gps_long]
# _geo
# Purpose: Centre and scale a venue's latitude/longitude for the network.
# Arguments: lat, lon — degrees, either may be None.
# Output: [lat_scaled, lon_scaled]
#
# ⚠ MISSING COORDINATES BECOME 0.0, WHICH IS THE CENTRE OF THE SCALE, i.e.
#   roughly Kansas -- not a neutral "unknown". That is the same compromise
#   _orZero makes everywhere else in this file, and it is only acceptable
#   because the network also sees enough other features to tell a real
#   Kansas race from a missing one. If coordinate coverage turns out to be
#   poor, this wants an explicit is_missing flag instead.
def _geo(lat, lon) -> list[float]:
    return [
        0.0 if lat is None else (float(lat) - GEO_LAT_CENTER) / GEO_LAT_SCALE,
        0.0 if lon is None else (float(lon) - GEO_LON_CENTER) / GEO_LON_SCALE,
    ]


def _buildSequenceVector(prior_result: dict, target_date_str: str,
                        races_before_prior: list[dict],
                        encoders: dict) -> list[float]:

    return [
        float(prior_result["normalized_time"]),
        float(prior_result["course_difficulty"]),
        float(_daysAgo(prior_result["date"], target_date_str)),
        float(prior_result["distance_meters"]),
        _encodeGrade(prior_result["grade"], encoders["grade"]),
 
        # bool -> float: True becomes 1.0, False becomes 0.0.
        # _orZero on is_indoor: meets_tf.is_indoor is a NULLABLE integer
        # (the difficulty join COALESCEs it for the same reason), and
        # float(None) would crash the extraction mid-corpus.
        float(prior_result["is_xc"]),
        _orZero(prior_result["is_indoor"]),

        # Weather this prior race was run in. _orZero handles NULLs
        # for meets where the weather backfill hasn't reached yet.
        _orZero(prior_result["temp_c"]),
        _orZero(prior_result["dew_point_c"]),
        _orZero(prior_result["humidity"]),
        _orZero(prior_result["apparent_temp_c"]),
        _orZero(prior_result["precipitation_mm"]),
        _orZero(prior_result["pressure_hpa"]),
        _orZero(prior_result["cloud_cover"]),
        _orZero(prior_result["wind_speed_km"]),
        _orZero(prior_result["wind_dir"]),
 
        # Altitude relative to THIS race's own prior history — see
        # _altitudeDelta docstring. _orZero handles NULL altitude
        # via the None-check inside _altitudeDelta itself.
        _altitudeDelta(prior_result["altitude_meters"], races_before_prior),
        # Absolute altitude of this race's venue (NEW — index 17).
        # Pairs with altitude_delta above: delta = "how far from your
        # normal", this = "where you are now". Together they let the
        # model learn the non-linear altitude effect neither expresses
        # alone. _orZero -> 0.0 until the elevation backfill populates it.
        _orZero(prior_result["altitude_meters"]),

        # Finishing position in that race (index 18). Field context a time
        # alone cannot carry -- see the SEQUENCE_FEATURES note on why this
        # belongs here and NOT in the target's context vector.
        _orZero(prior_result["place"]),

        # Where that race was (indices 19-20), centred and scaled.
        *_geo(prior_result["gps_lat"], prior_result["gps_long"]),
    ]

# buildAthleteExamples
# Purpose: Builds all training examples for ONE athlete using the
#          growing-window approach: for each race after the first,
#          everything before it becomes "sequence".
# Arguments:
#           athlete_results: this athlete's results, already in
#                          chronological order (one entry from
#                          by_athlete[athlete_id])
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of example dicts, each:
#         {
#           "sequence":      [[17 floats], [17 floats], ...],
#           "prior_results": result dicts for every race before the
#                             target (raw, for Chunk 4's altitude_delta),
#           "target_result": result dict (full, for Chunk 4),
#           "target":        float (normalized_time to predict),
#         }
def _forecastTwin(prior_results, target_result, full_sequence, rng):
    """A copy of one example with the last k prior races hidden, or None.

    ⚠ TRUNCATES THE END, NOT THE START. Dropping the OLDEST races would model
      "we only know their recent form", which is a different and much less
      useful problem -- and it is not what inference looks like. At inference
      the missing races are the ones between now and the target, i.e. the most
      RECENT ones relative to it.

    ⚠ THE SEQUENCE IS REBUILT, NOT SLICED. Every sequence vector carries
      days_ago relative to the target and features derived from the races
      before it, so a vector built in the full history is wrong in the
      truncated one. Slicing the list would keep the old days_ago and quietly
      teach the model a contradiction.
    """
    if len(prior_results) < MIN_PRIOR_FOR_TWIN:
        return None

    target_date = _asDate(target_result.get("date"))
    if target_date is None:
        return None

    # ★ SAMPLE INSIDE THE FEASIBLE WINDOW, NOT BLINDLY.
    #
    #   A gap drawn from the full 2-40 week range mostly lands outside what
    #   this athlete's history can express: too long and nothing survives, too
    #   short and nothing is hidden. Measured on a 10-race weekly season,
    #   blind sampling wasted 71% of attempts -- which made the real twin rate
    #   unpredictable AND season-length dependent, since a sparse racer's
    #   feasible window is a different shape from a weekly racer's.
    #
    #   The window is bounded by the athlete's own races:
    #       shortest  the gap to their LAST prior race   (hides >= 1)
    #       longest   the gap to race MIN_KEPT_RACES     (keeps >= 2)
    #
    #   Sampling in there produces a twin whenever one is possible, and the
    #   skew still favours the short end of whatever range that athlete has.
    dates = [_asDate(p.get("date")) or target_date for p in prior_results]
    lo_weeks = (target_date - dates[-1]).days / 7.0
    hi_weeks = (target_date - dates[MIN_KEPT_RACES - 1]).days / 7.0

    # Clip to the range we are willing to train on at all.
    lo_weeks = max(lo_weeks, FORECAST_GAP_MIN_WEEKS)
    hi_weeks = min(hi_weeks, FORECAST_GAP_MAX_WEEKS)
    if hi_weeks <= lo_weeks:
        return None

    weeks = lo_weeks + (rng.random() ** FORECAST_GAP_SKEW) * (hi_weeks - lo_weeks)
    cutoff = target_date - timedelta(days=weeks * 7.0)

    # Results arrive chronological, so the date cut IS a prefix -- taken as an
    # explicit count so the full_sequence slice below can never disagree with
    # it, whatever an individual date value does.
    k = sum(1 for d in dates if d < cutoff)
    kept = prior_results[:k]

    # The window makes both of these rare, but a duplicate date or a clip can
    # still land on an edge -- and a twin identical to its full example would
    # teach the model that is_forecast means nothing.
    if len(kept) < MIN_KEPT_RACES or len(kept) == len(prior_results):
        return None

    # ★ A PREFIX SLICE OF THE FULL EXAMPLE'S SEQUENCE, AND THAT IS CORRECT.
    #   The twin shares the full example's TARGET, so every kept race's
    #   days_ago (relative to that target) and its races-before-it prefix are
    #   byte-identical to the full sequence's first len(kept) rows. The old
    #   "rebuilt, not sliced" warning guarded against slicing a DIFFERENT
    #   target's sequence; same target, the slice is the rebuild.
    sequence = [v.copy() for v in full_sequence[:len(kept)]]
    return {
        "sequence": sequence,
        "prior_results": kept,
        "target_result": target_result,
        "target": float(target_result["normalized_time"]),
        "venue_row": target_result,
        "is_forecast": True,
        # Kept for diagnostics only -- NEVER a feature. At inference the number
        # of hidden races is exactly what nobody knows; a model trained on it
        # would depend on a value that cannot be supplied.
        "n_hidden": len(prior_results) - len(kept),
        "gap_weeks": round(weeks, 1),
    }


def _baseVectors(athlete_results: list[dict], encoders: dict) -> list:
    """One sequence vector per race, with days_ago (index 2) left at 0.

    ★ BUILT ONCE PER ATHLETE, NOT ONCE PER (EXAMPLE, PRIOR) PAIR. Of the 21
      features, only days_ago depends on which target the vector serves --
      everything else is a property of the race itself. The old code rebuilt
      the whole vector (including an O(L) altitude median) for every prior of
      every example: O(L³) per athlete, sklearn calls included. This is the
      O(L²)-once version; buildAthleteExamples then copies a base row and
      writes its days_ago, which is two cheap ops.
    """
    return [
        _buildSequenceVector(r, r["date"], athlete_results[:j], encoders)
        for j, r in enumerate(athlete_results)
    ]


def buildAthleteExamples(athlete_results: list[dict], encoders: dict,
                         rng=None) -> list[dict]:

    examples = []
    base  = _baseVectors(athlete_results, encoders)
    dates = [_parseDate(r["date"]) for r in athlete_results]

    # Builds training examples for an athlete using a growing window
    # approach for every race.
    # Skips athlete's first-ever race because it can't be a training
    # example (as it has no prior races).
    for i in range(1, len(athlete_results)):

        # Gets the result we're predicting
        target_result = athlete_results[i]

        # Gets all the prior results to build the training example.
        # Slicing: everything from index 0 up to (but not including) i.
        prior_results = athlete_results[:i]

        # Each prior's vector = its base row with days_ago filled in for
        # THIS target. Copy first -- the base rows are shared across every
        # example this athlete produces.
        target_date = dates[i]
        sequence = []
        for j in range(i):
            v = base[j].copy()
            v[2] = float((target_date - dates[j]).days)
            sequence.append(v)

        # Adds the current training examples to our list of training
        # examples. It contains the sequence, the context of the result
        # we're predicting, and the normalize time of the result we're predicting.
        # Creates a dict
        examples.append({
            "sequence": sequence,
            "prior_results": prior_results,
            "target_result": target_result,
            "target": float(target_result["normalized_time"]),
            # The TARGET race's venue. Sequence races keep only their
            # difficulty -- an embedding per history race would need the
            # index concatenated before the input projection, which is a
            # bigger change than this one.
            # Resolved to an index in saveAll, once the vocabulary is
            # built -- the raw row is kept here because the vocab does not
            # exist yet when this runs.
            "venue_row": target_result,
            # The history runs right up to the target: this is "what happens
            # this weekend", the only shape the model used to see.
            "is_forecast": False,
            "n_hidden": 0,
        })

        # ★ AND ITS TWIN. Same target, history cut short, flagged. Emitted
        #   ALONGSIDE rather than instead of -- both shapes occur at inference.
        if rng is not None and rng.random() < FORECAST_TWIN_RATE:
            twin = _forecastTwin(prior_results, target_result, sequence, rng)
            if twin is not None:
                examples.append(twin)

    return examples

# buildAllExamples
# Purpose: Runs buildAthleteExamples for every athlete and flattens
#          the per-athlete lists into one big list — this flat list
#          is the dataset Chunk 5 will pad and save to tensors.
# Arguments:
#           by_athlete: {athlete_id: [results...]} from groupByAthlete
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: flat list of example dicts (see buildAthleteExamples)
def buildAllExamples(by_athlete: dict, encoders: dict, rng=None):

    # Its OWN generator when none is passed, seeded the same way, so this path
    # is reproducible on its own. saveAll passes its generator instead, since
    # sharing one keeps a whole run deterministic end to end.
    if rng is None:
        rng = random.Random(SHUFFLE_SEED)

    all_examples = []

    # For each athlete it builds their training examples.
    for athlete_id, athlete_results in by_athlete.items():
        # .extend() appends every item from this athlete's list
        # onto all_examples, rather than appending the whole list
        # as one nested element.
        all_examples.extend(
            buildAthleteExamples(athlete_results, encoders, rng))

    print(f"Built {len(all_examples):,} training examples")
    return all_examples

# ------------------------------------------------------------------ #
# CHUNK 4 — CONTEXT FEATURE BUILDER
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   Chunk 3 built the "sequence" (history) for each example. Chunk 4
#   builds the "context" — a single vector describing the TARGET race
#   itself (the one we're predicting normalized_time for).
#
#   Per MODEL.md, the context vector is, in order:
#     [course_difficulty, distance_meters, day_of_year,
#      days_since_last_race, pool_encoded, gender_encoded,
#      school_encoded,
#      temp_c, dew_point_c, humidity, apparent_temp_c,
#      precipitation_mm, pressure_hpa, cloud_cover,
#      wind_speed_km, wind_dir]
#      altitude_delta]
#
#   That's 7 "race info" features + 9 weather features
#   + 1 altitude feature = 17 floats.

# ------------------------------------------------------------------ #
# Gender encoding
# ------------------------------------------------------------------ #
#
# Unlike grade/pool/school, gender is a clean, fixed "M"/"F" straight
# from the athletes table — no messy variants to clean up. A small
# hardcoded mapping is simpler than a full LabelEncoder and doesn't
# need to be saved/loaded in encoders.pkl.

GENDER_MAP = {"M": 0.0, "F": 1.0}

# _encodeGender
# Purpose: Converts a gender string to a float using GENDER_MAP.
# Arguments:
#           gender: "M", "F", or possibly something unexpected/None.
# Output: float — 0.0 for "M", 1.0 for "F", -1.0 for anything else
#         (so unexpected values are visible/debuggable downstream
#         rather than silently colliding with "M").
def _encodeGender(gender: str) -> float:
 
    # dict.get(key, default) returns the value for key if present,
    # otherwise returns default — avoids a KeyError for unexpected
    # gender values while still flagging them (-1.0 stands out).
    return GENDER_MAP.get(gender, -1.0)

# ------------------------------------------------------------------ #
# CHUNK 5 — PADDING, TENSORS, AND SAVING
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   PyTorch needs every training example to be the same shape so they
#   can be stacked into a batch. Our sequences have variable length —
#   an athlete with 2 races has a sequence of length 1, an athlete
#   with 20 races has a sequence of length 19.
#
#   The solution is PADDING: find the longest sequence in the dataset,
#   then pad every shorter sequence with rows of 0.0 up to that length.
#
#   But we need to tell the Transformer which positions are real and
#   which are padding — otherwise it'll "attend" to the zero rows and
#   corrupt the computation. That's what the ATTENTION MASK is for:
#     True  = real race (attend to this)
#     False = padding  (ignore this)
#
#   Final output — 5 files saved to model/data/:
#     sequences.pt  — shape [total_steps, 21]     (float32, RAGGED: every
#                     sequence end to end, sliced by offsets.pt)
#     offsets.pt    — shape [N + 1]                (int64)
#     masks.pt      — shape [N, max_seq_len]       (bool)
#     context.pt    — shape [N, 21]                (float32)
#     targets.pt    — shape [N]                    (float32)
#     encoders.pkl  — fitted LabelEncoders (for inference)
#
#   Where N = total number of training examples across all athletes.


# _maxSequenceLength
# Purpose: Finds the longest sequence across all examples.
#          This becomes the padded width every example is stretched to.
# Arguments:
#           examples: flat list of example dicts from buildAllExamples.
# Output: int — length of the longest sequence.
def _maxSequenceLength(examples: list[dict]) -> int:

    # len(ex["sequence"]) = number of prior races for that example.
    # max() returns the largest of those counts.
    # Goes through all training examples finding the length
    # of the longest sequence (the most races).
    return max(len(ex["sequence"]) for ex in examples)

# _padSequence
# Purpose: Pads ONE sequence to max_len by appending rows of zeros,
#          and builds the corresponding attention mask.
# Arguments:
#           sequence: list of 17-float vectors (the real race data).
#           max_len:  target length to pad up to.
# Output: tuple of:
#           padded   — list of max_len vectors (real + zero rows)
#           mask     — list of max_len bools (True=real, False=padding)
def _padSequence(sequence: list[list[float]], max_len: int):

    # ★ TRUNCATE FIRST, KEEPING THE MOST RECENT RACES. Without this a
    #   sequence longer than max_len produced a NEGATIVE pad_len, and
    #   [zero_row] * -3 is [] in Python -- so the row came back at its
    #   original length while every other row was max_len, and
    #   torch.tensor() then raised on the ragged nested list. A crash deep
    #   in _buildTensors, after the whole extraction had run.
    #
    #   The last max_len races are the right ones to keep: recency is the
    #   strongest signal about current ability, and the model reads
    #   `days_ago` from each row anyway.
    if len(sequence) > max_len:
        sequence = sequence[-max_len:]

    # Length of this specific raw sequence.
    real_len = len(sequence)

    # How many zero rows to add based on this sequence's length
    # and the max sequence length.
    pad_len = max_len - real_len

    # A single zero row — 17 zeros matching the sequence feature width.
    # We build one and reuse it rather than recomputing inside the loop.
    zero_row = [0.0] * SEQUENCE_FEATURES

     # Concatenate real rows + pad rows.
    # [zero_row] * pad_len creates a list of pad_len zero rows.
    padded = sequence + [zero_row] * pad_len

    # True for every real race, False for every padding row.
    mask = [True] * real_len + [False] * pad_len

    return padded, mask

# _buildTensors
# Purpose: Converts all examples into four PyTorch tensors ready for
#          training. Pads sequences to uniform length and builds masks.
# Arguments:
#           examples: flat list of example dicts (with "sequence",
#                     "context", "target" keys populated by Chunks 3-4).
#           max_len:  padded sequence length from _maxSequenceLength.
# Output: tuple of (sequences_tensor, masks_tensor,
#                   context_tensor, targets_tensor)
def _buildTensors(examples: list[dict], max_len: int):

    # Pre-allocate four Python lists — one entry per example.
    # We'll convert these to tensors in one shot at the end,
    # which is faster than calling torch.tensor() in a loop.
    all_sequences = []
    all_contexts  = []
    all_targets   = []
    all_venues    = []

    # For each example pads it's training sequence and builds
    # it's mask, then appends it to the python lists.
    # ★ RAGGED, NOT PADDED, AND THIS IS WHAT MAKES THE CORPUS FIT ON A DISK.
    #   Padding every example to max_len writes a [N, 64, 21] float32 block:
    #   at ~80M examples (one per race after an athlete's first, plus 50%
    #   forecast twins) that is 347 GB of chunk files. The median athlete has
    #   THREE races, so for half the corpus a 64-wide row is ~95% zeros.
    #
    #   Storing the sequences end to end with an offset per example writes
    #   only the rows that exist -- about 23 GB for the same data -- and the
    #   padding is rebuilt per BATCH in train.collateRagged, to that batch's
    #   own longest sequence rather than the global cap.
    #
    # ⚠ THE DISK SAVING IS THE WHOLE SAVING UNTIL BATCHES ARE LENGTH-SORTED.
    #   Per-batch padding sounds like it should shrink attention too, and with
    #   a SHUFFLED batch of 64 it does almost nothing: the batch max is the
    #   ~98th percentile of example lengths, so 95% of batches pad to the cap
    #   anyway. Measured mean padded width, cap 64:
    #
    #       shuffled batch of  64 -> 64.0        bucketed -> 18.2
    #       shuffled batch of 512 -> 64.0        bucketed -> 18.2
    #
    #   One long athlete sets L for everyone sharing their batch. Sorting by
    #   length within a chunk is what turns this into the 3.4x it looks like
    #   (measured: 591ms/step at L=64 against 174ms at L=18).
    #
    # ! THE MASK IS NOT STORED AT ALL. It is a function of the length, so
    #   saving it costs a byte per padded step to record something the
    #   offsets already say. collateRagged builds it, in the same polarity
    #   the encoder expects (True = real; transformer.forward inverts it).
    offsets = [0]
    for ex in examples:
        # Still truncated here, and still to the LAST max_len races -- see
        # _padSequence for why recency is the half worth keeping.
        seq = ex["sequence"]
        if len(seq) > max_len:
            seq = seq[-max_len:]

        all_sequences.extend(seq)
        offsets.append(len(all_sequences))
        all_contexts.append(ex["context"])
        all_targets.append(ex["target"])
        all_venues.append(ex["venue_idx"])

    # torch.tensor() converts a nested Python list into a tensor.
    # dtype=torch.float32 — standard precision for neural net weights.
    # dtype=torch.bool    — True/False mask, no gradient needed.
    sequences_tensor = torch.tensor(all_sequences, dtype=torch.float32)
    # ⚠ int64. An offset indexes a tensor with tens of millions of rows per
    #   chunk-set; int32 would overflow silently on a large corpus.
    offsets_tensor   = torch.tensor(offsets,       dtype=torch.long)
    context_tensor   = torch.tensor(all_contexts,  dtype=torch.float32)
    targets_tensor   = torch.tensor(all_targets,   dtype=torch.float32)
    # ★ int64, NOT float32. This is an index into an embedding table, not a
    #   measurement -- nn.Embedding requires a long, and a float here would be
    #   a runtime error rather than a silently wrong number.
    venues_tensor    = torch.tensor(all_venues,    dtype=torch.long)

    # Print shapes so we can sanity-check before saving.
    # e.g. sequences: [10_100_000, 21]  (ragged; 2.4M examples)
    #      context:   [2_400_000, 21]
    #      targets:   [2_400_000]
    # This prints as [depth, rows, columns], i.e. # of examples,
    # steps, features.
    print(f"  sequences : {list(sequences_tensor.shape)} (ragged, "
          f"{len(offsets) - 1:,} examples)")
    print(f"  offsets   : {list(offsets_tensor.shape)}")
    print(f"  context   : {list(context_tensor.shape)}")
    print(f"  targets   : {list(targets_tensor.shape)}")

    return (sequences_tensor, offsets_tensor, context_tensor,
            targets_tensor, venues_tensor)

# _saveTensors
# Purpose: Saves the four tensors to disk in model/data/.
#          torch.save uses Python's pickle format — torch.load()
#          at training time will restore the exact tensor.
# Arguments:
#           sequences_tensor, masks_tensor, context_tensor,
#           targets_tensor: the four tensors from _buildTensors.
#           output_dir: directory to save into (OUTPUT_DIR constant).
# Output: None. Prints each file path on save.
def _saveTensors(sequences_tensor, masks_tensor,
                 context_tensor, targets_tensor,
                 output_dir: str) -> None:
    
    # os.path.join glues the directory and filename together correctly
    # on any OS (Windows uses \, Linux uses /).
    files = {
        "sequences.pt": sequences_tensor,
        "masks.pt":     masks_tensor,
        "context.pt":   context_tensor,
        "targets.pt":   targets_tensor,
    }

    # Gets the path to each file, and then saves the tensors
    # to there.
    for filename, tensor in files.items():
        path = os.path.join(output_dir, filename)
        torch.save(tensor, path)
        print(f"  Saved {path}")

# _saveLengths
# Purpose: every example's sequence length, in the same global order the
#          chunks are indexed in.
#
# ★ WHAT LENGTH-SORTED BATCHING READS. ChunkAwareBatchSampler groups a batch
#   out of similar-length examples so the batch pads to 18 rather than to the
#   cap, and it needs the lengths BEFORE it can do that. The only other way
#   to get them is to torch.load() every chunk file at startup to read its
#   offsets -- tens of GB, to recover a number this function already has.
#
# ! int32, not int16. 331 fits in either, but the cap is a constant somebody
#   will raise, and a silent wrap at 32,767 would corrupt the sort rather
#   than fail.
def _saveLengths(lengths: list, output_dir: str) -> None:
    path = os.path.join(output_dir, "lengths.pt")
    torch.save(torch.tensor(lengths, dtype=torch.int32), path)
    print(f"  Saved {path} ({len(lengths):,} lengths)")


# _saveEncoders
# Purpose: Saves the fitted LabelEncoders to disk using pickle.
#          At inference time, the Flask app loads these to transform
#          the same categorical features the same way as training.
# Arguments:
#           encoders: dict of fitted LabelEncoders from buildEncoders.
#           output_dir: directory to save into.
# Output: None.
def _saveEncoders(encoders: dict, output_dir: str) -> None:

    path = os.path.join(output_dir, "encoders.pkl")

    # "wb" = write binary — pickle needs binary mode.
    # Writes the binary encoders for each string we
    # input in the model to this file
    with open(path, "wb") as f:
        pickle.dump(encoders, f)

    print(f"  Saved {path}")

# saveAll
# Purpose: Two-pass chunked save. Pass 1 measures the real sequence-length
#          distribution and applies the MAX_SEQ_LEN cap, printing both so the
#          cap can be checked rather than trusted. Pass 2 builds examples
#          in batches of CHUNK_SIZE, pads each batch to max_len, saves
#          each chunk to model/data/chunk_NNNN.pt immediately, then
#          discards it — keeps memory flat regardless of dataset size.
# Arguments:
#           by_athlete: {athlete_id: [results...]} from groupByAthlete.
#           encoders: dict of fitted LabelEncoders from Chunk 2.
#           output_dir: where to save (OUTPUT_DIR constant).
# Output: None. Saves chunk files + metadata.pkl to output_dir.
# ⚠ THE PARAMETER IS by_athlete, NOT examples. The body has always
#   iterated by_athlete.values(); the signature said `examples`, which is
#   never defined in this scope -- a NameError on the first call. The
#   docstring above already names it correctly.
def saveAll(athletes, encoders: dict, vocab: dict,
            output_dir: str) -> None:
    """athletes: an iterable of (identity, date-sorted result rows) -- in
    practice streamAthletes(conn), so the corpus never sits in RAM.

    ⚠ NO LENGTH PRE-PASS ANY MORE. The old pass 1 read every career length to
      pick a padded width; ragged chunks made the global width meaningless
      (padding is per batch), so max_len is just the truncation cap and the
      distribution is reported at the END from the lengths collected on the
      way through.
    """
    os.makedirs(output_dir, exist_ok=True)
    max_len = MAX_SEQ_LEN

    chunk_idx      = 0
    total_examples = 0
    all_lengths    = []   # per EXAMPLE, global order -- the sampler's food
    all_val        = []   # per EXAMPLE, global order -- train.py's split
    career_lengths = []   # per ATHLETE -- the distribution report
    n_val_athletes = 0
    buffer         = []  # holds up to SHUFFLE_FACTOR*CHUNK_SIZE before flushing

    # Local RNG, not random.seed(), so this does not reach out and change
    # the global random state for anything else in the process.
    rng = random.Random(SHUFFLE_SEED)
    hold = SHUFFLE_FACTOR * CHUNK_SIZE

    def flush_one():
        nonlocal chunk_idx, total_examples, buffer
        take = buffer[:CHUNK_SIZE]
        lens, vals = _saveChunk(take, max_len, chunk_idx, output_dir)
        all_lengths.extend(lens)
        all_val.extend(vals)
        chunk_idx      += 1
        total_examples += len(take)
        # Discard the flushed examples — this is what keeps RAM flat.
        buffer = buffer[CHUNK_SIZE:]

    # For each athlete builds training examples and context, stamps the
    # athlete-level validation flag, and saves in shuffled chunks.
    for identity, athlete_results in athletes:
        career_lengths.append(len(athlete_results))

        examples = buildAthleteExamples(athlete_results, encoders, rng)
        addContextToExamples(examples, encoders)

        # ★ THE WHOLE ATHLETE IS TRAIN OR VAL, NEVER BOTH. See _isValAthlete:
        #   splitting by example lets the model meet every val athlete during
        #   training and validation stops measuring generalisation.
        is_val = _isValAthlete(identity)
        n_val_athletes += bool(is_val) and bool(examples)
        for ex in examples:
            ex["venue_idx"] = venueIndex(ex.pop("venue_row"), vocab)
            ex["is_val"]    = is_val

        buffer.extend(examples)

        # Flush only once the buffer is SHUFFLE_FACTOR chunks deep, and
        # shuffle before taking a chunk off it -- so the emitted chunk is a
        # sample from a wide window of athletes rather than a contiguous run.
        while len(buffer) >= hold:
            rng.shuffle(buffer)
            flush_one()

    # Drain the tail. Shuffle once more, then emit full chunks and a final
    # short one -- the last chunk being short is fine, the sampler sizes
    # batches from the rows it actually holds.
    if buffer:
        rng.shuffle(buffer)
        while buffer:
            flush_one()

    # The distribution report the old pass 1 produced, now from the walk.
    career_lengths.sort()
    n = len(career_lengths)
    def pct(f):
        return career_lengths[min(n - 1, int(f * n))] if n else 0
    over = sum(1 for L in career_lengths if L > MAX_SEQ_LEN)
    print(f"  sequence lengths over {n:,} athletes: "
          f"median {pct(.50)}  p90 {pct(.90)}  p99 {pct(.99)}  "
          f"max {career_lengths[-1] if n else 0}")
    print(f"  cap {MAX_SEQ_LEN}: truncated {over:,} athletes "
          f"({100.0 * over / max(n, 1):.2f}%)")

    # ★ EVERY EXAMPLE'S SEQUENCE LENGTH, IN GLOBAL ORDER. Written here because
    #   this is the only place that knows them for free; the alternative is
    #   the sampler torch.load()ing all ~8,000 chunk files at startup just to
    #   read their offsets. int32 at ~80M examples is 320 MB, which loads in
    #   seconds and is what makes length-sorted batching possible.
    _saveLengths(all_lengths, output_dir)

    # ★ THE ATHLETE-DISJOINT SPLIT, IN THE SAME GLOBAL ORDER. train.py reads
    #   this instead of random_split when it exists.
    vm_path = os.path.join(output_dir, "val_mask.pt")
    torch.save(torch.tensor(all_val, dtype=torch.bool), vm_path)
    print(f"  Saved {vm_path} ({sum(all_val):,} val examples from "
          f"{n_val_athletes:,} athletes)")

    # Save metadata so DataLoader knows how many chunks exist.
    _saveMetadata(max_len, total_examples, chunk_idx, output_dir)
    _saveEncoders(encoders, output_dir)
    _saveVenueVocab(vocab, output_dir)

    print(f"Done. {total_examples:,} examples saved in {chunk_idx} chunks.")

# _saveChunk
# Purpose: Converts one buffer of examples to ragged tensors and saves them
#          to chunk_NNNN.pt. Padding happens per batch at training time, not
#          here -- see _buildTensors.
# Arguments:
#           examples:   list of up to CHUNK_SIZE example dicts.
#           max_len:    padded sequence length.
#           chunk_idx:  chunk number, used for filename.
#           output_dir: directory to save into.
# Output: None.
def _saveChunk(examples: list[dict], max_len: int,
               chunk_idx: int, output_dir: str):

    (sequences_t, offsets_t, context_t,
     targets_t, venues_t) = _buildTensors(examples, max_len)
    lengths = (offsets_t[1:] - offsets_t[:-1]).tolist()
    vals    = [bool(ex.get("is_val")) for ex in examples]

    path = os.path.join(output_dir, f"chunk_{chunk_idx:04d}.pt")

    # Saves tensors for this chunk to file specified in path.
    torch.save({
        "sequences": sequences_t,
        "offsets":   offsets_t,
        "context":   context_t,
        "targets":   targets_t,
        # ★ SEPARATE TENSOR, int64. An embedding index is a lookup key, not a
        #   measurement -- inside the float context the first Linear would
        #   read venue 4,000 as four thousand times venue 1.
        "venues":    venues_t,
    }, path)

    print(f"  Saved {path} ({len(examples):,} examples)")

    # ! RETURNED, so saveAll can accumulate them without a second pass. The
    #   sampler needs every length in global order, train.py's split needs
    #   every val flag in the same order; this is the only place with both.
    return lengths, vals

def _saveVenueVocab(vocab: dict, output_dir: str) -> None:
    """The venue vocabulary, beside the encoders.

    ⚠ THE MODEL'S EMBEDDING TABLE IS SIZED FROM THIS, so it must be saved with
      the chunks. Re-deriving it at training time from a different corpus
      slice would give a different size and silently reindex every venue --
      the embedding for Woodward Park would become some other course's.
    """
    path = os.path.join(output_dir, "venue_vocab.pkl")
    with open(path, "wb") as f:
        pickle.dump({"vocab": vocab,
                     "n_venues": len(vocab) + 1,      # +1 for UNKNOWN_VENUE
                     "min_races": MIN_VENUE_RACES}, f)
    print(f"  Saved {path} ({len(vocab):,} venues + 1 unknown bucket)")


# _saveMetadata
# Purpose: Saves metadata of all chunks so train.py's DataLoader knows
#          max_len, total examples, and how many chunk files exist.
# Arguments:
#           max_len:        padded sequence length all chunks share, derived from
#                           max results an athlete has.
#           total_examples: total number of training examples across all chunks.
#           num_chunks:     how many chunk_NNNN.pt files were written.
#           output_dir:     directory to save metadata.pkl into.
# Output: None.
def _saveMetadata(max_len: int, total_examples: int,
                  num_chunks: int, output_dir: str) -> None:

    path = os.path.join(output_dir, "metadata.pkl")
    with open(path, "wb") as f:
        pickle.dump({
            "max_len":        max_len,
            "total_examples": total_examples,
            "num_chunks":     num_chunks,
            "chunk_size":     CHUNK_SIZE,
        }, f)
    print(f"  Saved {path}")

# ------------------------------------------------------------------ #
# Date helpers
# ------------------------------------------------------------------ #

# _dayOfYear
# Purpose: Converts a "YYYY-MM-DD" date string into its day-of-year
#          number (1-366). This captures SEASONALITY — e.g. an XC
#          race in early September vs late November is a meaningfully
#          different point in the season, even across different years.
# Arguments:
#           date_str: date string, e.g. "2025-10-04"
# Output: int, 1-366
def _dayOfYear(date_str: str) -> int:
 
    # _parseDate is defined in Chunk 3 — reused here rather than
    # re-implemented, since it's the same "YYYY-MM-DD" -> date parse.
    parsed = _parseDate(date_str)
 
    # .timetuple() converts a date into a time.struct_time, which has
    # a .tm_yday field — the day-of-year count (Jan 1 = 1).
    return parsed.timetuple().tm_yday

# ------------------------------------------------------------------ #
# Context vector builder
# ------------------------------------------------------------------ #

# _buildContextVector
# Purpose: Builds the CONTEXT_FEATURES-number context vector for ONE example,
#          describing the target race (course, timing, cohort,
#          weather, altitude). This is the second half of each
#          training example, alongside the "sequence" from Chunk 3.
# Arguments:
#           target_result: the result dict for the race being
#                          predicted (example["target_result"])
#           sequence: this example's sequence from Chunk 3 — used
#                          to read off days_since_last_race without
#                          recomputing it
#           prior_results: result dicts for every race before the
#                          target (example["prior_results"]) — used
#                          to compute the target's altitude_delta
#                          relative to the athlete's full history
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of CONTEXT_FEATURES floats, in the fixed order documented above
def _buildContextVector(target_result: dict, sequence: list[list[float]],
                        prior_results: list[dict], encoders: dict,
                        is_forecast: bool = False) -> list[float]:
    
    # ★ THE ATHLETE'S LEVEL FOR THE TARGET RACE, AS AN ORDINAL. No encoder:
    #   buildPool already returns elem=0 < ms=1 < hs=2 < college=3 < pro=4,
    #   which is a real ordering. The previous version ran a LabelEncoder over
    #   "10-M"/"FR-F" strings and handed the resulting arbitrary integer to a
    #   Linear layer as a float -- claiming an ordering that did not exist, and
    #   encoding gender a second time on top of its own feature.
    pool_encoded = buildPool(target_result)

    # School encoder, same "None" substitution pattern as _encodeGrade
    # in Chunk 3 — buildEncoders fit on "None" wherever school was None.
    # Dict map, not transform(): see _encoderMap — this runs once per example,
    # ~80M times over the corpus.
    school_raw = target_result["school"] if target_result["school"] is not None else "None"
    sm = _encoderMap(encoders["school"])
    school_encoded = sm.get(school_raw, sm.get("None", 0.0))

    # sequence[-1] is the most recent prior race (sequence is in
    # chronological order, same as athlete_results). Index [2] of
    # each 17-element vector is days_ago (see Chunk 3's
    # _buildSequenceVector ordering) — for the LAST prior race,
    # "days_ago relative to this target" IS "days since last race".
    days_since_last_race = sequence[-1][2]

    # altitude_delta for the TARGET race, relative to the athlete's
    # FULL prior history.
    altitude_delta = _altitudeDelta(target_result["altitude_meters"], prior_results)

    return [
        # ★ is_forecast. NOT redundant with days_since_last_race: a real
        #   six-week gap and a truncated six-week gap produce the same number
        #   of days and mean opposite things -- one athlete did nothing, the
        #   other raced four times we are hiding. This is what separates them,
        #   and without it truncation would make the model worse.
        1.0 if is_forecast else 0.0,
        float(target_result["course_difficulty"]),
        float(target_result["distance_meters"]),
        float(_dayOfYear(target_result["date"])),
        days_since_last_race,
        pool_encoded,
        _encodeGender(target_result["gender"]),
        school_encoded,
 
        # Weather — each passed through _orZero for NULL -> 0.0.
        _orZero(target_result["temp_c"]),
        _orZero(target_result["dew_point_c"]),
        _orZero(target_result["humidity"]),
        _orZero(target_result["apparent_temp_c"]),
        _orZero(target_result["precipitation_mm"]),
        _orZero(target_result["pressure_hpa"]),
        _orZero(target_result["cloud_cover"]),
        _orZero(target_result["wind_speed_km"]),
        _orZero(target_result["wind_dir"]),
 
        altitude_delta,
        # Absolute altitude of the target race's venue (NEW — index 17).
        # Same pairing rationale as the sequence vector.
        _orZero(target_result["altitude_meters"]),

        # Where the target race is (indices 18-19). Known BEFORE the race,
        # so unlike `place` this is safe in the context vector.
        *_geo(target_result["gps_lat"], target_result["gps_long"]),
    ]

def addContextToExamples(examples: list[dict], encoders: dict) -> None:

    for example in examples:
        example["context"] = _buildContextVector(
            example["target_result"],
            example["sequence"],
            example["prior_results"],
            encoders,
            example.get("is_forecast", False),
        )

    # No print here: this now runs once per ATHLETE in the streaming flow,
    # and 4.7M progress lines is not progress. _saveChunk is the heartbeat.


# ------------------------------------------------------------------ #
# MAIN — the streaming flow
# ------------------------------------------------------------------ #

if __name__ == "__main__":

    # Creates a directory and all its parent directories if they don't exist.
    # exist_ok says don't crash if it already exists. It creates model/
    # and model/data/ if they don't exist (OUTPUT_DIR in constants).
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    initPool()

    try:
        with getConn() as conn:
            # Vocabulary first, from aggregate queries -- kilobytes back,
            # instead of a second pass over 54M streamed rows.
            print("Building encoders (DB aggregates)...")
            encoders = buildEncodersFromDB(conn)

            print("Building venue vocabulary (DB aggregates)...")
            vocab = buildVenueVocabFromDB(conn)

            # Then ONE streamed pass: two identity-sorted server-side
            # cursors, heap-merged, one athlete in RAM at a time.
            print("Streaming athletes and saving chunked tensors...")
            saveAll(streamAthletes(conn), encoders, vocab, OUTPUT_DIR)

    finally:
        closePool()