# Project: xc-predictor
# File:    engine/fit_era_correction.py
# Purpose: STEP 3 of the normalization chain — ERA adjustment, BANDED + FUSED.
#          After distance + geometry put every result on one physical scale,
#          a year-over-year drift remains (shoes, surfaces, training). This file
#          measures that drift INDEPENDENTLY per (sport, event band, gender)
#          partition, fusing two signals per partition, and (with --fit) pickles
#          one banded artifact.
#
#          WHY BANDED (the design change from v1):
#            The era effect is NOT one number per year. Super-shoes hit distance
#            events ~2017-19 and sprints barely and later (~2021 spikes). One
#            curve fit on everything would average a large distance effect with
#            a near-zero sprint effect — under-correcting 5Ks, OVER-correcting
#            100s, and smearing the jump across the wrong years. Race distance
#            is DISCRETE (standard events, nothing between), so bands aligned to
#            event families — not a spline over distance — are the honest model.
#
#          THE PLACEBO (what banding buys beyond accuracy):
#            Sprints take ~no foam benefit before ~2021. So the sprint band is
#            era's built-in control, the role straightaway races played for
#            geometry: a fat 2017-19 jump showing up in the SPRINT curve cannot
#            be shoes — it is the depth/aging leak caught red-handed. The
#            placebo report at the end prints exactly this comparison.
#
#          WHY TWO SIGNALS, FUSED (unchanged from v1):
#            SAME-ATHLETE consecutive-season deltas: clean year-pair SHAPE —
#              but in a HS/college population the delta is DOMINATED by
#              development (athletes improve ~2-5%/yr maturing; era is
#              ~0.5-1.5% TOTAL). Development is shared and one-directional,
#              so the median KEEPS it (medians kill disagreement, keep
#              consensus — and "I improved" is the consensus here).
#            BENCHMARK percentile level per season: clean LEVEL, leaks DEPTH.
#            Each is clean exactly where the other leaks; one weighted
#            least-squares curve satisfies both. TRUST_BENCHMARK_LEVEL dials
#            which wins where they disagree.
#
#          THE DEVELOPMENT TERM (the fix for Signal A's dominant leak):
#            Development has STRUCTURE — every 1-year delta carries ~one year
#            of it — so it is SOLVED FOR, not fudged: each difference equation
#            is  L[s1] - L[s0] + d_g = delta,  where d_g is a shared
#            development rate per GRADE TRANSITION (9->10 develops faster than
#            11->12; grade mix drifts with coverage, so one pooled d would
#            leak shape). d_g is identifiable ONLY inside the fusion: with
#            difference rows alone, adding c to d and tilting the curve -c/yr
#            fits equally well (a degeneracy); the benchmark's absolute rows
#            pin the levels and break it. The fitted d_g ladder is itself a
#            sanity read: it should DECREASE with age (9->10 big, Jr->Sr
#            small) — a non-decreasing ladder means contamination.
#
#          SIGNAL HYGIENE (new in this version — every one of these bit v1):
#            - dates are TEXT in the DB: parsed here, sane-year gated, ledgered
#              (the 0023/2223 garbage dies at this gate).
#            - normalized_time in the DB is STALE: this loader normalizes ON
#              THE FLY from raw time_seconds via normalizeTime (era step held
#              OFF — season is never passed — so fitting is never circular).
#            - NULL-aid rows (unlinked tfrrs, id-less) feed ONLY the benchmark;
#              they never enter the same-athlete signal (else they'd collapse
#              into one fake mega-athlete made of strangers).
#            - canon-linked meets carry BOTH sources' rows for one physical
#              race: deduped per athlete at load (dedup downstream warning #1).
#            - Signal A pairs same athlete + SAME EVENT across seasons, so any
#              distance-normalization error is a constant that cancels exactly
#              in the ratio.
#
#          HOW YOU CHECK IT: the per-partition GAP column (same-athlete vs
#          benchmark level), plus the cross-band placebo table. There is no
#          composition law here — these two reads ARE the correctness signal.
#
#          Chain position: distance -> geometry -> THIS (era) -> model.
#          Run AFTER: distance spline fit, geometry fit, tfrrs geometry
#          propagation + assumed-400 anchor runs (else TF indoor coverage is
#          thin and the ledger will show it).

import sys
import os
import math
import re
import pickle
import argparse
import heapq                                 # Stage B: bounded top-N heaps
from array import array                      # compact float32 storage for the
                                             # benchmark pools (4 bytes/time,
                                             # not ~28 for a Python float)
from collections import defaultdict

import numpy as np

# Both directories the shared modules can live in; inserting both is harmless
# (an insert that matches nothing changes nothing).
sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import getConn
# The band classifier and event parser live in normalize_distance and are
# IMPORTED, not mirrored: the fitter and the inference path MUST agree exactly
# on what band an event falls in, and this fitter already imports
# normalize_distance for normalizeTime — so importing the classifier adds zero
# coupling and removes an entire KEEP-IN-SYNC failure mode. (This is different
# from verify/merge, where NO-import is deliberate: a verifier must not import
# what it audits. A fitter and its consumer are not auditor and audited — they
# are two callers of one shared definition.)
from normalize_distance import (getPool, poolFor, normalizeTime,
                                metersFromDistance, parseEventShort,
                                classifyEraBand)
from corrections import distanceOverrideSQL, distanceDropSQL
# Date-aware curated venue facts (renovations, the Iowa chimera) — corrects
# date-blind geometry BEFORE the loader's venue rules read it.
from venue_geometry_overrides import applyVenueOverride
# The stream cache: loadSportCached wraps loadSport so knob-sweep runs skip the
# ~22-min stream. It auto-invalidates when geometry/distance pickles change.
from era_cache import loadSportCached


# Hand-verified distance corrections, GENERATED from corrections.py so the
# fitter and the backfill can never disagree. Empty table -> empty strings
# -> the query below is byte-for-byte what it was before this change.
# stored_expr mirrors the COALESCE fallbacks in the query below, so the
# override is clamped to never RAISE the stored distance (downward-only
# policy; see corrections._DISTANCE_OVERRIDES_XC header).
_OV_JOIN, _OV_COALESCE = distanceOverrideSQL(
    "r", "XC",
    stored_expr="COALESCE(m.distance, (mt.division_distances -> "
                "r.div_id::text ->> 'distance')::float)")
_OV_DROP = distanceDropSQL("r", "XC")


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

DEFAULT_OUT = "engine/data/era_curve.pkl"

# Sane-year gate for parsed dates. Anything outside is ledgered and dropped —
# this is where the 0023 TEST meets and the 2223 rows die, by rule not by
# cleanup pass.
SANE_YEAR_MIN = 1900
SANE_YEAR_MAX = 2026

# Post-normalization sanity band, seconds on the 5K-equivalent scale. Outside
# it = corrupt input or a normalization blow-up (a sprint time the spline
# extrapolated into nonsense); ledgered, never fitted. Wide on purpose: a fast
# college male ~750s, a slow MS girl ~2400s.
NORM_SANE_MIN = 400.0
NORM_SANE_MAX = 4000.0

# Same-athlete pairing: only CONSECUTIVE seasons (gap <= this) — each delta
# carries ~one year of aging, not a career arc.
MAX_SEASON_GAP = 1

# BENCHMARK_PERCENTILE: the tight elite tail used when the field is deep enough
# (top 1% is the most composition-stable era marker — see the placebo sweep).
BENCHMARK_PERCENTILE = 0.01
# MIN_BENCH_ATHLETES: the floor. A season's benchmark is drawn from at least
# this many athletes, so a thin field widens past 1% instead of collapsing to
# ~1 person. This is what makes the percentile VARIABLE: effective pct = the
# LARGER of "1% of n" and "MIN_BENCH_ATHLETES/n".
MIN_BENCH_ATHLETES = 20

# THE FUSION KNOB. Trust in the benchmark's ABSOLUTE LEVEL vs the same-athlete
# STEPS. 1.0 = balanced. The measure pass's GAP column tells you how to set it:
# benchmark showing MORE improvement than same-athlete => depth leak => lower;
# the reverse => aging leak => raise.
TRUST_BENCHMARK_LEVEL = 1.0

# Minimum support before a measurement is trusted — now enforced PER PARTITION.
MIN_DELTAS_PER_STEP = 30           # same-athlete pairs bridging a season pair
MIN_RESULTS_PER_SEASON = 200       # results for a stable percentile

# Ship threshold: a season needs at least this many benchmark results to be
# shipped as its OWN inference point. Sparser endpoint seasons (e.g. a mid-year
# 2026 with n=10) still HELP the solve as constraints, but at inference they
# would hand out a noise-level correction — so they inherit the nearest season
# that clears this bar. Tied to the same evidence the reference guard uses
# (benchmark count), NOT to a hard-coded year, so it self-applies every season.
MIN_SHIP_BENCH_N = 30   # reuse the percentile-stability bar

# A sparse season may inherit a solid neighbour ONLY this many years away.
# 1 = immediate neighbour only: fixes the current-year sliver (2026 -> 2025) and
# nothing else. Beyond this reach the "nearest solid" season is too far to be
# evidence (inheriting 1992 for a 1940 race is extrapolation, not a fill), so the
# season keeps its OWN solved value. Same principle as LADDER_MAX_EDGE_GAP: don't
# bridge across a big temporal gap.
MAX_CLAMP_REACH = 1

# Super-shoe transition window, flagged in every partition's report and
# compared across bands in the placebo table.
SHOE_WINDOW = (2017, 2023)

# Canon-meet dedup: two rows are "the same physical result" when they share
# (person, canon meet, event) and their RAW times land in the same bucket of
# this width. Matched pairs agree to ~0.1s (verified in dedup), so 0.5s
# buckets catch them; a pair straddling a bucket edge survives as a rare
# duplicate — accepted, bounded.
DEDUP_TIME_BUCKET = 0.75

# Server-side cursor fetch size: rows pulled per round-trip. The stream never
# holds more than this many raw rows at once.
STREAM_ITERSIZE = 50_000

# Working memory for the stream's ORDER BY aid sort, per transaction. It is
# PER sort/hash node PER worker -- but this stream is ONE sort and the
# server-side cursor runs SERIAL (cursors disable parallel query), so it's a
# single allocation. On a 64GB box running one sport at a time, 4GB collapses
# the sort's on-disk merge passes while leaving the OS page cache (which the
# seq-scans lean on) room. RAISE WITH CARE: running TF+XC concurrently, or
# re-enabling parallelism, MULTIPLIES this across those sorts.
STREAM_WORK_MEM_MB = 4096

# Progress heartbeat (rows) so a multi-hour stream is visibly alive.
PROGRESS_EVERY = 10_000_000

# Bands whose shoe-window jump SHOULD be ~zero (the controls) vs bands where
# the shoe effect should be at full size — the two rows of the placebo table.
PLACEBO_BANDS   = {"sprint", "hurdles_short"}
TREATMENT_BANDS = {"m5000", "m3200", "xc"}

# Seasons whose BENCHMARK LEVEL is untrustworthy -> excluded from the SOLVE
# (but still shown in the report, so you SEE what's being excluded). The
# same-athlete STEPS across these seasons are KEPT: a truncated field wrecks an
# absolute percentile, but a year-over-year delta between the athletes who DID
# race survives it. If a season has no usable steps either, it simply drops out
# of the curve — never pinned to garbage.
#   2020: COVID. Fields truncated -> top-10% percentile computed on a tiny,
#         unrepresentative field (bench n ~5x down; GAP spikes in EVERY
#         partition at once = broken season, not era).
UNTRUSTED_BENCHMARK_SEASONS = {2020, 2021}

# The reference season must be REPRESENTATIVE, not merely most recent: it
# must carry at least this fraction of the partition's biggest season's
# benchmark n. Kills the mid-year sliver anchor (XC July-2026, 2,855 rows
# anchoring against seasons of hundreds of thousands).
REFERENCE_REP_FRACTION = 0.25

