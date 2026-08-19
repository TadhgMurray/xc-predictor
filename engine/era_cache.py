# Project: xc-predictor
# File:    engine/era_cache.py
# Purpose: Cache the EXPENSIVE, KNOB-INDEPENDENT output of the era stream
#          (loadSport's steps + bench, and the Stage-B collectors) so that
#          knob-sweep runs (TRUST_BENCHMARK_LEVEL, MIN_SHIP_BENCH_N, ...) skip
#          the ~22-minute stream and go straight to the ~2-second fit.
#
# ============================================================================ #
# THE MAIN IDEA
# ============================================================================ #
# The pipeline has a seam: streaming 109M rows -> (steps, bench, collectors) is
# slow and does NOT depend on any fit knob; the solve/report that follows is
# fast and depends ENTIRELY on the knobs. So we cache the seam. A cached run
# and a fresh run feed the solver byte-identical signals — the cache changes
# SPEED, never ANSWERS. (Caching the fitted curve would be a bug: it bakes the
# knobs in. Caching the pre-solve signals does not.)
#
# ============================================================================ #
# THE CORRECTNESS TRAP: invalidation
# ============================================================================ #
# The stream calls normalizeTime, which reads distance_spline.pkl and
# geometry_spline.pkl. Refit geometry (as you just did) and the cached signals
# are STALE — normalized on the old artifact. So the cache is STAMPED with those
# pickles' modification times plus a schema version; if the stamp doesn't match
# on load, the cache is ignored and the stream re-runs. --fresh forces a rebuild
# regardless. This turns "did I remember to rebuild?" from a silent footgun into
# an automatic check.
#
# ============================================================================ #
# THE HARD PART: what is / isn't picklable
# ============================================================================ #
#   steps : defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
#   bench : defaultdict(lambda: defaultdict(array("f")))
# Those lambda factories are NOT picklable. So we snapshot to PLAIN dicts on the
# way out and REBUILD the defaultdict nesting on the way in. The Stage-B
# collector holds three plain dicts (ladder/xc_legs/tf_legs) — picklable as-is,
# but it is filled in-stream via a callback, so on a cache hit we must RESTORE
# those three dicts into the live collector or XC's ladder/bridge vanish.

import os
import time
import pickle
from array import array
from collections import defaultdict

# Upstream artifacts whose change invalidates the cache (the stream normalized
# against these). Paths mirror normalize_distance's own layout.
_DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "engine", "data")
_GEOMETRY_FILE = os.path.join(_DATA_DIR, "geometry_spline.pkl")
_DISTANCE_FILE = os.path.join(_DATA_DIR, "distance_spline.pkl")

# Where caches live, one file per sport.
_CACHE_DIR = os.path.join(_DATA_DIR, "era_stream_cache")

# Bump this whenever the CACHED STRUCTURE changes (a new field in a record, a
# changed key shape). An old cache with a different version is treated as stale,
# so a structural change can never be silently read back in the wrong shape.
_SCHEMA_VERSION = 1


# ============================================================================ #
# CHUNK 1 — THE STAMP: what makes a cache "current"
# ============================================================================ #

# _mtimeOr0
# Purpose:   The modification time of a file, or 0.0 if it doesn't exist.
#            0.0 is a safe sentinel: a missing upstream pickle just yields a
#            stamp that won't match a cache built when it existed.
# Arguments: path — the file to stat.
# Output:    the mtime as a float (seconds), or 0.0.
def _mtimeOr0(path) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:                      # file absent / unreadable
        return 0.0


# _cacheStamp
# Purpose:   The fingerprint a cache must match to be trusted: the schema
#            version plus the mtimes of every upstream artifact the stream
#            normalized against. If geometry or distance is refit, its mtime
#            changes, the stamp changes, and any old cache is rejected.
# Arguments: none.
# Output:    a small dict (all plain values -> trivially picklable/comparable).
def _cacheStamp() -> dict:
    return {
        "schema":        _SCHEMA_VERSION,
        "geometry_mtime": _mtimeOr0(_GEOMETRY_FILE),
        "distance_mtime": _mtimeOr0(_DISTANCE_FILE),
    }


# _cachePath
# Purpose:   The cache file path for one sport (one file each, so XC and TF
#            invalidate independently).
# Arguments: sport — "XC" | "TF".
# Output:    absolute path string.
def _cachePath(sport) -> str:
    return os.path.join(_CACHE_DIR, f"era_signals_{sport.lower()}.pkl")


