#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/recall_probe.py
# Purpose: For a CONFIRMED meet pair, explain WHY the unmatched finishers didn't
#          match -- so the recall loss (the ~20% in a meet that's obviously the
#          same) becomes a diagnosis instead of a mystery. Every anet finisher
#          who found no tfrrs match falls into exactly one bucket:
#
#            TIMING GAP   - the SAME normalized name IS on the tfrrs side, but no
#                           time within tolerance. The person is there; the clocks
#                           disagree (hand vs FAT, gun vs chip, a transcription
#                           slip). Recoverable by understanding the timing offset.
#            NAME GAP     - no matching name, BUT someone on the tfrrs side ran the
#                           SAME time. Likely the same person under a different
#                           name (maiden/married, nickname, accent, middle name).
#                           Recoverable by better name normalization.
#            ONLY IN ANET - neither name nor time lines up. Genuinely one-sided
#                           (anet-only entry), or a double miss. Hard / not worth it.
#
#          Plus a tfrrs-only count (tfrrs finishers no anet row matched) for the
#          mirror-image loss. Read-only.
#
#          ONE HONEST CAVEAT: at a dense meet many people legitimately share a
#          time, so the NAME GAP bucket includes some time-coincidence false
#          positives (two different people, same time, different names). Treat its
#          COUNT as an upper bound and eyeball the samples -- a real name gap looks
#          like a plausible same-person rename; a coincidence looks like strangers.

import argparse
import re
import sys

sys.path.insert(0, "scripts")
from database import getConn

TIME_TOLERANCE = 0.3
RESULTS = {"TF": "results_tf", "XC": "results"}


# ================================================================== #
# CHUNK 1 -- DB ACCESS + NORMALIZATION (matcher's keys, reused)
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
    """The +-tolerance offsets, in 0.1s steps (same as the matcher)."""
    span = int(TIME_TOLERANCE * 10)
    return [round(d * 0.1, 1) for d in range(-span, span + 1)]


# ================================================================== #
# CHUNK 2 -- FINISHER LOADERS (one meet; raw name kept for display)
# ================================================================== #


# _anetFinishers
# Purpose: anet finishers for one meet as (raw_name, normName, roundedTime). Raw
#          name (via the athletes join) is kept so the samples are human-readable.
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


# _tfrrsFinishers
# Purpose: tfrrs finishers for one meet as (raw_name, normName, roundedTime).
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
# CHUNK 3 -- TFRRS INDEXES (built once per meet, for fast lookups)
# ================================================================== #


# _buildIndexes
# Purpose: Precompute the lookups the categorizer needs, so each anet finisher is
#          O(1)-ish to classify.
# Arguments: tfrrs -- list of (raw, norm, t).
# Output:  dict with:
#            by_key   {(norm,t): [i,...]}        -- exact match key (i = tfrrs index)
#            names    {norm}                      -- "this name exists in tfrrs"
#            by_time  {t: [(raw,norm),...]}       -- "who ran this time"
#            n_to_t   {norm: [t,...]}             -- "what times this name has"
def _buildIndexes(tfrrs):
    by_key, names, by_time, n_to_t = {}, set(), {}, {}
    for i, (raw, norm, t) in enumerate(tfrrs):
        if not norm or t is None:
            continue
        by_key.setdefault((norm, t), []).append(i)
        names.add(norm)
        by_time.setdefault(t, []).append((raw, norm))
        n_to_t.setdefault(norm, []).append(t)
    return {"by_key": by_key, "names": names, "by_time": by_time, "n_to_t": n_to_t}


# ================================================================== #
# CHUNK 4 -- CLASSIFY ONE MISS (the three-way decision)
# ================================================================== #


# _matchedKeys
# Purpose: Return the tfrrs indices an anet (name,t) matches, or [] if none.
def _matchedKeys(name, t, idx, ticks):
    for dt in ticks:
        hit = idx["by_key"].get((name, round(t + dt, 1)))
        if hit:
            return hit
    return []


# _timeNeighbor
# Purpose: A tfrrs finisher at the same time (within tolerance) with a DIFFERENT
#          name -- the evidence for a NAME GAP. Returns its raw name or None.
def _timeNeighbor(name, t, idx, ticks):
    for dt in ticks:
        for raw, norm in idx["by_time"].get(round(t + dt, 1), []):
            if norm != name:
                return raw
    return None


