# Project: xc-predictor
# File:    engine/diagnose_geometry_pairs.py
# Purpose: STEP 1 of the geometry-correction pipeline — the matched-pair
#          MEASUREMENT diagnostic. It compares each athlete TO THEMSELVES
#          (same athlete, same event, close in time) to isolate two track
#          effects, emits two spline-ready point clouds, and prints a report so
#          a human can judge whether the signal is real BEFORE any curve is fit
#          to it. This file MEASURES; it does NOT fit.
#
#          Pipeline position:
#            geometry_db.loadTFGeometryResults()  ->  THIS FILE  ->  (2 clouds)
#            ->  fit_geometry_correction.py  ->  geometry_spline.pkl  ->  engine

import sys
import os
import csv

# Bare imports off scripts/ (project convention). loadTFGeometryResults already
# does the DB work, the filtering, and the event_distance mapping — we only ever
# consume the row dicts it hands back.
sys.path.insert(0, "scripts")
from geometry_db import loadTFGeometryResults


# ------------------------------------------------------------------ #
# WHY TWO SEPARATE CLOUDS  (the organising idea of the whole file)
# ------------------------------------------------------------------ #
#
# Track geometry has two effects we care about here — track LENGTH (a 200m
# track is all turns; a 400m far less) and BANKING (banked turns let you carry
# speed). A banked track is ALSO a short track, so if we measured them together
# every banked-vs-flat pair would smuggle in a length change too, and we could
# never tell which effect actually moved the time.
#
# So we measure each in its OWN cloud, each holding the OTHER effect still:
#
#     LENGTH cloud   : both races FLAT (banking pinned), lengths differ.
#     BANKING cloud  : lengths MATCHED within a tolerance (length pinned),
#                      banked vs flat.
#
# "Don't let one term eat the other's signal" — applied at measurement time,
# which is exactly what lets the two surfaces later be fit independently.
#
# The shared trick under both: compare an athlete to THEMSELVES, weeks apart, in
# the same event. Same person -> fitness cancels -> the time ratio is the track,
# not the runner. Median that ratio over thousands of athletes = the factor.
# ------------------------------------------------------------------ #


# ------------------------------------------------------------------ #
# CONSTANTS  (every knob is a tunable so the diagnostic self-validates)
# ------------------------------------------------------------------ #
#
# Each default below is ALSO threaded through the functions as a parameter. The
# point: re-run with a tighter window or tolerance and watch whether the
# measured factor MOVES. A factor that holds steady across settings is
# trustworthy; one that drifts is being contaminated by the knob you changed.
# ------------------------------------------------------------------ #

# Max days apart for two races to count as "close in time." This is the
# fitness-drift control: the tighter it is, the less a runner's shape could have
# changed between the two races, so the cleaner the ratio.
DEFAULT_WINDOW_DAYS = 28

# BANKING cloud only. Two track lengths count as "the same length" if they're
# within this many metres. 191/193/200 are the SAME ~200m track recorded with
# slop, not three different tracks — an exact match would shatter one track's
# pairs across a recording artefact. Tight (10m) so any leftover length effect
# inside the band is negligible next to banking.
DEFAULT_LENGTH_TOLERANCE_M = 10.0

# LENGTH cloud only. The minimum metres two lengths must differ by to count as a
# real length transition. 0.0 = emit every different length (raw, per spec). It
# is here as a knob: if the cloud piles up near the from==to diagonal and that
# pile looks like pure noise, raise this to cut the near-zero "transitions."
DEFAULT_MIN_LENGTH_DELTA_M = 0.0

# Where the two cloud CSVs are written. fit_geometry_correction.py reads them
# back from here. Created if it doesn't exist.
DEFAULT_OUTPUT_DIR = "engine/data"


# ------------------------------------------------------------------ #
# CLASSIFICATION + NUMERIC HELPERS  (tiny, single-purpose, reused everywhere)
# ------------------------------------------------------------------ #

# _isBanked
# Purpose:   Decide the banked half of the banked/flat binary for one row.
# Arguments: track_type — the meets_tf string, one of
#            {"Banked", "Flat", "Oversized", "Undersized", None}.
# Output:    True ONLY for the literal "Banked"; everything else (including None)
#            is not banked. Oversized/Undersized describe non-standard LENGTH,
#            not banking, so they are not banked here.
def _isBanked(track_type) -> bool:
    return track_type == "Banked"


