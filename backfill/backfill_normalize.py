# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/backfill_normalize.py  (unified XC + TF; supersedes the two
#          old per-sport backfills)
# Purpose: Recompute normalized_time for EVERY result by running the current
#          normalize_distance chain (distance -> geometry -> era) over the raw
#          rows and writing the value back. One engine, parameterised per sport.
#
#   WHY ONE FILE: the old design was two near-duplicate scripts. A fix to the XC
#   one never reached the TF one, so TF rotted (SQLite leftovers, undefined
#   names, a wrong unpack). Collapsing the shared machinery into a single runner
#   parameterised by SportConfig means each bug has exactly ONE home.
#
#   WHY GEOMETRY + ERA ARE THREADED NOW: the old backfills called the 4-arg
#   normalizeResult, so geometry and era silently no-op'd (every optional arg
#   defaulted to None). Re-running the backfill EXISTS to make those corrections
#   fire, so we thread date + track geometry + sport + event_short per row.
#
#   RESUME POLICY (owner decision): UNCONDITIONAL REWRITE. Every row already
#   holds a stale normalized_time, so `WHERE normalized_time IS NULL` would match
#   nothing. We drop that guard and rewrite all rows. Consequence, stated plainly:
#   this run is NOT resume-safe — an interrupted run must restart from the top.
#   The PK-cursor paging stays (it's how we stream 100M+ rows in bounded memory),
#   not for resumability but for a stable, index-driven scan order.
#
#   DRY-RUN BY DEFAULT.  Apply:
#       python scripts/backfill_normalize.py --sport XC   --apply
#       python scripts/backfill_normalize.py --sport TF   --apply
#       python scripts/backfill_normalize.py --sport both --apply   # XC then TF

import io                             # StringIO buffer for the COPY payload
import cProfile                       # --profile: find the hot function
import pstats                         # --profile: format the report
import sys
import time
import math                           # ln() for the distance-aware pace floor
import re                             # the wheelchair/seated title pattern
import heapq                          # fixed-size top/bottom-N heaps (sanity panel)
import random                         # bounded reservoir sample (approx percentiles)
import argparse
import concurrent.futures as cf       # the index builds, several at a time
from dataclasses import dataclass, field
from datetime import date            # date.fromisoformat parses the text date col

# engine/ holds normalize_distance; scripts/ holds database. Insert both so the
# imports below resolve regardless of where python is launched from.
sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

import psycopg2.errors           # LockNotAvailable, for the swap retry
import psycopg2.extras
from database import getConn, initPool, dbJobs, dbSetting
# poolFor + metersFromDistance are the SINGLE SOURCE OF TRUTH for pool + distance
# classification (the fitters import the same two). We import them so the backfill
# can name WHY a row is skipped (unknown pool vs no distance) WITHOUT re-deriving
# any of that logic here — it just calls the same functions the library uses.
from normalize_distance import (
    weatherTempAgg,
    normalizeResult, EVENT_DISTANCES_TF, poolFor, normPoolFor, metersFromDistance,
)
# results_tf.event_short is FREE TEXT: 64,079 distinct values across two scraper
# conventions ('3200m' vs "Men's 3200 Meters"). The exact-match dict priced only
# the 12 anet short codes, silently dropping 7,157,445 timed tfrrs distance rows
# as `no_distance`. event_parse tries the dict first, then parses. It also
# recovers the gender tfrrs TF rows lack, from the event name (20,027,808 rows).
from event_parse import distanceFromEventShort
from result_status import isSentinelTime as _statusSentinel


# ================================================================== #
# SKIP REASONS  —  the four (mutually exclusive) causes a row is held
#                  back. Named ONCE so counters and prints can't drift.
#                  Order here == the order they are TESTED in _classifyRow;
#                  a row is charged to the FIRST cause it trips, never two.
# ================================================================== #

# String tags for the reason census. A plain class (not an Enum) so the values
# ARE their own printable labels — no .value unwrapping in format strings.
# WRITTEN is the non-skip outcome, included so one census dict counts every row.
#
class _SkipReason:
    SENTINEL_TIME   = "sentinel_time"    # time is None or a DNF/DNS/DQ sentinel
    MANUAL_DROP     = "manual_drop"      # hand-confirmed cooked individual row
                                         # (real division/distance, fake result)
                                         #  listed in _RESULT_DROP by result_id.
    DEDUP_TWIN      = "dedup_twin"       # tfrrs copy of a result that also exists
                                         # in anet at the same canon-linked meet
                                         #  (same person_id + canon_meet_id). The
                                         #  anet twin is normalized; this one is
                                         #  dropped so the physical result is
                                         #  counted ONCE.
    WHEELCHAIR      = "wheelchair"       # wheelchair/seated division or event. A
                                         # racing chair covers distance far faster
                                         #  than a runner, so a normalized "time"
                                         #  is meaningless on the running scale
                                         #  and the athlete minted ~147 in a youth
                                         #  pool. Nuked HERE (owner's call,
                                         #  2026-08-27) so no normalized_time
                                         #  exists for the engine, the fill, the
                                         #  boards or the model to price. The
                                         #  engine's own SQL filter on
                                         #  meets.division covered anet XC only;
                                         #  fill_ratings then priced what the
                                         #  engine refused, which is how chairs
                                         #  reached the boards.
    NO_DISTANCE     = "no_distance"      # no event distance resolvable for the row
    INSANE_DISTANCE = "insane_distance"  # distance outside [MIN,MAX] — corrupt
                                         # (e.g. the *1609.344 double-conversion
                                         #  monsters, and the ~1,685 other
                                         #  over-ceiling corruptions the
                                         #  diagnostic found). NOT repairable in
                                         #  bulk (only ~157 decode cleanly), so
                                         #  we DROP rather than special-case.
    INSANE_PACE     = "insane_pace"      # distance sane but the implied pace is
                                         # physically impossible (the 15-hour
                                         #  "mile" slow-tail: garbage TIME on a
                                         #  real distance).
    INSANE_RAW      = "insane_raw"       # raw time faster than the world record
                                         # for its distance+gender — impossible
                                         #  for anyone, so corruption. Caught
                                         #  BEFORE normalization (a fake-fast raw
                                         #  time is the ROOT of the fake-elite
                                         #  normalized values).
    INSANE_DATE     = "insane_date"      # date parsed but sits outside the sane
                                         # year window (e.g. year 2222) — a marker
                                         #  of synthetic/corrupt rows (meet 233746:
                                         #  0.01s-incrementing times, future date).
                                         #  A merely ABSENT date does NOT trip this.
    UNKNOWN_POOL    = "unknown_pool"     # grade+gender+source -> no competitive pool
    LIBRARY_DROP    = "library_drop"     # normalizeResult itself returned drop/None
    WRITTEN         = "written"          # produced a value (not a skip; here for totals)

# The SKIP tags in test order, for a stable, complete census printout.
# Distance/pace guards run AFTER no_distance (a distance must exist to judge it)
# and BEFORE unknown_pool (a corrupt row shouldn't even reach pool classification).
_SKIP_ORDER = [
    _SkipReason.MANUAL_DROP,
    _SkipReason.DEDUP_TWIN,
    _SkipReason.SENTINEL_TIME,
    _SkipReason.WHEELCHAIR,
    _SkipReason.NO_DISTANCE,
    _SkipReason.INSANE_DISTANCE,
    _SkipReason.INSANE_PACE,
    _SkipReason.INSANE_RAW,
    _SkipReason.INSANE_DATE,
    _SkipReason.UNKNOWN_POOL,
    _SkipReason.LIBRARY_DROP,
]


# ================================================================== #
# SANITY-PANEL DATA STRUCTURES
#   _TopBucket : the N most extreme (smallest OR largest) normalized
#                times seen, with a traceback tuple each, in O(N) memory.
#   _Reservoir : a bounded uniform sample of kept values, so percentiles
#                are APPROXIMATE but memory is capped (not 31.8M floats).
# ================================================================== #

# Purpose : keep the N most-extreme (value, trace) pairs from a stream WITHOUT
#           holding the whole stream. A binary heap gives O(log N) inserts and
#           O(N) memory regardless of how many rows flow through.
#
# HOW the "keep smallest" vs "keep largest" duality works with ONE min-heap:
#   Python's heapq is a MIN-heap: heap[0] is always the smallest key.
#   - To keep the N SMALLEST values, we must be able to evict the CURRENT
#     LARGEST when a new smaller one arrives. A min-heap's cheap end is the
#     smallest, which is the wrong end. So we store keys NEGATED: the most
#     negative key (= the largest real value) sits at heap[0] ready to evict.
#   - To keep the N LARGEST, the natural min-heap is already right: heap[0]
#     is the smallest kept, evicted when something bigger comes.
#   `self._sign` (+1 for largest, -1 for smallest) flips every key on the way
#   in and back out, so the same push/evict code serves both modes.
#
# Fields:
#   _n     : capacity (how many extremes to retain).
#   _sign  : +1 keep-largest, -1 keep-smallest (negates keys for the min-heap).
#   _heap  : list of (signed_value, tiebreak, trace) tuples, managed by heapq.
#   _count : monotonic counter used ONLY as a tiebreak so two equal values
#            never force Python to compare the (unorderable) trace tuples.
#
class _TopBucket:

    def __init__(self, n, largest):
        self._n = n
        self._sign = 1 if largest else -1          # +1 largest, -1 smallest
        self._heap = []                            # heapq operates on this list
        self._count = 0                            # strictly-increasing tiebreak

    # Consider one (value, trace) for retention.
    # signed = value * sign  -> a min-heap on `signed` keeps the right extreme.
    # Push first; if we now hold more than N, pop the min `signed` (that's the
    # least-extreme kept item) so the bucket never exceeds capacity N.
    #
    def offer(self, value, trace):
        signed = value * self._sign
        self._count += 1
        heapq.heappush(self._heap, (signed, self._count, trace))
        if len(self._heap) > self._n:
            heapq.heappop(self._heap)              # drop the least-extreme kept

    # Return [(value, trace), ...] most-extreme FIRST, for printing.
    # We undo the sign to recover real values, then sort so the headline
    # (most suspicious) row prints at the top. reverse=True with real values
    # gives largest-first; for the smallest bucket we want smallest-first, so
    # we key on value*sign (ascending signed == most-extreme first) instead.
    #
    def sorted_items(self):
        items = [(sv * self._sign, tr) for (sv, _cnt, tr) in self._heap]
        # Key = value*sign is LARGER for the more-extreme end in both modes
        # (largest-mode: bigger value; smallest-mode: -value, bigger when value
        # is smaller). Sorting DESCENDING on it puts most-extreme FIRST for both.
        items.sort(key=lambda vt: vt[0] * self._sign, reverse=True)
        return items


# Purpose : hold a uniform random sample of at most `cap` kept values, so we can
#           report APPROXIMATE percentiles (p25/median/p75) without buffering all
#           ~31.8M values. Classic Algorithm R reservoir sampling: every value
#           seen has an equal chance of being in the final sample.
#
# HOW Algorithm R works (one line of probability):
#   Keep the first `cap` values outright. For the k-th value after that
#   (k > cap), keep it with probability cap/k, and if kept, overwrite a random
#   existing slot. This invariant holds the sample uniform at every point.
#
# Fields:
#   _cap  : sample capacity.
#   _buf  : the retained sample (a plain list of floats).
#   _seen : how many values have been offered so far (the k above).
#
class _Reservoir:

    def __init__(self, cap):
        self._cap = cap
        self._buf = []
        self._seen = 0

    # Offer one value; O(1). See the class docstring for the R invariant.
    def offer(self, value):
        self._seen += 1
        if len(self._buf) < self._cap:             # phase 1: fill the reservoir
            self._buf.append(value)
            return
        j = random.randint(1, self._seen)          # phase 2: keep w.p. cap/seen
        if j <= self._cap:
            self._buf[j - 1] = value               # evict a uniform-random slot

    # Approximate percentiles for the fractions in `qs` (e.g. [0.25, 0.5, 0.75]).
    # Sort the sample ONCE, then index by nearest-rank. Returns {q: value}, or an
    # empty dict if nothing was sampled (all rows skipped).
    #
    def percentiles(self, qs):
        if not self._buf:
            return {}
        s = sorted(self._buf)
        out = {}
        for q in qs:
            idx = min(len(s) - 1, int(q * len(s)))  # nearest-rank, clamped
            out[q] = s[idx]
        return out


# ================================================================== #
# CONFIG  —  one SportConfig row captures EVERY XC/TF difference, so
#            the loop body below never branches on sport again.
# ================================================================== #

@dataclass
# Purpose : hold every way the two sports differ, so _runBackfill is one body.
# Fields  :
#   sport        : "XC" | "TF"  — threaded into normalizeResult (era band + geom leg)
#   table        : results table to READ and UPDATE ('results' / 'results_tf')
#   has_event    : does the table carry event_short? (TF yes, XC no)
#   distance_src : 'meets_column' (XC: meets.distance by div_id)
#                  | 'event_short' (TF: EVENT_DISTANCES_TF by event string)
#   batch        : rows per page / per write round trip
#
class SportConfig:
    sport: str
    table: str
    has_event: bool
    has_school: bool          # does the table carry `school`? (both: confirmed)
    distance_src: str
    batch: int = 50_000
    #   write_mode : "copy"   -> COPY to staging, then rebuild the table once.
    #                "update" -> the original per-batch UPDATE. See THE WRITE.
    write_mode: str = "copy"


# Return the SportConfig for 'XC' or 'TF'. One source of truth for knobs.
def _configFor(sport, write_mode="copy"):
    table = {"XC": "results", "TF": "results_tf"}[sport]
    return SportConfig(
        sport=sport,
        table=table,
        write_mode=write_mode,
        has_event=(sport == "TF"),
        # XC's `results` definitely has school (the school-level map is built
        # from it). Set TF to True once results_tf.school is confirmed to exist.
        # CONFIRMED by information_schema: results_tf.school exists and is 100%
        # populated (20,131,757 tfrrs TF rows). TF had the SAME bug XC did --
        # every tfrrs TF row pooled college, judged against the loosest floor.
        has_school=True,
        distance_src=("event_short" if sport == "TF" else "meets_column"),
    )


# ================================================================== #
# DB PLUMBING
# ================================================================== #

# _assertDistinctBackends
# Purpose : PROVE, before a single row is read, that the read and write
#           connections are two DIFFERENT Postgres backend processes.
# Why     : a server-side (named) cursor's portal is owned by its TRANSACTION.
#           If both handles are one backend, the write side's COMMIT destroys the
#           read stream mid-scan -> "named cursor isn't valid anymore".
#           `is not` is NOT enough: a pool can hand back the same physical
#           connection wrapped in two Python objects. The BACKEND PID is ground
#           truth -- one PID == one transaction == one portal lifetime.
# Syntax  : get_backend_pid() is a psycopg2 connection method returning the int
#           PID of the server process on the far end of this socket.
# Raises  : RuntimeError, deliberately NOT `assert` -- `python -O` strips asserts,
#           and this invariant must hold in production too.
def _assertDistinctBackends(read_conn, write_conn):
    read_pid = read_conn.get_backend_pid()
    write_pid = write_conn.get_backend_pid()
    if read_pid == write_pid:
        raise RuntimeError(
            f"read and write connections share backend PID {read_pid}; "
            f"the first commit would kill the server-side cursor")


# _closeStreamQuietly
# Purpose : release the server-side portal WITHOUT ever raising.
# Why     : closing a named cursor issues `CLOSE <name>`, which trips the SAME
#           validity guard as fetching. If the cursor is already dead, close()
#           raises -- and because it runs inside a `finally`, that second
#           exception REPLACES the original one in the traceback. We swallow it
#           so the real error survives.
# Syntax  : psycopg2.Error is the base of ProgrammingError/InterfaceError/etc.
#           We catch that, never a bare `except:` -- a bare except would also eat
#           KeyboardInterrupt and GeneratorExit.
def _closeStreamQuietly(stream):
    try:
        stream.close()
    except psycopg2.Error:
        pass                                   # portal already gone; nothing to free


# _releaseReadLocks
# Purpose : END the read connection's transaction, so it stops holding locks.
# Why this is NOT optional, and why closing the cursor is not enough:
#   `cursor.close()` on a named cursor issues `CLOSE portal`. That frees the
#   portal. It does NOT end the transaction -- the connection sits in
#   `idle in transaction`, and a transaction that has READ a table holds an
#   ACCESS SHARE lock on it until it commits or rolls back.
#   The merge then runs `ALTER TABLE results RENAME TO results_old`, which needs
#   ACCESS EXCLUSIVE. ACCESS SHARE and ACCESS EXCLUSIVE conflict. The write
#   connection waits on a lock held by the read connection, in the SAME PROCESS,
#   forever. Observed live: 30 minutes of `wait=Lock`.
#   Ironic and worth remembering: the two-connection design that fixed the
#   "named cursor isn't valid anymore" crash is exactly what enables this
#   deadlock. Two backends cannot see each other's locks as their own.
# rollback() not commit(): the read side wrote nothing. Rollback is the cheaper
#   and more honest way to say "this transaction is over".
def _releaseReadLocks(read_conn):
    try:
        read_conn.rollback()
    except psycopg2.Error:
        pass                                   # connection already dead; nothing held


# ================================================================== #
# GEOMETRY INDEX  —  bulk-load ONCE into dicts, then O(1) lookups.
#                    Two grains: anet event-level, tfrrs meet-level.
# ================================================================== #
# WHY preload instead of join-per-batch: geometry is read for every one of
# 100M+ rows. Loading the (few million) geometry rows once and doing dict
# lookups avoids re-joining meets_tf on every page. Two small helpers build
# the two grains; a tiny class routes between them by source.

# Purpose : anet geometry at the EVENT grain, keyed (meet_id, div_id, event_id).
# Output  : { (meet_id, div_id, event_id) : (track_type, track_length) }.
# Only rows carrying some geometry are loaded; a miss later -> reference (no-op).
#
def _loadAnetGeometry(cur):
    cur.execute("""
        SELECT meet_id, div_id, event_id, track_type, track_length
        FROM meets_tf
        WHERE source = 'anet'
          AND (track_type IS NOT NULL OR track_length IS NOT NULL)
    """)
    return {(m, d, e): (tt, tl) for m, d, e, tt, tl in cur}


# Purpose : tfrrs geometry at the MEET grain, keyed meet_id only (tfrrs results
#           carry NULL div_id/event_id, so meet_id is the only usable key).
# Output  : { meet_id : (track_type, track_length) }.
#
def _loadTfrrsGeometry(cur):
    cur.execute("""
        SELECT meet_id, track_type, track_length
        FROM tfrrs_meet_geometry
        WHERE sport = 'TF'
    """)
    return {m: (tt, tl) for m, tt, tl in cur}


# Purpose : hold both grains and resolve geometry for a row by its source.
# Usage   : idx = GeometryIndex.build(cur);  tt, tl = idx.lookup(source, meet, div, event)
#
class GeometryIndex:

    def __init__(self, anet, tfrrs):
        self._anet = anet        # (meet,div,event) -> (type, length)
        self._tfrrs = tfrrs      # meet -> (type, length)

    @classmethod
    # Run the two bulk loads (one query each) and wrap them.
    def build(cls, cur):
        return cls(_loadAnetGeometry(cur), _loadTfrrsGeometry(cur))

    # Return (track_type, track_length) for a row, or (None, None) on a miss.
    # (None, None) -> normalize_distance treats it as the 400m-flat reference,
    # i.e. a clean geometry no-op ("no basis to correct").
    #
    def lookup(self, source, meet_id, div_id, event_id):
        if source == "tfrrs":
            return self._tfrrs.get(meet_id, (None, None))
        # anet (and any anet-shaped source): event-grain key
        return self._anet.get((meet_id, div_id, event_id), (None, None))


# ================================================================== #
# WEATHER INDEX  —  per-race weather for the correction (the last chain link).
# Built ONLY when the per-sport artifact exists (else the heavy weather-grid
# aggregation is skipped and weather is a clean no-op, like a missing pickle).
# ================================================================== #

import os as _os
import pickle as pickle

_WEATHER_ART = _os.path.join(_os.path.dirname(__file__), "..", "engine", "data",
                             "weather_correction_{sport}.pkl")

# Local race window is read FROM the artifact (race_local_hours) so the apply
# window can never drift from the fit window. Grid must match the fitter.
_WX_GRID = 0.25


# _snapCell
# Purpose : snap a (lat, lon) to its weather_grid cell EXACTLY as the backfill
#           stored it -- snap to the grid, THEN wrap longitude into 0..360
#           (Python % gives a positive result for negative lons, matching numpy).
#           Rounded to 2 dp so the float key matches the stored real column.
# Output  : (cell_lat, cell_lon) tuple, or None if either coord is missing.
def _snapCell(lat, lon):
    if lat is None or lon is None:
        return None
    clat = round(round(float(lat) / _WX_GRID) * _WX_GRID, 2)
    lon_snapped = round(float(lon) / _WX_GRID) * _WX_GRID
    clon = round(lon_snapped % 360, 2)                  # snap-then-wrap (as stored)
    return (clat, clon)


# _loadWxDict
# Purpose : one race-window weather summary per (cell, date), matching the
#           fitter's wx CTE (avg temp/wind/soil, sum precip, depth+snowfall snow;
#           local hour = UTC + round(signed_lon/15)). `hours` is the (lo, hi)
#           LOCAL window read from the artifact, so it equals the fit window.
# Output  : { (cell_lat, cell_lon, "YYYY-MM-DD") : (temp, wind, precip, soil, snow) }.
def _wxSql(hours, temp_agg):
    lo, hi = hours
    signed = "(CASE WHEN cell_lon > 180 THEN cell_lon - 360 ELSE cell_lon END)"
    local = f"mod(mod(hour + round({signed} / 15.0)::int, 24) + 24, 24)"
    return f"""
        SELECT cell_lat, cell_lon, date,
               {temp_agg}, avg(wind_speed_10m),
               sum(precipitation), avg(soil_moisture),
               avg(snow_depth) + coalesce(sum(snowfall), 0)
        FROM   weather_grid
        WHERE  {local} BETWEEN {lo} AND {hi}
        GROUP  BY cell_lat, cell_lon, date
    """


