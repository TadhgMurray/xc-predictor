#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/recall_probe_scored.py   (supersedes recall_probe.py)
# Purpose: Same recall diagnosis as recall_probe.py, but it now SCORES the name
#          gaps instead of counting raw "same time, different name" collisions.
#          The earlier probe over-counted: at a 3,700-runner meet dozens of people
#          share a time, so "different name at the same time" swept up strangers.
#
#          TWO CHANGES THAT FIX THAT:
#            1. BEST neighbor, not first. For an unmatched anet finisher we look at
#               EVERY tfrrs runner within the time window and keep the one whose
#               name is the closest, instead of whichever happened to be first.
#            2. SCORE the closest. A real name gap is a small change (Emory/Emery,
#               a dropped middle name, a reordered first/last); a coincidence is two
#               unrelated names that merely tied. difflib's similarity ratio (0..1)
#               plus a token-subset check separates them.
#
#          BUCKETS for each unmatched anet finisher:
#            timing gap   - exact name on tfrrs, time off (unchanged; ~0 in practice)
#            name gap STRONG  - best neighbor is a near-spelling (sim >= 0.85). These
#                              are near-certain same-person, and SAFE to recover
#                              because the time already matches exactly -- it's only
#                              fuzzy on ONE axis, not both.
#            name gap POSSIBLE - looser variant / dropped-or-reordered token
#                              (0.70 <= sim < 0.85, or one token-set inside the
#                              other). Review before trusting.
#            coincidence  - best neighbor is still unrelated (sim < 0.70, no token
#                              overlap). NOT the same person; not recoverable.
#            only in anet - no tfrrs runner at that time at all.
#
#          So "115 candidates" becomes "N strong (here they are) + M possible +
#          the rest coincidence" -- the actual recoverable count. Read-only.

import argparse
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, "scripts")
from database import getConn

TIME_TOLERANCE = 0.3
RESULTS = {"TF": "results_tf", "XC": "results"}
SIM_STRONG = 0.85          # >= this == near-spelling, near-certain same person
SIM_POSSIBLE = 0.70        # >= this == plausible variant, worth review


# ================================================================== #
# CHUNK 1 -- DB ACCESS + NORMALIZATION
# ================================================================== #


def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _normName(name):
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _roundTime(t):
    return None if t is None else round(float(t), 1)


def _ticks():
    span = int(TIME_TOLERANCE * 10)
    return [round(d * 0.1, 1) for d in range(-span, span + 1)]


# ================================================================== #
# CHUNK 2 -- FINISHER LOADERS (one meet; raw name kept for display)
# ================================================================== #


def _anetFinishers(table, meet_id):
    rows = _query(
        f"""
        SELECT (COALESCE(a.first_name,'')||' '||COALESCE(a.last_name,'')) AS name,
               r.time_seconds
        FROM {table} r
        LEFT JOIN athletes a ON a.athlete_id=r.athlete_id AND a.school=r.school
        WHERE r.source='anet' AND r.meet_id=%s
          AND r.time_seconds IS NOT NULL AND r.time_seconds<>999999
          AND r.place IS NOT NULL AND r.place<>0
        """,
        (meet_id,),
    )
    return [(raw or "?", _normName(raw), _roundTime(t)) for raw, t in rows]


def _tfrrsFinishers(table, meet_id):
    rows = _query(
        f"""
        SELECT r.athlete_name, r.time_seconds
        FROM {table} r
        WHERE r.source='tfrrs' AND r.meet_id=%s
          AND r.time_seconds IS NOT NULL
          AND r.place IS NOT NULL AND r.place<>0
        """,
        (meet_id,),
    )
    return [(raw or "?", _normName(raw), _roundTime(t)) for raw, t in rows]


# ================================================================== #
# CHUNK 3 -- TFRRS INDEXES
# ================================================================== #


# _buildIndexes
# Output: by_key {(norm,t):[i]} for exact match; names {norm} for "name exists";
#         by_time {t:[(raw,norm)]} for "who ran this time".
def _buildIndexes(tfrrs):
    by_key, names, by_time = {}, set(), {}
    for i, (raw, norm, t) in enumerate(tfrrs):
        if not norm or t is None:
            continue
        by_key.setdefault((norm, t), []).append(i)
        names.add(norm)
        by_time.setdefault(t, []).append((raw, norm))
    return {"by_key": by_key, "names": names, "by_time": by_time}


# ================================================================== #
# CHUNK 4 -- NAME SIMILARITY (the scorer)
# ================================================================== #


# _ratio
# Purpose: Global string similarity 0..1 (handles typos, transpositions, and
#          partial overlap gracefully -- stdlib, no install).
def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


# _tokens
# Purpose: Word tokens, split on spaces AND hyphens, so a hyphenated surname's
#          parts are separate -- lets a dropped component register as a subset.
def _tokens(name):
    return set(re.split(r"[ \-]+", name)) - {""}


# _matchKind
# Purpose: Classify the relationship between two normalized names.
# Output:  'strong' / 'possible' / 'coincidence'.
def _matchKind(a, b):
    ta, tb = _tokens(a), _tokens(b)
    r = _ratio(a, b)
    subset = bool(ta and tb and (ta <= tb or tb <= ta))   # one name inside the other
    if r >= SIM_STRONG:
        return "strong"
    if r >= SIM_POSSIBLE or subset:
        return "possible"
    if ta & tb:                       # share a token but otherwise unlike
        return "possible"             # err toward review, not auto-reject
    return "coincidence"


# ================================================================== #
# CHUNK 5 -- CLASSIFY ONE MISS
# ================================================================== #


def _matchedKeys(name, t, idx, ticks):
    for dt in ticks:
        hit = idx["by_key"].get((name, round(t + dt, 1)))
        if hit:
            return hit
    return []