# ---- STAGE B: XC self-measurement knobs ----
LADDER_K = 10                 # fixed-RANK marker: mean log time of the K
                              # fastest. Rank, not percentile — field growth
                              # moves a percentile marker; runner #K is the
                              # same competitive object in a 300-finisher
                              # year and a 900-finisher year.
LADDER_RETAIN_N = 20          # heap depth kept per series-year, so K is a
                              # fit-time knob up to 20 with NO re-stream
LADDER_MIN_FINISHERS = 30     # ~3x K: rank K must sit inside a real field,
                              # not a dual meet's mid-pack; the ladder
                              # report prints how many cells this kills
LADDER_MAX_EDGE_GAP = 2       # consecutive observed years, gap <= 2: a
                              # series dark for longer has likely changed
                              # character (prestige drift grows with gap)
LADDER_UNTRUSTED_EDGE_W = 0.25  # COVID edges DOWN-WEIGHTED, never dropped:
                              # dropping every 2020/21 edge disconnects the
                              # graph and pre/post-COVID lose their shared
                              # scale entirely
BRIDGE_BANDS = ("m3200", "m5000")   # the XC-adjacent track events
BRIDGE_TF_DOY = (91, 181)     # Apr 1..Jun 30, outdoor spring only: a Jan
                              # indoor 3000 is ~3 months after fall, a June
                              # 5000 ~9 — letting that mix drift across
                              # years re-injects the composition leak
BRIDGE_MIN_PAIRS = 200        # a season's bridge median needs real support

# Bands that CANNOT cleanly measure their own era, mapped to a clean donor
# band (same gender is matched in code). Key: (sport, band) -> (sport, band).
#   steeple -> m3200: too thin to fit its own curve; borrows the nearest
#   flat-distance physiology (approved 7/04).
#   XC: NO ENTRY, by owner decision (7/04) — XC self-measures (meet-series
#   ladder + cross-sport bridge, Stage B). The old m5000 borrow is
#   deliberately absent so it can never fire by accident; until Stage B
#   lands, an unfitted XC band simply no-ops at inference.
ERA_BORROW_MAP = {("TF", "steeple"): ("TF", "m3200")}



import time
from collections import defaultdict

# _PhaseTimer
# Purpose:   Accumulate wall-clock time spent in named phases across the whole
#            run, so the end-of-run summary shows WHERE the time went (stream
#            vs per-row prepare vs solve). Cheap: perf_counter() is a couple of
#            nanoseconds; calling it a few hundred million times for per-row
#            timing WOULD matter, so we time COARSE phases, never inner loops.
# Usage:     with timer.phase("stream"): ...   # accrues into that bucket
class _PhaseTimer:
    def __init__(self):
        self._totals = defaultdict(float)   # phase name -> seconds accumulated

    # phase — a context manager: records elapsed time between enter and exit
    #         into the named bucket. Returned by a generator wrapped with
    #         contextmanager so `with timer.phase("x"):` reads cleanly.
    def phase(self, name):
        return self._Span(self, name)

    # _record — add one span's seconds to its bucket (called by _Span on exit).
    def _record(self, name, seconds):
        self._totals[name] += seconds

    # report — print each phase's total, biggest first (the lever is on top).
    def report(self):
        print("\n=== TIMING (seconds by phase) ===")
        for name, secs in sorted(self._totals.items(), key=lambda kv: -kv[1]):
            print(f"    {name:>16}: {secs:8.1f}s")

    # _Span — the actual context manager: perf_counter() at enter and exit,
    #         difference recorded. A tiny inner class so the timing logic and
    #         its storage stay in one place.
    class _Span:
        def __init__(self, timer, name):
            self._timer, self._name = timer, name
        def __enter__(self):
            self._t0 = time.perf_counter()  # high-res monotonic start
            return self
        def __exit__(self, *exc):
            self._timer._record(self._name, time.perf_counter() - self._t0)

# ------------------------------------------------------------------ #
# TINY HELPERS
# ------------------------------------------------------------------ #

# _median
# Purpose:   Robust centre — one blow-up race shouldn't move an estimate.
# Arguments: xs — non-empty sequence of floats.
# Output:    median as float.
def _median(xs) -> float:
    return float(np.median(xs))


# _parseSeason
# Purpose:   Result date (a TEXT column in this schema — never call .year on
#            it) -> season year, sane-gated. One regex find beats format
#            guessing: it reads "2019" out of "2019-09-14" and "Sep 14, 2019"
#            alike, and REFUSES 0023/2223 because neither contains a
#            19xx/20xx group at all.
# Arguments: date_text — the raw date string (or None).
# Output:    integer season inside [SANE_YEAR_MIN, SANE_YEAR_MAX], else None.
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
def _parseSeason(date_text):
    if date_text is None:
        return None
    m = _YEAR_RE.search(str(date_text))       # first 19xx/20xx group anywhere
    if m is None:
        return None
    year = int(m.group(0))
    return year if SANE_YEAR_MIN <= year <= SANE_YEAR_MAX else None


# _cleanGender
# Purpose:   Normalize the athletes.gender value to the partition key's "M"/"F".
#            Anything else ("", "X", NULL) can't be partitioned -> None (the
#            caller ledgers and drops; a benchmark percentile over mixed
#            genders is a composition-drift leak, so unknowns can't just pool).
# Arguments: gender — raw value from the athletes join (may be None).
# Output:    "M", "F", or None.
def _cleanGender(gender):
    g = str(gender or "").strip().upper()
    return g if g in ("M", "F") else None


# _poolFor MOVED to normalize_distance.poolFor (2026-07-04): the era and
# distance fitters and the consumer are all callers of one definition —
# pickle keys and lookup keys can never drift apart. Imported above.


# _gradeKey
# Purpose:   The development-group label for one athlete's season pair — which
#            d_g unknown their delta helps solve. Built from the two grades
#            ("9->10", "Fr->So"); anything unparseable/odd falls to "other"
#            (its own pooled d — never silently mixed into a real transition).
# Arguments: g0, g1 — the athlete's raw grade strings in the earlier / later
#              season (either may be None/blank/garbage).
# Output:    a group key string.
_KNOWN_GRADES = {"5", "6", "7", "8", "9", "10", "11", "12",
                 "Fr", "So", "Jr", "Sr"}
def _gradeKey(g0, g1) -> str:
    a = str(g0 or "").strip()
    b = str(g1 or "").strip()
    if a in _KNOWN_GRADES and b in _KNOWN_GRADES:
        return f"{a}->{b}"
    return "other"

# _benchmarkIndex
# Purpose:   The index into a season's ASCENDING (fast-first) time array that
#            marks the benchmark. Variable by design: 1% of the field, but never
#            shallower than MIN_BENCH_ATHLETES deep — so a deep season uses the
#            tight 1% tail and a thin one widens to a real group instead of a
#            single athlete.
# Arguments: n — the season's result count (already known to clear the floor).
# Output:    a 0-based index (0 = fastest). Deeper field -> smaller index.
def _benchmarkIndex(n) -> int:
    pct_idx   = int(BENCHMARK_PERCENTILE * (n - 1))   # the 1% position
    floor_idx = MIN_BENCH_ATHLETES - 1                # the "at least K people" position
    # max(): whichever tail is DEEPER (further from the very tip) wins, so we
    # never take a tail shallower than MIN_BENCH_ATHLETES.
    return max(pct_idx, floor_idx)


# ================================================================== #
# CHUNK 1 — LOAD: streamed, normalized on the fly, aggregated in one pass
# ================================================================== #



#
#   THE STREAM (memory never scales with table size):
#
#     Postgres ── ORDER BY aid ──> row ──> _prepare*Row (parse/filter/normalize)
#                                     │
#             aid IS NULL ───────────►│──> benchmark pool ONLY (no identity)
#                                     │
#             aid changes ──► flush previous athlete's buffer:
#                             per (partition, event): season medians ->
#                             consecutive deltas -> steps[partition]
#                                     │
#             same aid  ────► dedupe (canon, event, time-bucket), then
#                             buffer the row + append to its benchmark pool
#
#   ORDER BY aid makes each athlete's rows CONSECUTIVE, so Signal A needs only
#   one athlete's rows in memory at a time; the benchmark accumulates as flat
#   float32 arrays (4 bytes/row). That is the whole memory story for ~100M rows.

# The two SQL streams. Conventions they encode (per the master doc + dedup):
#   - sentinels per source, one predicate for both: anet no-result = 999999 or
#     place 0; tfrrs = NULL time or place 0. COALESCE(place,-1) keeps rows
#     whose place is merely NULL (not a sentinel).
#   - every join carries source (the §0 invariant): meets/meets_tf rows are
#     matched per-source, never by bare ids across id spaces.
#   - the athletes join is a LATERAL probe by the canonical person id
#     (person_id = anet athlete_id, so linked tfrrs rows resolve too); LIMIT 1
#     because athletes' PK is (athlete_id, school) and one athlete can carry
#     several school rows — gender agrees across them.
#   - relays/field events are usability cuts -> SQL (a relay time can never
#     enter era; cutting it server-side is free). Value judgments (indoor
#     handling, band rules) stay in memory where they can be ledgered.

_ATHLETE_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT a.gender
        FROM athletes a
        WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
        LIMIT 1
    ) g ON TRUE
"""

_XC_SQL = f"""
    SELECT COALESCE(r.person_id, r.athlete_id) AS aid,
           r.date, r.time_seconds, r.grade, r.source, r.canon_meet_id,
           -- Per-source distance: anet rows read the meets table (as before);
           -- tfrrs XC rows read the meets_tfrrs jsonb blob, keyed by the row's
           -- own div_id (jsonb keys are strings, hence ::text). COALESCE tries
           -- anet first; for tfrrs, m.distance is NULL so it falls to the blob.
           COALESCE(
               {_OV_COALESCE}
               m.distance,
               (mt.division_distances -> r.div_id::text ->> 'distance')::float
           ) AS distance,
           m.location_id, g.gender,
           -- NEW: the tfrrs division title, pulled from the same blob we already
           -- join for distance. NULL for anet rows (mt is all-NULL there). Used
           -- ONLY as a gender fallback in _prepareXCRow when the athletes lateral
           -- returns no gender (unlinked tfrrs rows). Column order matters: this
           -- is LAST, so it unpacks as the final element of the row tuple.
           (mt.division_distances -> r.div_id::text ->> 'div_name') AS div_name,
           -- NEW, LAST column: the school. anet's real grades tell us each
           -- school's level; tfrrs rows have grade=NULL, so the school is the
           -- only honest level signal they carry. poolFor uses it ONLY when the
           -- grade is unusable AND the school is unambiguous. Appended last so
           -- no existing positional unpack shifts.
           r.school AS school
    FROM results r
    LEFT JOIN meets m
           ON m.div_id = r.div_id AND m.source = r.source
    -- tfrrs XC distance lives in meets_tfrrs.division_distances. r.source='tfrrs'
    -- sits in the ON (not WHERE): in a LEFT JOIN the ON only decides MATCHING,
    -- so anet rows pass through with mt all-NULL. In the WHERE it would DELETE
    -- every anet row from the stream. (Same lesson as the TF geometry join.)
    LEFT JOIN meets_tfrrs mt
           ON mt.meet_id = r.meet_id
          AND mt.sport   = 'XC'
          AND r.source   = 'tfrrs'
{_OV_JOIN}    {_ATHLETE_LATERAL}
    WHERE r.time_seconds IS NOT NULL
      AND r.time_seconds > 0
      AND r.time_seconds <> 999999
      AND COALESCE(r.place, -1) <> 0
{_OV_DROP}    ORDER BY aid
"""

_TF_SQL = f"""
    SELECT COALESCE(r.person_id, r.athlete_id) AS aid,
           r.date, r.time_seconds, r.grade, r.source, r.canon_meet_id,
           r.event_short,
           m.distance_meters,
           COALESCE(m.track_length, tg.track_length) AS track_length,
           COALESCE(m.track_type,   tg.track_type)   AS track_type,
           COALESCE(m.is_indoor,    tg.is_indoor)    AS is_indoor,
           COALESCE(m.location_id,  tg.location_id)  AS location_id,
           g.gender,
           -- NEW, LAST column. results_tf.school is 100% populated; tfrrs TF
           -- rows have grade=NULL, so the school is their only honest level
           -- signal. Appended last so no existing positional unpack shifts.
           r.school AS school
    FROM results_tf r
    LEFT JOIN meets_tf m
           ON m.meet_id  = r.meet_id
          AND m.div_id   = r.div_id
          AND m.event_id = r.event_id
          AND m.source   = r.source
    -- Per-source geometry: anet reads meets_tf (event grain); tfrrs reads
    -- the propagation stamps (meet grain). The r.source = 'tfrrs' condition
    -- lives in the ON, not the WHERE: in a LEFT JOIN the ON only decides
    -- MATCHING, so anet rows pass through with tg all-NULL — in the WHERE
    -- it would DELETE every anet row from the stream. It also enforces §0:
    -- an anet meet_id can never collide with a same-integer tfrrs stamp.
    LEFT JOIN tfrrs_meet_geometry tg
           ON tg.meet_id = r.meet_id
          AND tg.sport   = 'TF'
          AND r.source   = 'tfrrs'
    {_ATHLETE_LATERAL}
    WHERE r.time_seconds IS NOT NULL
      AND r.time_seconds > 0
      AND r.time_seconds <> 999999
      AND COALESCE(r.place, -1) <> 0
      AND COALESCE(r.is_relay, 0) = 0
      AND COALESCE(r.is_field, 0) = 0
    ORDER BY aid