def _loadWxDict(cur, hours, temp_agg="avg(apparent_temperature)"):
    cur.execute(_wxSql(hours, temp_agg))
    wx = {}
    for clat, clon, d, temp, wind, precip, soil, snow in cur:
        key = (round(float(clat), 2), round(float(clon), 2), d.isoformat())
        wx[key] = (temp, wind, precip, soil, snow)
    return wx


# ★ THE AGGREGATE IS CACHED BETWEEN RUNS (2026-09-26, owner: "speed up every
#   step in pipeline possible"). _loadWxDict groups the WHOLE of weather_grid
#   every run, in both backfills, behind a local-hour filter no index can
#   serve -- and between two weekly runs the grid usually has not changed at
#   all. The dict is pickled per sport and reused only while its KEY matches:
#
#     * the query text itself -- which carries race_local_hours and the
#       temperature aggregate from the artifact, so a refit that moves the
#       window or the aggregate misses; so does any edit to the SQL;
#     * _WX_CACHE_VERSION -- bump it when the Python side of _loadWxDict
#       (the key rounding, the value tuple) changes;
#     * a signal that weather_grid's ROWS have not changed, _wxGridSignal.
#
# ! THE SIGNAL IS THE TABLE'S OWN WRITE COUNTERS, NOT count(*) + max(date).
#   Those two cost a full scan -- the thing being avoided -- and they miss
#   the write that matters most: recompute_apparent_temp.py UPDATEs every
#   apparent_temperature in place, and neither number moves. So:
#     n_tup_ins / n_tup_upd / n_tup_del (pg_stat_user_tables) -- every row
#       inserted, updated or deleted since the counters began (checked on
#       PG16: an ON CONFLICT DO NOTHING skip does not count, a rolled-back
#       insert does -- a needless rebuild, never a stale hit);
#     the table's oid and pg_relation_filenode -- a drop/recreate, TRUNCATE,
#       VACUUM FULL or CLUSTER, none of which move those counters;
#     pg_stat_database.stats_reset and pg_postmaster_start_time() -- the
#       counters restart from zero after pg_stat_reset(), a single-table
#       reset (it stamps the database's stats_reset, checked on PG16), a
#       crash or a restart, and could otherwise climb back to the same
#       numbers over different rows. Any restart costs one rebuild.
#   With track_counts off the counters do not move at all, so the cache is
#   not used.
#
# ⚠ THE ONE WINDOW: a backend's counts reach the shared stats within ~10 s
#   of its commit, or at once when it exits. A write committed seconds
#   before this reads the signal, from a session still open, can be missed.
#   The pipeline's writer (04e) is a finished process by 05, so this
#   matters only to a hand-run writer racing a backfill -- and that race
#   gives a stale answer without the cache too. The signal is read BEFORE
#   the aggregate, so a write landing between the two makes the next run's
#   key differ: stale in the safe direction only.
#
# ! XC and TF each have their own file (their windows differ: 8-12 vs 9-20
#   local), so the two backfills running side by side never share one; the
#   write is tmp + os.replace, so a reader never sees half a pickle. Any
#   failure to read or write the cache falls back to the query.
_WX_CACHE = _os.path.join(_os.path.dirname(__file__), "..", "engine", "data",
                          "backfill_wx_{sport}.pkl")
_WX_CACHE_VERSION = 1


def _wxGridSignal(cur):
    """weather_grid's change signal (see above), or None when the counters
    cannot vouch for it and the aggregate must run."""
    cur.execute("""
        SELECT c.oid::bigint, pg_relation_filenode(c.oid)::bigint,
               s.n_tup_ins, s.n_tup_upd, s.n_tup_del,
               d.stats_reset::text, pg_postmaster_start_time()::text,
               current_setting('track_counts')
        FROM   pg_class c
        JOIN   pg_stat_user_tables s ON s.relid = c.oid
        JOIN   pg_stat_database d ON d.datname = current_database()
        WHERE  c.oid = to_regclass('weather_grid')
    """)
    row = cur.fetchone()
    if row is None or row[-1] != "on":
        return None
    return tuple(row[:-1])


def _loadWxDictCached(cur, sport, hours, temp_agg):
    path = _WX_CACHE.format(sport=sport)
    sig = _wxGridSignal(cur)                  # BEFORE the aggregate -- see above
    key = (_WX_CACHE_VERSION, " ".join(_wxSql(hours, temp_agg).split()), sig)
    if sig is not None:
        try:
            with open(path, "rb") as f:
                saved = pickle.load(f)
            if saved.get("key") == key:
                print(f"  weather: grid aggregate reused from {path} "
                      f"(weather_grid unchanged since it was built)")
                return saved["wx"]
        except FileNotFoundError:
            pass
        except Exception as exc:                          # noqa: BLE001
            print(f"  weather: cache unreadable ({type(exc).__name__}); "
                  f"rebuilding it")
    t0 = time.time()
    wx = _loadWxDict(cur, hours, temp_agg)
    print(f"  weather: grid aggregate built in {time.time() - t0:.1f}s")
    if sig is None:
        print("  weather: no trustworthy change signal on weather_grid "
              "(track_counts off?) -- not cached")
        return wx
    tmp = f"{path}.{_os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            pickle.dump({"key": key, "wx": wx}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        _os.replace(tmp, path)
    except OSError as exc:
        print(f"  weather: could not write the cache {path} ({exc}); "
              f"the run is unaffected")
        try:
            _os.remove(tmp)
        except OSError:
            pass
    return wx


# Per-source meet -> (cell_lat, cell_lon, course_key). XC anet from meets (one row
# per meet_id -- meets fans out per division); XC tfrrs from meets_tfrrs. TF anet
# from meets_tf_meta, OUTDOOR ONLY (indoor tracks are climate-controlled -> outside
# weather is meaningless; the fitter excludes them too). course_key is used ONLY
# for per-course mud muting; an unknown key falls back to the global curve (s_c=1).
def _loadMeetCellsAnetXC(cur):
    cur.execute("""
        SELECT DISTINCT ON (meet_id) meet_id, gps_lat, gps_long, course_name
        FROM   meets
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL AND meet_id IS NOT NULL
        ORDER  BY meet_id
    """)
    out = {}
    for m, lat, lon, course in cur:
        cell = _snapCell(lat, lon)
        if cell is not None:
            out[m] = (cell[0], cell[1], course)
    return out


def _loadMeetCellsTfrrsXC(cur):
    cur.execute("""
        SELECT meet_id, gps_lat, gps_long
        FROM   meets_tfrrs
        WHERE  sport = 'XC' AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
    """)
    out = {}
    for m, lat, lon in cur:
        cell = _snapCell(lat, lon)
        if cell is not None:
            out[m] = (cell[0], cell[1], None)          # no per-course key for tfrrs
    return out


def _loadMeetCellsAnetTF(cur):
    cur.execute("""
        SELECT meet_id, gps_lat, gps_long, location_id, is_indoor
        FROM   meets_tf_meta
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
          AND  COALESCE(is_indoor, 0) = 0              -- OUTDOOR only (indoor = no weather)
    """)
    out = {}
    for m, lat, lon, loc, indoor in cur:
        cell = _snapCell(lat, lon)
        if cell is not None:
            out[m] = (cell[0], cell[1], f"TF:loc:{loc}:out")
    return out


# WeatherIndex: holds the per-source cell maps + the shared wx dict, and resolves
# (weather_dict, course_key) for a row by (source, meet_id, date). Mirrors
# GeometryIndex. A miss at any step -> (None, None) -> weather is a clean no-op.
# The race window is pulled from the artifact so apply == fit.
def _loadMeetCellsTfrrsTF(cur):
    """tfrrs track meets: coordinates in meets_tfrrs (sport 'TF'), the
    indoor flag and the venue id on the geometry stamp (2026-09-06: 33M
    college rows had no weather because this loader did not exist and
    lookup() sent every tfrrs row to an empty dict). Indoor meets are
    left out, as anet's are; a meet with no stamp is taken as outdoor
    and corrected against the global reference (no venue key)."""
    cur.execute("""
        SELECT mt.meet_id, mt.gps_lat, mt.gps_long, g.location_id, g.is_indoor
        FROM   meets_tfrrs mt
        LEFT JOIN tfrrs_meet_geometry g
               ON g.meet_id = mt.meet_id AND g.sport = 'TF'
        WHERE  mt.sport = 'TF'
          AND  mt.gps_lat IS NOT NULL AND mt.gps_long IS NOT NULL
    """)
    out = {}
    for m, lat, lon, loc, indoor in cur:
        if indoor == 1:
            continue
        cell = _snapCell(lat, lon)
        if cell is not None:
            out[m] = (cell[0], cell[1],
                      f"TF:loc:{loc}:out" if loc is not None else None)
    return out


class WeatherIndex:

    def __init__(self, anet_cells, tfrrs_cells, wx):
        self._anet = anet_cells
        self._tfrrs = tfrrs_cells
        self._wx = wx

    @classmethod
    def build(cls, cur, sport):
        with open(_WEATHER_ART.format(sport=sport), "rb") as f:
            art = pickle.load(f)
        hours = tuple(art.get("race_local_hours", (8, 12)))
        if sport == "XC":
            anet = _loadMeetCellsAnetXC(cur)
            tfrrs = _loadMeetCellsTfrrsXC(cur)
        else:
            anet = _loadMeetCellsAnetTF(cur)           # outdoor anet TF
            tfrrs = _loadMeetCellsTfrrsTF(cur)         # outdoor tfrrs TF (stamped)
            #                                            flag -> no-op (never risk
            #                                            correcting an indoor race)
        # the temperature aggregate the artifact was fitted on (avg for XC,
        # max for track); normalize_distance refuses one it does not know
        wx = _loadWxDictCached(cur, sport, hours, weatherTempAgg(art))
        return cls(anet, tfrrs, wx)

    # lookup: (source, meet_id, race_date) -> (weather dict | None, course | None).
    def lookup(self, source, meet_id, race_date):
        if race_date is None:
            return None, None
        cells = self._tfrrs if source == "tfrrs" else self._anet
        meta = cells.get(meet_id)
        if meta is None:
            return None, None
        clat, clon, course = meta
        vals = self._wx.get((clat, clon, race_date.isoformat()))
        if vals is None:
            return None, course
        temp, wind, precip, soil, snow = vals
        # doy rides in the weather dict so the venue-normal lookup in
        # _applyWeather needs no signature change anywhere in the chain. It is
        # the fortnight half of the fitter's _eventKey -- without it every
        # lookup misses and the correction silently reverts to the old global
        # 55F reference. weatherNormalCensus() reports the miss rate.
        weather = {"apparent_temp": temp, "wind": wind, "precip": precip,
                   "soil": soil, "snow": snow,
                   "doy": race_date.timetuple().tm_yday}
        return weather, course


# _weatherEnabled: is the per-sport weather artifact present? Only then is the
# heavy weather index worth building; otherwise weather stays a no-op.
def _weatherEnabled(sport):
    return _os.path.exists(_WEATHER_ART.format(sport=sport))


# ================================================================== #
# NON-GEOMETRY LOOKUPS  —  distance + gender + date, in memory (as before)
# ================================================================== #

# athlete_id -> gender, one dict lookup per row (faster than a join).
#
# ⚠ THIS WAS `{aid: g for aid, g in cur}` OVER AN UNORDERED SELECT, AND THAT
#   IS LITERALLY "IT TAKES THE LAST GENDER" (owner, 2026-09-01). An athlete
#   with rows of both genders -- two people merged under one id, or a
#   mis-sexed feed row -- resolved to whichever row the heap handed back
#   LAST. Not a rule: physical table order, which moves under VACUUM and any
#   UPDATE, so the same athlete could pool differently between two runs of
#   this script with no data change at all.
#
# ⚠ AND THIS IS THE WORST PLACE OF THE THREE TO GET IT WRONG. The gender
#   picks the pool, the pool sets the SCALE that normalized_time is written
#   on, and normalized_time is frozen into the row -- the same failure the
#   pipeline's backfill-ordering comment warns about at a measured 64%
#   rating error. The engine and the boards can be re-run; this cannot,
#   without a re-backfill.
#
# ! THE SAME RULE AS THE OTHER TWO: majority of `athletes` rows, ties to 'M'
#   (gender DESC puts 'M' above 'F'). Sorted in SQL rather than in Python so
#   it reads as the same statement as the engine's lateral and
#   build_ranking_results._GENDER_TEMP_SQL. tests/test_gender_pick.py pins
#   all three together.
def _loadGenders(cur):
    cur.execute("""
        SELECT DISTINCT ON (athlete_id) athlete_id, gender
        FROM (
            SELECT athlete_id, gender, count(*) AS n
            FROM   athletes
            WHERE  gender IN ('M', 'F')
            GROUP  BY athlete_id, gender
        ) s
        ORDER BY athlete_id, n DESC, gender DESC
    """)
    out = {aid: g for aid, g in cur}
    # ★ THE ROWS OUTRANK THE PROFILES (issue 164): where person_gender has a
    #   verdict from the divisions the person raced under, it replaces the
    #   profile majority. Person level only here: the normalisation pool is
    #   one per person, and a split person's second pool is the engine's
    #   and the boards' business.
    cur.execute("SELECT to_regclass('public.person_gender')")
    if cur.fetchone()[0] is not None:
        cur.execute("SELECT person_id, gender FROM person_gender")
        n = 0
        for pid, g in cur:
            if g in ("M", "F"):
                out[pid] = g
                n += 1
        print(f"  genders: {n:,} from person_gender (the rows), the rest "
              f"from the profiles")
    return out


# ---- canon dedup pre-pass (uses the dedup layer's person_id + canon_meet_id) --
# The dedup step canon-links the SAME physical race across sources: a tfrrs meet
# fragment and its anet twin share canon_meet_id (= the anet meet id), and the
# same runner across sources shares person_id. So one physical result can exist
# TWICE in `results` (an anet copy + a tfrrs copy). We must normalize it ONCE.
# Rule: at a canon-linked meet, a MATCHED pair (same person_id + canon_meet_id in
# BOTH sources) survives as the ANET copy; the tfrrs twin is dropped. Rows with
# no twin (only one source has that person at that meet) are kept as-is. Non-
# canon meets (canon_meet_id NULL) are untouched. anet is kept because it carries
# athlete_id + grade (tfrrs XC has athlete_id NULL) and canon_meet_id is defined
# as the anet meet id -- the richer, better-keyed copy.

# _loadSeasonLevels
# Purpose : {person_id*10000 + academic_year: level} for athlete-seasons whose
#           RACE-LEVEL verdict is unanimous, written by engine/season_level.py.
# Arguments: cur - open cursor.
# Output  : dict, or {} when the table does not exist yet -- in which case
#           poolFor is called with season_level=None and behaves EXACTLY as
#           before. This wiring is INERT until season_level.py has run.
#
# * INT KEY, NOT A TUPLE. A (person_id, ay) tuple costs ~56 bytes plus two
#   object refs; at a few million athlete-seasons that is most of a gigabyte
#   for a dict consulted once per row. One packed int keeps it small, and
#   10000 sits safely above any academic year.
#
# ! ONLY `unanimous` ROWS CARRY A LEVEL -- season_level.py stores NULL for a
#   season whose races disagree. That is the whole safety mechanism: a high
#   schooler with one pro-meet appearance has a split season, gets NULL here,
#   and keeps their grade. The WHERE clause below IS the unanimity gate,
#   enforced by the data rather than re-implemented in this file.
def _loadGradeFix(cur):
    """{person_id*10000 + academic_year: (grade, level)} from grade_fix.

    ★ WHY THE BACKFILL HAS TO READ THIS AT ALL. It classifies pools itself,
      from the RAW grade on the row -- and the engine classifies from
      grade_sanity's VERDICT. While every pool normalised to 5000 that
      disagreement was survivable: a row in the wrong pool was still on the
      right time scale, and only the pool_mean it was measured against moved.

      Per-pool anchors ended that. ms now normalises to 3200 and hs to 5000,
      so a row the backfill calls ms and the engine calls hs is written at
      0.61x and read as though it were 1.00x -- a 64% rating error, frozen
      into normalized_time.

      Measured: person 26055806 carries raw grade 7 and a corroborated
      verdict of 9. The backfill normalised his 5000s at the ms anchor
      (factor 0.605) while the engine pooled him hs_m; he finished the
      season at 196.3 and led the high school board as a middle schooler.

    ⚠ THE KEY IS THE ACADEMIC YEAR. grade_sanity writes one row per academic
      year -- reasoned and stored on the same clock. Feeding this a calendar
      year misses on every spring race.

    ⚠ ABSENT TABLE -> EMPTY DICT, and the caller falls back to the raw grade,
      which is exactly the old behaviour. This wiring is inert until
      grade_sanity has run.
    """
    cur.execute("SELECT to_regclass('public.grade_fix')")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("SELECT person_id, season, grade, level FROM grade_fix")
    out = {}
    for pid, season, grade, level in cur:
        if pid is None or season is None:
            continue
        out[int(pid) * 10000 + int(season)] = (
            sys.intern(grade) if grade else None,
            sys.intern(level) if level else None)
    return out


def _loadSeasonLevels(cur):
    cur.execute("SELECT to_regclass('public.athlete_season_level')")
    if cur.fetchone()[0] is None:
        # ! THREE ELEMENTS, NOT TWO. grade_fix rides along here rather than
        #   becoming a fourth argument threaded through _buildLookups and
        #   _makeRowFn. Every existing `season_levels[0]` and `[1]` keeps its
        #   meaning, so no other call site changes and none can be missed.
        return {}, {}, _loadGradeFix(cur)
    # ⚠ athlete_season_level IS NOW KEYED (person_id, ay, sport). Rows come in
    #   three flavours -- 'XC', 'TF', and 'ALL' (the combined verdict, which is
    #   what this table held entirely before). Selecting without the sport
    #   returns up to THREE rows per season, and this dict comprehension would
    #   have silently kept whichever arrived last. No error, no warning, an
    #   arbitrary verdict per athlete.
    #
    # ★ TWO DICTS, NOT ONE COMPOUND KEY. The lookup wants "this sport if it has
    #   a verdict, else the combined one", and two dicts express that as one
    #   `or` at the call site. A single dict keyed on sport would need the
    #   caller to try twice and get the fallback order right every time.
    cur.execute("""SELECT person_id, ay, sport, level
                   FROM   athlete_season_level
                   WHERE  level IS NOT NULL AND unanimous""")
    by_sport, combined = {}, {}
    for pid, ay, sport, lvl in cur:
        if pid is None or ay is None:
            continue
        key = int(pid) * 10000 + int(ay)
        if sport == "ALL":
            combined[key] = sys.intern(lvl)
        else:
            by_sport[(key, sport)] = sys.intern(lvl)
    return by_sport, combined, _loadGradeFix(cur)


# _academicYearOf
# Purpose : July onward belongs to the year the season STARTS, so a fall XC
#           season and the following spring TF are ONE season.
# Arguments: date - a date object or an ISO 'YYYY-MM-DD' string.
# Output  : int year, or None.
#
# * MUST MATCH season_level._academicYearExpr EXACTLY. If the SQL and this
#   disagree by one year the lookup misses on every row and the override
#   silently stops firing -- no error, no warning, just a no-op.
def _academicYearOf(date):
    if date is None:
        return None
    if isinstance(date, str):
        if len(date) < 7:
            return None
        return int(date[0:4]) if int(date[5:7]) >= 7 else int(date[0:4]) - 1
    return date.year if date.month >= 7 else date.year - 1


# _loadMatchedTwinKeys
# Purpose : the set of (person_id, canon_meet_id) that appear in BOTH sources for
#           this sport -- i.e. the matched cross-source duplicates. A tfrrs row
#           whose (person_id, canon_meet_id) is in this set has an anet twin and
#           is dropped (dedup_twin); the anet copy is normalized instead.
# Arguments: cur — open cursor; table — results table for the sport.
# Output  : a set of (person_id, canon_meet_id) tuples.
def _loadMatchedTwinKeys(cur, table):
    cur.execute(f"""
        SELECT person_id, canon_meet_id
        FROM {table}
        WHERE person_id IS NOT NULL
          AND canon_meet_id IS NOT NULL
        GROUP BY person_id, canon_meet_id
        HAVING COUNT(DISTINCT source) > 1
    """)
    return {(pid, cmid) for pid, cmid in cur}


# _loadCanonTfrrsDistances
# Purpose : for each canon-linked meet, the realistic tfrrs distance PER GENDER,
#           so a corrupt ANET distance borrows its correct twin -- NOT one modal
#           distance for the whole meet. A meet often has two distances (e.g.
#           women's 3-mile 4828m + men's 4-mile 6437m); a meet-level modal would
#           give the women's division the men's distance. So we key on
#           (canon_meet_id, gender), reading gender from the tfrrs blob div_name,
#           and take the modal sane distance within each (canon, gender).
# Arguments: cur — open cursor; table — results table; tfrrs_blob — the
#            {(meet_id, div_id): {"distance","div_name"}} already loaded.
# Output  : {(canon_meet_id, gender): distance_metres}. gender is 'M'/'F'; a
#           None-gender tfrrs division also stores under key (canon, None) as a
#           last-resort fallback.
def _loadCanonTfrrsDistances(cur, table, tfrrs_blob):
    if not tfrrs_blob:                               # TF or no blob -> nothing
        return {}
    cur.execute(f"""
        SELECT canon_meet_id, meet_id, div_id, COUNT(*) AS n
        FROM {table}
        WHERE source = 'tfrrs'
          AND canon_meet_id IS NOT NULL
        GROUP BY canon_meet_id, meet_id, div_id
    """)
    tally = {}                                       # (canon, gender) -> {dist: n}
    for cmid, meet_id, div_id, n in cur:
        info = tfrrs_blob.get((meet_id, div_id))
        if not info:
            continue
        dist = info.get("distance")
        if dist is None:
            continue
        dist = float(dist)
        if not (_MIN_DISTANCE_M <= dist <= _MAX_DISTANCE_M):
            continue                                 # only borrow a SANE distance
        gender = _genderFromDivName(info.get("div_name"))
        key = (cmid, gender)
        tally.setdefault(key, {})
        tally[key][dist] = tally[key].get(dist, 0) + n
    out = {}
    for key, dists in tally.items():
        out[key] = max(dists.items(), key=lambda kv: kv[1])[0]   # modal within gender
    return out