# _bestNeighbor
# Purpose: Among ALL tfrrs runners within the time window (different name), the one
#          whose name is closest to the anet name. THIS is the fix for the old
#          "first arbitrary neighbor" over-counting.
# Output:  (raw, norm, ratio) of the best, or (None, None, -1) if no neighbor.
def _bestNeighbor(name, t, idx, ticks):
    best_raw, best_norm, best_r = None, None, -1.0
    for dt in ticks:
        for raw, norm in idx["by_time"].get(round(t + dt, 1), []):
            if norm == name:
                continue                      # exact == would already be a match
            r = _ratio(name, norm)
            if r > best_r:
                best_raw, best_norm, best_r = raw, norm, r
    return best_raw, best_norm, best_r


# _classifyMiss
# Purpose: Bucket one unmatched anet finisher.
# Output:  (bucket, detail_string).
def _classifyMiss(raw, name, t, idx, ticks):
    if name in idx["names"]:                  # exact name elsewhere, time off
        return "timing", f"{raw:<26} anet {t:>7.1f}  (name on tfrrs, time differs)"
    n_raw, n_norm, r = _bestNeighbor(name, t, idx, ticks)
    if n_raw is None:
        return "only_anet", f"{raw:<26} anet {t:>7.1f}  (no tfrrs runner at this time)"
    kind = _matchKind(name, n_norm)           # 'strong'/'possible'/'coincidence'
    return kind, f"anet {raw!r:<24} vs tfrrs {n_raw!r}  (sim {r:.2f})"


# ================================================================== #
# CHUNK 6 -- BUCKET + REPORT
# ================================================================== #


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


# _bucketAnet
# Purpose: Walk every anet finisher into shared + the miss buckets, tracking which
#          tfrrs rows matched (for the tfrrs-only mirror count).
def _bucketAnet(anet, idx, ticks):
    counts = {k: 0 for k in ("shared", "timing", "strong", "possible",
                             "coincidence", "only_anet")}
    samples = {"strong": [], "possible": [], "coincidence": []}
    matched_tfrrs = set()
    for raw, name, t in anet:
        if not name or t is None:
            continue
        hits = _matchedKeys(name, t, idx, ticks)
        if hits:
            counts["shared"] += 1
            matched_tfrrs.update(hits)
            continue
        bucket, detail = _classifyMiss(raw, name, t, idx, ticks)
        counts[bucket] += 1
        if bucket in samples and len(samples[bucket]) < 8:
            samples[bucket].append(detail)
    return counts, samples, matched_tfrrs


# _reportPair
def _reportPair(sport, anet_meet, tfrrs_meet):
    table = RESULTS[sport]
    anet = _anetFinishers(table, anet_meet)
    tfrrs = _tfrrsFinishers(table, tfrrs_meet)
    idx = _buildIndexes(tfrrs)
    counts, samples, matched = _bucketAnet(anet, idx, _ticks())

    print(f"\n=== {sport}: anet {anet_meet} <-> tfrrs {tfrrs_meet} ===")
    print(f"  anet finishers {len(anet):,}   tfrrs finishers {len(tfrrs):,}")
    print(f"  shared                 {counts['shared']:>7,}  "
          f"({_pct(counts['shared'], len(anet)):.1f}% of anet)")
    print(f"  unmatched anet:")
    print(f"    timing gap           {counts['timing']:>7,}")
    print(f"    name gap STRONG      {counts['strong']:>7,}  <- recoverable, near-certain")
    print(f"    name gap possible    {counts['possible']:>7,}  <- review")
    print(f"    coincidence          {counts['coincidence']:>7,}  <- not the same person")
    print(f"    only in anet         {counts['only_anet']:>7,}")
    print(f"  tfrrs-only (mirror)    {len(tfrrs) - len(matched):>7,}")

    rec_lo, rec_hi = counts["strong"], counts["strong"] + counts["possible"]
    print(f"  => recoverable by name fix: {rec_lo:,} confident, up to {rec_hi:,} with review")

    if samples["strong"]:
        print("  -- STRONG (the real near-spellings) --")
        for s in samples["strong"]:
            print(f"     {s}")
    if samples["coincidence"]:
        print("  -- coincidence (correctly rejected) --")
        for s in samples["coincidence"][:3]:
            print(f"     {s}")


# ================================================================== #
# CHUNK 7 -- PAIRS + DRIVER
# ================================================================== #


def _parsePairs(text):
    if not text:
        return []
    return [tuple(int(x) for x in chunk.split(":")) for chunk in text.split(",")]


def _topPairs(sport, n):
    rows = _query(
        """
        SELECT anet_id, tfrrs_id FROM entity_links
        WHERE entity_type='meet' AND sport=%s ORDER BY confidence DESC LIMIT %s
        """,
        (sport, n),
    )
    return [(a, t) for a, t in rows]


def _parseArgs():
    p = argparse.ArgumentParser(description="Scored recall probe for confirmed meet pairs.")
    p.add_argument("--sport", required=True, choices=["TF", "XC"])
    p.add_argument("--pairs", help='explicit pairs: "240132:24888,92633:6334"')
    p.add_argument("--top", type=int, default=3)
    return p.parse_args()


def main():
    args = _parseArgs()
    pairs = _parsePairs(args.pairs) or _topPairs(args.sport, args.top)
    print(f"=== scored recall probe: {len(pairs)} {args.sport} meet pair(s) ===")
    for anet_meet, tfrrs_meet in pairs:
        _reportPair(args.sport, anet_meet, tfrrs_meet)
    print("\n=== done. STRONG = recover now; possible = review; coincidence = ignore. ===")


if __name__ == "__main__":
    main()