"""


# _prepareXCRow
# Season-timing control: pair only races at the same POINT IN THE SEASON, so
# within-season fitness (Sep->Nov improvement) doesn't masquerade as era. Bin
# the day-of-year to a fortnight (~"a week either side"). NOTE: a bin, not a
# true sliding +/-7d window -- edge-straddling recurrences are lost; refine to
# sliding if this helps but weakly.
_SEASON_BIN_DAYS = 14

# Fast day-of-year: two compiled patterns (ISO and US) + a cumulative-days
# table. Cheaper than strptime across ~30M rows; leap day is ignored (a 1-day
# error is nothing at fortnight resolution).
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_US_DATE  = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)  # days before month m


# _dayOfYear
# Purpose:   Day-of-year (1-366) from the TEXT date, for season-timing bins.
#            Tries ISO then US layout; returns None if neither parses (caller
#            then treats the row as benchmark-only -> ledger shows the rate).
# Arguments: date_text — the raw r.date string (or None).
# Output:    int day-of-year, or None.
def _dayOfYear(date_text):
    if date_text is None:
        return None
    s = str(date_text)
    m = _ISO_DATE.match(s)
    if m:
        mon, day = int(m.group(2)), int(m.group(3))
    else:
        m = _US_DATE.match(s)
        if not m:
            return None
        mon, day = int(m.group(1)), int(m.group(2))
    if not (1 <= mon <= 12 and 1 <= day <= 31):
        return None
    return _CUM_DAYS[mon - 1] + day


# _xcCourseKey
# Purpose:   XC Signal-A pairing key = the COURSE identity, so only same-course
#            races pair and course difficulty cancels in the delta (as it does
#            for a fixed track). A course is (venue, race distance): the doc
#            keys courses on (location_id, distance) because one venue hosts
#            multiple races. distance -> nearest 100m (XC races are round; this
#            fuses year-to-year metadata noise while keeping 5K vs 8K apart) --
#            the same granularity the feasibility probe validated.
# Arguments: dist_m      — resolved race distance in meters.
#            location_id — venue id, or None when the meet has no location.
# Output:    ("flat", dist_100, location_id), or None when the course can't be
#            resolved (no venue) -> caller makes the row benchmark-only.
def _xcCourseKey(dist_m, location_id, date_text):
    if location_id is None:
        return None                                # no venue -> no verifiable course
    doy = _dayOfYear(date_text)
    if doy is None:
        return None                                # no season-date -> can't timing-control
    dist_100 = int(round(dist_m / 100.0)) * 100    # nearest 100m bucket
    bin_ = doy // _SEASON_BIN_DAYS                  # season-timing fortnight
    return ("flat", dist_100, location_id, bin_)


# _genderFromName
# Purpose:   Recover gender from a tfrrs division title when the athletes lateral
#            gave us none (unlinked tfrrs rows have no person_id -> no gender).
#            "Women"/"Girls" -> "F", "Men"/"Boys" -> "M", else None.
# Arguments: div_name — the blob's division title string, or None.
# Output:    "M", "F", or None.
#   THE TRAP: the substring "men" is inside "women", so we MUST test women/girls
#   FIRST, or every women's race would misread as M. (Same rule the distance
#   inference used; copied here so this module stays self-contained.)
def _genderFromName(div_name):
    if not div_name:
        return None
    low = div_name.lower()                 # case-insensitive match
    if "women" in low or "girls" in low:   # F tested first — women contains "men"
        return "F"
    if "men" in low or "boys" in low:
        return "M"
    return None


# Purpose:   One XC row -> a clean record, or None + a ledger reason. Every
#            drop has a named rule; nothing vanishes silently.
# Arguments: row    — the SQL tuple, in _XC_SQL column order.
#            ledger — defaultdict(int) of drop-reason counts (mutated here).
# Output:    record dict {aid, season, ntime, raw, canon, event_key, pkey}, or
#            None if the row was dropped. Field meanings:
#              aid       — canonical person id, or None (benchmark-only row)
#              season    — sane-gated year
#              ntime     — on-the-fly normalized time (distance step only for
#                          XC; NO era — season is never passed to normalizeTime)
#              raw       — raw seconds (the dedup bucket keys on RAW time,
#                          because that's what both sources agree on to ~0.1s)
#              canon     — canon_meet_id or None (None -> row can't be deduped)
#              event_key — ("flat", dist_100, location_id) COURSE key (or
#                          None if no venue): Signal A pairs within it
#              pkey      — ("XC", "xc", gender): the partition
def _prepareXCRow(row, ledger):
    # div_name then school are the last two columns (see _XC_SQL). div_name is
    # NULL for anet rows; school is present on both sources.
    aid, date_t, t, grade, source, canon, distance, location_id, gender, \
        div_name, school = row

    season = _parseSeason(date_t)
    if season is None:
        ledger["bad_date"] += 1; return None

    gender = _cleanGender(gender)
    if gender is None:
        # FALLBACK: the athletes lateral gave no gender (an unlinked tfrrs row).
        # Recover it from the division title before dropping — this is what
        # rescues the ~1.4M unlinked tfrrs XC rows. _cleanGender again so the
        # recovered value passes the same normalization the lateral value did.
        gender = _cleanGender(_genderFromName(div_name))
    if gender is None:                     # still nothing -> genuinely genderless
        ledger["unknown_gender"] += 1; return None

    dist_m = metersFromDistance(distance)
    if dist_m is None or dist_m <= 0:
        # A row still lands here only if it has NO resolvable distance from
        # EITHER source: anet meets.distance NULL AND (for tfrrs) no blob entry
        # for this div_id (e.g. a genuine no-distance race we couldn't infer).
        # The old "tfrrs always drops here" rule is gone — tfrrs XC now carries
        # real + inferred distances from meets_tfrrs.
        ledger["no_distance"] += 1; return None

    # SSOT pooling. `school` lets poolFor recover the level that tfrrs's NULL
    # grade cannot supply -- and it MUST be passed here, or this fitter would
    # key its curves by the OLD pooling while the backfill uses the new one.
    # That silent divergence is exactly what poolFor exists to prevent.
    pool = poolFor(grade, gender, source, school)
    if pool is None:
        ledger["unknown_pool"] += 1; return None

    # season is NOT passed -> the era step inside normalizeTime never fires ->
    # fitting era on era-corrected values (circularity) is impossible by
    # construction, even with a stale era pickle sitting on disk.
    # sport IS passed (new): XC and TF carry measurably different distance
    # laws (XC local exp ~0.95-1.05, TF ~1.06-1.22), and the distance
    # artifact keys per-sport curves. Signal A never needed this (same
    # event -> the factor cancels in the ratio); the BENCHMARK did (a
    # wrong-law factor error times event-mix drift leaks into levels).
    # Era stays dark: sport without season arms nothing.
    ntime = normalizeTime(t, dist_m, pool, sport="XC")
    if ntime is None:
        ledger["norm_failed"] += 1; return None
    if not (NORM_SANE_MIN <= ntime <= NORM_SANE_MAX):
        ledger["insane_norm"] += 1; return None

    # SAME-COURSE: event_key carries the VENUE too, so Signal A pairs only
    # within a course. No location -> None -> benchmark-only (no clean delta).
    event_key = _xcCourseKey(dist_m, location_id, date_t)

    return {"aid": aid, "season": season, "ntime": ntime, "raw": float(t),
            "canon": canon, "event_key": event_key,
            "pkey": ("XC", "xc", gender), "grade": grade}


# _isoDate
# Purpose:   Normalize the TEXT date to ISO 'YYYY-MM-DD' (zero-padded) for
#            the venue override's string-ordered range checks. US-format
#            dates are REBUILT — string-comparing '1/15/2010' against ISO
#            bounds is garbage ('1' < '2' makes every US date look
#            pre-renovation). Unparseable -> None -> the override abstains
#            (a row we can't place in time gets no date-keyed correction).
# Arguments: date_text — raw r.date (either format, or None/garbage).
# Output:    'YYYY-MM-DD' string, or None.
def _isoDate(date_text):
    if not date_text:
        return None
    m = _ISO_DATE.match(date_text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _US_DATE.match(date_text)
    if not m:
        return None
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


# _prepareTFRow
# Purpose:   One TF row -> a clean record or None + ledger reason. Adds the
#            TF-only concerns: event parsing/banding, and the indoor-geometry
#            rule that keeps this loader from laundering indoor drift into era.
# Arguments: row, ledger — as in _prepareXCRow (columns per _TF_SQL).
# Output:    record dict as in _prepareXCRow (event_key = (kind, meters), pkey
#            = ("TF", band, gender)), or None.
#
#   THE INDOOR RULE (geometry downstream warning #1, applied at the source):
#     indoor + unknown length  -> DROP. Normalizing it would default to the
#         400m-flat reference — silently uncorrected — and if indoor share
#         grew over the years, that growth reads as "the era got faster".
#         The fitter EXCLUDES unknown geometry; only inference defaults it.
#     outdoor + unknown length -> KEEP, no track args. Outdoor US tracks are
#         400m flat at a measured 100.0% (census C) — reference IS the truth.
#     no venue row at all      -> DROP (can't even tell indoor from outdoor).
#         If the ledger shows tfrrs rows massing here, the geometry
#         propagation output isn't reaching this join — stop and say so.
def _prepareTFRow(row, ledger):
    (aid, date_t, t, grade, source, canon, event_short,
     dm, track_length, track_type, is_indoor, location_id, gender, school) = row

    season = _parseSeason(date_t)
    if season is None:
        ledger["bad_date"] += 1; return None

    gender = _cleanGender(gender)
    if gender is None:
        ledger["unknown_gender"] += 1; return None

    parsed = parseEventShort(event_short)     # {"meters":..., "kind":...}
    if parsed["kind"] == "racewalk":          # different locomotion class —
        ledger["racewalk"] += 1; return None  # excluded by rule, as in geometry
    if parsed["kind"] == "relay":             # belt (SQL is_relay) + braces
        ledger["relay"] += 1; return None     # (the parser) — some relays have
                                              # is_relay unset

    band = classifyEraBand("TF", event_short, dm)
    if band is None:
        ledger["unbandable"] += 1; return None

    # Column-first distance resolution, the geometry lesson: distance_meters is
    # ~30% populated; the event_short parser is the workhorse fallback.
    dist_m = dm if (dm is not None and dm > 0) else parsed["meters"]
    if dist_m is None or dist_m <= 0:
        ledger["no_distance"] += 1; return None

    # VENUE OVERRIDE: curated (venue, date-range) facts correct date-blind
    # meta (renovated venues; Iowa's chimera fixes LENGTH too) before the
    # venue rules and normalizeTime read the fields. is_indoor is never
    # overridden — the None-is-not-0 semantics below stay untouched.
    geo, ov_rule = applyVenueOverride(
        {"track_type": track_type, "track_length": track_length,
         "is_indoor": is_indoor},
        location_id, _isoDate(date_t))
    if ov_rule is not None:
        ledger["venue_override"] += 1
        track_type, track_length = geo["track_type"], geo["track_length"]

    if is_indoor is None and track_length is None:
        ledger["unknown_venue"] += 1; return None
    if is_indoor == 1 and track_length is None:
        ledger["indoor_unknown_geometry"] += 1; return None

    # results_tf.school CONFIRMED present and 100% populated (20.1M tfrrs rows),
    # so TF gets the same school-level recovery as XC. Without it every tfrrs TF
    # row pools college and is judged against the loosest raw-time floor.
    pool = poolFor(grade, gender, source, school)
    if pool is None:
        ledger["unknown_pool"] += 1; return None

    # sport="TF": the per-sport distance law (see the XC site's comment).
    # Still no season -> the circularity guard holds unchanged.
    ntime = normalizeTime(t, dist_m, pool, sport="TF",
                          track_length=track_length, track_type=track_type)
    if ntime is None:
        ledger["norm_failed"] += 1; return None
    if not (NORM_SANE_MIN <= ntime <= NORM_SANE_MAX):
        ledger["insane_norm"] += 1; return None

    return {"aid": aid, "season": season, "ntime": ntime, "raw": float(t),
            "canon": canon, "doy": _dayOfYear(date_t),  # Stage B: the
            "event_key": (parsed["kind"], int(round(dist_m))),  # bridge's
            "pkey": ("TF", band, gender), "grade": grade}  # spring gate


# _flushAthlete
# Purpose:   Turn ONE athlete's buffered rows into Signal-A deltas — each
#            TAGGED with its grade transition (the development group whose d_g
#            it helps solve) — and forget them. Same athlete + SAME EVENT +
#            consecutive seasons -> one log-delta; the distance normalization's
#            per-event constant cancels exactly in the ratio, so spline error
#            at extrapolated distances (sprints) cannot leak into the steps.
# Arguments: buffer — {(pkey, event_key): {season: [ntime,...]}} — this one
#              athlete's rows, grouped by partition+event then season.
#            grades — {season: grade} — this athlete's grade per season (one
#              grade per season regardless of event; first seen wins).
#            steps  — {pkey: {(s0,s1): {gkey: [log_delta,...]}}} — the global
#              Signal-A accumulator: deltas grouped by season pair AND
#              development group (mutated).
# Output:    none (mutates steps).
def _flushAthlete(buffer, grades, steps) -> None:
    for (pkey, _event), seasons in buffer.items():
        med = {s: _median(v) for s, v in seasons.items()}   # one value/season
        ordered = sorted(med)                               # earliest first
        for i in range(len(ordered) - 1):                   # adjacent pairs
            s0, s1 = ordered[i], ordered[i + 1]
            if s1 - s0 > MAX_SEASON_GAP:
                continue                                    # skipped a season
            t0, t1 = med[s0], med[s1]
            if t0 > 0 and t1 > 0:
                gkey = _gradeKey(grades.get(s0), grades.get(s1))
                # log(next/prev): negative = the athlete got faster.
                steps[pkey][(s0, s1)][gkey].append(math.log(t1 / t0))


# _consumeStream
# Purpose:   The single pass: walk the aid-ordered stream, route every row
#            through prepare -> (dedupe) -> benchmark pool, and flush each
#            athlete into Signal A the moment their block ends.
# Arguments: cur     — an OPEN server-side cursor already executing the sport's
#              SQL (iterating it pulls STREAM_ITERSIZE rows per round-trip).
#            prepare — _prepareXCRow or _prepareTFRow.
#            steps   — Signal-A accumulator {pkey: {(s0,s1): [delta,...]}}.
#            bench   — benchmark accumulator {pkey: {season: array('f')}}.
#            ledger  — drop-reason counter.
# Output:    none (mutates steps / bench / ledger).
def _consumeStream(cur, prepare, steps, bench, ledger, collect=None) -> None:
    cur_aid = object()          # sentinel unequal to every real aid (incl None)
    buffer, grades, seen, n_rows = {}, {}, set(), 0

    for row in cur:
        n_rows += 1
        if n_rows % PROGRESS_EVERY == 0:
            print(f"    ...{n_rows:,} rows streamed", flush=True)

        rec = prepare(row, ledger)
        if rec is None:
            continue

        # No identity -> benchmark only. Never buffered: with ORDER BY aid the
        # NULLs arrive as one giant trailing block, and buffering it would be
        # both the mega-athlete bug AND a memory blow-up.
        if rec["aid"] is None:
            bench[rec["pkey"]][rec["season"]].append(rec["ntime"])
            if collect is not None:      # the ladder still wants this row —
                collect(rec)             # a finisher time needs no identity
            ledger["kept_benchmark_only"] += 1
            continue

        if rec["aid"] != cur_aid:             # athlete boundary: flush + reset
            _flushAthlete(buffer, grades, steps)
            buffer, grades, seen, cur_aid = {}, {}, set(), rec["aid"]

        # Canon-meet dedupe (within one athlete): the same physical result
        # arrives once per source at canon-linked meets; keep the first.
        # int(raw / bucket) buckets the RAW time — matched pairs agree ~0.1s.
        if rec["canon"] is not None:
            key = (rec["canon"], rec["event_key"],
                   int(rec["raw"] / DEDUP_TIME_BUCKET))
            if key in seen:
                ledger["deduped_canon"] += 1
                continue                      # drop BEFORE benchmark: the
            seen.add(key)                     # duplicate feeds neither signal

        bench[rec["pkey"]][rec["season"]].append(rec["ntime"])   # benchmark: always
        if collect is not None:          # Stage B accumulators see every
            collect(rec)                 # kept, canon-DEDUPED record
        # Signal A only when the course is resolved (event_key not None). XC rows
        # with no location can't form a clean same-course delta -> benchmark-only.
        # (TF event_key is always set, so this branch is unchanged for TF.)
        if rec["event_key"] is not None:
            buffer.setdefault((rec["pkey"], rec["event_key"]),
                              {}).setdefault(rec["season"], []).append(rec["ntime"])
            grades.setdefault(rec["season"], rec["grade"])   # first seen wins
            ledger["kept"] += 1
        else:
            ledger["kept_no_course"] += 1

    _flushAthlete(buffer, grades, steps)      # the last athlete has no
                                              # boundary row — flush explicitly
    print(f"    stream done: {n_rows:,} rows", flush=True)


# _printLedger
# Purpose:   The per-reason accounting — every dropped row has a named rule.
# Arguments: sport, ledger.
# Output:    none (prints, sorted by count so the big reasons lead).
def _printLedger(sport, ledger) -> None:
    print(f"\n  [{sport}] loader ledger:")
    for reason, n in sorted(ledger.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:>28}: {n:,}")


# _tuneSortMemory
# Purpose:   Grant THIS transaction's sort node more working memory so the
#            ORDER BY aid sort does fewer on-disk merge passes -- the wait=IO the
#            stream's first FETCH spends its startup in. A 191M-row sort won't
#            fit entirely; more memory just means fewer passes.
# Arguments: conn — open pooled connection, BEFORE the cursor executes (the
#                   planner reads work_mem at execute time, so this runs first).
#            mb   — work_mem to grant, in megabytes.
# Output:    None. SET LOCAL is transaction-scoped -> reverts on block exit ->
#            never leaks to the next borrower of the pooled connection.
def _tuneSortMemory(conn, mb):
    with conn.cursor() as cur:                      # throwaway cursor just for the SET
        # SET can't take a bound %s and work_mem wants a quoted size literal;
        # int(mb) is our own trusted number, so formatting it in is safe.
        cur.execute(f"SET LOCAL work_mem = '{int(mb)}MB'")


# loadSport
# Purpose:   Stream one sport's table end-to-end into the two aggregates.
# Arguments: sport — "XC" | "TF".
# Output:    (steps, bench):
#              steps — {pkey: {(s0,s1): {gkey: [log_delta,...]}}} Signal-A raw
#                      deltas, grouped by season pair and development group
#              bench — {pkey: {season: array('f')}} Signal-B time pools
#            where pkey = (sport, band, gender).
def loadSport(sport, timer, collect=None):   
    steps  = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    bench  = defaultdict(lambda: defaultdict(lambda: array("f")))
    ledger = defaultdict(int)

    sql     = _XC_SQL if sport == "XC" else _TF_SQL
    prepare = _prepareXCRow if sport == "XC" else _prepareTFRow

    print(f"\nstreaming {sport} results (server-side cursor, "
          f"itersize {STREAM_ITERSIZE:,})...", flush=True)
    with getConn() as conn:
        cur = conn.cursor(name=f"era_stream_{sport.lower()}")
        cur.itersize = STREAM_ITERSIZE
        _tuneSortMemory(conn, STREAM_WORK_MEM_MB)   # bigger sort memory, BEFORE execute
        with timer.phase(f"stream_{sport}"):      # <-- time the whole drain
            cur.execute(sql)
            _consumeStream(cur, prepare, steps, bench, ledger, collect)
    _printLedger(sport, ledger)
    return steps, bench


# ================================================================== #
# CHUNK 2 — SIGNAL A: reduce raw deltas to trusted per-pair steps
# ================================================================== #

# _stepMedians
# Purpose:   Reduce each (season pair, development group) bucket's deltas to
#            ONE robust step + support count. The median over thousands of
#            athletes kills two-sided noise; what it KEEPS is everything the
#            bucket shares — era step + that group's development rate — which
#            is exactly what the d_g term in the solve is for. Buckets too
#            thin to trust are POOLED into the pair's "other" bucket (never
#            silently mixed into a real transition); a pooled bucket still
#            thin after pooling is dropped.
# Arguments: steps — {(s0,s1): {gkey: [log_delta,...]}} for ONE partition.
# Output:    {((s0,s1), gkey): (median_log_step, count)} for buckets with >=
#            MIN_DELTAS_PER_STEP support.
def _stepMedians(steps) -> dict:
    out = {}
    for pair, by_group in steps.items():
        overflow = []                              # thin groups pool here
        for gkey, deltas in by_group.items():
            if gkey != "other" and len(deltas) >= MIN_DELTAS_PER_STEP:
                out[(pair, gkey)] = (_median(deltas), len(deltas))
            else:
                overflow.extend(deltas)            # thin OR already-"other"
        if len(overflow) >= MIN_DELTAS_PER_STEP:
            out[(pair, "other")] = (_median(overflow), len(overflow))
    return out


# ================================================================== #
# CHUNK 3 — SIGNAL B: benchmark percentile LEVEL per season
# ================================================================== #

# _resolveReference
# Purpose:   The partition's reference season: the most recent season that is
#            also REPRESENTATIVE — clearing both the absolute support bar and
#            REFERENCE_REP_FRACTION of the biggest season's n. v1 ("max
#            season in the data") was fragile — one stray current-year row
#            made a near-empty season the reference; the absolute bar alone
#            still let a mid-year sliver (XC July-2026, 2,855 rows) anchor a
#            partition whose real seasons carry orders of magnitude more.
# Arguments: season_times — {season: array('f')} for one partition.
# Output:    integer season, or None if no season clears both bars.
def _resolveReference(season_times):
    if not season_times:
        return None
    biggest = max(len(arr) for arr in season_times.values())
    bar = max(MIN_RESULTS_PER_SEASON, REFERENCE_REP_FRACTION * biggest)
    eligible = [s for s, arr in season_times.items() if len(arr) >= bar]
    return max(eligible) if eligible else None


# _benchmarkLevels
# Purpose:   Per-season benchmark log-level: the BENCHMARK_PERCENTILE (fast-end)
#            time each season, as a log-ratio to the reference season's. A
#            population marker — different people each year — so no aging leak;
#            its known blind spot is field-depth change.
# Arguments: season_times — {season: array('f')} for one partition.
#            reference    — the season pinned to level 0.
# Output:    {season: (log_level, n_results)} for seasons clearing the support
#            bar; empty dict if the reference itself doesn't (can't anchor).
def _benchmarkLevels(season_times, reference) -> dict:
    pct_time = {}
    for season, arr in season_times.items():
        n = len(arr)
        if n < MIN_RESULTS_PER_SEASON:
            continue
        a   = np.sort(np.asarray(arr, dtype=np.float64))  # ascending: fast first
        idx = _benchmarkIndex(n)                           # was: int(pct*(n-1))
        pct_time[season] = (float(a[idx]), n)

    if reference not in pct_time:
        return {}
    ref_t = pct_time[reference][0]
    # each season's percentile time as a log-ratio to the reference's.
    return {s: (math.log(t / ref_t), n) for s, (t, n) in pct_time.items()}


# ================================================================== #
# CHUNK 4 — THE FUSION: one curve + development rates, solved together
# ================================================================== #
#
# Solved independently per (sport, band, gender). Unknowns: one log-level per
# season PLUS one development rate per grade-transition group. Equation
# flavours:
#   SAME-ATHLETE step :  L[s1] - L[s0] + d_g = step     (difference row: the
#                        observed delta = era difference + that group's
#                        shared development)
#   BENCHMARK level   :  L[s]                = bench[s] (absolute row)
#   ANCHOR            :  L[reference]        = 0        (pins the scale)
#
#     benchmark:   L[2015]=+.006     L[2019]=+.003     L[ref]=0     (dots)
#                      |                 |                |
#     same-ath :       +--(-.029)-->-----+---(-.027)---->+          (gaps,
#                          each gap = era gap + d_g)
#
#   IDENTIFIABILITY (why d_g needs the fusion): with difference rows alone,
#   adding a constant c to every d_g and tilting the curve by -c per year fits
#   every step equation EQUALLY well — a degeneracy, no unique answer. The
#   benchmark's absolute rows pin the curve's actual levels at many seasons,
#   so the tilt is no longer free, so the d_g are forced. Signal A alone
#   cannot tell development from era; the two signals TOGETHER can.

# _seasonUniverse
# Purpose:   Every season any constraint mentions — the curve's nodes.
# Arguments: step_medians — {((s0,s1), gkey): (step, count)}; benchmark;
#            reference.
# Output:    sorted list of integer seasons (column order for the matrix).
def _seasonUniverse(step_medians, benchmark, reference) -> list:
    seasons = {reference}
    for ((s0, s1), _g) in step_medians:
        seasons.add(s0); seasons.add(s1)
    seasons.update(benchmark.keys())
    return sorted(seasons)


# _devUniverse
# Purpose:   Every development group any step bucket mentions — the d_g nodes.
# Arguments: step_medians — {((s0,s1), gkey): (step, count)}.
# Output:    sorted list of gkey strings (column order after the seasons).
def _devUniverse(step_medians) -> list:
    return sorted({g for (_pair, g) in step_medians})


# _buildConstraintRows
# Purpose:   Stack both signals + the anchor into one weighted system. Columns
#            are [seasons..., dev groups...]; difference rows carry +1/-1 on
#            their two seasons AND +1 on their group's d column; absolute and
#            anchor rows never touch the d columns (0 there) — which is
#            exactly WHY the benchmark can pin the curve without being able
#            to absorb development.
# Arguments: seasons      — season node list (column order, first block).
#            dev_keys     — dev-group node list (column order, second block).
#            step_medians — {((s0,s1), gkey): (step, count)} difference
#              constraints.
#            benchmark    — {s: (level, n)} absolute constraints.
#            reference    — the season pinned to 0.
# Output:    (A, b, w) parallel lists — rows, targets, weights. Weights:
#            sqrt(count) for steps; sqrt(n)*TRUST for benchmark; 1e6 anchor.
def _buildConstraintRows(seasons, dev_keys, step_medians, benchmark, reference):
    col  = {s: i for i, s in enumerate(seasons)}                # season cols
    dcol = {g: len(seasons) + i for i, g in enumerate(dev_keys)}  # d_g cols
    gcol = len(seasons) + len(dev_keys)     # ONE development-TREND column (last)
    n = len(seasons) + len(dev_keys) + 1    # +1 for gamma
    A, b, w = [], [], []

    for ((s0, s1), gkey), (step, count) in step_medians.items():
        row = np.zeros(n)
        row[col[s1]] = 1.0
        row[col[s0]] = -1.0
        row[dcol[gkey]] = 1.0        # this bucket's shared BASELINE development
        # TREND: development rate drifts with era. dev for a transition at time t
        # = d_g + gamma*(t - ref). The coefficient is how far the transition's
        # midpoint sits from the reference; gamma (solved) scales it. NOTE: this
        # column is collinear with a QUADRATIC bend of the level curve -- only
        # the benchmark's absolute rows (which leave this column 0, below) pin
        # that bend and make gamma identifiable as development, not era curvature.
        row[gcol] = (s0 + s1) / 2.0 - reference
        A.append(row); b.append(step); w.append(math.sqrt(count))

    for s, (level, n_res) in benchmark.items():
        row = np.zeros(n)
        row[col[s]] = 1.0
        A.append(row); b.append(level)
        w.append(math.sqrt(n_res) * TRUST_BENCHMARK_LEVEL)

    arow = np.zeros(n)
    arow[col[reference]] = 1.0
    A.append(arow); b.append(0.0); w.append(1e6)   # effectively exact

    return A, b, w


# _solveFusedCurve
# Purpose:   Solve the stacked weighted least squares — the actual fusion —
#            and split the solution vector back into its two kinds of unknown.
# Arguments: A, b, w — from _buildConstraintRows. seasons, dev_keys — the node
#            lists (their order defined the columns, so it maps them back).
# Output:    (curve, dev):
#              curve — {season: log_level}, 0 at the reference, negative =
#                      faster than the reference.
#              dev   — {gkey: log_rate}, that transition's shared year-over-
#                      year development (negative = the group improves).
def _solveFusedCurve(A, b, w, seasons, dev_keys):
    A = np.array(A); b = np.array(b); sw = np.sqrt(np.array(w))
    # scaling each row and target by sqrt(weight) makes plain lstsq minimise
    # the WEIGHTED squared error; [:, None] broadcasts across a row's columns.
    coeffs, *_ = np.linalg.lstsq(A * sw[:, None], b * sw, rcond=None)
    curve = {s: float(coeffs[i]) for i, s in enumerate(seasons)}
    dev   = {g: float(coeffs[len(seasons) + i]) for i, g in enumerate(dev_keys)}
    gamma = float(coeffs[len(seasons) + len(dev_keys)])   # the single trend column (last)
    return curve, dev, gamma


# ================================================================== #
# CHUNK 5 — SHAPE (jumpy on purpose; the piecewise upgrade's one home)
# ================================================================== #

# _benchCount
# Purpose:   The support count for a season's SHIPPED level — the number the
#            clamp gates "ship its own value?" on. CRITICAL: this must be the
#            count of the signal that ACTUALLY PRODUCED the level, which differs
#            by sport:
#              - TF  : the percentile pool (bench_p), n = pool size.
#              - XC  : the LADDER, whose per-season finisher count can be far
#                      smaller than the raw percentile pool that XC never uses.
#            Both branches store that n as benchmark[season] = (level, n), and
#            that same n is what the report prints as "bench n". Reading it here
#            makes the clamp and the report agree BY CONSTRUCTION — the old
#            version read len(bench_p[season]) (raw pool), which for XC saw a
#            "solid" pool even when the ladder had only ~10 finishers, so the
#            clamp silently skipped seasons the report showed as thin.
# Arguments: season    — the year to count.
#            benchmark — {season: (level, n)} from _benchmarkLevels or the ladder.
# Output:    the support n (0 if the season isn't in the benchmark dict).
def _benchCount(season, benchmark) -> int:
    entry = benchmark.get(season)           # (level, n) or None
    return entry[1] if entry is not None else 0


# _nearestShippableSeason
# Purpose:   For a SPARSE season, find the nearest season that DOES clear
#            MIN_SHIP_BENCH_N, so it can borrow that neighbour's level.
#            "Nearest" = smallest absolute year-distance (2026 -> 2025).
# Arguments: year     — the sparse season needing a stand-in.
#            solid    — the pre-computed list of seasons that clear the bar.
# Output:    the donor season, or None if NO season clears the bar (degenerate
#            partition — caller then leaves the value untouched).
def _nearestShippableSeason(year, solid):
    if not solid:                            # nothing solid to borrow from
        return None
    return min(solid, key=lambda s: abs(s - year))   # closest by |year distance|


# _donorWithinReach
# Purpose:   The nearest solid season to `year`, but ONLY if it is within
#            MAX_CLAMP_REACH years. This is the SCOPE gate that keeps the clamp
#            to true endpoint slivers: 2026 -> 2025 (reach 1) passes, but a
#            deep-past season whose nearest solid neighbour is many years away
#            (e.g. 1940 -> 1992) is refused, so that season keeps its OWN solved
#            value. Separated from _nearestShippableSeason on purpose: that one
#            answers "who is nearest"; this one answers "is nearest close enough
#            to trust" — two concerns, two helpers.
# Arguments: year  — the sparse season needing a stand-in.
#            solid — the seasons clearing the support bar.
# Output:    the donor season, or None (none exist, or the nearest is too far).
def _donorWithinReach(year, solid):
    donor = _nearestShippableSeason(year, solid)
    if donor is None:                        # no solid season at all
        return None
    if abs(donor - year) > MAX_CLAMP_REACH:  # nearest solid is too distant
        return None                          # -> keep own value (deep past)
    return donor


# _clampSparseSeasons
# Purpose:   Rewrite the solved curve so an under-supported ENDPOINT sliver
#            inherits its IMMEDIATE solid neighbour's level (2026 -> 2025). This
#            is the SHIP-time transform: the solve already ran on the full curve
#            (sparse seasons still constrained the fit); we only change what a
#            caller GETS at inference for a thin trailing/leading year. A sparse
#            season whose nearest solid neighbour is more than MAX_CLAMP_REACH
#            years away keeps its OWN solved value — so the deep past is left
#            intact, not flattened to a distant constant.
#            Also RECORDS every remap it makes, so a caller can log it — the
#            clamp must never move a season silently (house rule).
# Arguments: curve     — {season: level} straight from the solve.
#            benchmark — {season: (level, n)} support counts (ladder n / pool n).
# Output:    (out, remaps):
#              out    — a NEW {season: level}; slivers remapped, everything else intact.
#              remaps — [(season, from_level, donor_season, to_level)], one per
#                       season whose shipped value was changed (empty if none).
def _clampSparseSeasons(curve, benchmark):
    # Precompute the solid set ONCE (seasons clearing the ship bar), so the
    # per-season lookup below is a simple membership/nearest search. Gates on
    # benchmark[season] = (level, n) — the support of the signal that produced
    # the level (ladder n for XC, pool n for TF), matching the report's column.
    solid = [s for s in curve if _benchCount(s, benchmark) >= MIN_SHIP_BENCH_N]
    out, remaps = {}, []
    for season, level in curve.items():
        if _benchCount(season, benchmark) >= MIN_SHIP_BENCH_N:
            out[season] = level              # solid: keep its own solved level
            continue
        donor = _donorWithinReach(season, solid)   # nearest solid, but <= reach
        if donor is None:                    # no CLOSE donor (deep past / none):
            out[season] = level              # keep own value, don't extrapolate
            continue
        out[season] = curve[donor]           # sliver: inherit the neighbour's level
        remaps.append((season, level, donor, curve[donor]))   # record the move
    return out, remaps


# _reportClamps
# Purpose:   Print each shipped-curve remap the clamp made, so nothing moves
#            silently. Isolated in its own helper so _clampSparseSeasons stays a
#            pure transform and _fitEraCurve stays a two-liner.
# Arguments: label  — the partition label, e.g. "XC|xc|F", for the log line.
#            remaps — the [(season, from, donor, to)] list from the clamp.
# Output:    none (prints one line per remap; prints nothing if the list is empty).
def _reportClamps(label, remaps):
    for season, frm, donor, to in remaps:
        print(f"    [clamp] {label} {season} (sparse, n<{MIN_SHIP_BENCH_N}): "
              f"ship {frm:+.4f} -> {to:+.4f} (inherits {donor})")


# _fitEraCurve
# Purpose:   Turn the solved {season: level} into the SHIPPED curve. Its whole
#            job is the solve->ship transform; today that means clamping sparse
#            endpoint seasons to their nearest well-supported neighbour (2026,
#            n=10, inherits 2025), and LOGGING each remap. The solve is
#            untouched — only the shipped values change.
# Arguments: curve     — {season: level} from _solveFusedCurve.
#            benchmark — {season: (level, n)} — the SUPPORT COUNTS the clamp
#                        gates on (ladder n for XC, pool n for TF). Same dict the
#                        report prints, so [clamp] lines match the "bench n" col.
#            label     — the partition label, passed through for the log line.
# Output:    the shipped {season: level}.
def _fitEraCurve(curve, benchmark, label) -> dict:
    shipped, remaps = _clampSparseSeasons(curve, benchmark)
    _reportClamps(label, remaps)             # visible remaps; silent if none
    return shipped


# ================================================================== #
# CHUNK 6 — REPORT: per-partition cross-check + the cross-band placebo
# ================================================================== #

# _sameAthleteOnlyLevels
# Purpose:   Chain the same-athlete steps — DEVELOPMENT-CORRECTED — into
#            per-season levels, so the report shows what that signal says
#            alone once its known dominant leak is subtracted. Raw chaining
#            would just plot cumulative development (~2-5%/yr in this
#            population, dwarfing era); subtracting the solved d_g first makes
#            the residual GAP against the benchmark readable again — it now
#            shows only the contamination the model DIDN'T capture.
#            Mechanics: combine each pair's group buckets into one corrected
#            step (count-weighted mean of median - d_g), then walk the season
#            graph out from the reference, level = parent's level ± step.
# Arguments: step_medians — {((s0,s1), gkey): (step, count)}.
#            dev          — {gkey: log_rate} from the joint solve.
#            reference    — the season pinned to level 0.
# Output:    {season: log_level} for seasons reachable from the reference.
def _sameAthleteOnlyLevels(step_medians, dev, reference, gamma=0.0) -> dict:
    # 1. one corrected step per season pair: count-weighted mean over groups,
    #    removing the FULL development model = baseline d_g PLUS the era trend
    #    gamma*(midpoint - reference). gamma defaults to 0.0 so a caller wanting
    #    the RAW (uncorrected) curve passes dev={} and gamma=0.
    num, den = defaultdict(float), defaultdict(float)
    for (pair, gkey), (step, count) in step_medians.items():
        s0, s1 = pair
        trend = gamma * ((s0 + s1) / 2.0 - reference)      # this transition's drift
        num[pair] += (step - dev.get(gkey, 0.0) - trend) * count
        den[pair] += count
    corrected = {pair: num[pair] / den[pair] for pair in num}

    # 2. chain: seasons = nodes, corrected steps = edges, walk from reference.
    adj = defaultdict(list)
    for (s0, s1), step in corrected.items():
        adj[s0].append((s1, step))            # forward edge adds the step
        adj[s1].append((s0, -step))           # reverse edge subtracts it
    levels = {reference: 0.0}
    stack = [reference]
    while stack:
        s = stack.pop()
        for nbr, step in adj[s]:
            if nbr not in levels:
                levels[nbr] = levels[s] + step
                stack.append(nbr)
    return levels


# _fmtLvl
# Purpose:   Format a log-level for the diagnostic table, or a dash for None
#            (a season not reachable from the reference in one of the curves).
# Arguments: x — a float, or None.
# Output:    a fixed-width signed string, e.g. "+0.0123" / "   -   ".
def _fmtLvl(x):
    return f"{x:+.4f}" if x is not None else "   -   "


# _reportRawVsCorrected
# Purpose:   Decompose the same-athlete signal to see WHERE its wrong-signed
#            drift lives. Reuses _sameAthleteOnlyLevels twice -- once with dev
#            OFF (empty dict -> nothing subtracted -> RAW = era + full
#            development) and once with the solved dev ON (CORRECTED = the
#            same-ath* column = era + residual). "removed" is their difference
#            (the development the model took out); "bench" is the benchmark
#            level for comparison. READ: if CORRECTED drifts away from a stable
#            BENCH, the same-athlete residual is contaminated -- the dev/era
#            split did not hold here (identifiability degeneracy), so XC can't
#            measure its own era and the borrow is justified.
# Arguments: step_medians — {((s0,s1), gkey): (step, count)}.
#            dev          — {gkey: log_rate} from the joint solve.
#            benchmark    — {season: (level, n)} absolute levels.
#            reference    — the season pinned to 0.
# Output:    none (prints).
def _reportRawVsCorrected(step_medians, dev, benchmark, reference, gamma=0.0) -> None:
    raw  = _sameAthleteOnlyLevels(step_medians, {},  reference, 0.0)     # dev+trend OFF -> raw
    corr = _sameAthleteOnlyLevels(step_medians, dev, reference, gamma)   # full model -> corrected
    print("\n    RAW vs DEV-CORRECTED same-athlete "
          "(does CORRECTED track BENCH, or drift off it?):")
    print(f"      {'season':>6}  {'raw':>9}  {'corrected':>10}  "
          f"{'removed':>9}  {'bench':>9}")
    for s in sorted(corr):
        r, c = raw.get(s), corr.get(s)
        removed = (r - c) if (r is not None and c is not None) else None
        b = benchmark.get(s)
        print(f"      {s:>6}  {_fmtLvl(r):>9}  {_fmtLvl(c):>10}  "
              f"{_fmtLvl(removed):>9}  {_fmtLvl(b[0] if b else None):>9}")


# _reportCrossCheck
# Purpose:   Per season: the dev-corrected same-athlete level, the benchmark
#            level, their GAP, the fused level, and the benchmark's support n.
#            The GAP is THE correctness signal — with development now modeled,
#            a residual gap means a leak the model does NOT capture (depth on
#            the benchmark side, or grade-mix effects d_g missed).
# Arguments: curve, step_medians, dev, benchmark, reference — one partition's.
# Output:    none (prints).
def _reportCrossCheck(curve, step_medians, dev, benchmark, reference, gamma=0.0) -> None:
    sa = _sameAthleteOnlyLevels(step_medians, dev, reference, gamma)
    bm = {s: lvl for s, (lvl, _n) in benchmark.items()}
    bn = {s: n for s, (_lvl, n) in benchmark.items()}

    print(f"    {'season':>6}  {'same-ath*':>9}  {'benchmark':>9}  "
          f"{'GAP':>8}  {'fused':>9}  {'bench n':>10}   (*dev-corrected)")
    for s in sorted(set(sa) | set(bm) | set(curve)):
        a, m, fu = sa.get(s), bm.get(s), curve.get(s)
        a_s  = f"{a:+.4f}"  if a  is not None else "   --  "
        m_s  = f"{m:+.4f}"  if m  is not None else "   --  "
        fu_s = f"{fu:+.4f}" if fu is not None else "   --  "
        gap  = f"{(a - m):+.4f}" if (a is not None and m is not None) else "   --  "
        n_s  = f"{bn[s]:,}" if s in bn else "--"
        print(f"    {s:>6}  {a_s:>9}  {m_s:>9}  {gap:>8}  {fu_s:>9}  {n_s:>10}")


# _reportDevRates
# Purpose:   Print the solved development ladder — a free sanity read. The
#            rates should be NEGATIVE (improvement) and shrink with age
#            (9->10 biggest, Jr->Sr smallest). A ladder that isn't roughly
#            decreasing, or a positive rung, means the solve is confounded —
#            distrust the partition's curve until explained.
# Arguments: dev          — {gkey: log_rate} from the joint solve.
#            step_medians — {((s0,s1), gkey): (step, count)} (for support n).
# Output:    none (prints).
def _reportDevRates(dev, step_medians) -> None:
    support = defaultdict(int)
    for (_pair, gkey), (_step, count) in step_medians.items():
        support[gkey] += count
    print(f"    development rates (log/yr; negative = improving):")
    for gkey in sorted(dev):
        d = dev[gkey]
        print(f"      {gkey:>8}: {d:+.4f} ({(math.exp(d) - 1) * 100:+.2f}%/yr, "
              f"n={support[gkey]:,})")


# _shoeJump
# Purpose:   One partition's fused level change across SHOE_WINDOW.
# Arguments: curve — {season: level}.
# Output:    log jump (hi - lo), or None if either endpoint is missing.
def _shoeJump(curve):
    lo, hi = SHOE_WINDOW
    if lo not in curve or hi not in curve:
        return None
    return curve[hi] - curve[lo]


# _reportPlacebo
# Purpose:   THE control read, across partitions: shoe-window jumps in the
#            placebo bands (sprints/short hurdles — true shoe effect ~0 before
#            2021) vs the treatment bands (5K/3200/XC — full effect). Placebo
#            ~0 + treatment negative = the machinery measured shoes, not a
#            leak. A fat placebo jump = depth/aging contamination, caught
#            BEFORE anything is trusted.
# Arguments: fitted — {pkey_string: {"curve":..., "reference":...}}.
# Output:    none (prints).
def _reportPlacebo(fitted) -> None:
    lo, hi = SHOE_WINDOW
    print(f"\n=== PLACEBO CHECK: fused level change {lo}->{hi} "
          f"(negative = got faster) ===")
    for label, bands in (("PLACEBO  (expect ~0)", PLACEBO_BANDS),
                         ("TREATMENT (expect < 0)", TREATMENT_BANDS)):
        print(f"  {label}:")
        for key, art in sorted(fitted.items()):
            band = key.split("|")[1]          # key = "SPORT|band|gender"
            if band not in bands:
                continue
            j = _shoeJump(art["curve"])
            j_s = (f"{j:+.4f} ({(math.exp(j) - 1) * 100:+.2f}%)"
                   if j is not None else "(window not covered)")
            print(f"    {key:>24}: {j_s}")

# _meanOrNone
# Purpose:   Mean of a list, or None for an empty/absent one — keeps the
#            trajectory table's missing-cell handling in a single place.
# Arguments: xs — list of floats, or None.
# Output:    float mean, or None.
def _meanOrNone(xs):
    return float(np.mean(xs)) if xs else None


# _placeboTrajectory
# Purpose:   Show WHEN treatment (distance) pulls away from placebo (sprint),
#            year by year — so SHOE_WINDOW is set from the effect's OBSERVED
#            onset in THIS population, not the elite-adoption dates. SHOE_WINDOW
#            only feeds the placebo readout (never the fitted curve), so a
#            mis-set window mis-reads a fit that may be fine. This table is the
#            evidence for where the window belongs.
#            Per year: mean fused level over placebo bands, mean over treatment
#            bands, and (treatment - placebo). That difference is ~flat while
#            neither effect is active and SHRINKS when foam starts helping
#            distance — the year it starts shrinking is the window's start.
# Arguments: fitted — {pkey_string: {"curve":..., ...}}.
# Output:    none (prints).
def _placeboTrajectory(fitted) -> None:
    placebo_by_year, treat_by_year = defaultdict(list), defaultdict(list)
    for key, art in fitted.items():
        band = key.split("|")[1]                       # key = "SPORT|band|gender"
        if band in PLACEBO_BANDS:
            bucket = placebo_by_year
        elif band in TREATMENT_BANDS:
            bucket = treat_by_year
        else:
            continue
        for season, level in art["curve"].items():
            bucket[season].append(level)               # collect levels per year

    print("\n=== PLACEBO TRAJECTORY (mean fused level/yr; window = where "
          "treat-plac starts shrinking) ===")
    print(f"    {'year':>6}  {'placebo':>9}  {'treat':>9}  {'treat-plac':>10}")
    for y in sorted(set(placebo_by_year) | set(treat_by_year)):
        p = _meanOrNone(placebo_by_year.get(y))
        t = _meanOrNone(treat_by_year.get(y))
        p_s  = f"{p:+.4f}" if p is not None else "   --  "
        t_s  = f"{t:+.4f}" if t is not None else "   --  "
        diff = f"{t - p:+.4f}" if (p is not None and t is not None) else "   --  "
        print(f"    {y:>6}  {p_s:>9}  {t_s:>9}  {diff:>10}")


# ================================================================== #
# CHUNK 7 — PER-PARTITION FIT + PERSIST + ORCHESTRATION
# ================================================================== #

# _fitPartition
# Purpose:   The whole v1 pipeline, applied to ONE (sport, band, gender)
#            partition: reference -> both signals -> fuse -> report.
# Arguments: pkey    — (sport, band, gender) tuple.
#            steps_p — {(s0,s1): [delta,...]} this partition's raw deltas.
#            bench_p — {season: array('f')} this partition's time pools.
# Output:    {"curve", "reference", "dev_rates", "n_steps", "n_bench"} or None
#            if the partition can't support a fit (no anchorable reference, or
#            fewer than 2 constrained seasons) — skipped loudly, never silently.
def _fitPartition(pkey, steps_p, bench_p, bench_override=None):
    label = "|".join(str(p) for p in pkey)
    reference = _resolveReference(bench_p)
    if reference is None:
        print(f"\n--- {label}: SKIPPED (no season clears "
              f"{MIN_RESULTS_PER_SEASON} results — thin band; consider the "
              f"borrow map) ---")
        return None

    step_medians = _stepMedians(steps_p)
    # STAGE B: XC partitions take the LADDER as their absolute signal — the
    # composition-fixed replacement for the population percentile. It is
    # re-anchored to THIS fit's reference; if the ladder never solved that
    # season, the partition is skipped LOUDLY: the absolute rows are what
    # break the d_g degeneracy, and fitting without them returns numbers
    # that satisfy every step equation while being confidently wrong.
    if bench_override is not None:
        benchmark = _reanchorLevels(bench_override, reference)
        if benchmark is None:
            print(f"\n--- {label}: SKIPPED (ladder has no level at "
                  f"reference {reference} — cannot anchor) ---")
            return None
    else:
        benchmark = _benchmarkLevels(bench_p, reference)
    # Split: benchmark_fit drives the SOLVE (bad seasons removed); the full
    # benchmark still goes to the REPORT below, so the excluded garbage stays
    # visible instead of silently disappearing.
    benchmark_fit = {s: v for s, v in benchmark.items()
                     if s not in UNTRUSTED_BENCHMARK_SEASONS}
    seasons      = _seasonUniverse(step_medians, benchmark_fit, reference)
    dev_keys     = _devUniverse(step_medians)
    if len(seasons) < 2:
        print(f"\n--- {label}: SKIPPED (only {len(seasons)} constrained "
              f"season) ---")
        return None

    A, b, w    = _buildConstraintRows(seasons, dev_keys, step_medians,
                                      benchmark_fit, reference)
    curve, dev, gamma = _solveFusedCurve(A, b, w, seasons, dev_keys)

    n_steps = sum(n for _s, n in step_medians.values())
    n_bench = sum(len(a) for a in bench_p.values())
    print(f"\n--- {label}  (reference {reference}; {len(step_medians)} step "
          f"buckets / {n_steps:,} deltas; {n_bench:,} benchmark results) ---")
    _reportDevRates(dev, step_medians)
    print(f"    development TREND gamma = {gamma:+.6f} log/yr^2  "
          f"(+ = dev rate ROSE over time; the drift a constant d_g leaves)")
    _reportCrossCheck(curve, step_medians, dev, benchmark, reference, gamma)
    _reportRawVsCorrected(step_medians, dev, benchmark, reference, gamma)

    return {"curve": _fitEraCurve(curve, benchmark, label), "reference": reference,
            "dev_rates": dev, "dev_trend": gamma,   # audit: ladder + trend
            "n_steps": n_steps, "n_bench": n_bench}


# _saveCurves
# Purpose:   Pickle the banded artifact the way normalize_distance loads it —
#            "kind"-tagged (dispatch on data, not deployment: old single-curve
#            pickles route to the legacy path, this one to the banded path).
# Arguments: fitted — {"SPORT|band|gender": {curve, reference, ...}}; out path.
# Output:    none (writes the file).
def _saveCurves(fitted, out) -> None:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump({"kind": "era_banded",
                     "curves": fitted,
                     "trust_benchmark": TRUST_BENCHMARK_LEVEL,
                     "shoe_window": SHOE_WINDOW}, f)


# _donorKey
# Purpose:   Map a borrower partition key to the key it borrows its era curve
#            from, keeping the SAME gender. None for bands that don't borrow.
# Arguments: key -- a partition key "SPORT|band|gender" (e.g. "XC|xc|F").
# Output:    the donor key (e.g. "TF|m5000|F"), or None.
def _donorKey(key):
    sport, band, gender = key.split("|")            # keys are always 3 fields
    donor = ERA_BORROW_MAP.get((sport, band))       # (donor_sport, donor_band) or None
    if donor is None:
        return None
    return f"{donor[0]}|{donor[1]}|{gender}"         # reattach the SAME gender


# _applyBorrowMap
# Purpose:   Replace each confounded partition's artifact with its donor's, so
#            the SAVED curve is the clean one. Runs AFTER the placebo report (raw
#            XC stays visible as the reason) and BEFORE the pickle is written.
#            Copies the whole donor artifact (curve + its reference year, kept
#            consistent) and tags borrowed_from as an audit trail.
# Arguments: fitted -- {key: artifact}; mutated IN PLACE.
# Output:    none (prints each borrow / skip).
def _applyBorrowMap(fitted):
    for key in list(fitted):                         # list(): we mutate while looping
        donor_key = _donorKey(key)
        if donor_key is None:
            continue                                 # this band measures its own era
        if donor_key not in fitted:                  # donor thin/absent -> don't fake it
            print(f"    borrow SKIPPED: {key} <- {donor_key} (donor not fitted)")
            continue
        fitted[key] = dict(fitted[donor_key],        # copy donor curve+reference ...
                           borrowed_from=donor_key)  # ... plus provenance
        print(f"    era borrow: {key} <- {donor_key}")


# ================================================================== #
# CHUNK 6.5 — STAGE B: XC SELF-MEASUREMENT (ladder + bridge)
# ================================================================== #
# XC's old benchmark was a global percentile, and a percentile moves when
# the POPULATION moves — coverage growth read as era, and because the
# fusion solves d_g jointly, the contamination bled into the same-athlete
# column too: neither printed XC column was an independent witness.
# Two composition-fixed level signals replace it:
#   LADDER  (feeds the solve): per recurring series — the SAME course key
#     Signal A already pairs on — a fixed-rank marker per year, differenced
#     within each series into a graph over seasons (geometry's g1 solve
#     aimed at time). Thousands of series average out per-series prestige
#     drift; their spread is the honest error bar.
#   BRIDGE  (report ONLY, never fed): each athlete's fall XC against their
#     own era-corrected spring 3200/5000. The course-vs-track offset and
#     the fall->spring development are ~constant per pair-year, so they
#     cancel in season differences (the assumed-400 seam trick, aimed at
#     era). Kept OUT of the solve so its agreement with the ladder stays
#     an INDEPENDENT check — the placebo XC never had.

class _StageBCollectors:
    # Purpose:   The three accumulators both instruments need, filled
    #            DURING the existing streams (no second pass over 200M
    #            rows). Memory stays bounded by construction: the ladder
    #            keeps a top-N heap + one count per series-year, never
    #            rows; bridge legs keep (sum, n), never lists.
    def __init__(self):
        self.ladder  = {}   # (pkey, course_key, season) -> [neg-heap, count]
        self.xc_legs = {}   # (aid, season, gender)      -> [sum_log, n]
        self.tf_legs = {}   # (aid, season, band, gender)-> [sum_log, n]

    # collectorFor
    # Purpose:   The one callable _consumeStream is handed per sport.
    def collectorFor(self, sport):
        return self.collectXC if sport == "XC" else self.collectTF

    # collectXC
    # Purpose:   One kept, deduped XC record -> a ladder observation (needs
    #            a resolved course, NOT an identity) and a bridge XC leg
    #            (needs an identity — it pairs across sports).
    # Arguments: rec — a _prepareXCRow record.
    def collectXC(self, rec):
        if rec["event_key"] is not None:
            self._ladderPush(rec)
        if rec["aid"] is not None:
            leg = self.xc_legs.setdefault(
                (rec["aid"], rec["season"], rec["pkey"][2]), [0.0, 0])
            leg[0] += math.log(rec["ntime"]); leg[1] += 1

    # _ladderPush
    # Purpose:   Bounded top-N for one series-year cell. A max-heap of
    #            NEGATED times keeps the N smallest: heap[0] is the worst
    #            time retained, so a faster arrival replaces it in
    #            O(log N) and everything slower is ignored for free.
    # Arguments: rec — the record (its ntime and cell key are read).
    def _ladderPush(self, rec):
        cell = self.ladder.setdefault(
            (rec["pkey"], rec["event_key"], rec["season"]), [[], 0])
        heap = cell[0]
        cell[1] += 1                          # every finisher counts toward
        if len(heap) < LADDER_RETAIN_N:       # the floor, retained or not
            heapq.heappush(heap, -rec["ntime"])
        elif -rec["ntime"] > heap[0]:         # faster than the worst kept
            heapq.heapreplace(heap, -rec["ntime"])

    # collectTF
    # Purpose:   One kept TF record -> a bridge TF leg, admitted only for
    #            the bridge bands with an identity, inside the spring-
    #            outdoor day window (the composition gate — see the
    #            BRIDGE_TF_DOY comment).
    # Arguments: rec — a _prepareTFRow record (carries "doy").
    def collectTF(self, rec):
        if rec["pkey"][1] not in BRIDGE_BANDS or rec["aid"] is None:
            return
        doy = rec.get("doy")
        if doy is None or not (BRIDGE_TF_DOY[0] <= doy <= BRIDGE_TF_DOY[1]):
            return
        leg = self.tf_legs.setdefault(
            (rec["aid"], rec["season"], rec["pkey"][1], rec["pkey"][2]),
            [0.0, 0])
        leg[0] += math.log(rec["ntime"]); leg[1] += 1


# _ladderMarkers
# Purpose:   Ladder cells -> per-pkey, per-series {season: marker}. A cell
#            earns a marker only with LADDER_MIN_FINISHERS in the field
#            (rank K must sit inside a real race). Marker = mean of the K
#            fastest LOG times — log first, then mean: levels live in log
#            space everywhere else in this file.
# Arguments: ladder — the collector's cell dict.
# Output:    ({pkey: {course_key: {season: marker}}}, cells killed by floor).
def _ladderMarkers(ladder):
    series, killed = {}, 0
    for (pkey, ckey, season), (heap, count) in ladder.items():
        if count < LADDER_MIN_FINISHERS:
            killed += 1
            continue
        top = sorted(-x for x in heap)[:LADDER_K]   # un-negate; K fastest
        marker = sum(math.log(v) for v in top) / len(top)
        series.setdefault(pkey, {}).setdefault(ckey, {})[season] = marker
    return series, killed


# _ladderEdges
# Purpose:   One pkey's series -> difference edges: consecutive OBSERVED
#            seasons, gap <= LADDER_MAX_EDGE_GAP. Edges touching untrusted
#            (COVID) seasons are down-weighted rather than dropped —
#            dropping them disconnects the season graph at 2020 and the
#            pre/post halves lose any shared scale.
# Arguments: series_for_pkey — {course_key: {season: marker}}.
# Output:    list of (s0, s1, delta, weight).
def _ladderEdges(series_for_pkey):
    edges = []
    for markers in series_for_pkey.values():
        seasons = sorted(markers)
        for s0, s1 in zip(seasons, seasons[1:]):    # consecutive observed
            if s1 - s0 > LADDER_MAX_EDGE_GAP:
                continue
            w = (LADDER_UNTRUSTED_EDGE_W
                 if (s0 in UNTRUSTED_BENCHMARK_SEASONS
                     or s1 in UNTRUSTED_BENCHMARK_SEASONS) else 1.0)
            edges.append((s0, s1, markers[s1] - markers[s0], w))
    return edges


# _solveDifferenceGraph
# Purpose:   Edges -> per-season levels by weighted least squares on
#            L[s1] - L[s0] = delta, anchored at the newest season (callers
#            re-anchor to the fit's reference). This is geometry's g1
#            machinery aimed at seasons instead of track lengths.
# Arguments: edges — (s0, s1, delta, weight) list.
# Output:    ({season: level}, {season: touching-edge count}), or
#            (None, None) when too thin to constrain anything.
def _solveDifferenceGraph(edges):
    if len(edges) < 2:
        return None, None
    seasons = sorted({s for e in edges for s in (e[0], e[1])})
    col = {s: i for i, s in enumerate(seasons)}
    A, b, w = [], [], []
    support = {s: 0 for s in seasons}
    for s0, s1, delta, weight in edges:
        row = np.zeros(len(seasons))
        row[col[s0]], row[col[s1]] = -1.0, 1.0     # L[s1] - L[s0]
        A.append(row); b.append(delta); w.append(weight)
        support[s0] += 1; support[s1] += 1
    anchor = np.zeros(len(seasons))
    anchor[col[seasons[-1]]] = 1.0                 # pin the newest season
    A.append(anchor); b.append(0.0); w.append(1e6)
    sw = np.sqrt(np.array(w))                      # sqrt-weight rows: plain
    coeffs, *_ = np.linalg.lstsq(np.array(A) * sw[:, None],   # lstsq then
                                 np.array(b) * sw, rcond=None)  # minimises
    return ({s: float(coeffs[i]) for i, s in enumerate(seasons)},  # weighted
            support)                                               # error


# _ladderLevels
# Purpose:   The ladder end to end, per pkey — cells -> markers -> edges ->
#            solved levels — emitted in _benchmarkLevels' EXACT shape
#            ({season: (level, n)}), so the fusion runs byte-identical on
#            either signal. n = edges touching the season (its support).
# Arguments: ladder — the collector's cell dict.
# Output:    {pkey: {season: (level, n)}}; prints one diagnostic line each.
def _ladderLevels(ladder):
    series, killed = _ladderMarkers(ladder)
    out = {}
    for pkey, series_for_pkey in series.items():
        edges = _ladderEdges(series_for_pkey)
        levels, support = _solveDifferenceGraph(edges)
        if levels is None:
            continue
        out[pkey] = {s: (lvl, support[s]) for s, lvl in levels.items()}
        print(f"    [ladder] {'|'.join(map(str, pkey))}: "
              f"{len(series_for_pkey):,} series, {len(edges):,} edges, "
              f"{len(levels)} seasons (cells under floor: {killed:,})")
    return out


# _reanchorLevels
# Purpose:   Shift a levels dict so the fit's reference sits at 0 — the
#            ladder/bridge solved in their own frame; the fusion's anchor
#            row demands L[reference] = 0 in the SAME frame.
# Arguments: levels — {season: (level, n)}; reference — the fit's season.
# Output:    the shifted dict, or None if the reference isn't among the
#            solved seasons (caller must skip loudly, not guess).
def _reanchorLevels(levels, reference):
    if reference not in levels:
        return None
    ref = levels[reference][0]
    return {s: (lvl - ref, n) for s, (lvl, n) in levels.items()}


# _bridgeLevels
# Purpose:   Pair each athlete's fall XC (year Y) with their own spring TF
#            legs (year Y+1), era-correct the TF side by ITS band's fitted
#            curve (log corrected = log ntime - L[year]), and reduce the
#            per-athlete gaps to per-season medians. Offsets that are
#            ~constant per pair (course-vs-track, fall->spring development)
#            survive here and die when the caller re-anchors.
# Arguments: coll — the collectors; fitted — {key: artifact} (TF curves);
#            gender — this XC partition's gender.
# Output:    {season: (median_gap, n_pairs)} for seasons clearing
#            BRIDGE_MIN_PAIRS; {} when no TF curve was available at all.
def _bridgeLevels(coll, fitted, gender):
    gaps = defaultdict(list)
    for (aid, season, g), (xc_sum, xc_n) in coll.xc_legs.items():
        if g != gender:
            continue
        tf_logs = []
        for band in BRIDGE_BANDS:
            leg = coll.tf_legs.get((aid, season + 1, band, g))
            art = fitted.get(f"TF|{band}|{g}")
            if leg is None or art is None:
                continue
            level = art["curve"].get(season + 1)
            if level is None:
                continue                     # no curve node that year
            tf_logs.append(leg[0] / leg[1] - level)
        if tf_logs:
            gaps[season].append(xc_sum / xc_n
                                - sum(tf_logs) / len(tf_logs))
    return {s: (_median(v), len(v)) for s, v in gaps.items()
            if len(v) >= BRIDGE_MIN_PAIRS}


# _reportBridgeAgreement
# Purpose:   XC's placebo table: the ladder (what the solve USED) beside
#            the bridge (what it never saw), both re-anchored to the fitted
#            reference. Agreement is the license to trust XC's curve;
#            structured disagreement is the stop sign before --fit.
# Arguments: coll, fitted, ladder_by_pkey — as built in runEraCorrection.
# Output:    none (prints; degrades to a note when TF curves are absent).
def _reportBridgeAgreement(coll, fitted, ladder_by_pkey):
    print("\n=== XC LADDER vs BRIDGE (independent agreement = XC's placebo) ===")
    printed = False
    for pkey, ladder in sorted(ladder_by_pkey.items()):
        key = "|".join(map(str, pkey))
        art = fitted.get(key)
        if art is None or "borrowed_from" in art:
            continue
        reference = art["reference"]
        lad = _reanchorLevels(ladder, reference)
        raw = _bridgeLevels(coll, fitted, pkey[2])
        bridge = _reanchorLevels(raw, reference) if raw else None
        if lad is None or bridge is None:
            print(f"    {key}: bridge unavailable (no TF curves this run, "
                  f"or too few pairs at the reference)")
            continue
        printed = True
        print(f"\n    {key}  (reference {reference}; both re-anchored)")
        print(f"    {'season':>8} {'ladder':>9} {'bridge':>9} "
              f"{'diff':>9} {'pairs':>7}")
        for s in sorted(set(lad) & set(bridge)):
            l, b = lad[s][0], bridge[s][0]
            mark = "  <- untrusted" if s in UNTRUSTED_BENCHMARK_SEASONS else ""
            print(f"    {s:>8} {l:>+9.4f} {b:>+9.4f} {l - b:>+9.4f} "
                  f"{bridge[s][1]:>7,}{mark}")
    if not printed:
        print("    (no XC partition with both instruments available)")


# runEraCorrection
# Purpose:   The whole step: stream each sport -> fit every partition ->
#            placebo table -> (only with --fit) persist. MEASURES by default.
# Arguments: do_fit — fit + write when True, else measure+report only.
#            out    — output pickle path.
#            sports — list of sports to run ("XC", "TF").
# Output:    the fitted dict if do_fit, else None.
def runEraCorrection(do_fit=False, out=DEFAULT_OUT, sports=("XC", "TF"),
                     use_cache=True):
    timer = _PhaseTimer()                         # <-- one timer for the run
    fitted = {}
    coll = _StageBCollectors()                    # Stage B: filled in-stream
    ladder_by_pkey = {}                           # {} until XC has streamed
    for sport in sports:
        # Cache seam: on a fresh hit this rebuilds steps/bench and RESTORES the
        # Stage-B collector in ~seconds; on a miss/stale/--fresh it runs the real
        # loadSport (filling coll in-stream) and writes the cache. loadSport and
        # the per-sport callback are injected so era_cache needs no import back.
        steps, bench = loadSportCached(
            sport, timer, coll,
            load_fn=loadSport,
            collect_fn=coll.collectorFor(sport),
            use_cache=use_cache)
        if sport == "XC":                         # the ladder solves right
            ladder_by_pkey = _ladderLevels(coll.ladder)   # after XC streams
        for pkey in sorted(set(steps) | set(bench)):
            # XC partitions get the ladder as their absolute signal; TF
            # partitions keep the percentile benchmark unchanged.
            override = ladder_by_pkey.get(pkey) if pkey[0] == "XC" else None
            with timer.phase("fit"):              # <-- time all the solving
                art = _fitPartition(pkey, steps.get(pkey, {}),
                                    bench.get(pkey, {}),
                                    bench_override=override)
            if art is not None:
                fitted["|".join(str(p) for p in pkey)] = art
    # The bridge report needs the TF curves, so it runs after BOTH sports
    # have fitted — which is also why no sport-order flip was needed: the
    # bridge never feeds the solve, it only judges it.
    _reportBridgeAgreement(coll, fitted, ladder_by_pkey)
    _reportPlacebo(fitted)
    print("\n=== ERA BORROW MAP (confounded bands take a clean donor's curve) ===")
    _applyBorrowMap(fitted)                       # after placebo (raw XC visible), before save
    timer.report()                                # <-- WHERE the time went

    if not do_fit:
        print("\nMEASURE-only. Read (1) each partition's GAP column, (2) the "
              "placebo table. Placebo bands jumping = contamination — retune "
              "TRUST_BENCHMARK_LEVEL before --fit.")
        return None

    _saveCurves(fitted, out)
    print(f"\nbanded era curves ({len(fitted)} partitions) written -> {out}")
    return fitted


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Banded era correction: per-(sport, band, gender) fused "
                    "curves. Measure (default) or --fit.")
    parser.add_argument("--fit", action="store_true",
                        help="fit and pickle the banded curves "
                             "(default: measure+report only)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="output pickle path")
    parser.add_argument("--sport", choices=["XC", "TF", "both"],
                        default="both",
                        help="which sport(s) to run (default: both)")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore the stream cache and re-stream from the DB "
                             "(the cache also auto-invalidates when the geometry "
                             "or distance pickle changes)")
    args = parser.parse_args()

    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)
    runEraCorrection(do_fit=args.fit, out=args.out, sports=sports,
                     use_cache=not args.fresh)