# _borrowCanonDistance
# Purpose : pick the tfrrs distance to lend a corrupt anet row -- matched by
#           gender first (the correct twin), then falling back to a gender-None
#           tfrrs division, then to the meet's single distance if only one exists.
# Arguments: canon_distances — from _loadCanonTfrrsDistances; canon — the row's
#            canon_meet_id; gender — the anet row's gender ('M'/'F'/None).
# Output  : a distance in metres, or None if nothing suitable to borrow.
def _borrowCanonDistance(canon_distances, canon, gender):
    # exact gender match (women's anet div -> women's tfrrs distance)
    d = canon_distances.get((canon, gender))
    if d is not None:
        return d
    # gender-None tfrrs division (title carried no gender signal)
    d = canon_distances.get((canon, None))
    if d is not None:
        return d
    # last resort: if the meet has exactly ONE distance across all genders, use it
    vals = {v for (c, _g), v in canon_distances.items() if c == canon}
    if len(vals) == 1:
        return next(iter(vals))
    return None                                      # ambiguous -> don't guess


# div_id -> distance, for ANET XC (distance lives on meets, keyed by div_id).
# TF gets distance from EVENT_DISTANCES_TF instead, so this stays XC-only.
#
def _loadMeetDistances(cur):
    cur.execute("SELECT div_id, distance FROM meets")
    return {d: dist for d, dist in cur}


# ★ ONE PATTERN FOR EVERY WHEELCHAIR SEAM. The engine's _xcQuery carries the
#   same trio in SQL ('wheelchair|seated|ambulator') for anet XC; this is the
#   Python spelling for the two seams SQL could not reach (tfrrs blob titles,
#   TF event names) plus the anet set below. Change one, change both.
_WHEELCHAIR_RX = re.compile(r"wheelchair|seated|ambulator", re.IGNORECASE)


# anet XC divisions whose TITLE says wheelchair/seated -- resolved once to a
# set so the hot loop pays a membership test, exactly like _RESULT_DROP.
def _loadWheelchairDivs(cur):
    cur.execute("SELECT div_id FROM meets "
                "WHERE division ~* 'wheelchair|seated|ambulator'")
    return frozenset(d for (d,) in cur)


# ★ THE PERSON CARRIES IT, NOT THE RACE (2026-08-27, owner's call).
#   Division titles only label SOME of a wheelchair athlete's races. The
#   unlabelled ones were being priced on the running scale -- a chair
#   5000m is far faster than a runner's, so those rows rated absurdly
#   high and fed straight into pool means and course difficulties. One
#   labelled race is proof of how the athlete competes, so every row of
#   theirs is withheld, both sports, both feeds.
#
#   Identity follows the codebase's career key: person_id when the
#   dedup layer linked one, else (source, athlete_id) -- NOT athlete_id
#   alone, whose id space is per-feed and would over-catch across them.
def _loadWheelchairPeople(cur, tfrrs_distances):
    wheel_pairs = [(m, d) for (m, d), info in tfrrs_distances.items()
                   if info.get("div_name")
                   and _WHEELCHAIR_RX.search(info["div_name"])]
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS _wcp"
                "(meet_id bigint, div_id bigint)")
    cur.execute("TRUNCATE _wcp")
    if wheel_pairs:
        psycopg2.extras.execute_values(
            cur, "INSERT INTO _wcp VALUES %s", wheel_pairs, page_size=5000)
    cur.execute("""
        WITH wc AS (
            SELECT r.person_id, r.athlete_id, r.source
            FROM   results r
            JOIN   meets m ON m.div_id = r.div_id
                          AND m.meet_id = r.meet_id
                          AND m.source = r.source
            WHERE  m.division ~* 'wheelchair|seated|ambulator'
            UNION ALL
            SELECT r.person_id, r.athlete_id, r.source
            FROM   results r JOIN _wcp w ON w.meet_id = r.meet_id
                                        AND w.div_id = r.div_id
            WHERE  r.source = 'tfrrs'
            UNION ALL
            SELECT r.person_id, r.athlete_id, r.source
            FROM   results_tf r
            WHERE  r.event_short ~* 'wheelchair|seated|ambulator')
        SELECT DISTINCT person_id, athlete_id, source FROM wc""")
    persons, athletes = set(), set()
    for person_id, athlete_id, source in cur:
        if person_id is not None:
            persons.add(person_id)
        elif athlete_id is not None:
            athletes.add((source, athlete_id))
    return frozenset(persons), frozenset(athletes)


# (meet_id, div_id) -> {"distance": float, "div_name": str} for TFRRS XC,
# from the jsonb blob meets_tfrrs.division_distances.
#
# KEY SHAPE (learned from peek_blob, correcting the first attempt): the blob
# is keyed by a PER-MEET-LOCAL div_id ('0','1','2',...), and result rows carry
# that same per-meet-local div_id as an int. So div_id ALONE is NOT unique --
# every meet has a '0'. The identity is (meet_id, div_id), matching the
# project invariant "meet_id is not a key; identity is (meet_id, sport,
# source)". Keying on div_id alone collapsed 16,402 meets down to ~64 unique
# small ints (the bug that loaded 64 rows). The composite key fixes it.
#
# We keep div_name too, because tfrrs XC results have athlete_id=NULL and no
# gender column -- gender is recovered from the div_name ("Women's 6k" -> F)
# at pool-classification time. One lookup, both fields.
#
def _loadTfrrsBlobDistances(cur):
    cur.execute("""
        SELECT meet_id, division_distances
        FROM meets_tfrrs
        WHERE division_distances IS NOT NULL
    """)
    out = {}
    for meet_id, blob in cur:
        if not blob:
            continue
        for div_str, info in blob.items():       # '0' -> {"distance","div_name"}
            if not isinstance(info, dict):
                continue
            dist = info.get("distance")
            if dist is None:
                continue
            try:
                key = (meet_id, int(div_str))    # composite: meet + local div
            except (TypeError, ValueError):
                continue                         # unparseable div index -> skip
            out[key] = {"distance": float(dist),
                        "div_name": info.get("div_name")}
    return out


# NOTE: there is no _loadMeetDates anymore. date rides the result row
# (results.date / results_tf.date), read as row[_DATE] and parsed by
# _parseRowDate — one date source for every sport, tfrrs included. This is
# what closes tfrrs era coverage: a meet-level date dict would have missed it.


# ================================================================== #
# THE READ  —  page unnormalized rows by PK cursor (stable scan order)
# ================================================================== #

# The FULL-TABLE SELECT for one sport, drained by a server-side cursor in a
# single pass. No `WHERE result_id >`, no `ORDER BY`, no `LIMIT`: those existed
# only to PAGINATE. A server-side cursor streams every row in one query, so we
# drop all three — which also removes the index-ordered walk `ORDER BY` forced.
#
# FIXED column width both sports, via literal-NULL substitution for the two
# columns that only exist on results_tf (TF), not results (XC):
#   - event_id    : XC's `results` has NO event_id (XC has no track events);
#                   NULL AS event_id keeps the 10-column shape and makes XC's
#                   geometry lookup key (meet,div,None) miss -> geometry no-op,
#                   which is CORRECT (track geometry is a TF concept only).
#   - event_short : same story; XC has no event string.
# So the _ID.._DATE unpack never branches on sport. NO `normalized_time IS NULL`
# guard (unconditional rewrite) and no resumability (owner choices) — which is
# what lets the read be one uninterrupted stream.
#
# ★ #47 IS APPLIED IN THE STREAM, NOT IN PYTHON. age_band_result names the
#   rows whose banded "grade" is really an AGE range (see
#   engine/age_band_grades.py). Those rows must reach normalizeResult with NO
#   grade, so poolFor falls through to the evidence it already trusts -- the
#   school and the season verdict -- instead of reading "11-12" as eleventh
#   and twelfth grade and pooling a professional as a high schooler.
#
# ! A JOIN RATHER THAN A SET OF result_ids IN MEMORY, for two reasons. The
#   flagged set is unbounded in principle (it grows with every youth meet
#   ingested), and a Python-side test would be a SECOND place that decides
#   what a grade means -- grade_sanity does it in SQL at the allraces build,
#   and two implementations of one rule drift. Left-joining a small table
#   into a sequential scan is a hash join with a tiny hash table.
#
# ⚠ IT NULLS ONLY grade. The row keeps its time, its meet and its person, and
#   is still rated -- #47 removes a false claim about level, it does not
#   discard a race.
_ONLY_CHANGED = {"on": False}


def _streamSQL(cfg, age_band=False):
    # Per-sport literal-NULL for the TF-only columns, so both sports yield the
    # SAME 10 columns in the SAME order.
    # ! THE ALIAS IS BAKED IN HERE, NOT ADDED AT THE CALL SITE. The table now
    #   carries the alias `r` so the #47 join has something to key against,
    #   and prefixing at use would produce `r.NULL AS event_id` on the branch
    #   that substitutes a literal.
    event_id_col    = "r.event_id"    if cfg.has_event else "NULL AS event_id"
    event_short_col = ("r.event_short" if cfg.has_event
                       else "NULL AS event_short")
    # `school` exists on results (XC). If results_tf lacks it, substitute a
    # literal NULL -- the SAME literal-NULL trick used above for event_id, so
    # both sports keep the identical column shape. A NULL school simply means
    # poolFor's school lookup abstains and behaviour is exactly as before.
    school_col      = "r.school"      if cfg.has_school else "NULL AS school"
    # `date` rides the RESULT row so era gets a per-result date with no join.
    #
    # The #47 join and the column shape move together: when age_band_result is
    # absent the query is byte-for-byte what it always was, and `grade` is the
    # plain column. There is no third state.
    if age_band:
        grade_col = ("CASE WHEN ab.result_id IS NULL THEN r.grade END "
                     "AS grade")
        band_join = (f"\n        LEFT JOIN age_band_result ab"
                     f"\n               ON ab.sport = '{cfg.sport}'"
                     f"\n              AND ab.result_id = r.result_id")
    else:
        grade_col, band_join = "r.grade AS grade", ""
    # ★ ONLY THE PEOPLE WHOSE POOL MOVED (2026-09-06, --only-changed): a
    #   row's normalised time depends on its pool, and the pool on the
    #   person's gender verdict; when nothing else about a sport changed
    #   since the last full pass, the rows worth re-normalising are those
    #   of the people 04d moved (person_gender_changed). Pair with
    #   --write-mode update: the staging set is a sliver of the table.
    only = (f"\n        JOIN person_gender_changed pgc ON pgc.person_id = r.person_id"
            if _ONLY_CHANGED["on"] else "")
    return f"""
        SELECT r.result_id, r.source, r.meet_id, r.div_id, {event_id_col},
               {event_short_col}, r.time_seconds, {grade_col},
               r.athlete_id, r.date,
               r.person_id, r.canon_meet_id, {school_col}
        FROM {cfg.table} r{band_join}{only}
    """


# _openStream
# Purpose : open a SERVER-SIDE (named) cursor on the READ connection and start
#           the single streaming query. A named cursor makes Postgres hold the
#           result set server-side and ship it in chunks of `itersize`, instead
#           of the client re-issuing a paginated SELECT per page. Iterating the
#           cursor (`for row in cur`) then pulls rows lazily with no re-seek.
# Arguments: read_conn — a connection DEDICATED to reading (no writes may run on
#                        it while this cursor is open — that's why writes use a
#                        second connection).
#            cfg       — the SportConfig (chooses table + column shape).
# Output   : an open server-side cursor, already executed, ready to iterate.
def _openStream(read_conn, cfg):
    # ! PROBED BEFORE THE NAMED CURSOR OPENS, on the same connection. A plain
    #   SELECT here is safe -- the "no other work on this connection" rule
    #   applies once the server-side cursor is streaming, not before it. A
    #   missing optional table must degrade, not crash.
    with read_conn.cursor() as probe:
        probe.execute(
            "SELECT to_regclass('public.age_band_result') IS NOT NULL")
        age_band = bool(probe.fetchone()[0])
    if age_band:
        print(f"[{cfg.sport}] #47 age bands ON -- rows in age-banded divisions "
              f"reach poolFor with no grade")
    else:
        print(f"[{cfg.sport}] age_band_result not found -- banded grades will "
              f"be read as GRADES. Run engine/age_band_grades.py --write "
              f"(issue #47)")
    cur = read_conn.cursor(name=f"backfill_stream_{cfg.sport.lower()}")  # named => server-side
    cur.itersize = cfg.batch          # rows shipped per network round trip
    cur.execute(_streamSQL(cfg, age_band))   # begins the single full-table scan
    return cur


# Row tuple positions, named once (order MUST match _readSQL's SELECT).
# _DATE is followed by the dedup columns person_id + canon_meet_id -> range(12).
_ID, _SRC, _MEET, _DIV, _EVENT_ID, _EVENT_SHORT, _TIME, _GRADE, _AID, _DATE, \
    _PERSON, _CANON, _SCHOOL = range(13)
# _SCHOOL is APPENDED last on purpose: every existing index keeps its position,
# so no other unpack site can silently shift. It feeds poolFor's school lookup,
# which recovers the level tfrrs's NULL grade cannot supply.


# ================================================================== #
# PER-ROW NORMALIZE  —  one raw row -> (result_id, value|None).
#                       Small helpers keep this readable; the library does math.
# ================================================================== #

# DNF/DNS/DQ are stored as huge times; treat >100000 (or None) as non-times.
# ! AND anet TF's 20,000 s (SortInt 20,000,000), which slipped under that line
#   and was rated as a five-and-a-half-hour mile -- result_status owns the list.
def _isSentinelTime(time_seconds):
    return (time_seconds is None or time_seconds > 100_000
            or _statusSentinel(time_seconds))


# ---- distance + pace guards (the normalize-path defense the fitter's own
#      guards do NOT cover: the fitter gates its TRAINING PAIRS; this gates
#      EVERY result row being converted). Bounds mirror the fitter's
#      MIN/MAX_DISTANCE and pace floors so the two paths agree on "valid". ----

# A running race lives in this metre band. Below ~800m the power law breaks
# (anaerobic); above 12,000m nothing this law covers is a race (10K champs +
# fuzz). The double-conversion monsters (3000*1609 = 4.8M) and the ~1,685
# other over-ceiling corruptions all fail the MAX; genuine races never do.
_MIN_DISTANCE_M = 600.0        # 600 since issue 42; see event_parse._MIN_DISTANCE
_MAX_DISTANCE_M = 12_000.0

# Pace sanity — the CEILING only (a flat slow bound for garbage-slow times).
# The fast side is NOT a pace floor anymore: it's the per-gender world-record
# curve below (_rawTimeAboveWR), which is the physically-exact version of the
# same idea. A pace floor was an approximation; the WR curve IS the limit.
# Slow-side pace CEILING. Pace = time/distance is distance-INVARIANT: it cancels
# the exact distance the scaler later multiplies back, so ONE mile-pace line
# catches a stopped-clock 1000m and a stopped-clock 3-miler identically.
#
# Was 1.5 s/m (~40:00/mi) -- so loose that nothing real OR fake ever hit it: the
# garbage cluster sits at ~1.44-1.50 s/m (a 1000m "run" in 24:51, a mile in
# 38:31 = slower than WALKING), and squeaked under at 1.4915. Those are stopped
# clocks / DNFs recorded as times / unit errors, not runners.
#
# Now 20:00/mile. Real people top out around a walk (~0.75 s/m); a 35:00 5K is
# 0.42 s/m and a 62:00 5K is 0.744 s/m -- both survive with room to spare. The
# gap between the slowest real finisher and the garbage is wide and empty, and
# this line sits inside it. The knob is the MINUTES-PER-MILE below; the s/m
# threshold is derived, so tune in the units you think in (raise to 25.0 to
# loosen if a real slow finisher ever gets clipped).
_SLOW_CEILING_MIN_PER_MILE = 20.0
_METERS_PER_MILE = 1609.344
_MAX_PACE_S_PER_M = (_SLOW_CEILING_MIN_PER_MILE * 60.0) / _METERS_PER_MILE  # 0.7456 s/m


# _distanceSane
# Purpose : is this resolved distance inside the physical race band? The
#           first guard, because a corrupt distance poisons everything after.
# Argument: distance — resolved metres (already known non-None here).
# Output  : True if usable, False -> caller charges INSANE_DISTANCE.
def _distanceSane(distance):
    return _MIN_DISTANCE_M <= distance <= _MAX_DISTANCE_M


# Per-pool XC distance ceilings — a COARSE pre-filter only. The DECISIVE fast
# guard is the per-gender WR curve (_rawTimeAboveWR). These caps just reject
# grossly-wrong distances; they do NOT police each pool's exact race menu
# (college_f DOES race 8k). Generous: the longest a pool plausibly races + slack.
_POOL_MAX_DISTANCE_XC = {
    "college_f": 10000.0,
    "college_m": 10000.0,
    "hs_f":      10000.0,
    "hs_m":      10000.0,
    "ms_f":       8000.0,
    "ms_m":       8000.0,
}
_DEFAULT_POOL_MAX_XC = 12000.0


# Distance ceiling for a pool (XC only; TF distances are event-fixed).
def _poolMaxDistance(pool, sport):
    if sport != "XC":
        return _MAX_DISTANCE_M
    return _POOL_MAX_DISTANCE_XC.get(pool, _DEFAULT_POOL_MAX_XC)


# Coarse: is the distance within the longest this pool plausibly races?
def _distanceInPoolRange(distance, pool, sport):
    return distance <= _poolMaxDistance(pool, sport)


# Slow-side only: reject garbage-slow times over the flat ceiling. The FAST
# side is handled by the WR curve, not here (a per-gender curve is stricter
# and exactly right where a flat pace floor was only approximate).
#
def _paceSane(time_seconds, distance):
    return (time_seconds / distance) <= _MAX_PACE_S_PER_M


# ---- the RAW-TIME world-record cap, per gender ----
# A raw time faster than the world record for its distance is impossible for
# ANYONE of that gender, so it's corruption — caught BEFORE normalization (a
# fake-fast raw time is the root cause of the fake-elite normalized values;
# stop it at the source). No official WR exists at every XC distance (8000m,
# 6000m, ...), so instead of a per-distance table we fit the endurance curve
# t = C * d^b through the real track WRs (1500 / mile / 3000 / 3200 / 5000 /
# 10000) per gender, then LOWER the curve to sit at/under EVERY real WR (so a
# genuine record is never dropped) and subtract 1s of slack. A raw time BELOW
# this curve at its distance is faster than the world record over the whole
# race — impossible. Coefficients are from a log-log fit (see the derivation
# run); women's curve sits above men's, as it must.
# ---- the per-pool RAW-TIME floor curve (forgiving) ----
# WHY per-pool and not a single WR curve: a global (open-WR) raw cap is far too
# permissive for youth pools -- a middle-schooler "running" 3:58/mile over 4000m
# is nowhere near the open WR, so a WR cap misses it, yet it's flatly impossible
# for a 12-year-old. Each pool instead gets its own floor curve t = C_pool * d^b,
# sharing one endurance exponent b (the curve SHAPE) but anchored at a FORGIVING
# per-pool 5k-equivalent -- set BELOW each pool's real record, so a genuine
# record is never clipped, and only the clearly-impossible-for-that-pool drops.
# Unknown gender uses the level's MALE (faster, lower) floor, so we only ever
# drop the universally-fake.
_RAW_CURVE_B = 1.070727                       # endurance exponent (shared shape)

# ★ THE EXPONENT IS NOT CONSTANT ACROSS DISTANCE, and treating it as one clipped
#   a whole tier of real middle-distance running.
#
#   Fitted from actual HS boys records:
#        800 -> 1600   234/104.5 = 2.24   b = 1.16
#       1600 -> 3200   508/234   = 2.17   b = 1.12
#       3200 -> 5000   803/508   = 1.58   b = 1.03
#
#   It FALLS with distance, because record progression is not one athlete
#   scaling up -- an 800m specialist and a 5k runner are different people, and
#   the gap between their events is wider than any single power law.
#
#   With b = 1.070727 everywhere, the hs_m floor at 800m came out at 1:49.62.
#   The HS boys record is 1:44.5 and several hundred high schoolers break 1:49.6
#   every year, so the gate was silently dropping their best races -- measured
#   on Cooper Lutkenhaus, every 800 he ran faster than 1:49.6, which is all of
#   them from 2025 on. The floors matched the observed cutoffs to the hundredth
#   in both hs_m (109.62) and ms_m (118.05), so this was the whole cause.
#
#   1.12 is the mid-range value, chosen over 1.16 deliberately: it is the
#   shallower of the two short-side fits, so it is the more forgiving, and this
#   gate exists to catch corruption (2.4-second and 11.7-second "800m" times are
#   real rows in the corpus) rather than to police elites.
#
#   Margins it leaves, hs_m with a 13:00 anchor:
#        800m   floor 1:40.0   record 1:44.5   4.5s
#       1600m   floor 3:37.5   record 3:54    16.5s
#       3200m   floor 7:53     record 8:28    35s
#
#   ⚠ ONLY BELOW 5000m. Above it the original exponent stands: raising b there
#     would TIGHTEN the long end, and the college_m 10k margin is already the
#     binding constraint (the note on the college_m anchor explains why it was
#     not tightened to catch the 8k mislabels).
_RAW_CURVE_B_SHORT = 1.12
_RAW_CURVE_D5K = 5000.0 ** _RAW_CURVE_B        # precomputed 5000^b for anchoring

