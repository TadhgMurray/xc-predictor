#!/usr/bin/env python
# Project: xc-predictor
# Subset:  diagnostics
# File:    split_probe.py
# Purpose: find WHAT splits a division into two applied-factor populations.
#
# THE OBSERVATION THIS EXISTS TO EXPLAIN
#     cluster A   1.21510   constant to 5dp
#     cluster B   0.96328   constant to 5dp
#     ratio       1.26142
#   Constant within cluster and independent of pace means the split is driven
#   by a CATEGORICAL row attribute resolved before normalization -- not by
#   weather, ability, or anything continuous.
#
# THE METHOD
#   A discriminator is any attribute where each of its values maps to exactly
#   ONE factor bucket. Perfect partition = that attribute is the cause (or a
#   proxy for it). The prime suspect, given _xcQuery's own comment about the
#   per-division jsonb, is the ENGINE-DECODED DISTANCE.
#
# WHY THE CROSSTAB IS IN PYTHON
#   The attributes come from two places: `venue` and `gender` are computed by
#   the engine's query, everything else lives on the raw results row. Merging
#   on result_id in Python means the engine's SQL is used verbatim and nothing
#   is reimplemented. Writing this as one big SQL statement would mean copying
#   _xcQuery's joins, which is the exact mistake v1 made.
#
# WRITES NOTHING.
#
# USAGE (from the project root)
#   python scripts\split_probe.py --meet 26785 --div 0

import argparse
import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn
from speed_ratings_db import COLUMNS, _xcQuery, _splitVenueKey, \
    loadCanonicalNames


# =============================================================================
# SECTION 0 - CONSTANTS
# =============================================================================

MIN_TIME, MAX_TIME = 600.0, 3600.0   # streamResults' defaults, kept identical

# Round the factor to this many places to form a bucket. 4 is chosen from the
# data: observed within-cluster spread is ~1e-5, between-cluster gap is 0.25.
# At 6dp every row becomes its own bucket and the crosstab says nothing.
BUCKET_DP = 4

MAX_VALUES_PER_DIM = 12              # keep `school` readable


# =============================================================================
# SECTION 1 - THE KNOBS
# Each entry is (label, accessor). The generic crosstab() below is called once
# per entry, so adding a candidate cause is ONE LINE. Same shape as
# flag_records(races, key_fn, value_fn, better): the logic is written once and
# the differences are parameters.
#
# Each accessor takes the merged row dict and returns a display string.
# =============================================================================

DISCRIMINATORS = [
    ("engine distance", lambda r: str(r["distance"])),
    ("engine course",   lambda r: str(r["course"])[:28]),
    ("gender",          lambda r: r["gender"] or "(unresolved)"),
    ("source",          lambda r: r["source"]),
    ("grade",           lambda r: (r["grade"] or "").strip() or "(blank)"),
    ("athlete_id",      lambda r: "NULL" if r["athlete_id"] is None else "set"),
    ("canon link",      lambda r: "NULL" if r["canon_meet_id"] is None
                                  else "linked"),
    ("school",          lambda r: str(r["school"])[:28]),
]


# =============================================================================
# SECTION 2 - PRIMITIVES
# =============================================================================

def banner(title):
    print()
    print("-" * 70)
    print(title)
    print("-" * 70)