# ============================================================================ #
# CHUNK 2 — SNAPSHOT: nested defaultdicts -> plain dicts (for writing)
# ============================================================================ #
# The lambdas that make `steps`/`bench` auto-nesting are what break pickle, so
# we copy their CONTENTS into plain dicts. array("f") IS picklable, so bench's
# leaf arrays are kept as-is; only the defaultdict WRAPPER is shed.

# _plainSteps
# Purpose:   Snapshot the steps structure into plain nested dicts. The TRUE
#            shape (from _consumeStream's contract, line 806) is THREE dict
#            levels then a leaf list:
#                steps[pkey][pair][gkey] -> [log_delta, ...]
#            We copy every level so no defaultdict factory (lambda) reaches the
#            pickle; the leaf list is list()-copied to detach it.
# Arguments: steps — defaultdict(defaultdict(defaultdict(list))).
# Output:    plain {pkey: {pair: {gkey: [deltas]}}}.
def _plainSteps(steps) -> dict:
    return {pkey: {pair: {gkey: list(deltas)
                          for gkey, deltas in by_group.items()}
                   for pair, by_group in by_pair.items()}
            for pkey, by_pair in steps.items()}


# _plainBench
# Purpose:   Snapshot the 2-level bench structure into plain dicts, keeping the
#            leaf array("f") intact (it pickles fine; only the wrapper is shed).
# Arguments: bench — defaultdict(defaultdict(array("f"))).
# Output:    plain {season_key_level: {inner: array("f")}} dict.
def _plainBench(bench) -> dict:
    return {k: {ik: iv for ik, iv in inner.items()}
            for k, inner in bench.items()}


# ============================================================================ #
# CHUNK 3 — REBUILD: plain dicts -> the defaultdict shapes the code expects
# ============================================================================ #
# loadSport's callers rely on steps/bench being DEFAULTDICTS (they index
# missing keys and expect auto-creation elsewhere). So on read we pour the
# plain snapshot back into the exact factory nesting loadSport used.

# _stepsFactory / _benchFactory
# Purpose:   Recreate the SAME auto-nesting factories loadSport built, so a
#            rebuilt structure behaves identically to a freshly-streamed one.
#            (Named module-level defs, not lambdas — so THESE are picklable too,
#            though we only ever call them at load time.)
def _stepsFactory():
    return defaultdict(lambda: defaultdict(lambda: defaultdict(list)))


def _benchFactory():
    return defaultdict(lambda: defaultdict(lambda: array("f")))


# _rebuildSteps
# Purpose:   Plain {pkey:{pair:{gkey:[deltas]}}} -> the 3-level defaultdict
#            steps the fitter expects. THREE nested loops, one per dict level,
#            because the real structure is pkey -> pair -> gkey -> list. Getting
#            the depth wrong is exactly the bug that made by_group a list.
# Arguments: plain — a _plainSteps snapshot.
# Output:    a defaultdict-nested steps structure.
def _rebuildSteps(plain):
    steps = _stepsFactory()
    for pkey, by_pair in plain.items():
        for pair, by_group in by_pair.items():
            for gkey, deltas in by_group.items():
                steps[pkey][pair][gkey] = list(deltas)   # leaf reattached as list
    return steps


# _rebuildBench
# Purpose:   Plain {k:{ik:array}} -> the 2-level defaultdict bench.
# Arguments: plain — a _plainBench snapshot.
# Output:    a defaultdict-nested bench structure.
def _rebuildBench(plain):
    bench = _benchFactory()
    for k, inner in plain.items():
        for ik, iv in inner.items():
            bench[k][ik] = iv                     # array("f") kept as-is
    return bench


# ============================================================================ #
# CHUNK 4 — THE COLLECTOR: snapshot / restore its three plain dicts
# ============================================================================ #
# _StageBCollectors holds ladder / xc_legs / tf_legs, all plain dicts of lists,
# so we can copy them out and pour them back in without touching the class.

# _snapshotCollector
# Purpose:   Copy the collector's three accumulators into a plain dict for the
#            cache. Shallow-copies the top level; leaf lists/heaps are already
#            plain and immutable-enough for our read-only reuse.
# Arguments: coll — a live _StageBCollectors.
# Output:    {"ladder":..., "xc_legs":..., "tf_legs":...} plain dict.
def _snapshotCollector(coll) -> dict:
    return {"ladder":  dict(coll.ladder),
            "xc_legs": dict(coll.xc_legs),
            "tf_legs": dict(coll.tf_legs)}