# Forgiving per-pool floor as a 5k-EQUIVALENT (seconds). BELOW each pool's real
# record on purpose: college_m record ~12:57 -> floor 12:00; HS boys 14:03 ->
# 13:00; etc. Verified to catch a 3:58/mile MS 4k while a 14:03 HS 5k, a 3:59
# HS mile, and the 12:57 collegiate record all survive.
_POOL_RAW_FLOOR_5K = {
    "college_m": 720.0,   # 12:00 — forgiving. Gives a ~98s margin on the real
                          #  collegiate 10k record (26:50.21, Kipkurui 2025) at
                          #  10000m. NOTE: the 8k-stored "19:54 college" fakes in
                          #  the tail are DISTANCE MISLABELS (44462 etc.) fixed by
                          #  _DISTANCE_OVERRIDES, not by tightening this floor --
                          #  tightening to catch them (anchor 760) shrinks the 10k
                          #  margin to a risky 14s, so we don't.
    "college_f": 840.0,   # 14:00
    "hs_m":      780.0,   # 13:00   (record 14:03)
    "hs_f":      900.0,   # 15:00
    "ms_m":      840.0,   # 14:00   (fastest MS ~15:00)
    "ms_f":      960.0,   # 16:00
    "elem_m":    900.0,   # 15:00 — slower than ms_m's 14:00, since elementary
    "elem_f":   1080.0,   # 18:00   racing is younger and shorter
    "elem_unknown_gender": 960.0,
    "college_unknown_gender": 720.0,   # = male floor of the level
    "hs_unknown_gender":      780.0,
    "ms_unknown_gender":      840.0,
}
_DEFAULT_RAW_FLOOR_5K = 720.0   # any other pool: the most-permissive (college_m)


# _rawFloorFor
# Purpose : the forgiving fastest-plausible raw time for (distance, pool) -- the
#           pool's floor curve evaluated at this distance. A raw time BELOW it is
#           faster than anyone in that pool has plausibly run, so it's corruption
#           (a fake-fast youth time the open-WR cap can't see).
# Arguments: distance — resolved metres; pool — the classified pool.
# Output  : the minimum plausible raw time in seconds.
def _rawFloorFor(distance, pool):
    floor5k = _POOL_RAW_FLOOR_5K.get(pool, _DEFAULT_RAW_FLOOR_5K)
    b = _RAW_CURVE_B if distance >= 5000.0 else _RAW_CURVE_B_SHORT
    # Both branches anchor at the SAME 5k floor, so the curve is continuous at
    # 5000m -- no step for a race that lands either side of the join.
    return floor5k * ((distance / 5000.0) ** b)


# _rawTimePlausible
# Purpose : is this raw time at/above the forgiving floor for its distance and
#           pool? Runs AFTER pool is known (it needs the pool). Replaces the old
#           global WR cap -- this is stricter for youth (catches fake-fast MS/HS
#           times) yet still forgiving (never clips a real record).
# Arguments: time_seconds — raw time; distance — metres; pool — classified pool.
# Output  : True if plausible (keep), False if impossibly-fast-for-pool (drop).
def _rawTimePlausible(time_seconds, distance, pool):
    return time_seconds >= _rawFloorFor(distance, pool)


# ---- per-pool NORMALIZED-time floor (the 5k-equivalent record for each pool) ----
# WHY this is needed ON TOP of the raw-time WR cap: the WR cap is built from
# TRACK records, but XC races at 8000m/6000m/etc. on grass and hills, where real
# times are 8-12% slower than track. So a too-fast XC time (an implausible 20:40
# 8k — 2 min faster than any real college 8k) still passes the track-based raw
# cap, then normalizes to a sub-record 5k-equivalent. Since normalized_time IS a
# 5k-equivalent, we floor it at each pool's REAL 5k record: anything faster is
# impossible for that pool. The floor sits AT the record, so a legit fast time
# (college_m 13:30) sits ABOVE it and survives — only sub-record times drop.
# ---- the 3:00/mile absurd-pace floor (auto-drop obvious fakes) ----
# A coarse, pool-blind sanity line: NO real distance-running result is faster
# than 3:00/mile pace over an XC distance. This is deliberately LOOSER than the
# per-gender WR curve above — its job isn't to catch every impossibility (the
# WR cap does that), it's to auto-drop the OBVIOUSLY fake so they never reach
# the manual mislabel-triage list. Anything between "3:00/mile fast" and the WR
# curve is ambiguous (could be a real elite or a mild mislabel) and is LEFT for
# the triage diagnostic + manual override table to judge. We do NOT floor the
# normalized OUTPUT per pool anymore: a 13:00 5k is real, and those per-pool
# caps wrongly piled legit elites at the floor. Physical impossibility (WR cap,
# distance band, this absurd-pace line) is the only automatic drop.
_ABSURD_PACE_S_PER_M = 180.0 / 1609.344   # 3:00/mile = 0.11185 s/m


# _paceNotAbsurd
# Purpose : reject a raw time faster than 3:00/mile pace for its distance — the
#           coarse "obvious fake" gate. Pool-blind on purpose.
# Arguments: time_seconds — raw time; distance — resolved metres.
# Output  : True if plausible, False -> caller charges INSANE_PACE (absurd-fast).
def _paceNotAbsurd(time_seconds, distance):
    return (time_seconds / distance) >= _ABSURD_PACE_S_PER_M


# Sane-year window for a parsed date. The known corruption is 0023/0025-style
# years (documented in the era notes); date.fromisoformat ACCEPTS them as valid
# year-23 dates, so parsing alone can't reject them — an explicit gate must.
_MIN_SANE_YEAR = 1980
_MAX_SANE_YEAR = 2035


# _parseRowDate
# Purpose : turn the row's TEXT date column into a datetime.date. Returns a
#           2-tuple (date_or_None, corrupt_flag):
#             - absent/empty date -> (None, False): legit missing date, era just
#               no-ops; the row is still a real result and is kept.
#             - a date that PARSES but sits outside the sane-year window (e.g.
#               2222-09-01 on the synthetic meet-233746 rows whose times also
#               increment by 0.01s) -> (None, True): that's not a missing date,
#               it's CORRUPT data, so the caller drops the row.
#             - a sane date -> (date, False).
# Argument: raw — results.date / results_tf.date, expected 'YYYY-MM-DD' text.
# Output  : (date|None, is_corrupt).
def _parseRowDate(raw):
    if not raw:                              # None or '' -> legit-absent, keep row
        return None, False
    try:
        d = date.fromisoformat(str(raw))     # strict 'YYYY-MM-DD' -> date object
    except ValueError:                       # malformed string -> treat as absent
        return None, False
    if not (_MIN_SANE_YEAR <= d.year <= _MAX_SANE_YEAR):
        return None, True                    # parsed but insane year -> CORRUPT
    return d, False


# ---- manual distance overrides for known-mislabelled meets ----
# (meet_id, div_id) -> true distance in metres. The mislabels the triage
# diagnostic surfaces (a 4-mile stored as 8k, etc.) can't be auto-detected from
# the data, so a human confirms each on athletic.net and adds a line here. This
# is consulted FIRST, before the meets column and the tfrrs blob, so an override
# wins over the corrupt stored distance. Version-controlled, reversible, and it
# documents WHY each row exists. Distances confirmed by hand on athletic.net.
# Hand-verified data corrections. These MOVED to corrections.py so the
# FITTERS can apply them too: they used to live here, which meant the
# backfill wrote corrected distances while fit_distance_exponent fitted
# on the broken ones. That split is what drove college_f|XC to an
# impossible exponent of 0.51 and got the whole pool gated out.
# PER-SPORT (2026-07-13): result_id / (meet,div) key spaces OVERLAP between
# results and results_tf, so sport-blind containers executed XC-intent
# corrections against innocent TF rows (40,159 would-be casualties measured).
# Import the selectors; each consumer picks its sport's container ONCE.
from corrections import (_DISTANCE_OVERRIDES_BY_SPORT, _DISTANCE_DROP_BY_SPORT,
                         _RESULT_DROP_BY_SPORT, _GENDER_OVERRIDES_BY_SPORT,
                         _RESULT_OVERRIDE_BY_SPORT)


# Individual COOKED rows to drop by result_id. Unlike _DISTANCE_DROP (whole
# division) or _DISTANCE_OVERRIDES (fix the distance), these are single corrupt
# results inside an OTHERWISE-REAL division -- e.g. three Battle Ground "PBs" in
# the real 7k meet 9529 (18:52/19:03/19:07 for 7000m = ~4:20/mi, impossible for
# HS), confirmed fake on athletic.net. The distance is right and the division is
# real, so only these specific rows are removed. Checked FIRST in the row fn.


# Per-division GENDER override: (meet_id, div_id) -> 'M' | 'F'. For divisions
# stored with the wrong gender (e.g. a women's race labelled men's), so the row
# lands in the right pool (college_f, not college_m). Forced right before
# poolFor in the hot path. Distance is fixed separately via _DISTANCE_OVERRIDES.


# Per-RESULT distance + gender pins -- the combined-division splitter.
# Some tfrrs divisions pack TWO races into one div under a single blob distance
# (e.g. 9888 div 2 = "JV Men's 5 Mile & JV Women's 5000 M"). Neither
# _DISTANCE_OVERRIDES nor _GENDER_OVERRIDES can fix it: both are keyed
# (meet, div), so they can't tell the men from the women INSIDE one div. This
# map pins a single result_id's distance and/or gender, applied BEFORE the blob,
# so each gender in a mixed div gets its correct distance and therefore pool.
# Value: (distance_metres|None, gender|None); None on either field = leave as
# resolved normally. Keyed by result_id.


# Resolve (distance_metres, gender_from_blob) for one row.
#   DROP     : a (meet_id, div_id) in _DISTANCE_DROP has no recoverable true
#              distance -> return (None, None) so the row is skipped (no_distance).
#   OVERRIDE : a manual (meet_id, div_id) -> true distance wins over everything
#              (known mislabels a human confirmed and corrected).
#   TF : distance from EVENT_DISTANCES_TF; no blob gender (returns None).
#   XC : distance from meets.distance by div_id (ANET); if that misses and the
#        row is tfrrs, from the blob keyed by (meet_id, div_id) -- and the SAME
#        blob entry yields the div_name we read gender from (tfrrs XC has no
#        gender column, athlete_id=NULL, so div_name is the only source).
# Returns (distance|None, gender|None). gender is None for anet (it comes from
# the genders dict there) and for TF; only tfrrs XC fills it from the blob.
#
# ============================================================================
# 2026-07-16 -- THE DIV_ID COLLISION FIX (0.2, live in the ruler since day one)
#
# THE BUG (measured, not theorised)
#   The old resolution order was:
#       dist = meet_distances.get(row[_DIV])     # div_id ALONE, no source gate
#       if dist is not None: return dist, None
#       if row[_SRC] == "tfrrs": ... blob ...    # UNREACHABLE for div 1 and 3
#
#   `meet_distances` is keyed by meets.div_id -- a GLOBAL id. A tfrrs row's
#   div_id is a PER-MEET LOCAL INDEX (0,1,2...). 0.2: "The integers collide by
#   coincidence." And `meets` contains EXACTLY TWO rows below div_id 30:
#       (div_id=1, meet_id=1, 5000.0)   (div_id=3, meet_id=1, 5000.0)
#   So every tfrrs XC row at local div 1 or 3 -- 15,639 MEETS -- took meet 1's
#   distance and never reached its own blob.
#
#   What the blob actually held for those meets:
#       3711  "Men's 8k"                 8000.0      ruler used 5000
#       4249  "Men's 10k"               10000.0      ruler used 5000
#       4730  "Men's 7.985k"             7985.0      ruler used 5000
#       21014 "Men 8075 Meter Run CC"    8075.0      ruler used 5000
#   Somebody measured those courses and typed the number. The data was fine.
#   THE LOADER NEVER ASKED. 8000/5000 = 1.6 -> those fields read ~-40% and were
#   the largest single class in the 7/16 z-cut crop.
#
# THE SECOND HALF OF THE BUG
#   `return dist, None` also returned gender NONE. tfrrs XC has athlete_id NULL
#   and no gender column (0.8) -- div_name is the ONLY source. So those 15,639
#   meets' rows landed in unknown_gender pools. 4.6: college_unknown_gender|XC
#   held 111,248 rated athletes, and "a baseline blending two genders has
#   inflated variance and HIDES real outliers". ONE fix, not forty.
# ============================================================================

def _tfrrsXcDistance(row, tfrrs_blob):
    # Purpose   : tfrrs XC distance + gender, from the blob and NOTHING ELSE.
    # Arguments : row        -- the result row tuple.
    #             tfrrs_blob -- (meet, div) -> {"distance", "div_name"}, built
    #                           by _loadTfrrsBlobDistances.
    # Output    : (distance | None, gender | None).
    # WHY NO FALLBACK TO meets: 2.4 -- "the ONE resolution path this enables...
    #   and it NEVER TOUCHES meets". A fallback would re-open the collision
    #   this function exists to close.
    # Note      : no blob entry -> (None, None) -> the row dies as no_distance.
    #   THAT IS CORRECT. NULL is better than stale: 5000 asserts "this athlete
    #   ran a 5K", and they ran 8k. A missing blob means "not captured yet"
    #   (2.4), which is a scrape gap, not a distance.
    info = tfrrs_blob.get((row[_MEET], row[_DIV]))   # COMPOSITE key -- 0.2
    if info is None:
        return None, None
    return info["distance"], _genderFromDivName(info["div_name"])


def _anetXcDistance(row, meet_distances):
    # Purpose   : anet XC distance -- meets.distance via the GLOBAL div_id.
    # Arguments : row, meet_distances -- div_id -> distance.
    # Output    : (distance | None, None).
    # Note      : keying on div_id ALONE is CORRECT here and only here. For anet
    #   it IS the global id that carries the distance (2.3). Gender is None BY
    #   DESIGN: anet rows get theirs from the athletes join, which is richer
    #   than a division title.
    return meet_distances.get(row[_DIV]), None


def _resolveDistanceGender(cfg, row, meet_distances, tfrrs_blob):
    key = (row[_MEET], row[_DIV])
    # Select THIS sport's containers (three dict hits per row -- noise next to
    # the parsing below, and it keeps the correction keyed to the table it was
    # verified against instead of executing sport-blind).
    ro = _RESULT_OVERRIDE_BY_SPORT[cfg.sport].get(row[_ID])   # per-RESULT pin wins first
    if ro is not None and ro[0] is not None:         # distance pinned for this row
        return ro[0], ro[1]                          # gender rides along (may be None)
    if key in _DISTANCE_DROP_BY_SPORT[cfg.sport]:    # unfixable -> skip the row
        return None, None
    override = _DISTANCE_OVERRIDES_BY_SPORT[cfg.sport].get(key)

    # The stored resolution runs even when an override exists, because the
    # override is now judged AGAINST it -- see the clamp below.
    if cfg.distance_src == "event_short":
        # (metres, gender). The gender rides back as `blob_gender`, which the row
        # fn already consults AFTER the athletes-table lookup and BEFORE giving
        # up -- exactly the slot it belongs in. anet TF rows hit the exact dict
        # and return gender None, so their athlete-table gender still wins.
        stored, stored_gender = distanceFromEventShort(row[_EVENT_SHORT])
    elif row[_SRC] == "tfrrs":
        # XC: THE SOURCE DECIDES, BEFORE any lookup. See _anetXcDistance /
        # _tfrrsXcDistance and 0.2 -- results.div_id has a DUAL MEANING that
        # only `source` disambiguates, and the two id spaces COLLIDE.
        stored, stored_gender = _tfrrsXcDistance(row, tfrrs_blob)
    else:
        stored, stored_gender = _anetXcDistance(row, meet_distances)

    if override is not None:
        # ★ DOWNWARD ONLY -- the policy at the top of corrections.py's
        #   _DISTANCE_OVERRIDES_XC. An override may lower a distance or fill
        #   a missing/insane one; one that would RAISE a sane stored value is
        #   refused here, whatever sits in the table. Inflated labels mint
        #   fake elite ratings; deflated ones only make someone look slow.
        if stored is None or not _distanceSane(stored) or override <= stored:
            # gender None on purpose: _blobGender recovers it later, same as
            # the pre-clamp behaviour of the override path.
            return override, None
        return stored, stored_gender

    return stored, stored_gender


# _blobGender
# Purpose : the division-title gender for a row, resolved INDEPENDENTLY of how
#           its distance was found. This exists because _resolveDistanceGender
#           short-circuits: a row whose distance came from _DISTANCE_OVERRIDES,
#           _RESULT_OVERRIDE, or anet's meets table returns gender=None, even
#           when the tfrrs blob knows the answer. That silently sent recoverable
#           rows to unknown_pool (2,006,187 of them, 5.1% of the corpus).
# Arguments: row; tfrrs_blob -- (meet,div) -> {"div_name":..., "distance":...}.
# Output   : "M" | "F" | None.
# Detail   : tfrrs only. anet rows carry gender on the athlete, so a title guess
#           there would ADD noise rather than remove it.
def _blobGender(row, tfrrs_blob):
    if row[_SRC] != "tfrrs":
        return None
    info = tfrrs_blob.get((row[_MEET], row[_DIV]))
    if not info:
        return None
    return _genderFromDivName(info.get("div_name"))


# _genderFromDivName
# Purpose : recover gender from a tfrrs division title when the row itself
#           can't supply it (tfrrs XC has athlete_id=NULL and results has NO
#           gender column -- confirmed by peek_blob). Mirrors the era/distance
#           fitters' _genderFromName: match WOMEN FIRST, because "Women's 6k"
#           contains "men" as a substring -- a men-first test would misread
#           every women's race as male. Returns 'M'/'F' or None.
# Argument: div_name — the blob's division title, e.g. "Men's 8k" / "Invite 6K Women".
# Output  : 'F', 'M', or None (title carries no gender signal).
def _genderFromDivName(div_name):
    if not div_name:
        return None
    t = div_name.lower()
    if "women" in t or "girls" in t or "female" in t:   # WOMEN checked first
        return "F"
    if "men" in t or "boys" in t or "male" in t:
        return "M"
    return None


# Purpose : build the per-row traceback tuple printed in the sanity panel, so a
#           suspicious normalized time can be chased back to its raw source. Kept
#           tiny and separate because BOTH the write path (feeds the heaps) and
#           the debugger care about exactly these fields.
# Arguments: row — the raw DB tuple; pool — the classified pool; distance — the
#            resolved event distance in metres.
# Output   : (result_id, raw_time, distance, source, date, pool, meet_id, div_id) — plain,
#            printable, and independent of the normalized value it accompanies.
def _makeTrace(row, pool, distance):
    return (row[_ID], row[_TIME], distance, row[_SRC], row[_DATE], pool,
            row[_MEET], row[_DIV])


# _callLibrary
# Purpose : run the pure normalize chain for one already-validated row. Isolated
#           from the classification branches so _classifyRow stays short and the
#           library call site has exactly one home.
# Arguments: cfg — SportConfig (threads sport/event_short); row — raw tuple;
#            distance — resolved metres; gender — looked-up gender;
#            track_type/track_length — geometry for this row (or None -> no-op);
#            race_date — parsed date obj or None (-> era no-op).
# Output   : the normalizeResult dict {"normalized_time","pool","drop"}.
def _callLibrary(cfg, row, distance, gender, track_type, track_length, race_date,
                 weather=None, course=None, pool=None):
    return normalizeResult(
        time_seconds=row[_TIME],
        distance=distance,
        # ⚠ RAW GRADE STAYS, BUT IT NO LONGER DECIDES. `pool` below is the
        #   one this file already resolved -- grade_fix verdict, season
        #   level, school and source all considered. grade is still passed
        #   because nothing else here supplies it, and normalizeResult only
        #   consults it when no pool is handed in.
        grade=row[_GRADE],
        gender=gender,
        date=race_date,                        # -> season for era
        track_length=track_length,             # -> geometry length
        track_type=track_type,                 # -> banking (only if "Banked")
        sport=cfg.sport,                       # -> banded era + which geom leg
        event_short=row[_EVENT_SHORT],         # -> era band classification
        weather=weather,                       # -> race-day weather (None -> no-op)
        course=course,                         # -> per-course mud sensitivity
        pool=pool,                             # -> ALREADY RESOLVED, see above
    )