def fetch(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


# =============================================================================
# SECTION 3 - LOAD AND MERGE
# Two sources, joined on result_id in Python.
# =============================================================================

def loadEngineRows(cur, meet_id, div_id):
    """The engine's real loader, narrowed to one division.

    _xcQuery's text ends inside its WHERE clause, so appending another AND is
    a legal narrowing that touches no join. tw="" skips the dedup anti-join,
    which is why `canon link` is a discriminator below -- if a twin is the
    cause, that column will show it.
    """
    sql = _xcQuery(MIN_TIME, MAX_TIME, tw="") + \
        "\n          AND r.meet_id = %s AND r.div_id = %s"
    return [dict(zip(COLUMNS, r)) for r in fetch(cur, sql, (meet_id, div_id))]


def loadRawRows(cur, meet_id, div_id):
    """The columns the engine query does not return, keyed by result_id."""
    rows = fetch(cur, """
        SELECT result_id, time_seconds, athlete_id, person_id, canon_meet_id
        FROM results
        WHERE meet_id = %s AND div_id = %s
    """, (meet_id, div_id))
    keys = ("result_id", "time_seconds", "athlete_id", "person_id",
            "canon_meet_id")
    return {r[0]: dict(zip(keys, r)) for r in rows}


def mergeRows(engine_rows, raw_rows, names):
    """Produce one flat dict per row carrying every candidate attribute.

    `venue` is decoded with _splitVenueKey -- the established decoder -- so the
    distance reported is byte-for-byte the one the engine used. The "XC:"
    prefix is re-added because packResults adds it before the splitter ever
    sees a key.
    """
    merged = []
    for e in engine_rows:
        raw = raw_rows.get(e["result_id"])
        if not raw or not raw["time_seconds"] or not e["normalized_time"]:
            continue

        if e["venue"] is None:
            course, distance = None, None
        else:
            course, _cid, distance = _splitVenueKey("XC:" + e["venue"], names)

        merged.append({
            "result_id":     e["result_id"],
            "bucket":        round(raw["time_seconds"] / e["normalized_time"],
                                   BUCKET_DP),
            "course":        course,
            "distance":      distance,
            "gender":        e["gender"],
            "source":        e["source"],
            "grade":         e["grade"],
            "school":        e["school"],
            "athlete_id":    raw["athlete_id"],
            "canon_meet_id": raw["canon_meet_id"],
        })
    return merged


# =============================================================================
# SECTION 4 - THE BUCKET CENSUS
# Confirm HOW MANY populations exist before asking what separates them.
# =============================================================================

def censusBuckets(merged):
    counts = defaultdict(int)
    for r in merged:
        counts[r["bucket"]] += 1

    print(f"  {'bucket':>10} {'rows':>6}")
    for bucket, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {bucket:>10} {n:6d}")

    # Buckets in size order = stable column headers for every crosstab.
    return [b for b, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


# =============================================================================
# SECTION 5 - THE GENERIC CROSSTAB
# ONE function, called once per DISCRIMINATORS entry.
# =============================================================================

def crosstab(merged, label, accessor, buckets):
    """Cross-tab one attribute against the factor buckets and judge it.

    PERFECT SPLIT - every value lands in exactly one bucket. A clean partition
                    is what a causal attribute looks like.
    partial       - some values straddle; contributing, not the cause.
    no signal     - orthogonal to the split.
    """
    table = defaultdict(lambda: defaultdict(int))
    for r in merged:
        table[accessor(r)][r["bucket"]] += 1

    if not table:
        print(f"  [{label}] no rows")
        return

    print(f"  [{label}]")
    print(f"    {'value':<30}" + "".join(f"{str(b):>12}" for b in buckets))

    straddlers = 0
    ordered = sorted(table, key=lambda v: -sum(table[v].values()))
    for value in ordered[:MAX_VALUES_PER_DIM]:
        cells = "".join(f"{table[value].get(b, 0):>12}" for b in buckets)
        if sum(1 for b in table[value] if table[value][b]) > 1:
            straddlers += 1
        print(f"    {value[:30]:<30}{cells}")

    if straddlers == 0:
        print(f"    ==> PERFECT SPLIT on {label}")
    elif straddlers < len(ordered):
        print(f"    ==> partial ({straddlers} of {len(ordered)} straddle)")
    else:
        print(f"    ==> no signal")


# =============================================================================
# SECTION 6 - ORCHESTRATION
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    args = ap.parse_args()

    names = loadCanonicalNames()

    with getConn() as conn:
        with conn.cursor() as cur:
            engine_rows = loadEngineRows(cur, args.meet, args.div)
            raw_rows = loadRawRows(cur, args.meet, args.div)
            merged = mergeRows(engine_rows, raw_rows, names)

            banner(f"FACTOR BUCKETS - meet {args.meet} div {args.div}")
            if not merged:
                print("  nothing usable; run probe_meet.py for the reason")
                return
            buckets = censusBuckets(merged)

            if len(buckets) < 2:
                print("\n  One bucket only. The division is internally "
                      "consistent, so the problem is the SHARED factor, not a "
                      "split. Go back to probe_meet.py sections 3 and 6.")
                return

            banner("WHAT SEPARATES THE BUCKETS?")
            for label, accessor in DISCRIMINATORS:
                crosstab(merged, label, accessor, buckets)


if __name__ == "__main__":
    main()