# _isFlat
# Purpose:   Decide whether we can POSITIVELY confirm a row is flat (not banked).
# Arguments: track_type — same domain as _isBanked.
# Output:    True for "Flat"/"Oversized"/"Undersized"; False for "Banked" AND for
#            None. The None->False is the load-bearing choice: the length cloud
#            must KNOW both races are flat to hold banking constant, and a NULL
#            track_type can't be vouched for, so it's unusable there. track_type
#            is only ~5.7% populated — this is the honest price of that guarantee.
def _isFlat(track_type) -> bool:
    return track_type is not None and track_type != "Banked"


# _daysApart
# Purpose:   The time gap between two races, as a plain non-negative day count.
# Arguments: date_a, date_b — datetime.date objects (the loader's 'date' field).
# Output:    abs(days between them). Used twice: to gate a pair against the
#            window, and to rank candidates so we keep the closest-in-time one.
def _daysApart(date_a, date_b) -> int:
    # date - date gives a timedelta; .days pulls the whole-day count; abs() so
    # whichever order we happened to pass them in can't flip the sign.
    return abs((date_a - date_b).days)


# _median
# Purpose:   Middle value of a list of numbers — our headline factor per cloud.
# Arguments: xs — a non-empty list of floats (callers guard the empty case).
# Output:    the median. Median NOT mean on purpose: a runner who fell at one of
#            the two races throws a wild ratio that drags a mean but barely nudges
#            a median. Hand-rolled (no statistics import) to match the engine.
def _median(xs) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2                          # // is integer division — middle index
    if n % 2 == 1:                        # odd count -> one true middle element
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0    # even -> average the two middle values