# Purpose : build the hot per-row classifier with all lookups + sport baked in,
#           so the loop calls it with just (row). The closure keeps the per-row
#           signature to one argument while carrying every constant.
#
# Output  : fn(row) -> (result_id, value, reason, trace) where
#             value  : the normalized_time (float) on WRITTEN, else None.
#             reason : one of _SkipReason.* — WHY the row went where it did.
#             trace  : the (id, raw_time, distance, source, date, pool, meet_id, div_id) tuple,
#                      present whenever we got far enough to know the distance
#                      (so the sanity heaps can print it); None on the earliest
#                      rejects where those fields aren't meaningful yet.
#
# The four skip branches are tested in the SAME order documented in _SkipReason,
# and a row is charged to the FIRST cause it trips. Each branch is one short
# step; the helpers above keep this body from growing long.
#
def _makeRowFn(cfg, geom_idx, genders, season_levels, meet_distances,
               tfrrs_distances,
               matched_twins, canon_distances, wx_idx,
               wheel_divs=frozenset(), wheel_persons=frozenset(),
               wheel_athletes=frozenset()):
    # PER-SPORT selection happens HERE, once, in the closure's constant pool --
    # the per-row checks below stay bare set/dict membership tests, so the
    # sport fix costs the hot loop nothing.
    result_drop = _RESULT_DROP_BY_SPORT[cfg.sport]
    gender_ov = _GENDER_OVERRIDES_BY_SPORT[cfg.sport]
    result_ov = _RESULT_OVERRIDE_BY_SPORT[cfg.sport]

    def fn(row):
        # 0) RESULT DROP — a hand-confirmed cooked individual row (real division,
        #    right distance, but this specific result is fake). Checked first.
        if row[_ID] in result_drop:
            return row[_ID], None, _SkipReason.MANUAL_DROP, None

        # 0b) DEDUP TWIN — at a canon-linked meet, the same physical result can
        #     exist as an anet copy AND a tfrrs copy (same person_id +
        #     canon_meet_id). Keep the anet copy; drop the tfrrs twin so the
        #     result is normalized ONCE. Unmatched rows (no twin) fall through.
        if row[_SRC] == "tfrrs" and row[_PERSON] is not None \
                and row[_CANON] is not None \
                and (row[_PERSON], row[_CANON]) in matched_twins:
            return row[_ID], None, _SkipReason.DEDUP_TWIN, None

        # 1) SENTINEL TIME — cheapest reject; never call anything on a non-time.
        if _isSentinelTime(row[_TIME]):
            return row[_ID], None, _SkipReason.SENTINEL_TIME, None

        # 1b) WHEELCHAIR/SEATED -- not a running race, so no normalized_time
        #     at all (see _SkipReason.WHEELCHAIR). Three seams, one pattern:
        #     TF reads the event name, tfrrs XC the blob's division title,
        #     anet XC the preloaded division set.
        #     THE PERSON FIRST: one labelled race withholds every race
        #     they ran, because the labels are incomplete (see
        #     _loadWheelchairPeople).
        if row[_PERSON] is not None:
            if row[_PERSON] in wheel_persons:
                return row[_ID], None, _SkipReason.WHEELCHAIR, None
        elif (row[_SRC], row[_AID]) in wheel_athletes:
            return row[_ID], None, _SkipReason.WHEELCHAIR, None
        if cfg.distance_src == "event_short":
            ev = row[_EVENT_SHORT]
            if ev and _WHEELCHAIR_RX.search(ev):
                return row[_ID], None, _SkipReason.WHEELCHAIR, None
        elif row[_SRC] == "tfrrs":
            info = tfrrs_distances.get((row[_MEET], row[_DIV]))
            if info and info.get("div_name") \
                    and _WHEELCHAIR_RX.search(info["div_name"]):
                return row[_ID], None, _SkipReason.WHEELCHAIR, None
        elif row[_DIV] in wheel_divs:
            return row[_ID], None, _SkipReason.WHEELCHAIR, None

        # 2) NO DISTANCE — can't put a time on the 5K scale without a distance.
        #    tfrrs XC falls back to the division_distances blob (keyed by
        #    (meet_id, div_id)), which ALSO yields the div_name -> gender that
        #    tfrrs rows otherwise lack. So one call resolves both.
        distance, blob_gender = _resolveDistanceGender(
            cfg, row, meet_distances, tfrrs_distances)
        # 2-borrow) If this is an anet row at a canon-linked meet whose distance
        #    is missing or corrupt, borrow the tfrrs twin's distance -- MATCHED BY
        #    GENDER, so a women's division gets the women's distance and a men's
        #    gets the men's (a meet often has both, e.g. 3-mile women + 4-mile
        #    men). A manual _DISTANCE_OVERRIDE already won inside resolve, so this
        #    only fills gaps the override table didn't cover.
        if row[_SRC] == "anet" and row[_CANON] is not None \
                and (distance is None or not _distanceSane(distance)):
            anet_gender = genders.get(row[_AID])     # anet gender for the match
            borrowed = _borrowCanonDistance(canon_distances, row[_CANON],
                                            anet_gender)
            if borrowed is not None:
                distance = borrowed
        if distance is None:
            return row[_ID], None, _SkipReason.NO_DISTANCE, None

        # 2a) INSANE DISTANCE — a resolved distance outside the race band is
        #     corruption (the *1609.344 monsters + the ~1,685 other over-ceiling
        #     rows). Drop, don't repair: only ~157 decode cleanly, not worth a
        #     special path. Traceable, so the sanity panel can still show them.
        if not _distanceSane(distance):
            trace = _makeTrace(row, "insane_distance", distance)
            return row[_ID], None, _SkipReason.INSANE_DISTANCE, trace

        # 2b) INSANE PACE — sane distance but impossible implied pace. Two sides:
        #     the slow ceiling (garbage-slow, the 15-hour "mile") and the absurd
        #     3:00/mile fast floor (obvious fakes, so they never reach the manual
        #     mislabel-triage list).
        #
        # ★ DEMOTED FROM A SKIP TO A CENSUS LABEL: THE ROW STILL NORMALIZES AND
        #   normalized_time IS WRITTEN. An impossible pace is a judgment about
        #   the LABEL, and refusing to write nt destroyed the only evidence a
        #   label detector can use: meet 264234 div 1050225 had 74 of 78 rows
        #   refused this way (real times normalized at a wrong distance), so
        #   the division was invisible to every rebuild pass -- the guard took
        #   the blame for the label's crime, and hid the corpse.
        #
        #   Writing nt is SAFE because rating creation has its own band:
        #   speed_ratings.packResults rejects normalized_time outside the
        #   pool's pace band (0.12-0.72 s/m of the anchor), and both of this
        #   guard's extremes land outside it -- these rows are never RATED, so
        #   no board or solve sees them. Only the rebuild's gap table, whose
        #   entire job is judging labels, now can.
        #
        # ! INSANE_RAW below is NOT demoted: the per-pool raw-time floor (the
        #   fake-elite guard) has no rating-time equivalent tight enough --
        #   the pack band is garbage-only loose -- so writing those would put
        #   fake elites back on the boards.
        pace_insane = (not _paceSane(row[_TIME], distance)
                       or not _paceNotAbsurd(row[_TIME], distance))

        # 3) UNKNOWN POOL — classify the pool HERE, via the imported poolFor (the
        #    same single-source-of-truth the library uses). Doing it now lets us
        #    NAME this cause distinctly, instead of it hiding inside the library's
        #    generic drop flag alongside bad-time drops.
        #    Gender: the genders dict (keyed by athlete_id) answers for anet, but
        #    tfrrs XC has athlete_id=NULL -> a miss -> None. Fall back to the
        #    blob-derived gender (from div_name). poolFor already routes a tfrrs
        #    M/F to college_m/college_f, so a recovered gender is all it needs.
        #     Gender precedence: the athlete's own gender (anet) > the gender that
        #     rode along with the distance > the division title, looked up fresh.
        #     That last fallback is what rescues rows whose distance came from an
        #     override (which returns gender=None and used to strand them in
        #     unknown_pool despite the blob knowing perfectly well).
        # ★ person_id IS AN athlete_id -- THE CANONICAL ONE. speed_ratings
        #   resolves gender with
        #       a.athlete_id = COALESCE(r.person_id, r.athlete_id)
        #   and this file only tried athlete_id. tfrrs XC rows carry
        #   athlete_id = NULL, so for them the dict always missed and gender
        #   fell to the division title.
        #
        #   That is fine for "Men's Race" and "Women's Race" and useless for
        #   "Open Race", which names no gender at all. Those rows became
        #   college_unknown_gender -- anchored at 6000 -- while the ENGINE,
        #   reading gender through person_id, put the same athletes in
        #   college_m at 8000. Written on one scale, read on another.
        #
        #   Measured at the John Reif Memorial Run (meet 27110): div 0
        #   "Men's Race 5000 Meters" normalised at factor 1.670, div 1
        #   "Open Race 5000 Meters" at 1.238 -- same course, same day, same
        #   5000m in the blob. Four of the top eight College (M) performances
        #   came out of div 1, at 143-155 off ordinary 15:00-16:06 runs.
        #
        # ! ORDER MATTERS: athlete_id first, then person_id, then the title.
        #   An anet row's own athlete gender is the most specific fact
        #   available; person_id only answers when the row has no athlete of
        #   its own.
        gender = (genders.get(row[_AID]) or genders.get(row[_PERSON])
                  or blob_gender or _blobGender(row, tfrrs_distances))
        gender = gender_ov.get((row[_MEET], row[_DIV]), gender)  # force if mislabeled
        _ro = result_ov.get(row[_ID])                # per-result gender pin (mixed div)
        if _ro is not None and _ro[1] is not None:
            gender = _ro[1]
        #     School: anet's real grades tell us each school's level; tfrrs rows
        #     have grade=NULL, so the school is the ONLY honest level signal they
        #     carry. poolFor uses it ONLY when the grade is unusable AND the
        #     school is unambiguous (not collided, not 'unattached', not thin) --
        #     otherwise it falls back to exactly the old tfrrs->college rule.
        #     Season level: the athlete-season's UNANIMOUS race-level verdict,
        #     from engine/season_level.py. poolFor ARBITRATES it against the
        #     grade -- grade stays the default and the season only wins where
        #     the two DISAGREE, so every row where they agree is untouched.
        #
        #     * WHY THE SEASON AND NOT THE RACE. The engine keys on
        #       (person_id, pool), so a pool that changed race-to-race would
        #       split one athlete into two half-solved unknowns. Cooper
        #       Lutkenhaus raced Millrose and a Texas UIL district meet six
        #       weeks apart as a high school junior: his season is NOT
        #       unanimous, the lookup returns None, and his grade stands.
        #
        #     ! Pieter Heesters, 2026 road 10 km, grade '12' four years after
        #       he left Gilman: pooled hs_m and rated 143.65. His 2026 season
        #       is unanimously open, so the stale grade loses.
        #
        #     The row already carries person_id and date, so this costs one
        #     dict lookup and no change to the SELECT -- adding a column there
        #     would have shifted every index constant in the row tuple.
        _ay = _academicYearOf(row[_DATE])
        # This sport's verdict if it has one, else the combined verdict --
        # so a season with no votes for this sport is no worse off than it was
        # before the table gained a sport dimension.
        # ! _sl_key IS BUILT UNCONDITIONALLY NOW, because the grade_fix
        #   lookup below needs it whether or not athlete_season_level exists.
        #   It was previously scoped inside this branch, so an empty
        #   season-level table would have left it unbound.
        _sl_key = (row[_PERSON] * 10000 + _ay
                   if (row[_PERSON] and _ay) else None)
        if season_levels[1] is not None and _sl_key is not None:
            _season_lvl = (season_levels[0].get((_sl_key, cfg.sport))
                           or season_levels[1].get(_sl_key))
        else:
            _season_lvl = None
        # ★ THE VERDICT OUTRANKS THE ROW'S OWN GRADE, exactly as
        #   pool_resolve.resolvePool's stage 0 does for the engine. The two
        #   must agree or normalized_time is written on one pool's anchor
        #   and read on another's.
        #
        #   The precedence mirrors resolvePool line for line:
        #     a corroborated grade REPLACES the raw one, and having replaced
        #     it the season level is suppressed -- a trusted grade decides
        #     alone;
        #     a verdict with no grade (the field rule, or a grade that
        #     stopped advancing) means the raw grade is NOT to be used, so
        #     the grade goes to None and the verdict's level carries the row;
        #     no verdict at all leaves both exactly as they were.
        # The precedence (verdict over grade, grade over season level) is
        # normalize_distance.normPoolFor now -- one function, shared with the
        # model's feature extraction, which must know this row's anchor.
        # See its header for the rule and why it mirrors resolvePool.
        _gf = (season_levels[2].get(_sl_key)
               if (season_levels[2] and row[_PERSON] and _ay) else None)
        pool = normPoolFor(row[_GRADE], gender, row[_SRC], row[_SCHOOL],
                           season_level=_season_lvl, fixed=_gf)
        if pool is None:                           # grade+gender+source -> no pool
            trace = _makeTrace(row, "unknown_pool", distance)
            return row[_ID], None, _SkipReason.UNKNOWN_POOL, trace

        # 3a) PER-POOL RAW-TIME FLOOR — now that the pool is known, reject any
        #     raw time faster than that pool's forgiving floor curve. This is the
        #     ROOT-CAUSE guard for fake-elite youth times: a 3:58/mile "4k" by a
        #     middle-schooler is impossible for ms_m (though nowhere near the open
        #     WR), so it dies here, before normalization. Forgiving -- the floor
        #     sits below each pool's real record, so no genuine performance drops.
        if not _rawTimePlausible(row[_TIME], distance, pool):
            trace = _makeTrace(row, pool, distance)
            return row[_ID], None, _SkipReason.INSANE_RAW, trace

        # 3b) POOL-RANGE DISTANCE — coarse reject of a distance grossly outside
        #     what this pool races (the leftover distance-mislabel pre-filter).
        if not _distanceInPoolRange(distance, pool, cfg.sport):
            trace = _makeTrace(row, pool, distance)
            return row[_ID], None, _SkipReason.INSANE_DISTANCE, trace

        # 4) THE LIBRARY — geometry routes by source; date rides the row.
        track_type, track_length = geom_idx.lookup(
            row[_SRC], row[_MEET], row[_DIV], row[_EVENT_ID])
        race_date, date_corrupt = _parseRowDate(row[_DATE])   # (date|None, corrupt)
        if date_corrupt:                           # parsed but insane year (2222)
            trace = _makeTrace(row, pool, distance)   # = synthetic/corrupt row
            return row[_ID], None, _SkipReason.INSANE_DATE, trace
        # WEATHER: per-race lookup (cell+date). None when disabled or this
        # meet/day has no weather -> normalize treats it as the reference.
        weather, course = (wx_idx.lookup(row[_SRC], row[_MEET], race_date)
                           if wx_idx is not None else (None, None))
        # ! THE POOL RESOLVED ABOVE, NOT ONE RE-DERIVED INSIDE THE LIBRARY.
        #   normalizeResult's own getPool(grade, gender) sees only the raw
        #   grade -- no school, no source, no season verdict, no grade_fix --
        #   and since per-pool anchors it is choosing the SCALE, not just a
        #   label. Handing it the pool this file already computed is what
        #   makes the written normalized_time and the engine's reading of it
        #   the same number.
        out = _callLibrary(cfg, row, distance, gender,
                            track_type, track_length, race_date,
                            weather=weather, course=course, pool=pool)

        trace = _makeTrace(row, out["pool"], distance)
        if out["drop"] or out["normalized_time"] is None:
            # Reached the library but it declined (bad time/distance INSIDE the
            # library, now unambiguous because unknown_pool was caught above).
            return row[_ID], None, _SkipReason.LIBRARY_DROP, trace
        if pace_insane:
            # ★ WRITTEN AND LABELLED. The census still counts it under
            #   insane_pace (the number to watch), but the value goes to the
            #   table so the rebuild's gap table can finally see the division
            #   the bad label broke. packResults' pool pace band keeps it out
            #   of the ratings.
            return (row[_ID], out["normalized_time"],
                    _SkipReason.INSANE_PACE, trace)
        return row[_ID], out["normalized_time"], _SkipReason.WRITTEN, trace
    return fn


# ================================================================== #
# CENSUS  —  the reason counters + sanity buckets, behind ONE record()
#            call so the hot loop body stays a single line.
# ================================================================== #

# Sanity-panel knobs (small, so the memory story is trivial and stated):
_TOP_N = 20                 # how many fastest AND slowest rows to retain
_RESERVOIR_CAP = 200_000    # sample size for approximate percentiles (~1.6MB)
_PCTS = [0.25, 0.5, 0.75]   # which percentiles the panel prints


# ---- one-pass SUSPECT-DIVISION audit ----
# The sanity panel only shows the top-N extreme ROWS, so one loud meet (50 fake
# rows) floods it and hides every other suspect -> a fix-one-see-20 treadmill.
# The audit instead flags whole DIVISIONS, each once, so a single dry run prints
# the COMPLETE remaining suspect set (post-override, post-guard, post-curve),
# not just the current tail. Three signal types:
#   fast     : division median normalized_time under its pool floor  (mislabel/
#              fake-fast) -- the "distance stored too long" family.
#   slow     : division median over a generous ceiling (garbage-slow, short
#              races scaled to hours).
#   mislabel : nearly the WHOLE division is fast (frac_under ~1.0) -- the
#              signature of a distance mislabel vs a real elite race (few fast).
_AUDIT_FAST_FLOOR_5K = {           # per-pool "suspicious-fast" median floor (s)
    "college_m": 780.0, "college_f": 900.0,
    "hs_m": 820.0,      "hs_f": 940.0,
    "ms_m": 900.0,      "ms_f": 1020.0,
}
_AUDIT_FAST_DEFAULT = 820.0
_AUDIT_SLOW_CEIL_S = 3000.0        # 50:00 5k-equiv median => garbage-slow division
_AUDIT_MIN_ROWS = 5                # need a few rows to judge a division
_AUDIT_MISLABEL_FRAC = 0.80        # frac of rows under floor to call it a mislabel

# Drop reasons that mean DATA CORRUPTION (not a legit non-time). A division whose
# rows mostly land here (e.g. stored 0m -> insane_distance) is a corrupt division
# the audit must surface, even though those rows produced no normalized value.
_CORRUPT_DROP_REASONS = frozenset({
    _SkipReason.INSANE_DISTANCE,
    _SkipReason.INSANE_RAW,
    _SkipReason.INSANE_DATE,
})


# _auditFastFloor
# Purpose : the suspicious-fast median floor for a pool (audit only; looser than
#           the row-level raw curve, since here we judge a whole division median).
# Arguments: pool — classified pool.
# Output  : floor in seconds.
def _auditFastFloor(pool):
    return _AUDIT_FAST_FLOOR_5K.get(pool, _AUDIT_FAST_DEFAULT)


@dataclass
# Purpose : accumulate per-division normalized-time stats over the whole stream,
#           so the audit can flag every suspect division once. Keyed by
#           (meet, div, pool); stores count, min, and the values needed for a
#           median. To bound memory we keep only a small capped sample per
#           division for the median (exact median of a whole huge division is
#           unnecessary for a floor test).
# Fields:
#   stats : (meet, div, pool) -> {"n", "min", "under", "sample", "corrupt"}.
#           "corrupt" counts rows in this division DROPPED for a data-corruption
#           reason (insane_distance/raw/date) -- so a division whose rows are
#           mostly discarded (e.g. stored 0m) SURFACES in the audit instead of
#           vanishing (dropped rows never produced a normalized value, so the
#           written-only path could not see them -- the 0m blind spot).
class _SuspectDivisions:
    stats: dict = field(default_factory=dict)

    # _slot — get/create the per-division record (shared by write + drop paths).
    def _slot(self, meet, div, pool):
        key = (meet, div, pool)
        s = self.stats.get(key)
        if s is None:
            s = {"n": 0, "min": None, "under": 0, "sample": [], "corrupt": 0}
            self.stats[key] = s
        return s

    # offer — fold one WRITTEN row into its division's running stats. Cheap:
    # a dict bump, a min, an under-floor tally, and a capped sample append.
    def offer(self, value, trace):
        pool, meet, div = trace[5], trace[6], trace[7]
        s = self._slot(meet, div, pool)
        s["n"] += 1
        if s["min"] is None or value < s["min"]:
            s["min"] = value
        if value < _auditFastFloor(pool):
            s["under"] += 1
        if len(s["sample"]) < 64:                  # capped sample for the median
            s["sample"].append(value)

    # offerDrop — fold one CORRUPTION-DROPPED row (insane_distance/raw/date) into
    # its division so a division that is mostly discarded still surfaces. Uses the
    # trace's meet/div/pool (pool may be a reason-string on some early drops, but
    # meet+div are always real), keeping the division visible.
    def offerDrop(self, trace):
        pool, meet, div = trace[5], trace[6], trace[7]
        s = self._slot(meet, div, pool)
        s["corrupt"] += 1

    # flagged — after the stream, return the suspect divisions with their kind
    # ('fast' | 'slow' | 'mislabel' | 'corrupt'), median, min, n, frac_under. One
    # entry per division; a division can be flagged for more than one reason.
    def flagged(self):
        import statistics
        out = []
        for (meet, div, pool), s in self.stats.items():
            corrupt = s["corrupt"]
            written = s["n"]
            # a division qualifies if it has enough WRITTEN rows to judge, OR it
            # is largely CORRUPT (many dropped rows) -- the 0m blind-spot case.
            if written < _AUDIT_MIN_ROWS and corrupt < _AUDIT_MIN_ROWS:
                continue
            kinds = []
            med = statistics.median(s["sample"]) if s["sample"] else None
            frac = (s["under"] / written) if written else 0.0
            if written >= _AUDIT_MIN_ROWS and med is not None:
                if med < _auditFastFloor(pool):
                    kinds.append("fast")
                if med > _AUDIT_SLOW_CEIL_S:
                    kinds.append("slow")
                if frac >= _AUDIT_MISLABEL_FRAC:
                    kinds.append("mislabel")
            # corrupt: most of the division's rows were discarded for corruption
            if corrupt >= _AUDIT_MIN_ROWS and corrupt >= written:
                kinds.append("corrupt")
            if not kinds:
                continue
            out.append({
                "meet": meet, "div": div, "pool": pool,
                "n": written, "corrupt": corrupt,
                "median": med if med is not None else -1.0,
                "min": s["min"] if s["min"] is not None else -1.0,
                "frac_under": frac, "kinds": "+".join(kinds),
            })
        out.sort(key=lambda r: r["median"])        # worst (fastest) first
        return out