# _classifyMiss
# Purpose: Put one unmatched anet finisher into a bucket.
# Output:  (bucket, detail) where bucket is 'timing'/'name'/'only_anet' and detail
#          is a short display string for the sample.
def _classifyMiss(raw, name, t, idx, ticks):
    if name in idx["names"]:
        tfrrs_times = sorted(set(idx["n_to_t"].get(name, [])))
        return "timing", f"{raw:<26} anet {t:>7.1f}  tfrrs has {tfrrs_times[:3]}"
    neighbor = _timeNeighbor(name, t, idx, ticks)
    if neighbor is not None:
        return "name", f"anet {raw!r:<24} @{t:<7.1f} vs tfrrs {neighbor!r} @~{t}"
    return "only_anet", f"{raw:<26} anet {t:>7.1f}  (no name, no time on tfrrs side)"


# ================================================================== #
# CHUNK 5 -- REPORT ONE PAIR
# ================================================================== #


# _bucketAnet
# Purpose: Walk every anet finisher, splitting into shared vs the three miss
#          buckets, and track which tfrrs rows got matched (for tfrrs-only count).
# Output:  (counts dict, samples dict, matched_tfrrs set).
def _bucketAnet(anet, idx, ticks):
    counts = {"shared": 0, "timing": 0, "name": 0, "only_anet": 0}
    samples = {"timing": [], "name": [], "only_anet": []}
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
        if len(samples[bucket]) < 6:
            samples[bucket].append(detail)
    return counts, samples, matched_tfrrs


# _reportPair
# Purpose: Print the recall breakdown for one (anet_meet, tfrrs_meet) pair.
def _reportPair(sport, anet_meet, tfrrs_meet):
    table = RESULTS[sport]
    anet = _anetFinishers(table, anet_meet)
    tfrrs = _tfrrsFinishers(table, tfrrs_meet)
    idx = _buildIndexes(tfrrs)
    ticks = _ticks()
    counts, samples, matched = _bucketAnet(anet, idx, ticks)

    print(f"\n=== {sport}: anet {anet_meet} <-> tfrrs {tfrrs_meet} ===")
    print(f"  anet finishers {len(anet):,}   tfrrs finishers {len(tfrrs):,}")
    shared = counts["shared"]
    print(f"  shared              {shared:>7,}  ({_pct(shared, len(anet)):.1f}% of anet)")
    print(f"  unmatched anet:")
    print(f"    timing gap        {counts['timing']:>7,}  (name on tfrrs, time off -> timing offset)")
    print(f"    name gap          {counts['name']:>7,}  (time present, name differs -> normalization)")
    print(f"    only in anet      {counts['only_anet']:>7,}  (neither -> one-sided / hard)")
    tfrrs_only = len(tfrrs) - len(matched)
    print(f"  tfrrs-only (mirror) {tfrrs_only:>7,}  (tfrrs finishers no anet row matched)")

    for label, key in (("timing-gap", "timing"), ("name-gap candidates", "name")):
        if samples[key]:
            print(f"  -- {label} samples --")
            for s in samples[key]:
                print(f"     {s}")


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


# ================================================================== #
# CHUNK 6 -- WHICH PAIRS + DRIVER
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
    p = argparse.ArgumentParser(description="Diagnose recall loss in confirmed meet pairs.")
    p.add_argument("--sport", required=True, choices=["TF", "XC"])
    p.add_argument("--pairs", help='explicit pairs: "240132:24888,92633:6334"')
    p.add_argument("--top", type=int, default=3, help="if no --pairs, probe top-N meets.")
    return p.parse_args()


def main():
    args = _parseArgs()
    pairs = _parsePairs(args.pairs) or _topPairs(args.sport, args.top)
    print(f"=== recall probe: {len(pairs)} {args.sport} meet pair(s) ===")
    for anet_meet, tfrrs_meet in pairs:
        _reportPair(args.sport, anet_meet, tfrrs_meet)
    print("\n=== done. timing gap -> timing study; name gap -> normalization. ===")


if __name__ == "__main__":
    main()