# _iqr
# Purpose:   Inter-quartile range (Q3 - Q1) — the TRUST SIGNAL next to a median.
# Arguments: xs — a non-empty list of floats.
# Output:    Q3 - Q1. Tight IQR = the effect is consistent across athletes (trust
#            it); wide IQR = something is still confounding it. Simple nearest-
#            rank quartiles — enough for an eyes-first read; the later fit does
#            the rigorous statistics.
def _iqr(xs) -> float:
    s = sorted(xs)
    n = len(s)
    q1 = s[n // 4]                # ~25th percentile by position
    q3 = s[(3 * n) // 4]          # ~75th percentile by position
    return q3 - q1


# ------------------------------------------------------------------ #
# GROUPING  (only ever compare like-with-like)
# ------------------------------------------------------------------ #

# _indexByAthleteEvent
# Purpose:   Bucket every result row by (athlete_id, event_short, gender) so the
#            cloud builders can only ever pair a person with THEMSELVES in the
#            very same event.
# Arguments: results — the list of row dicts from loadTFGeometryResults(). Each
#            dict carries athlete_id / event_short / gender / date / time_seconds
#            / track_type / track_length / is_indoor / event_distance.
# Output:    dict mapping (athlete_id, event_short, gender) -> list of that
#            group's row dicts. These per-group lists are the ONLY thing the
#            pair-finders ever look inside.
def _indexByAthleteEvent(results) -> dict:
    # WHY all three in the key: event_short (e.g. "5000m"), not the raw distance,
    # so we never pair two different events that merely share a distance; and
    # gender, so a man's 5000m never pairs with a woman's.
    groups = {}
    for row in results:
        key = (row["athlete_id"], row["event_short"])
        # setdefault returns the existing list for key, or inserts a fresh [] and
        # returns THAT — so .append always has a list to land in, no if-in branch.
        groups.setdefault(key, []).append(row)
    return groups


# ------------------------------------------------------------------ #
# THE LENGTH CLOUD
# ------------------------------------------------------------------ #
#
# A length sample compares two FLAT races (banking held still) of the same
# athlete/event at DIFFERENT track lengths, close in time. Because it's the same
# runner weeks apart, fitness ~cancels, so the ratio of their times is (close to)
# pure length effect.
#
# The build is a handful of small steps, one function each:
#     _lengthPairsForGroup   -> every qualifying pair inside one athlete's group
#     _closestPair           -> pick the closest-in-time pair from a set (shared)
#     _closestPerTransition  -> keep one closest pair PER distinct length transition
#                               (built on _canonicalLengthKey + _bucketByTransition)
#     _emitLengthSample      -> turn a pair into one (from,to,dist,ratio) tuple
#     buildLengthCloud       -> run those over every group, collect the cloud
# ------------------------------------------------------------------ #

# _lengthPairsForGroup
# Purpose:   Yield every valid length-pair inside ONE athlete/event group.
# Arguments: group              — list of row dicts for one (athlete,event,gender)
#            window_days        — max days apart (the close-in-time gate)
#            min_length_delta_m — minimum length difference to count (0.0 = any)
# Output:    a generator of (row_a, row_b) tuples. A pair qualifies when:
#              - both rows are FLAT          (banking held constant)   [_isFlat]
#              - both have a known length    (track_length not None)
#              - lengths differ by >= delta  (a real length transition)
#              - races within window_days    (fitness held ~constant)
#            Order inside the tuple is NOT fixed here; _emitLengthSample does that.
def _lengthPairsForGroup(group, window_days, min_length_delta_m):
    n = len(group)
    for i in range(n):
        a = group[i]
        # Skip a whole outer row early if it can't anchor a flat, known-length
        # pair — cheaper than re-checking it against every inner row.
        if not _isFlat(a["track_type"]) or a["track_length"] is None:
            continue
        # j starts at i+1: never pair a row with itself, and never emit the same
        # unordered pair twice (we'd just be double-counting it).
        for j in range(i + 1, n):
            b = group[j]
            if not _isFlat(b["track_type"]) or b["track_length"] is None:
                continue
            if a["track_length"] == b["track_length"]:
                continue                     # same length = no transition to read
            if abs(a["track_length"] - b["track_length"]) < min_length_delta_m:
                continue                     # gap too small to trust as real
            if _daysApart(a["date"], b["date"]) > window_days:
                continue                     # too far apart, fitness may have moved
            yield (a, b)                     # yield, not append: stream pairs lazily


# _closestPair
# Purpose:   From a group's candidate pairs, keep ONLY the single pair whose two
#            races are nearest in time. (Shared by both clouds.)
# Arguments: pairs — an iterable of (row_a, row_b) candidates (may be empty).
# Output:    the (row_a, row_b) with the smallest _daysApart, or None if there
#            were no candidates.
#            WHY one-per-group: a prolific athlete with 50 qualifying races must
#            not flood the sample with 50 correlated points. One pair each, and
#            the closest-in-time one, because the tighter the gap the less fitness
#            could have drifted — the cleanest single read this athlete can give.
def _closestPair(pairs):
    best = None
    best_gap = None
    for a, b in pairs:
        gap = _daysApart(a["date"], b["date"])
        # First candidate seeds best; after that we only swap in a strictly
        # closer one, so ties keep whichever we saw first (order-stable).
        if best_gap is None or gap < best_gap:
            best = (a, b)
            best_gap = gap
    return best


# _canonicalLengthKey
# Purpose:   The order-independent identity of a length transition — so that
#            (180,200) and (200,180) are recognised as the SAME transition.
# Arguments: pair — a (row_a, row_b) tuple.
# Output:    (min_length, max_length). Used to bucket pairs by which transition
#            they are, so we can keep one closest instance of each.
def _canonicalLengthKey(pair):
    a, b = pair
    la, lb = a["track_length"], b["track_length"]
    return (min(la, lb), max(la, lb))


# _bucketByTransition
# Purpose:   Group a group's candidate pairs by which length-transition each one
#            is, so each distinct transition can be reduced to its best instance.
# Arguments: pairs — an iterable of (row_a, row_b) candidates.
# Output:    dict mapping (min_length, max_length) -> list of the pairs that ARE
#            that transition. e.g. fifteen runnings of 200<->400 all land in the
#            (200, 400) bucket together.
def _bucketByTransition(pairs) -> dict:
    buckets = {}
    for pair in pairs:
        key = _canonicalLengthKey(pair)
        buckets.setdefault(key, []).append(pair)
    return buckets


# _closestPerTransition
# Purpose:   Keep ONE pair per distinct length-transition — the closest-in-time
#            instance of each. This is the per-transition rule (NOT one-per-group):
#            an athlete who ran 180m, 200m and 400m ovals contributes all THREE
#            transitions (180->200, 180->400, 200->400), not just their single
#            nearest pair — while fifteen runnings of 200<->400 still collapse to
#            one. Keeps rare-length coverage; still kills the flooding problem.
# Arguments: pairs — an iterable of (row_a, row_b) candidates (may be empty).
# Output:    list of (row_a, row_b), one per transition. Empty list if no
#            candidates.
def _closestPerTransition(pairs) -> list:
    # Bucket by transition, then REUSE _closestPair to pick the best in each
    # bucket. Buckets are never empty (a key is created only when we add to it),
    # so _closestPair never returns None here.
    buckets = _bucketByTransition(pairs)
    return [_closestPair(bucket) for bucket in buckets.values()]


# _emitLengthSample
# Purpose:   Turn one length-pair into a single spline-ready sample tuple.
# Arguments: pair — a (row_a, row_b) tuple (both flat, different lengths).
# Output:    (length_from, length_to, event_distance, ratio) where
#            length_from < length_to (canonical SHORTER->longer direction) and
#            ratio = time(shorter) / time(longer). ratio > 1 means the shorter,
#            turnier track was slower.
#            WHY canonical direction: with every sample on one side of the
#            from<to diagonal the cloud reads cleanly AND the reverse transition
#            is just 1/ratio — defined that way downstream, which makes
#            A->B->A == 1 hold exactly (the self-consistency normalize_distance
#            gets for free from its formula).
def _emitLengthSample(pair):
    a, b = pair
    # Sort the two rows by length so "from" is always the shorter track.
    if a["track_length"] <= b["track_length"]:
        shorter, longer = a, b
    else:
        shorter, longer = b, a
    ratio = shorter["time_seconds"] / longer["time_seconds"]
    # event_distance is the race distance — identical for both rows (same event),
    # so either row's copy is fine; we take the shorter's.
    return (shorter["track_length"], longer["track_length"],
            shorter["event_distance"], ratio)


# buildLengthCloud
# Purpose:   Build the whole LENGTH cloud — one sample per qualifying athlete/
#            event group.
# Arguments: results            — row dicts from loadTFGeometryResults()
#            window_days        — close-in-time gate (default DEFAULT_WINDOW_DAYS)
#            min_length_delta_m — min length difference (default 0.0 = any)
# Output:    list of (length_from, length_to, event_distance, ratio) tuples — the
#            raw, continuous, UN-bucketed cloud that fit_geometry_correction.py
#            will later fit a surface to.
def buildLengthCloud(results, window_days=DEFAULT_WINDOW_DAYS,
                     min_length_delta_m=DEFAULT_MIN_LENGTH_DELTA_M) -> list:
    groups = _indexByAthleteEvent(results)
    cloud = []
    # Per group: find every qualifying pair, reduce to one-closest-PER-TRANSITION,
    # emit each. buildLengthCloud stays short — it ORCHESTRATES the helpers above.
    for group in groups.values():
        pairs = _lengthPairsForGroup(group, window_days, min_length_delta_m)
        for pair in _closestPerTransition(pairs):
            cloud.append(_emitLengthSample(pair))
    return cloud

# _indexFlatAnnotated
# Purpose:   PASS 2's lookup table: the annotated rows an assumed row could
#            pair with — flat, known length, NOT ~400 (an assumed-400 vs an
#            annotated-400 is same-length: no transition to read).
# Arguments: results — the annotated row dicts from loadTFGeometryResults.
# Output:    dict (athlete_id, event_short) -> list of qualifying rows.
def _indexFlatAnnotated(results) -> dict:
    index = {}
    for row in results:
        if (_isFlat(row["track_type"]) and row["track_length"] is not None
                and abs(row["track_length"] - 400.0) > 1.0):
            key = (row["athlete_id"], row["event_short"])
            index.setdefault(key, []).append(row)
    return index


# _bridgeKey
# Purpose:   The one-best-pair identity for a bridge candidate: which
#            athlete/event/transition it is. Mirrors _closestPerTransition's
#            rule, expressed as a dict key because pass 2 streams (we can't
#            collect-then-reduce a 70M stream; we reduce AS we stream).
# Arguments: assumed_row, annotated_row — the two sides.
# Output:    (athlete_id, event_short, min_len, max_len).
def _bridgeKey(assumed_row, annotated_row):
    la, lb = assumed_row["track_length"], annotated_row["track_length"]
    return (assumed_row["athlete_id"], assumed_row["event_short"],
            min(la, lb), max(la, lb))


# _emitLengthSampleTagged
# Purpose:   The 5-field sample: the usual 4 plus the assumed tag (1 when
#            either side is assumed). Replaces _emitLengthSample's output
#            shape everywhere, so the CSV has ONE schema.
# Arguments: pair — (row_a, row_b), both flat, lengths differ.
# Output:    (length_from, length_to, event_distance, ratio, assumed_flag).
def _emitLengthSampleTagged(pair):
    lf, lt, dist, ratio = _emitLengthSample(pair)     # reuse the old logic
    flag = 1 if (pair[0].get("assumed") or pair[1].get("assumed")) else 0
    return (lf, lt, dist, ratio, flag)


# buildAssumedBridge
# Purpose:   PASS 2, whole: stream assumed rows against the annotated index,
#            keep the closest-in-time pair per (athlete, event, transition),
#            emit tagged samples. Memory = the index + one dict entry per
#            bridging group; the 70M stream itself is never resident.
# Arguments: annotated_results — pass 1's rows;  window_days — the gate.
# Output:    list of 5-field samples (all with assumed_flag = 1).
def buildAssumedBridge(annotated_results, window_days=DEFAULT_WINDOW_DAYS):
    from geometry_db import streamAssumed400Records
    index = _indexFlatAnnotated(annotated_results)
    best = {}                                # key -> (gap_days, pair)
    for arow in streamAssumed400Records():
        group = index.get((arow["athlete_id"], arow["event_short"]))
        if not group:
            continue                         # semi-join tail: no flat partner
        for brow in group:
            gap = _daysApart(arow["date"], brow["date"])
            if gap > window_days:
                continue
            key = _bridgeKey(arow, brow)
            if key not in best or gap < best[key][0]:
                best[key] = (gap, (arow, brow))   # streaming reduce: keep best
    return [_emitLengthSampleTagged(pair) for _gap, pair in best.values()]


# ------------------------------------------------------------------ #
# THE BANKING CLOUD
# ------------------------------------------------------------------ #
#
# A banking sample compares two races of the same athlete/event at a MATCHED
# track length (within a tolerance, so length is held still), one on a BANKED
# track and one on a FLAT track, close in time. With length pinned and fitness
# ~cancelled, the time ratio is (close to) pure banking effect.
#
# Two differences from the length cloud, both deliberate:
#   - It stays ONE-CLOSEST-PER-GROUP (reuses _closestPair directly, no
#     per-transition layer). banked-vs-flat at a matched length is a single kind
#     of comparison, so there's no rare-coverage to rescue the way distinct
#     length transitions needed it — only flooding to defend against, which
#     _closestPair already does.
#   - The pair is emitted in canonical (banked, flat) order so the ratio
#     direction is fixed regardless of which row came first.
#
# The build mirrors the length cloud's shape:
#     _bankingPairsForGroup -> every matched-length banked-vs-flat pair in a group
#     _closestPair          -> the closest-in-time one (shared, unchanged)
#     _emitBankingSample    -> turn that pair into one (dist, length, ratio) tuple
#     buildBankingCloud     -> run those over every group, collect the cloud
# ------------------------------------------------------------------ #

# _bankingPairsForGroup
# Purpose:   Yield every valid banked-vs-flat pair inside ONE athlete/event group.
# Arguments: group       — list of row dicts for one (athlete,event,gender)
#            window_days  — max days apart (the close-in-time gate)
#            length_tol   — max metres the two lengths may differ and still count
#                           as "the same length" (length held constant)
# Output:    a generator of (banked_row, flat_row) tuples — note the CANONICAL
#            order: banked first, flat second, regardless of input order. A pair
#            qualifies when:
#              - exactly one row is banked and one is flat   [_isBanked/_isFlat]
#              - both have a known track_length              [not None]
#              - their lengths differ by <= length_tol       [length held still]
#              - the races are within window_days            [fitness held still]
def _bankingPairsForGroup(group, window_days, length_tol):
    n = len(group)
    for i in range(n):
        a = group[i]
        if a["track_length"] is None:
            continue
        # Classify the outer row once (banked / flat / neither) — cheaper than
        # re-deciding it against every inner row.
        a_banked = _isBanked(a["track_type"])
        a_flat = _isFlat(a["track_type"])
        if not (a_banked or a_flat):
            continue                         # unknown banking -> unusable
        for j in range(i + 1, n):
            b = group[j]
            if b["track_length"] is None:
                continue
            # We need EXACTLY one banked + one flat. Order the pair canonically as
            # (banked, flat); if both rows are the same class, there's no contrast.
            if a_banked and _isFlat(b["track_type"]):
                banked, flat = a, b
            elif a_flat and _isBanked(b["track_type"]):
                banked, flat = b, a
            else:
                continue                     # same class (or unknown) -> skip
            if abs(banked["track_length"] - flat["track_length"]) > length_tol:
                continue                     # lengths not matched -> length leaks in
            if _daysApart(a["date"], b["date"]) > window_days:
                continue                     # too far apart, fitness may have moved
            yield (banked, flat)


# _emitBankingSample
# Purpose:   Turn one banked-vs-flat pair into a single spline-ready sample tuple.
# Arguments: pair — a (banked_row, flat_row) tuple (matched length, opposite
#            banking).
# Output:    (event_distance, track_length, ratio) where
#            ratio = time(flat) / time(banked). Banked is the advantaged surface
#            (you carry speed through banked turns), so banked time is the smaller
#            one and ratio > 1 means "banking helped" — the same reading direction
#            as the length cloud's ratio > 1 = "the slower track."
#            track_length is the BANKED race's length: the effect we're crediting
#            is the banked oval's geometry, so we key it on the banked size (the
#            two lengths are within length_tol anyway, so the choice barely moves
#            it). event_distance is identical for both rows; taken from banked.
def _emitBankingSample(pair):
    banked, flat = pair
    ratio = flat["time_seconds"] / banked["time_seconds"]
    return (banked["event_distance"], banked["track_length"], ratio)


# buildBankingCloud
# Purpose:   Build the whole BANKING cloud — one sample per qualifying athlete/
#            event group (closest-in-time pair, no per-transition split).
# Arguments: results      — row dicts from loadTFGeometryResults()
#            window_days  — close-in-time gate (default DEFAULT_WINDOW_DAYS)
#            length_tol   — matched-length band (default DEFAULT_LENGTH_TOLERANCE_M)
# Output:    list of (event_distance, track_length, ratio) tuples — the raw,
#            continuous cloud that fit_geometry_correction.py fits the banking
#            surface to.
def buildBankingCloud(results, window_days=DEFAULT_WINDOW_DAYS,
                      length_tol=DEFAULT_LENGTH_TOLERANCE_M) -> list:
    groups = _indexByAthleteEvent(results)
    cloud = []
    # One pair per group: find matched-length banked-vs-flat pairs, keep the
    # closest in time, emit. buildBankingCloud stays short — it ORCHESTRATES the
    # helpers above, same as buildLengthCloud.
    for group in groups.values():
        pairs = _bankingPairsForGroup(group, window_days, length_tol)
        best = _closestPair(pairs)
        if best is not None:                 # group had no qualifying pair -> skip
            cloud.append(_emitBankingSample(best))
    return cloud


# ------------------------------------------------------------------ #
# REPORTING  (eyes-first: is the signal real, and how tight is it?)
# ------------------------------------------------------------------ #
#
# These print a human-readable summary so a person can judge each cloud BEFORE
# anyone fits a curve. The headline per display-group is the MEDIAN ratio (the
# factor) next to the IQR (the trust signal — tight = consistent across athletes,
# wide = still confounded).
#
# IMPORTANT: the bucketing here is for DISPLAY ONLY. We group ratios to print
# them, but the clouds returned by buildLengthCloud / buildBankingCloud stay raw
# and continuous — the fit step needs every point, un-bucketed.

# _summarizeCloud
# Purpose:   The shared report workhorse. Prints the overall median/IQR over a
#            cloud, then the same per display-group, groups ordered by sample
#            count (best-sampled first). Both clouds put `ratio` last in their
#            tuple, so we always read it as sample[-1].
# Arguments: title    — heading for this cloud.
#            cloud    — list of sample tuples (…, ratio).
#            key_fn   — sample -> the display-group key (collapses a dimension).
#            label_fn — group key -> its printed label.
# Output:    None (prints).
def _summarizeCloud(title, cloud, key_fn, label_fn) -> None:
    print(f"\n  {title}: {len(cloud)} samples")
    if not cloud:
        print("    (empty — no qualifying pairs)")
        return

    # Overall first — the single number for "does this effect exist at all".
    overall = [sample[-1] for sample in cloud]
    print(f"    {'OVERALL':<18}  n={len(overall):>5}  "
          f"median={_median(overall):.4f}  IQR={_iqr(overall):.4f}")

    # Then per display-group. setdefault collects each group's ratios; we never
    # touch the raw cloud, just read ratios out of it.
    groups = {}
    for sample in cloud:
        groups.setdefault(key_fn(sample), []).append(sample[-1])

    # sorted(..., key=count, reverse=True): most-sampled groups on top, since
    # those are the ones whose median/IQR you can actually trust.
    for key in sorted(groups, key=lambda k: len(groups[k]), reverse=True):
        ratios = groups[key]
        print(f"    {label_fn(key):<18}  n={len(ratios):>5}  "
              f"median={_median(ratios):.4f}  IQR={_iqr(ratios):.4f}")


# _summarizeLengthCloud
# Purpose:   Report the length cloud, display-grouped by the (from -> to) length
#            transition (collapsing over event_distance for readability).
# Arguments: cloud — buildLengthCloud output: (length_from, length_to, dist, ratio)
# Output:    None (prints).
def _summarizeLengthCloud(cloud) -> None:
    _summarizeCloud(
        "LENGTH cloud", cloud,
        # lambda writes a small, throwaway function inline.
        key_fn=lambda s: (s[0], s[1]),                 # (length_from, length_to)
        label_fn=lambda k: f"{k[0]:g}->{k[1]:g}m",     # e.g. "200->400m"
    )


# _summarizeBankingCloud
# Purpose:   Report the banking cloud, display-grouped by the banked oval length
#            (collapsing over event_distance).
# Arguments: cloud — buildBankingCloud output: (dist, track_length, ratio)
# Output:    None (prints).
def _summarizeBankingCloud(cloud) -> None:
    _summarizeCloud(
        "BANKING cloud", cloud,
        key_fn=lambda s: s[1],                         # track_length
        label_fn=lambda k: f"{k:g}m banked",           # e.g. "200m banked"
    )


# ------------------------------------------------------------------ #
# ORCHESTRATION + PERSISTENCE
# ------------------------------------------------------------------ #

# _writeCloudCSV
# Purpose:   Persist one cloud to CSV — header row then one row per sample. The
#            clouds are plain numeric tuples, so csv.writerows dumps them straight
#            out; fit_geometry_correction.py reads these back.
# Arguments: path   — output file path.
#            header — column names, in the same order as each sample's fields.
#            cloud  — list of sample tuples.
# Output:    the number of rows written (== len(cloud)).
def _writeCloudCSV(path, header, cloud) -> int:
    # newline="" is the csv module's required idiom — without it, Windows writes
    # a blank line between every row.
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(cloud)
    return len(cloud)

# _tagAnnotated
# Purpose:   Append the assumed flag (0 = annotated) to every 4-field length
#            sample, so annotated and bridge samples share ONE 5-field schema
#            in the combined CSV. The builders stay untouched — tagging at
#            the orchestration seam keeps the schema change in one place.
# Arguments: cloud — 4-field samples (length_from, length_to, dist, ratio).
# Output:    the same samples as 5-field tuples, flag 0.
def _tagAnnotated(cloud) -> list:
    # tuple + tuple concatenates: (a,b,c,d) + (0,) -> (a,b,c,d,0).
    # The trailing comma in (0,) is what makes it a 1-tuple, not the int 0.
    return [sample + (0,) for sample in cloud]


# runGeometryDiagnostic
# Purpose:   The whole diagnostic: load annotated rows, build both clouds,
#            build the ASSUMED-400 BRIDGE (pass 2), report all three, and
#            persist — length CSV now 5-field (…, assumed), banking CSV
#            unchanged (assumed rows are all Flat; they can never enter the
#            banking cloud, so it has no flag to carry).
# Arguments: (unchanged from before)
# Output:    (combined_length_cloud, banking_cloud) — combined = annotated
#            (flag 0) + bridge (flag 1), the exact rows the CSV holds.
def runGeometryDiagnostic(window_days=DEFAULT_WINDOW_DAYS,
                          length_tol=DEFAULT_LENGTH_TOLERANCE_M,
                          min_length_delta_m=DEFAULT_MIN_LENGTH_DELTA_M,
                          output_dir=DEFAULT_OUTPUT_DIR):

    # 1. PASS 1 — load annotated rows; build both clouds exactly as before.
    print("loading TF geometry results...", flush=True)
    results = loadTFGeometryResults()
    print(f"  {len(results):,} results")
    length_cloud  = buildLengthCloud(results, window_days, min_length_delta_m)
    banking_cloud = buildBankingCloud(results, window_days, length_tol)

    # 2. PASS 2 — stream the assumed-400 candidates against the annotated
    #    index; emits 5-field samples, every one flagged 1. THE SLOW STEP:
    #    first run pays the 70M-row semi-join.
    print("building assumed-400 bridge (streams ~70M candidates)...", flush=True)
    bridge = buildAssumedBridge(results, window_days)

    # 3. ONE schema: tag the annotated samples 0, concatenate.
    combined = _tagAnnotated(length_cloud) + bridge

    # 4. Report. _summarizeCloud reads the ratio as sample[-1] — true for
    #    4-field rows, FALSE for 5-field (the flag is last). Slicing s[:4]
    #    for display restores that contract without touching the summarizer;
    #    the clouds themselves keep all 5 fields for the CSV.
    print(f"\n=== geometry diagnostic  (window={window_days}d, "
          f"length_tol={length_tol:g}m, min_length_delta={min_length_delta_m:g}m) ===")
    _summarizeLengthCloud(length_cloud)              # annotated, 4-field as-is
    _summarizeCloud("BRIDGE cloud (assumed-400)",
                    [s[:4] for s in bridge],         # display-slice, see above
                    key_fn=lambda s: (s[0], s[1]),
                    label_fn=lambda k: f"{k[0]:g}->{k[1]:g}m")
    _summarizeBankingCloud(banking_cloud)

    # 5. Persist — length header gains the 5th column; banking unchanged.
    os.makedirs(output_dir, exist_ok=True)
    length_path  = os.path.join(output_dir, "geometry_length_cloud.csv")
    banking_path = os.path.join(output_dir, "geometry_banking_cloud.csv")
    _writeCloudCSV(length_path,
                   ["length_from", "length_to", "event_distance",
                    "ratio", "assumed"],
                   combined)
    _writeCloudCSV(banking_path,
                   ["event_distance", "track_length", "ratio"],
                   banking_cloud)
    print(f"\nwrote {len(combined):,} length samples "
          f"({len(bridge):,} bridge)  -> {length_path}")
    print(f"wrote {len(banking_cloud):,} banking samples -> {banking_path}")

    return combined, banking_cloud

# ------------------------------------------------------------------ #
# CLI: every tunable is a flag, so a sweep is one command away —
#   python engine/diagnose_geometry_pairs.py --window-days 14
# and you watch whether the measured factor MOVES (it shouldn't, much).
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    # Handles command line arguments.
    import argparse

    # Creates the parser, adds arguments when added or defaults to
    # constant values.
    parser = argparse.ArgumentParser(
        description="Matched-pair geometry diagnostic (length + banking clouds).")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help="max days apart for a matched pair")
    parser.add_argument("--length-tol", type=float, default=DEFAULT_LENGTH_TOLERANCE_M,
                        help="banking cloud: matched-length band (metres)")
    parser.add_argument("--min-length-delta", type=float,
                        default=DEFAULT_MIN_LENGTH_DELTA_M,
                        help="length cloud: min length difference to count (metres)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="where the two cloud CSVs are written")
    args = parser.parse_args()

    runGeometryDiagnostic(args.window_days, args.length_tol,
                          args.min_length_delta, args.output_dir)