@dataclass
# Purpose : accumulate, over the whole stream, (a) a per-reason ROW count and
#           (b) the sanity panel's extremes + percentile sample. One object so
#           the drain loop calls census.record(...) and nothing else.
#
# Fields:
#   counts    : reason-tag -> row count (every row lands in exactly one).
#   fastest   : _TopBucket keeping the N SMALLEST normalized times (the
#               suspicious-fast floor where unit/label corruption surfaces).
#   slowest   : _TopBucket keeping the N LARGEST (the suspicious-slow ceiling).
#   reservoir : bounded uniform sample of WRITTEN values -> approx percentiles.
#
class _Census:
    counts:    dict = field(default_factory=dict)
    fastest:   _TopBucket = field(default_factory=lambda: _TopBucket(_TOP_N, largest=False))
    slowest:   _TopBucket = field(default_factory=lambda: _TopBucket(_TOP_N, largest=True))
    reservoir: _Reservoir = field(default_factory=lambda: _Reservoir(_RESERVOIR_CAP))
    suspects:  _SuspectDivisions = field(default_factory=_SuspectDivisions)

    # Tally one row. Always bump its reason counter; and if it was WRITTEN,
    # feed the normalized value into both extreme buckets, the reservoir, and
    # the per-division suspect accumulator (skipped rows touch only the counter).
    #
    def record(self, value, reason, trace):
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if reason == _SkipReason.WRITTEN:
            self.fastest.offer(value, trace)
            self.slowest.offer(value, trace)
            self.reservoir.offer(value)
            self.suspects.offer(value, trace)
        elif reason in _CORRUPT_DROP_REASONS and trace is not None:
            # a division whose rows are DROPPED for corruption (e.g. stored 0m ->
            # insane_distance) would otherwise be invisible to the audit, which
            # only sees written rows. Track the drop so the division surfaces.
            self.suspects.offerDrop(trace)


# ================================================================== #
# THE WRITE  —  COPY to a staging table, then ONE set-based merge at the end.
# ================================================================== #
#
# WHY THIS CHANGED (measured, not guessed). The old path ran
#     UPDATE results SET normalized_time = ... FROM (VALUES ...) WHERE ...
# once per 10,000 rows. EXPLAIN (ANALYZE, BUFFERS) on exactly that statement:
#
#     Update on results  (actual time=5538.716 ...)
#       Buffers: shared hit=317387 read=55752 dirtied=54575 written=38290
#       ->  Nested Loop  (actual time=0.064..258.447 rows=10000)
#             ->  Index Scan using results_pkey  (10000 searches, ~22us each)
#
# FINDING the rows cost 258 ms. WRITING them cost 5,281 ms — 95% of the
# statement. 54,575 dirty 8 KB pages for 10,000 rows is 45 KB of disk written
# to change one 4-byte float. The cause is write amplification, not a bad plan:
#   * every UPDATE writes a NEW heap tuple (Postgres never updates in place),
#   * HOT (heap-only tuple) cannot fire, because `normalized_time` is indexed
#     AND the table was bulk-loaded at fillfactor=100 (no free space on-page),
#   * so all EIGHT indexes on `results` take a fresh random-page insertion.
#
# No batch size, no synchronous_commit, no COPY-into-UPDATE fixes that. The
# only operation whose cost is NOT proportional to (rows x indexes) is:
#
#   1. COPY the (result_id, normalized_time) pairs into an UNLOGGED, INDEXLESS
#      staging table. COPY is the fastest path bytes can take into Postgres:
#      it skips the SQL parser and planner entirely.
#   2. Build the finished table in ONE pass:
#         CREATE TABLE results_new AS
#           SELECT <cols>, s.nt FROM results r LEFT JOIN bf_staging s ...
#      One sequential scan, one hash join, sequential heap writes.
#   3. Build each index ONCE, by sorting (sequential writes), instead of
#      34.8M random B-tree insertions.
#   4. Swap the tables.
#
# Cost model, XC (34,777,176 rows), measured 289 us/row on the UPDATE path:
#   old:  ~2.8 hours of UPDATE
#   new:  COPY ~2 min + rebuild ~10 min + 8 index builds ~15 min  =>  ~30 min
#
# The write path is now selected by `write_mode`:
#   "update" — the original per-batch UPDATE. Kept as the reference path and
#              the fallback. Now returns the CORRECT rowcount (see below).
#   "copy"   — stage + rebuild. The fast path. Default.

_WRITE_PAGE = 10_000                # rows per server round trip on the update path
_STAGING = "bf_staging"             # scratch table name (per-sport suffix added)


# _stagingTable
# Purpose : one scratch table per sport, so `--sport both` never collides.
# Output  : 'bf_staging_xc' / 'bf_staging_tf'.
def _stagingTable(sport):
    return f"{_STAGING}_{sport.lower()}"


# _createStaging
# Purpose : (re)create the landing zone for (result_id, normalized_time).
# Detail  : NO INDEXES -- we never look a row up in it, we hash-join it once.
#           UNLOGGED skips the WAL entirely; on a server crash Postgres simply
#           TRUNCATEs it, which costs nothing because this backfill is idempotent
#           and restartable. That is the whole safety argument: it is scratch.
#           DROP-first makes the script rerunnable with no stale half-filled table.
def _createStaging(write_conn, sport):
    table = _stagingTable(sport)
    with write_conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE UNLOGGED TABLE {table} "
                    f"(result_id bigint NOT NULL, nt real NOT NULL)")
    write_conn.commit()
    print(f"  staging table {table} created (unlogged, no indexes)")
    return table


# _bufferToCopyText
# Purpose : render [(nt, rid), ...] into the tab-separated text COPY expects.
# Syntax  : COPY's default TEXT format is: fields separated by \t, rows ended by
#           \n, no quoting. Our two fields are a float and an int, so neither can
#           contain a tab or a newline -- no escaping is needed and none is done.
#           `"".join(generator)` builds the whole payload in ONE allocation;
#           repeated `s += ...` would be quadratic.
#           io.StringIO wraps that string in a file-like object, because
#           copy_expert reads from a file, not from a string.
# Note    : column order here is (result_id, nt) -- the STAGING table's order --
#           while the buffer holds (nt, rid). The swap happens right here, once.
def _bufferToCopyText(updates):
    # \N is COPY's NULL literal -- skips now ride the buffer too
    payload = "".join(
        f"{rid}\t{nt if nt is not None else chr(92) + 'N'}\n"
        for nt, rid in updates)
    return io.StringIO(payload)


# _copyToStaging
# Purpose : ship one buffer into the staging table with a single COPY.
# Syntax  : copy_expert takes a full COPY statement and a file object. We use
#           `COPY tbl (cols) FROM STDIN` -- FROM STDIN means "read the rows from
#           this connection", which is what the file object supplies.
#           Naming the columns explicitly means we do not depend on the table's
#           physical column order.
# Returns : len(updates). COPY has no per-row rowcount to consult; every row in
#           the payload lands or the whole statement raises. That is stronger
#           than a rowcount, not weaker.
def _copyToStaging(write_conn, table, updates):
    # ★ A SKIPPED ROW IS OMITTED FROM STAGING, NOT STAGED AS NULL -- AND ON
    #   THIS PATH THAT IS HOW IT BECOMES NULL.
    #
    #   _accumulate queues (None, rid) for every skip so the UPDATE fallback
    #   erases a fossil normalized_time in place. That is right for UPDATE and
    #   fatal here: this table is `nt real NOT NULL`, so the first batch
    #   carrying any skip died with
    #
    #       NotNullViolation: null value in column "nt" of relation
    #       "bf_staging_xc"        COPY line 14: "60176255 \N"
    #
    #   and the whole COPY is one statement, so the batch took the good rows
    #   down with it.
    #
    # ! AND OMITTING THEM IS NOT A COMPROMISE -- IT IS THE DESIGN.
    #   _mergeSelectList emits `s.nt AS normalized_time` with NO COALESCE, so
    #   a row that staging does not mention comes out of the LEFT JOIN as
    #   NULL already. Staging the NULL would write the same value the join
    #   produces for free. _accumulate's own comment says as much.
    #
    # ! len(updates) STILL, NOT len(rows). The caller counts these as resolved,
    #   and a skip IS resolved -- to NULL. Returning the copied count would
    #   silently restate the census the drain loop already keeps.
    rows = [u for u in updates if u[0] is not None]
    if rows:
        with write_conn.cursor() as cur:
            cur.copy_expert(f"COPY {table} (result_id, nt) FROM STDIN",
                            _bufferToCopyText(rows))
    write_conn.commit()                    # bounded memory; the table is unlogged
    return len(updates)


# _flushPage
# Purpose : the UPDATE path -- write ONE page with ONE statement, report matches.
# Why one page per call: execute_values with page_size < len(rows) silently
#   issues MULTIPLE statements, and cur.rowcount then reports only the LAST one.
#   Passing page_size >= len(page) guarantees exactly one statement, so
#   cur.rowcount is the true match count for this page. (This is the bug that
#   made a 50,000-row batch report `written 10,000`.)
def _flushPage(cur, table, page):
    psycopg2.extras.execute_values(cur, f"""
        UPDATE {table}
        SET normalized_time = data.nt
        FROM (VALUES %s) AS data(nt, rid)
        WHERE {table}.result_id = data.rid
    """, page, page_size=len(page))        # one statement <=> trustworthy rowcount
    return cur.rowcount


# _flushUpdate
# Purpose : the ORIGINAL write path, corrected. Kept as the reference and fallback.
# Fixes vs the version that shipped:
#   * rowcount SUMMED across pages (was: last page only -> 5x undercount);
#   * cursor closed via `with` (was: one leaked cursor per batch).
def _flushUpdate(write_conn, table, updates):
    written = 0
    with write_conn.cursor() as cur:       # `with` guarantees cur.close()
        for i in range(0, len(updates), _WRITE_PAGE):
            written += _flushPage(cur, table, updates[i:i + _WRITE_PAGE])
    write_conn.commit()                    # ONE commit per batch (bounded WAL)
    return written


# _flush
# Purpose : the single write entry point. Dispatches on write_mode; a no-op on an
#           empty buffer. `target` is the results table on the update path and
#           the STAGING table on the copy path -- the caller decides which.
def _flush(write_conn, target, updates, write_mode):
    if not updates:
        return 0
    if write_mode == "copy":
        return _copyToStaging(write_conn, target, updates)
    return _flushUpdate(write_conn, target, updates)


# ================================================================== #
# THE MERGE  —  rebuild the results table once, from the staging table.
# ================================================================== #