# _restoreCollector
# Purpose:   Pour a cached collector snapshot back INTO the live collector, so a
#            cache hit leaves `coll` exactly as a real stream would have. We
#            .update() rather than reassign, because the SAME coll object is
#            shared across sports (XC fills ladder+xc_legs, TF fills tf_legs) —
#            so restoring one sport must not clobber the other's contribution.
# Arguments: coll — the live collector to fill; snap — a _snapshotCollector dict.
# Output:    none (mutates coll in place).
def _restoreCollector(coll, snap) -> None:
    coll.ladder.update(snap["ladder"])
    coll.xc_legs.update(snap["xc_legs"])
    coll.tf_legs.update(snap["tf_legs"])


# ============================================================================ #
# CHUNK 5 — FRESHNESS + READ/WRITE
# ============================================================================ #

# _cacheFresh
# Purpose:   Is there a cache at `path` whose stamp matches NOW's stamp?
#            Any mismatch (schema bump, geometry/distance refit) -> not fresh.
# Arguments: path — the cache file; stamp — the current _cacheStamp().
# Output:    (fresh: bool, payload_or_None). On a fresh hit we return the loaded
#            payload too, so the caller doesn't re-open the file.
def _cacheFresh(path, stamp):
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
    except Exception:                             # corrupt/partial write -> ignore
        return False, None
    return (payload.get("stamp") == stamp), payload


# _writeCache
# Purpose:   Persist one sport's stream output the INSTANT the stream ends
#            (before any fit can crash — a fit-time error must never cost the
#            22-minute stream). Snapshots the un-picklable structures first.
# Arguments: path  — destination; stamp — the freshness fingerprint;
#            steps, bench — loadSport's return; coll — the shared collector.
# Output:    none (writes the file).
def _writeCache(path, stamp, steps, bench, coll) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "stamp":     stamp,
        "steps":     _plainSteps(steps),
        "bench":     _plainBench(bench),
        "collector": _snapshotCollector(coll),
    }
    # Write to a temp file then rename: an interrupted write can't leave a
    # half-file that later reads as "fresh but truncated".
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)                          # atomic on the same filesystem


# ============================================================================ #
# CHUNK 6 — THE PUBLIC WRAPPER (the one thing runEraCorrection calls)
# ============================================================================ #

# loadSportCached
# Purpose:   Drop-in for loadSport(sport, timer, collect=...) that consults the
#            cache first. On a fresh hit: rebuild steps/bench, restore the
#            collector, return in ~seconds. On a miss/stale/--fresh: run the
#            real loadSport, then write the cache before returning.
# Arguments: sport      — "XC" | "TF".
#            timer      — the _PhaseTimer (passed straight through to loadSport).
#            coll       — the shared _StageBCollectors (filled on a miss by the
#                         stream's callback; RESTORED on a hit).
#            load_fn    — loadSport itself, injected so this module doesn't need
#                         to import the fitter (avoids a circular import).
#            collect_fn — coll.collectorFor(sport), the per-sport callback.
#            use_cache  — False (from --fresh) forces the slow path + rewrite.
# Output:    (steps, bench) — identical shape to loadSport, cache hit or miss.
def loadSportCached(sport, timer, coll, load_fn, collect_fn, use_cache=True):
    path  = _cachePath(sport)
    stamp = _cacheStamp()

    if use_cache:
        fresh, payload = _cacheFresh(path, stamp)
        if fresh:
            print(f"  [cache] {sport}: hit — skipping stream "
                  f"({os.path.basename(path)})", flush=True)
            _restoreCollector(coll, payload["collector"])   # XC ladder/bridge back
            return (_rebuildSteps(payload["steps"]),
                    _rebuildBench(payload["bench"]))
        reason = "stale (geometry/distance refit or schema bump)" \
            if os.path.exists(path) else "no cache"
        print(f"  [cache] {sport}: miss — {reason}; streaming.", flush=True)
    else:
        print(f"  [cache] {sport}: --fresh — forcing re-stream.", flush=True)

    # SLOW PATH: the real stream. coll is filled in-stream via collect_fn.
    steps, bench = load_fn(sport, timer, collect=collect_fn)
    _writeCache(path, stamp, steps, bench, coll)            # persist immediately
    return steps, bench