# _tableColumns
# Purpose : the live column list of `table`, in physical order.
# Why read it instead of hardcoding: `results` has ~28 columns and has already
#   grown new ones this project (person_id, canon_meet_id). A hardcoded list
#   would silently DROP any column added since this file was written -- a data
#   loss bug that no test would catch. We ask the catalog every run.
# Syntax  : information_schema.columns is the SQL-standard catalog view.
#           ordinal_position preserves physical order, which CREATE TABLE AS
#           will then reproduce in the new table.
def _tableColumns(cur, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    return [r[0] for r in cur.fetchall()]


# _mergeSelectList
# Purpose : build the SELECT list for the rebuild: every column of `results`
#           verbatim, EXCEPT normalized_time, which comes from staging.
# Syntax  : `s.nt` bare -- a LEFT JOIN leaves s.nt NULL for
#           any row the backfill SKIPPED (sentinel time, insane distance, dedup
#           twin...). COALESCE keeps that row's EXISTING value rather than
#           nulling it. This is what makes the rebuild non-destructive: skipped
#           rows are preserved exactly as they were.
#   If you WANT skipped rows nulled (a true unconditional rewrite), replace the
#   COALESCE with a bare `s.nt`. The current backfill semantics are "leave it
#   alone", so COALESCE is the faithful translation of the UPDATE path.
# ===========================================================================
# THIS BACKFILL IS AN UNCONDITIONAL REWRITE. `s.nt`, NOT COALESCE(s.nt, ...).
# ===========================================================================
# The merge used to write `COALESCE(s.nt, r.normalized_time)`, so a row the
# backfill SKIPPED kept whatever an older run had left in the column. That reads
# as conservative and is the opposite: this pass LOOKED at every row and DECIDED
# that the skipped ones have no valid normalized_time.
#
# It silently undid the corrections file. `corrections._DISTANCE_DROP` contains
# (171237, 716785) and (171237, 718066) -- the Blackfoot divisions. The backfill
# dutifully dropped them, wrote them nothing, and COALESCE restored the stale
# values. Those rows then walked into the speed engine and took eight of the top
# ten slots in hs_m|XC with a 5K-equivalent of 609 seconds.
#
# The same line preserved normalized_time = 20.6s on rows the sanity gates had
# rejected, and let `dedup_twin` rows keep an old value and re-enter the engine
# as duplicate races.
#
# A stale value is worse than a missing one. NULL says "we do not know".
# 609 seconds says "this high schooler ran a 10-minute 5K".
#
# CONSEQUENCE: ~4.2M XC rows (sentinel_time, dedup_twin, insane_*, unknown_pool,
# library_drop) go from a fossil value to NULL. That is the correct state: every
# one of them was examined and refused.
#
# The `--limit + --apply + --write-mode copy` combination is already blocked in
# main(), which matters more now: a partial staging set would NULL every row past
# the limit.
def _mergeSelectList(cols):
    parts = []
    for c in cols:
        if c == "normalized_time":
            parts.append("s.nt AS normalized_time")     # NOT COALESCE -- see above
        else:
            parts.append(f"r.{c}")
    return ",\n               ".join(parts)


# _indexDefsFor
# Purpose : capture every index on `table` as the exact CREATE statement needed to
#           rebuild it, and the PK constraint separately.
# Syntax  : pg_indexes.indexdef IS a full `CREATE INDEX name ON tbl USING ...`
#           statement -- Postgres hands us the DDL. We must rename both the index
#           and the table, because the new table is `<table>_new` and index names
#           are database-wide unique.
# The PRIMARY KEY is excluded here: it is enforced by a CONSTRAINT, and
#   `CREATE INDEX results_pkey ...` would create a plain index with no constraint.
#   We re-add it with ALTER TABLE ... ADD PRIMARY KEY instead.
def _indexDefsFor(cur, table):
    cur.execute("""
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
        ORDER BY indexname
    """, (table,))
    defs = []
    for name, ddl in cur.fetchall():
        if name.endswith("_pkey"):
            continue                           # rebuilt as a constraint, not an index
        # ' ON public.results ' -> ' ON public.results_new ', and the index name
        # gets a _new suffix so it cannot collide with the still-living original.
        ddl = ddl.replace(f" {name} ON ", f" {name}_new ON ", 1)
        ddl = ddl.replace(f".{table} USING", f".{table}_new USING", 1)
        ddl = ddl.replace(f" {table} USING", f" {table}_new USING", 1)
        defs.append((name, ddl))
    return defs


# _timedExec
# Purpose : run one statement, print how long it took. These take minutes; a
#           silent terminal for 20 minutes is indistinguishable from a hang.
def _timedExec(cur, sql, label):
    t0 = time.time()
    cur.execute(sql)
    print(f"    [{time.time() - t0:7.1f}s] {label}")


# _buildNewTable
# Purpose : STEP 1 of the merge. One sequential scan of `results`, one hash join
#           against the (small, unlogged) staging table, sequential heap writes
#           into a brand new table. No indexes exist on it yet, so nothing is
#           maintained during the write. This is the whole speedup.
# Syntax  : CREATE UNLOGGED TABLE ... AS SELECT. Unlogged again skips the WAL for
#           the biggest write in the job; we flip it to LOGGED after the swap.
def _buildNewTable(cur, table, staging, cols):
    # LOGGED, not UNLOGGED. This is counterintuitive and it cost me a bug:
    #   UNLOGGED here saves the WAL on this one statement, but the table must
    #   later become LOGGED, and `ALTER TABLE ... SET LOGGED` REWRITES the whole
    #   heap AND every index on it. So unlogged-then-convert = two full heap
    #   rewrites plus an index rebuild you already paid for. Creating it LOGGED
    #   is one rewrite. The WAL is written sequentially either way.
    # work_mem sizes the hash table for the LEFT JOIN against staging. At the
    #   default 4MB, a 34.8M-row hash table spills to disk in hundreds of
    #   batches. 2GB holds it in RAM. work_mem is per-node and per-session --
    #   no config file, no restart.
    cur.execute("SET work_mem = '2GB'")
    cur.execute("SET max_parallel_workers_per_gather = 4")   # parallel seq scan
    _timedExec(cur, f"""
        CREATE TABLE {table}_new AS
        SELECT {_mergeSelectList(cols)}
        FROM {table} r
        LEFT JOIN {staging} s ON s.result_id = r.result_id
    """, f"CREATE TABLE {table}_new AS SELECT ... (heap rebuild)")


# _buildIndexes
# Purpose : STEP 2. Build the PK, then every secondary index, ONCE each, on the
#           finished heap. Postgres builds an index by SORTING the whole column
#           and writing the B-tree bottom-up -- sequential I/O. That is what the
#           UPDATE path could never do: it inserted one random key at a time.
# maintenance_work_mem governs how much of that sort stays in RAM.
# max_parallel_maintenance_workers lets Postgres sort with several cores.
#
# ★ THE SECONDARY INDEXES ARE BUILT SEVERAL AT A TIME (2026-09-26, owner:
#   "speed up every step in pipeline possible"). Eight indexes one after
#   another is eight full scans of the heap (58GB on results_tf) in series. They are
#   independent -- same table, different columns, no shared state -- and
#   concurrent CREATE INDEXes on one table take SHARE locks, which do not
#   conflict with each other. The heap scans also ride each other's pages
#   (synchronize_seqscans), so two builds read the heap about once. Same
#   shape as racecast/build_ranking_results.py's _INDEX_JOBS; the DDL is
#   the same statements, so the indexes are the same indexes.
#
# ⚠ WHICH MEANS <table>_new IS COMMITTED BEFORE THE SWAP, NOT AT IT. Another
#   connection cannot see a table this connection has not committed. So the
#   heap and its PK commit first, then the builders run on their own pooled
#   connections, then the swap -- whose first act was already a commit (see
#   _swapWithRetry). Nothing reads <table>_new until the swap renames it, so
#   the live table is never touched by any of this.
#
# ! A FAILED BUILD DROPS <table>_new, so a failed run leaves what it always
#   left: the live table as it was, no <table>_new, the staging table intact
#   for scripts/resume_merge.py --finish. Only a process KILLED mid-build
#   (SIGKILL, power loss) now leaves <table>_new behind; the next run's
#   _assertNoLeftovers stops at second zero and resume_merge.py --clean
#   drops it (it was never live, so dropping it is always safe).
#
# ! EVERY SETTING GOES THROUGH dbSetting()/dbJobs(). This builder used to SET
#   8GB and 6 workers straight over the quiet caps (16GB of sort memory with
#   both sports building); at several builds per sport that would multiply.
#   Now the peak is jobs x memory x 2 sports: under XCP_DB_QUIET=1, 2 builds
#   x 2GB x 2 sports = 8GB; unquieted, 3 x 2GB x 2 = 12GB; strict, one build
#   at a time at 512MB, serial as before.
_INDEX_JOBS = dbJobs(3)
_INDEX_MEM = "2GB"          # per build -- see the peak above
_INDEX_WORKERS = 2          # per build; 3 builds x 2 workers stays inside
                            # max_parallel_workers (8); a build granted fewer
                            # workers than it asked for just runs with fewer


def _buildOneIndex(job):
    """One CREATE INDEX on its own pooled connection, committed there."""
    name, ddl = job
    t0 = time.time()
    with getConn() as c:
        with c.cursor() as cur:
            cur.execute(f"SET maintenance_work_mem = "
                        f"'{dbSetting('maintenance_work_mem', _INDEX_MEM)}'")
            cur.execute(f"SET max_parallel_maintenance_workers = "
                        f"{dbSetting('max_parallel_maintenance_workers', _INDEX_WORKERS)}")
            cur.execute(ddl)
        c.commit()
    return name, time.time() - t0


def _buildSecondaries(index_defs):
    """Every secondary index, _INDEX_JOBS at a time. On the first failure the
    builds not yet started are cancelled, the running ones finish, and the
    failure is raised."""
    jobs = max(1, min(_INDEX_JOBS, len(index_defs)))
    print(f"    building {len(index_defs)} secondary indexes, {jobs} at a time")
    pool = cf.ThreadPoolExecutor(max_workers=jobs)
    try:
        futs = [pool.submit(_buildOneIndex, job) for job in index_defs]
        cf.wait(futs, return_when=cf.FIRST_EXCEPTION)
    finally:
        # ! cancel_futures: on a failure (or Ctrl-C) the builds not yet
        #   started never start; the running ones finish before we return,
        #   so the DROP that follows a failure does not queue behind them.
        pool.shutdown(wait=True, cancel_futures=True)
    for f in futs:
        if not f.cancelled() and f.exception() is not None:
            raise f.exception()
    for f in futs:
        name, dt = f.result()
        print(f"    [{dt:7.1f}s] index {name}_new")


def _buildIndexes(cur, table, index_defs):
    # maintenance_work_mem sizes the SORT that builds each B-tree bottom-up.
    # This is the whole reason the rebuild is fast: sorted, sequential writes
    # instead of 34.8M random single-key insertions. Both are session settings.
    cur.execute(f"SET maintenance_work_mem = "
                f"'{dbSetting('maintenance_work_mem', '8GB')}'")
    cur.execute(f"SET max_parallel_maintenance_workers = "
                f"{dbSetting('max_parallel_maintenance_workers', 6)}")
    # The PK alone, on this connection, before anything else: ADD PRIMARY KEY
    # takes ACCESS EXCLUSIVE and would queue every concurrent build behind it.
    _timedExec(cur,
        f"ALTER TABLE {table}_new ADD PRIMARY KEY (result_id)",
        f"PRIMARY KEY on {table}_new")
    cur.connection.commit()               # the builders can see <table>_new now
    try:
        _buildSecondaries(index_defs)
    except BaseException:
        _dropHalfBuilt(cur, table)
        raise


def _dropHalfBuilt(cur, table):
    """Put a failed index phase back to where a rolled-back merge would be."""
    try:
        cur.connection.rollback()
        cur.execute(f"DROP TABLE IF EXISTS {table}_new")
        cur.connection.commit()
        print(f"    index build failed: dropped {table}_new "
              f"({table} untouched; staging kept for resume_merge.py --finish)")
    except Exception as exc:                               # noqa: BLE001
        print(f"    index build failed, and {table}_new could not be dropped "
              f"({type(exc).__name__}): resume_merge.py --table {table} --clean")


# _swapTables
# Purpose : STEP 3. Replace the old table with the new one, ATOMICALLY.
# Detail  : all of this runs inside ONE transaction (we do not commit until the
#           caller does). Postgres takes an ACCESS EXCLUSIVE lock on the rename;
#           any concurrent reader blocks for the duration and then sees the new
#           table. No reader ever sees a half-swapped state.
#   * SET LOGGED restores WAL protection to the real table.
#   * The old table is renamed, NOT dropped -- `results_old` survives until you
#     drop it by hand. That is your undo button, and it is why this is safe.
#   * Index names are renamed back so nothing downstream that hardcodes an index
#     name breaks.
# ★ AND IT WAITS IMPATIENTLY, SO THE SITE CAN STAY UP THROUGH A RUN.
#
#   Every statement below needs ACCESS EXCLUSIVE, which conflicts with the
#   ACCESS SHARE any reader holds. With the site stopped that is free. With
#   it serving, gunicorn's workers are readers -- and Postgres queues lock
#   requests IN ORDER, so a rename waiting behind one slow page load makes
#   every NEW request queue behind the rename. One slow query freezes the
#   whole site, which is a worse outage than the maintenance window it was
#   meant to avoid, and it arrives unannounced.
#
#   lock_timeout makes the attempt give up instead of queueing. On timeout
#   the queue drains, readers finish, and the next attempt takes the gap.
#   This is the standard online-DDL pattern and it is the ONLY thing between
#   "the pipeline runs weekly with the site up" and a random freeze.
#
# ! LOCAL, so it dies with the transaction and never leaks into the session
#   that runs the rest of the merge.
#
# ⚠ AND IT MUST BE ALL-OR-NOTHING. The renames are ONE transaction: half a
#   swap leaves `results_old` claiming `results_pkey` with no `results` at
#   all. A timeout mid-sequence rolls the whole thing back, which is why the
#   retry can simply start over.
SWAP_LOCK_TIMEOUT = "3s"
SWAP_ATTEMPTS = int(_os.environ.get("XCP_SWAP_ATTEMPTS") or 20)
SWAP_BACKOFF = 15          # seconds between attempts; 20 x 15s = 5 minutes


def _lockHolders(cur, table):
    """Who holds a lock on `table` right now -- printed when the swap waits.

    ⚠ THE 2026-09-22 RUN LOST ITS XC BACKFILL HERE AND COULD NOT SAY WHY.
      "readers hold the table" twenty times, then a RuntimeError telling the
      reader to check pg_stat_activity -- five minutes after the holder was
      gone. So the holders are read WHILE they hold it: pid, what it is
      (a client, or autovacuum -- an anti-wraparound vacuum does not yield),
      how long its transaction has been open, and its query.
    """
    try:
        cur.execute("""
            SELECT a.pid, coalesce(a.application_name, ''), a.backend_type,
                   coalesce(a.state, ''), l.mode,
                   coalesce(extract(epoch FROM now() - a.xact_start)::int, 0),
                   left(regexp_replace(coalesce(a.query, ''), '\\s+', ' ', 'g'),
                        140)
            FROM   pg_locks l
            JOIN   pg_stat_activity a ON a.pid = l.pid
            WHERE  l.relation = to_regclass(%s)
              AND  l.granted
              AND  a.pid <> pg_backend_pid()
            ORDER  BY a.xact_start NULLS LAST""", (table,))
        rows = cur.fetchall()
        cur.execute("ROLLBACK")
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK")
        return [f"(could not read pg_locks: {type(exc).__name__})"]
    return [f"pid {pid}  {app or btype}  {state}  {mode}  xact {age}s  {q}"
            for pid, app, btype, state, mode, age, q in rows]


def _swapWithRetry(cur, body, what, table=None):
    """Run `body(cur)` inside a transaction that refuses to queue for locks."""
    # ⚠⚠ COMMIT WHAT CAME BEFORE, OR THE FIRST ROLLBACK TAKES IT (2026-09-25:
    #    "relation results_tf_new does not exist" on attempt 4). The merge
    #    builds <table>_new and every index on the SAME connection, in the
    #    transaction psycopg2 opened implicitly; the `BEGIN` below is then
    #    only a warning inside it, and the lock-timeout ROLLBACK undid the
    #    whole rebuild -- heap and indexes -- after three readers (a
    #    link_freshmen dry run left running server-side) held the table.
    #    Committed here, <table>_new survives a failed swap, which is what
    #    the RuntimeError below has always promised.
    cur.connection.commit()
    holders = []
    for attempt in range(1, SWAP_ATTEMPTS + 1):
        try:
            cur.execute("BEGIN")
            cur.execute(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'")
            body(cur)
            cur.execute("COMMIT")
            if attempt > 1:
                print(f"    {what}: took the lock on attempt {attempt}")
            return
        # ⚠ AND DEADLOCK TOO. A swap that renames two tables a reader
        #   touches in the other order deadlocks rather than timing out, and
        #   the deadlock detector fires first. Same meaning, same rollback,
        #   same fix -- see build_ranking_results.swapIn.
        except (psycopg2.errors.LockNotAvailable,
                psycopg2.errors.DeadlockDetected):
            cur.execute("ROLLBACK")
            print(f"    {what}: readers hold the table, attempt {attempt}"
                  f"/{SWAP_ATTEMPTS} -- retrying in {SWAP_BACKOFF}s")
            if table and (attempt == 1 or attempt == SWAP_ATTEMPTS
                          or attempt % 5 == 0):
                holders = _lockHolders(cur, table)
                for h in holders or ["(nobody holds it now -- a short read)"]:
                    print(f"      holder: {h}")
            time.sleep(SWAP_BACKOFF)
    # ! A FAILURE HERE COSTS NOTHING BUT THE SWAP. The heap and its indexes
    #   are built and committed; <table>_new survives, so a rerun resumes
    #   rather than rebuilding. Raising beats swapping half of it.
    raise RuntimeError(
        f"{what}: could not take ACCESS EXCLUSIVE in "
        f"{SWAP_ATTEMPTS} attempts. Holders at the last attempt: "
        f"{'; '.join(holders) or 'see the holder lines above'}. "
        f"<table>_new is built and waiting; XCP_SWAP_ATTEMPTS raises the "
        f"number of tries.")


def _swapTables(cur, table, index_defs):
    # No SET LOGGED here: _buildNewTable already created the table LOGGED.
    # SET LOGGED would rewrite the heap AND all the indexes we just built.
    #
    # ORDER MATTERS, and the reason is not obvious:
    #   `ALTER TABLE t RENAME TO t_old` does NOT rename t's indexes. After that
    #   statement, t_old still OWNS the names `t_pkey`, `idx_t_v`, ... and index
    #   names are unique per SCHEMA, not per table. So renaming the new table's
    #   indexes into those names collides:
    #       ERROR: relation "results_pkey" already exists
    #   Reproduced on PG16 before this comment was written.
    # Hence: move the OLD names out of the way FIRST, then claim them.
    def _rename(c):
        c.execute(f"ALTER TABLE {table} RENAME TO {table}_old")

        # Step out of the way. results_pkey -> results_old_pkey, etc.
        c.execute(f"ALTER INDEX {table}_pkey RENAME TO {table}_old_pkey")
        for name, _ in index_defs:
            c.execute(f"ALTER INDEX {name} RENAME TO {name}_old")

        # Now the names are free.
        c.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        c.execute(f"ALTER INDEX {table}_new_pkey RENAME TO {table}_pkey")
        for name, _ in index_defs:
            c.execute(f"ALTER INDEX {name}_new RENAME TO {name}")

    _swapWithRetry(cur, _rename, f"{table} swap", table=table)
    print(f"    swapped: {table}_new -> {table};  old kept as {table}_old")


# _rewriteChangedRows
# Purpose : when only a few rows' normalized_time changed, UPDATE those rows
#           in place instead of rebuilding the whole table.
# Output  : True when done (the table is final, staging dropped); False when
#           the caller must rebuild -- and then NOTHING has been written.
#
# ★ A WEEK WITH LITTLE NEW LEAVES MOST OF THE TABLE AS IT WAS (2026-09-26,
#   owner: "speed up every step in pipeline possible"). The rebuild rewrites
#   every row and every index -- 495.6s of heap and 338.7s of indexes on
#   results_tf -- even when a handful of values moved. The rows whose value
#   changed are exactly the rows where the rebuild's
#       s.nt AS normalized_time   (LEFT JOIN staging s: NULL when unstaged)
#   differs from what the row holds now. Writing just those gives the same
#   normalized_time on every row as the rebuild, and every other column is
#   untouched either way.
#
# ! THE COMPARISON IS BITWISE AND NULL-AWARE: float4send() on both sides,
#   IS DISTINCT FROM. A plain `<>` calls 0 and -0 equal and NULL unknown;
#   bytes cannot disagree with what the rebuild would have written.
#
# ! TWO LOOKS, CHEAP ONE FIRST. A 1% block sample (TABLESAMPLE SYSTEM)
#   joined to staging estimates the count; a big estimate goes straight to
#   the rebuild, so an in-season week (hundreds of thousands of new rows)
#   pays a staging scan, not a full diff. Only a small estimate runs the
#   exact diff, and its LIMIT stops it at the first row past the threshold.
#   The estimate is only a gate: the exact diff decides.
#
# ⚠ WHAT IT DOES NOT DO THAT THE REBUILD DID. No swap, so no <table>_old
#   undo copy (02_drop_old removes that next run anyway), and nothing about
#   the table is reset: a CREATE TABLE AS strips defaults, CHECKs, NOT NULLs
#   and triggers (scripts/repair_constraints.py), an UPDATE keeps them. So a
#   table carrying a trigger is REBUILT -- an UPDATE would fire it -- and any
#   error here (a CHECK, a lock timeout) rolls the UPDATE back and rebuilds.
#   Readers are never blocked: an UPDATE's ROW EXCLUSIVE lock does not
#   conflict with them, where the swap needed ACCESS EXCLUSIVE.
#
# _REWRITE_MAX_ROWS: past this, the rebuild. Each changed row costs a PK
#   probe, a new heap tuple and an entry in every index (not HOT: the
#   normalized_time index sees the column change) -- about a dozen random
#   pages. 200,000 of them is a minute or two even from a cold cache, where
#   the rebuild writes the whole heap and all nine indexes; and 200,000 dead
#   tuples is under 0.6% of results' ~35M rows, a small fraction of
#   autovacuum's 20% trigger, so the table stays near as compact as a
#   rebuild leaves it.
_REWRITE_MAX_ROWS = 200_000
_REWRITE_SAMPLE_PCT = 1       # ~1.9M rows of results_tf: enough to see 0.1%
_CHANGED = ("float4send(r.normalized_time) "
            "IS DISTINCT FROM float4send(s.nt)")


def _rewriteChangedRows(write_conn, table, staging):
    print("-" * 70)
    print(f"REWRITE: does {table} need a rebuild, or only its changed rows?")
    try:
        with write_conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '2min'")
            # the exact diff hashes staging, as the rebuild's CREATE does
            cur.execute(f"SET LOCAL work_mem = '{dbSetting('work_mem', '2GB')}'")
            cur.execute("SELECT count(*) FROM pg_trigger "
                        "WHERE tgrelid = %s::regclass AND NOT tgisinternal",
                        (table,))
            if cur.fetchone()[0]:
                print(f"    {table} carries triggers an UPDATE would fire "
                      f"-- rebuilding instead")
                write_conn.rollback()
                return False
            _timedExec(cur, f"ANALYZE {staging}", f"ANALYZE {staging}")
            t0 = time.time()
            cur.execute(f"""
                SELECT count(*) FILTER (WHERE {_CHANGED})
                FROM   (SELECT result_id, normalized_time FROM {table}
                        TABLESAMPLE SYSTEM ({_REWRITE_SAMPLE_PCT})) r
                LEFT JOIN {staging} s ON s.result_id = r.result_id
            """)
            est = cur.fetchone()[0] * 100 // _REWRITE_SAMPLE_PCT
            print(f"    [{time.time() - t0:7.1f}s] ~{est:,} changed rows "
                  f"(from a {_REWRITE_SAMPLE_PCT}% block sample)")
            if est > _REWRITE_MAX_ROWS:
                print(f"    more than {_REWRITE_MAX_ROWS:,} -- rebuilding")
                write_conn.rollback()
                return False
            t0 = time.time()
            cur.execute(f"""
                CREATE TEMP TABLE bf_changed ON COMMIT DROP AS
                SELECT r.result_id, s.nt
                FROM   {table} r
                LEFT JOIN {staging} s ON s.result_id = r.result_id
                WHERE  {_CHANGED}
                LIMIT  {_REWRITE_MAX_ROWS + 1}
            """)
            n = cur.rowcount
            print(f"    [{time.time() - t0:7.1f}s] exact diff: "
                  f"{n:,}{'+' if n > _REWRITE_MAX_ROWS else ''} changed rows")
            if n > _REWRITE_MAX_ROWS:
                print(f"    more than {_REWRITE_MAX_ROWS:,} -- rebuilding")
                write_conn.rollback()
                return False
            cur.execute("ANALYZE bf_changed")       # a PK probe per row, not a scan
            _timedExec(cur, f"""
                UPDATE {table} r SET normalized_time = c.nt
                FROM   bf_changed c
                WHERE  r.result_id = c.result_id
            """, f"UPDATE {n:,} rows of {table}")
            if cur.rowcount != n:
                # the PK makes this impossible; a rebuild's own PK would fail
                # loudly on whatever made it possible, so hand it over
                print(f"    UPDATE touched {cur.rowcount:,} rows, not {n:,} "
                      f"-- rolled back, rebuilding")
                write_conn.rollback()
                return False
        write_conn.commit()
    except psycopg2.Error as exc:
        write_conn.rollback()
        print(f"    rewrite failed ({type(exc).__name__}: "
              f"{str(exc).strip().splitlines()[0] if str(exc).strip() else ''})"
              f" -- rolled back, nothing written; rebuilding instead")
        return False
    with write_conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
    write_conn.commit()
    print(f"REWRITE COMPLETE: {n:,} rows of {table} updated in place "
          f"(no rebuild, no swap, no {table}_old)")
    print("-" * 70)
    return True


# _mergeStagingIntoTable
# Purpose : the whole merge, orchestrated. Called ONCE, after the stream drains.
# Arguments: write_conn; table -- 'results'/'results_tf'; staging -- scratch name.
# Output   : None. Prints a timed line per step.
# Safety   : the old table survives as `<table>_old`. Verify, then:
#              DROP TABLE results_old;
#              VACUUM (ANALYZE) results;
# _preflight
# Purpose : print the disk the rebuild will need, BEFORE it starts.
# Why     : CREATE TABLE ... AS builds a full second copy of the heap, and the
#           ORIGINAL survives as <table>_old until you drop it by hand. So peak
#           usage is roughly 2x the table + 2x the indexes. On results_tf (~58GB
#           of heap) that is well over 100GB. Running out of disk mid-rebuild
#           leaves a half-built table and an aborted transaction -- recoverable,
#           but you will have wasted an hour finding out.
# Syntax  : pg_total_relation_size includes the heap, its indexes, and TOAST.
# _assertNoLeftovers
# Purpose : FAIL AT SECOND ZERO if a previous run left <table>_old or
#           <table>_new behind.
# Why this exists: _swapTables runs `ALTER TABLE t RENAME TO t_old` as its FIRST
#   statement -- but that is the LAST step of the merge, ~14 minutes of heap
#   rebuild and index building later. A pre-existing t_old raises DuplicateTable
#   at that point, the whole transaction rolls back, and every minute is lost.
#   Observed: 495.6s CREATE TABLE + 338.7s of index builds, discarded.
#   The staging table survives (it commits per batch), so the work is resumable
#   -- but the right answer is to check the precondition BEFORE paying for it.
# Syntax  : to_regclass('public.foo') returns the table's OID or NULL. It is the
#           exception-free existence test; `SELECT 1 FROM foo` would raise and
#           abort the surrounding transaction.
def _assertNoLeftovers(cur, table):
    for suffix in ("_old", "_new"):
        name = f"{table}{suffix}"
        cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
        if cur.fetchone()[0] is not None:
            raise RuntimeError(
                f"\n{'!' * 70}\n"
                f"{name} already exists. The swap would fail AFTER the rebuild.\n"
                f"Stopping now instead of wasting ~14 minutes.\n\n"
                f"  If {table}_old is a verified undo copy from an earlier merge:\n"
                f"      python scripts/verify_merge.py --table {table} --drop\n"
                f"  If it is debris from a crash:\n"
                f"      python scripts/resume_merge.py --table {table} --clean\n"
                f"{'!' * 70}")


def _preflight(cur, table):
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s::regclass)), "
                "       pg_total_relation_size(%s::regclass)", (table, table))
    pretty, raw = cur.fetchone()
    print(f"    {table} is {pretty}; the rebuild needs ~{2 * raw / 1e9:.0f} GB free")
    print(f"    (the original survives as {table}_old until you DROP it)")
    print(f"    *** the launcher/scraper MUST be OFF: rows written to {table} ***")
    print(f"    *** during the rebuild land in {table}_old and are LOST      ***")


def _mergeStagingIntoTable(write_conn, table, staging):
    print("-" * 70)
    print(f"MERGE: rebuilding {table} from {staging}")
    with write_conn.cursor() as cur:
        _assertNoLeftovers(cur, table)         # precondition, checked FIRST
        # Fail loudly instead of hanging. If ANY session still holds a lock on
        # `table` when we reach the swap, wait 2 minutes and then raise, rather
        # than sitting in `wait=Lock` indefinitely with no output. The usual
        # culprit is the launcher/scraper; the second is a psql window someone
        # left `idle in transaction`. Diagnose with:
        #     SELECT pid, state, query FROM pg_stat_activity WHERE state <> 'idle';
        cur.execute("SET lock_timeout = '2min'")
        _preflight(cur, table)
        # No index on staging: the planner wants a HASH join here (build the hash
        # from staging, probe it with a sequential scan of `results`), and a hash
        # join never consults an index. Building one would cost minutes for
        # nothing. ANALYZE, however, is essential -- without stats the planner
        # cannot size the hash table and may pick a catastrophic nested loop.
        _timedExec(cur, f"ANALYZE {staging}",
                   f"ANALYZE {staging} (so the planner sizes the hash join)")
        cols = _tableColumns(cur, table)
        print(f"    {len(cols)} columns carried across: {', '.join(cols[:6])}, ...")
        index_defs = _indexDefsFor(cur, table)
        print(f"    {len(index_defs)} secondary indexes to rebuild")

        _buildNewTable(cur, table, staging, cols)
        _buildIndexes(cur, table, index_defs)
        _swapTables(cur, table, index_defs)
    write_conn.commit()                    # the swap becomes visible HERE, atomically

    with write_conn.cursor() as cur:
        _timedExec(cur, f"ANALYZE {table}", f"ANALYZE {table} (fresh planner stats)")
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
    write_conn.commit()
    print(f"MERGE COMPLETE. Verify, then:  DROP TABLE {table}_old;")
    print("-" * 70)


# ================================================================== #
# PROFILING  —  measure, never guess. Use WITH --limit.
# ================================================================== #

# _printProfileReport
# Purpose : dump ONE ordering of a finished profile to stdout.
# Syntax  : pstats.Stats(pr, stream=buf) redirects the report into a StringIO
#           (an in-memory text file) rather than straight to stdout, so we can
#           wrap it in our own header. `.sort_stats(k).print_stats(n)` is a
#           fluent chain: sort by key k, emit the top n rows.
def _printProfileReport(pr, sort_key, rows=25):
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats(sort_key).print_stats(rows)
    print(f"\n{'=' * 70}\nPROFILE sorted by {sort_key}\n{'=' * 70}")
    print(buf.getvalue())


# _profiled
# Purpose : run fn(*args) under cProfile, print TWO reports, return fn's result.
# Why two orderings -- this is the whole trick of reading a profile:
#   tottime = time in the function EXCLUDING sub-calls (its own bytecode).
#             The bottleneck is the TOP of this list.
#   cumtime = time in the function INCLUDING everything it called.
#             The REASON it is being called so much is the top of this list.
# Caveat  : cProfile adds ~1-2 us of overhead PER CALL, systematically inflating
#           functions called many times with tiny bodies. Read the RATIOS.
# `finally` guarantees the report prints even if fn raises -- a crashed run still
#   tells you where it was standing.
def _profiled(fn, *args):
    pr = cProfile.Profile()
    pr.enable()
    try:
        return fn(*args)
    finally:
        pr.disable()
        _printProfileReport(pr, "tottime")   # who is burning CPU
        _printProfileReport(pr, "cumtime")   # why they are being called


# ================================================================== #
# PROGRESS
# ================================================================== #

# One status line per batch: totals + rate (no remaining-count query —
#     there's no IS NULL guard to count against on an unconditional rewrite).
def _printProgress(processed, skipped, written, start_time, apply):
    elapsed = time.time() - start_time
    rate = processed / elapsed if elapsed > 0 else 0
    tag = "written" if apply else "would-write"
    print(f"processed {processed:,} | {tag} {written:,} | "
          f"skipped {skipped:,} | {rate:,.0f}/s")


# ================================================================== #
# THE LOOP
# ================================================================== #

# _buildLookups
# Purpose : load the three in-memory lookup tables ONCE (genders, geometry index,
#           XC meet-distances) so the hot loop does dict hits, never per-row SQL.
#           Uses its OWN short-lived cursor on the read connection BEFORE the
#           streaming cursor is opened (you can't run these queries once the
#           server-side stream is live on that connection).
# Arguments: read_conn — the read connection; cfg — the SportConfig.
# Output   : (geom_idx, genders, meet_distances) ready to bake into row_fn.
def _buildLookups(read_conn, cfg):
    with read_conn.cursor() as cur:                # plain cursor, closed before streaming
        geom_idx = GeometryIndex.build(cur)
        genders = _loadGenders(cur)
        season_levels = _loadSeasonLevels(cur)
        # XC distance: anet from meets, tfrrs from the division_distances blob.
        # TF resolves distance from EVENT_DISTANCES_TF, so both dicts stay empty.
        meet_distances = _loadMeetDistances(cur) if cfg.sport == "XC" else {}
        tfrrs_distances = (_loadTfrrsBlobDistances(cur)
                           if cfg.sport == "XC" else {})
        # Canon dedup: the matched cross-source twins (drop the tfrrs copy) and
        # the tfrrs distance per canon meet (so a corrupt anet distance can be
        # borrowed). Both use the dedup layer's person_id + canon_meet_id.
        matched_twins = _loadMatchedTwinKeys(cur, cfg.table)
        canon_distances = _loadCanonTfrrsDistances(cur, cfg.table, tfrrs_distances)
        # anet XC wheelchair/seated divisions by title; TF and tfrrs XC are
        # matched per row (event name / blob title) and need no preload.
        wheel_divs = (_loadWheelchairDivs(cur)
                      if cfg.sport == "XC" else frozenset())
        # cross-sport and cross-feed: built from ALL three seams at once
        wheel_persons, wheel_athletes = _loadWheelchairPeople(
            cur, tfrrs_distances)
        # WEATHER index: heavy (aggregates weather_grid), built ONLY when the
        # per-sport artifact exists; otherwise weather stays a clean no-op.
        wx_idx = WeatherIndex.build(cur, cfg.sport) if _weatherEnabled(cfg.sport) else None
    print(f"  lookups: {len(genders):,} genders, "
          f"{len(season_levels[0]) + len(season_levels[1]):,} season levels, "
          f"{len(season_levels[2]):,} grade verdicts, "
          f"{len(meet_distances):,} anet XC distances, "
          f"{len(tfrrs_distances):,} tfrrs XC distances, "
          f"{len(matched_twins):,} canon twins, "
          f"{len(canon_distances):,} canon distances  (date rides the row)")
    if wx_idx is not None:
        print(f"  weather: index built ({len(wx_idx._wx):,} cell-days) -- "
              f"ACTIVE for {cfg.sport}")
    else:
        print(f"  weather: no artifact for {cfg.sport} -- no-op")
    if wheel_divs:
        print(f"  wheelchair: {len(wheel_divs):,} anet XC divisions nuked "
              "by title")
    print(f"  wheelchair athletes: {len(wheel_persons):,} linked people + "
          f"{len(wheel_athletes):,} unlinked -- ALL their races withheld")
    return (geom_idx, genders, season_levels, meet_distances,
            tfrrs_distances,
            matched_twins, canon_distances, wx_idx, wheel_divs,
            wheel_persons, wheel_athletes)


# _accumulate
# Purpose : classify one row, record it in the census (reason + sanity buckets),
#           and buffer it for writing IFF it produced a value. A tiny helper so
#           the drain loop body stays one line and the "skip vs write" branch
#           lives in exactly one place.
# Arguments: row_fn  — the baked per-row classifier -> (rid, value, reason, trace);
#            row     — a raw DB tuple;
#            updates — the current write buffer (mutated in place);
#            census  — the _Census collecting counts + extremes + percentiles.
# Output   : 1 if the row was SKIPPED (no value), else 0. The caller sums these
#            into the skipped counter; written rows are appended to `updates`.
def _accumulate(row_fn, row, updates, census):
    rid, value, reason, trace = row_fn(row)
    census.record(value, reason, trace)            # ALWAYS tally (skip or write)
    if value is None:
        # ! SKIPS WRITE NULL (2026-08-27). The copy-mode merge already
        #   NULLs unstaged rows, but the UPDATE fallback path never
        #   touched them -- a skipped row kept its fossil normalized_time
        #   forever, and downstream its fossil rating. An examined-and-
        #   refused row must LAND as NULL on every write path.
        updates.append((None, rid))
        return 1                                   # skipped (some _SkipReason.*)
    updates.append((value, rid))                   # (value, id) matches VALUES(nt, rid)
    return 0


# _drainStream
# Purpose : THE hot loop. Iterate the server-side read cursor row by row,
#           transform each, buffer writes, and flush the buffer in batches on the
#           WRITE connection. On a dry run it counts intended writes and never
#           touches the write connection. Streaming means no per-page re-query.
# Arguments: stream     — the open server-side cursor (read side).
#            write_conn — the dedicated write connection.
#            cfg        — SportConfig (table name + batch size).
#            row_fn     — baked per-row transform.
#            apply      — write when True, else count only.
#            limit      — stop after this many rows (0 = no limit; for --limit).
# Output   : (processed, written, skipped, census). The census carries the
#            per-reason breakdown + sanity panel, printed once at the end.
def _drainStream(stream, write_conn, cfg, row_fn, apply, limit, target):
    processed, written, skipped = 0, 0, 0
    updates = []
    census = _Census()                             # counts + extremes + reservoir
    start = time.time()

    for row in stream:                             # lazy pull; no re-seek per row
        skipped += _accumulate(row_fn, row, updates, census)
        processed += 1

        if len(updates) >= cfg.batch:              # buffer full -> flush one batch
            written += (_flush(write_conn, target, updates, cfg.write_mode)
                        if apply else len(updates))
            updates = []
            _printProgress(processed, skipped, written, start, apply)

        if limit and processed >= limit:           # --limit sanity slice
            break

    # final partial buffer
    written += (_flush(write_conn, target, updates, cfg.write_mode)
                if apply else len(updates))
    _printProgress(processed, skipped, written, start, apply)
    return processed, written, skipped, census


# _runBackfill
# Purpose : orchestrate ONE sport end to end with TWO connections — a read
#           connection holding the server-side stream, and a separate write
#           connection for the batched UPDATEs (you cannot write on a connection
#           whose server-side cursor is mid-stream, hence two).
# Arguments: read_conn, write_conn — the two connections; cfg; apply; limit.
# Output   : (processed, written, skipped, census).
def _runBackfill(read_conn, write_conn, cfg, apply, limit):
    # FAIL FAST. If the pool handed us one backend twice, the first commit would
    # kill the read stream 50,000 rows in. Prove they are distinct at row 0.
    _assertDistinctBackends(read_conn, write_conn)

    (geom_idx, genders, season_levels, meet_distances, tfrrs_distances,
     matched_twins, canon_distances, wx_idx,
     wheel_divs, wheel_persons, wheel_athletes) = _buildLookups(
        read_conn, cfg)
    row_fn = _makeRowFn(cfg, geom_idx, genders, season_levels,
                        meet_distances, tfrrs_distances, matched_twins,
                        canon_distances, wx_idx, wheel_divs=wheel_divs,
                        wheel_persons=wheel_persons,
                        wheel_athletes=wheel_athletes)

    # ⚠⚠ END THE LOOKUP TRANSACTION BEFORE THE STREAM OPENS (2026-09-24). The
    #    lookups ran on read_conn, and psycopg2 keeps one transaction open until
    #    it is told otherwise -- so every table they touched stayed locked
    #    ACCESS SHARE for as long as the stream then ran on the same
    #    transaction. _loadWheelchairPeople reads results, meets AND results_tf
    #    by design (a chair athlete is one person across sports), so the TF
    #    backfill held `results` for its whole 30-minute stream, and the XC
    #    backfill, running beside it, could not swap `results` in: "could not
    #    take ACCESS EXCLUSIVE in 20 attempts", holder
    #    `xcp-pipeline idle in transaction xact 1816s FETCH FORWARD 50000 FROM
    #    "backfill_stream_tf"`. The pipeline was blocking itself; the site's
    #    readers, 0-3 s each, were never the problem. Same on 2026-09-22.
    #
    # ! COMMIT, NOT ROLLBACK. The lookups created TEMP _wcp in this
    #   transaction, and a rollback would un-create it. Nothing was written to
    #   a real table, so the commit changes nothing but the locks.
    read_conn.commit()

    # WHERE the buffer lands. On the copy path it is the scratch table; on the
    # update path it is the results table itself. One variable, decided once.
    staging = None
    target = cfg.table
    if apply and cfg.write_mode == "copy":
        staging = _createStaging(write_conn, cfg.sport)
        target = staging

    stream = _openStream(read_conn, cfg)           # server-side cursor, single scan
    try:
        out = _drainStream(stream, write_conn, cfg, row_fn, apply, limit, target)
    finally:
        _closeStreamQuietly(stream)                # never masks the real error
        _releaseReadLocks(read_conn)               # MUST precede the merge (see above)

    # The read transaction is now over, so its ACCESS SHARE lock on `table` is
    # gone and the merge's ALTER TABLE ... RENAME can take ACCESS EXCLUSIVE.
    # Only merge if we actually staged rows.
    if staging is not None and not _rewriteChangedRows(
            write_conn, cfg.table, staging):
        _mergeStagingIntoTable(write_conn, cfg.table, staging)
    return out


# ================================================================== #
# END-OF-RUN REPORTS  —  the reason census + the sanity panel.
#   Kept as small, separate printers so each has one job and main()
#   stays a thin orchestrator.
# ================================================================== #

# _fmtTrace
# Purpose : render one traceback tuple as a compact, aligned line for the panel.
# Argument: trace — (result_id, raw_time, distance, source, date, pool, meet_id, div_id).
# Output  : a single formatted string (no newline).
def _fmtTrace(trace):
    rid, raw_time, distance, source, dt, pool, meet_id, div_id = trace
    # raw_time/distance can be None on odd rows; guard the numeric formats.
    rt = f"{raw_time:.2f}" if raw_time is not None else "None"
    dm = f"{distance:.1f}" if distance is not None else "None"
    return (f"id={rid} meet={meet_id} div={div_id} raw={rt}s dist={dm}m "
            f"src={source} date={dt} pool={pool}")


# _printReasonCensus
# Purpose : print the per-reason ROW breakdown — the answer to "why were rows
#           held back". Every row is in exactly one bucket, so the four skips
#           plus WRITTEN sum to processed. Percentages make the mix legible.
# Arguments: census — the filled _Census; processed — total rows seen.
# Output  : None (prints).
def _printReasonCensus(census, processed):
    print("-" * 70)
    print("SKIP CENSUS (why rows were held back):")
    denom = processed if processed else 1          # guard divide-by-zero
    for reason in _SKIP_ORDER:                     # skips first, in test order
        n = census.counts.get(reason, 0)
        print(f"  {reason:<14} {n:>12,}  ({100.0 * n / denom:5.1f}%)")
    w = census.counts.get(_SkipReason.WRITTEN, 0)  # then the written rows
    print(f"  {'written':<14} {w:>12,}  ({100.0 * w / denom:5.1f}%)")
    ip = census.counts.get(_SkipReason.INSANE_PACE, 0)
    if ip:
        print(f"  ! insane_pace rows ARE written now (label-judgment, not "
              f"corruption) --\n    counted above as the number to watch. "
              f"packResults' pool pace band keeps\n    them out of the "
              f"ratings; only the rebuild's gap table sees them.")


# _writeSuspectFile
# Purpose : write every flagged division to a TSV-ish file the evidence dumper
#           can read back — one line per division, with the athletic.net URL and
#           the columns needed to triage (kinds, n, frac_under, median, min).
# Arguments: flagged — list from _SuspectDivisions.flagged(); path — output file.
# Output  : None (writes the file).
# ⚠ A DIAGNOSTIC ARTIFACT MUST NOT KILL THE RUN. _printFooter is called
#   AFTER the connection blocks close, so on --apply the rows are already
#   written and the tables already swapped by the time this runs. An
#   unwritable path here used to raise out of main() -- the database correct,
#   the process exit code non-zero, and any pipeline script with `set -e`
#   aborting every stage after it over a report file. Measured 2026-08-30: a
#   root-owned suspects_xc.txt left by an earlier run as root took down a
#   dry run at the last line.
def _writeSuspectFile(flagged, path):
    try:
        _writeSuspectFileOrRaise(flagged, path)
    except OSError as exc:
        print(f"    [suspects] could NOT write {path}: {exc}")
        print(f"    [suspects] {len(flagged):,} flagged divisions were "
              f"computed and are lost for this run only -- the run itself is "
              f"unaffected. Fix the path's ownership and re-run to keep them.")


def _writeSuspectFileOrRaise(flagged, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("kinds\tmeet\tdiv\tpool\tn\tcorrupt\tfrac_under\tmedian\tmin\turl\n")
        for r in flagged:
            url = (f"https://www.athletic.net/CrossCountry/meet/"
                   f"{r['meet']}/results/{r['div']}")
            fh.write(f"{r['kinds']}\t{r['meet']}\t{r['div']}\t{r['pool']}\t"
                     f"{r['n']}\t{r['corrupt']}\t{r['frac_under']:.2f}\t"
                     f"{r['median']:.0f}\t{r['min']:.0f}\t{url}\n")


# _suspectSummary
# Purpose : one-line-per-kind tally so you see the shape of the remaining work
#           (how many mislabels vs slow vs fast-only) at a glance.
# Arguments: flagged — list from _SuspectDivisions.flagged().
# Output  : None (prints).
def _suspectSummary(flagged):
    by_kind = {}
    for r in flagged:
        by_kind[r["kinds"]] = by_kind.get(r["kinds"], 0) + 1
    parts = ", ".join(f"{k}={n}" for k, n in sorted(by_kind.items()))
    print(f"  by kind: {parts if parts else '(none)'}")


# _printSuspectAudit
# Purpose : the one-pass suspect-division audit. Prints a summary + the first
#           chunk inline, and writes the COMPLETE list to a file so the whole
#           remaining suspect set is captured in a single dry run (no treadmill).
# Arguments: census — filled _Census; path — file to write the full list to.
# Output  : None (prints + writes).
def _printSuspectAudit(census, path):
    flagged = census.suspects.flagged()
    print("-" * 70)
    print(f"SUSPECT-DIVISION AUDIT (every division flagged once; {len(flagged):,} total):")
    _suspectSummary(flagged)
    _writeSuspectFile(flagged, path)
    print(f"  full list written to: {path}")
    head = flagged[:_TOP_N]
    print(f"  worst {len(head)} (fastest median first):")
    print(f"    {'kinds':<20} {'meet':>8} {'div':>9} {'pool':>10} "
          f"{'n':>4} {'corr':>5} {'frac':>5} {'med':>6}")
    for r in head:
        med = r["median"]
        med_s = f"{med:>6.0f}" if med >= 0 else "     -"
        print(f"    {r['kinds']:<20} {r['meet']:>8} {str(r['div']):>9} "
              f"{r['pool']:>10} {r['n']:>4} {r['corrupt']:>5} "
              f"{r['frac_under']:>5.2f} {med_s}")


# _printExtremeList
# Purpose : print one extreme list (fastest OR slowest) from a _TopBucket.
#           Split out so the two lists share identical formatting.
# Arguments: title — heading; bucket — the _TopBucket to drain for printing.
# Output  : None (prints).
def _printExtremeList(title, bucket):
    print(f"  {title}")
    items = bucket.sorted_items()                  # most-extreme first
    if not items:
        print("    (none — every row skipped)")
        return
    for value, trace in items:
        print(f"    norm={value:8.2f}s  {_fmtTrace(trace)}")


# _printSanityPanel
# Purpose : print the fastest + slowest normalized times (where unit/label
#           corruption surfaces) and the approximate percentile spread (the
#           shape of the healthy middle). Percentiles are labelled "~" because
#           they come from the bounded reservoir sample, not the full stream.
# Arguments: census — the filled _Census.
# Output  : None (prints).
def _printSanityPanel(census):
    print("-" * 70)
    print(f"SANITY PANEL (normalized_time; N={_TOP_N} each end):")
    _printExtremeList("fastest (suspicious-fast floor):", census.fastest)
    _printExtremeList("slowest (suspicious-slow ceiling):", census.slowest)

    pcts = census.reservoir.percentiles(_PCTS)     # {q: value} or {}
    if pcts:
        spread = "  ".join(f"~p{int(q*100)}={pcts[q]:.1f}s" for q in _PCTS)
        print(f"  percentiles (approx, reservoir sample): {spread}")
    else:
        print("  percentiles: (no written rows sampled)")


# ================================================================== #
# MAIN
# ================================================================== #

# _runOneSport
# Purpose : the whole backfill for ONE sport -- stream, normalize, report.
#           Extracted from main() so `--sport both` can call it twice without
#           duplicating a line of the pipeline.
# Arguments: sport -- 'XC' | 'TF'; apply -- write, else dry run;
#            limit -- stop after N rows (0 = whole table).
# Output   : (processed, written, skipped) for the run summary.
# Detail   : each sport gets its OWN pair of connections. A server-side cursor
#            holds its connection for the whole stream, so reusing XC's read
#            connection for TF would mean opening a second cursor on a connection
#            that is still mid-stream. Fresh connections per sport, closed after.
def _printHeader(cfg, apply, limit):
    print("=" * 70)
    print(f"backfill normalized_time — {cfg.sport} ({cfg.table})   "
          f"{'APPLY' if apply else 'DRY RUN'}"
          f"{f'  (limit {limit:,})' if limit else ''}   "
          f"streaming, write_mode={cfg.write_mode}")
    print("=" * 70)


def _printFooter(sport, processed, written, skipped, census, apply):
    print("-" * 70)
    verb = "written" if apply else "would write"
    print(f"done. processed {processed:,} | {verb} {written:,} | skipped {skipped:,}")
    # The two reports: WHY rows were skipped, and WHAT the values look like.
    _printReasonCensus(census, processed)
    _printSanityPanel(census)
    _printSuspectAudit(census, f"suspects_{sport.lower()}.txt")


# _runOneSport
# THE CONNECTION FIX: both connections stay inside their `with` blocks for the
# WHOLE run. getConn() is a @contextmanager. The old code did
#     read_conn = _coerceConn(getConn())
# which called __enter__() and then dropped the manager object. Nothing held a
# reference, so CPython's refcounter finalized the generator IMMEDIATELY, firing
# its `finally: conn.rollback(); putconn(conn)` -- returning the connection to the
# pool while we still held a handle on it. The next getConn() then handed back the
# SAME connection, so read_conn IS write_conn, and _flush's commit destroyed the
# server-side cursor at row 50,001.
#
# `with getConn() as a, getConn() as b:` is exactly two nested `with` statements.
# Because the outer block has NOT exited, its connection is still checked OUT, so
# the inner getConn() is FORCED to hand back a different one. That structural fact
# is what guarantees two backends -- _assertDistinctBackends then proves it.
# It also fixes a leak: the old code never called putconn at all.
def _runOneSport(sport: str, apply: bool, limit: int, profile: bool = False,
                 write_mode: str = "copy"):
    cfg = _configFor(sport, write_mode)
    _printHeader(cfg, apply, limit)

    with getConn() as read_conn, getConn() as write_conn:
        if profile:
            processed, written, skipped, census = _profiled(
                _runBackfill, read_conn, write_conn, cfg, apply, limit)
        else:
            processed, written, skipped, census = _runBackfill(
                read_conn, write_conn, cfg, apply, limit)

    _printFooter(sport, processed, written, skipped, census, apply)
    return processed, written, skipped


def main():
    ap = argparse.ArgumentParser(description="Backfill normalized_time (XC, TF, or both).")
    # `both` runs XC then TF in one invocation. They touch DIFFERENT tables
    # (results / results_tf) and share no state but the pool, so running them
    # back to back is safe and saves a full re-import of the splines.
    ap.add_argument("--sport", choices=["XC", "TF", "both"], required=True)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--only-changed", action="store_true",
                    help="only rows of people in person_gender_changed (04d's "
                         "list of moved verdicts); implies --write-mode update")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N rows PER SPORT (0 = whole table). Use a "
                         "small value to sanity-check the `skipped` count in "
                         "seconds before a full --apply.")
    ap.add_argument("--write-mode", choices=["copy", "update"], default="copy",
                    help="copy (default): stage via COPY, then rebuild the table "
                         "in one pass. ~6x faster. Leaves <table>_old behind. "
                         "update: the original per-batch UPDATE. Slower, but it "
                         "touches nothing but the one column. Use with --limit.")
    ap.add_argument("--profile", action="store_true",
                    help="run under cProfile and print the hot-function report. "
                         "Use WITH --limit (e.g. --limit 200000).")
    args = ap.parse_args()

    # A partial rebuild would DISCARD every row past the limit's staging coverage
    # -- no, worse: COALESCE keeps them, but you would swap in a table built from
    # a partial staging set for no reason and pay the full rebuild cost. --limit
    # is a sanity slice; pair it with --write-mode update.
    if args.limit and args.apply and args.write_mode == "copy":
        ap.error("--limit with --apply requires --write-mode update "
                 "(a rebuild over a partial staging set rebuilds the WHOLE table)")

    if args.only_changed:
        _ONLY_CHANGED["on"] = True
        args.write_mode = "update"
    initPool()
    sports = ["XC", "TF"] if args.sport == "both" else [args.sport]

    totals = {}
    for sport in sports:
        if len(sports) > 1:
            print()
        totals[sport] = _runOneSport(sport, args.apply, args.limit,
                                     args.profile, args.write_mode)

    if len(sports) > 1:
        print("\n" + "=" * 70)
        print("BOTH SPORTS COMPLETE")
        print("=" * 70)
        verb = "written" if args.apply else "would write"
        gp = gw = gs = 0
        for sport, (p, w, s) in totals.items():
            print(f"  {sport:<4} processed {p:>12,} | {verb} {w:>12,} | skipped {s:>12,}")
            gp += p; gw += w; gs += s
        print(f"  {'ALL':<4} processed {gp:>12,} | {verb} {gw:>12,} | skipped {gs:>12,}")
        print("=" * 70)


if __name__ == "__main__":
    main()