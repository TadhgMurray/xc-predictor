#!/usr/bin/env python
# Project: xc-predictor
# Subset:  diagnostics
# File:    probe_meet.py
# Purpose: READ-ONLY provenance dossier for one XC division, built from the
#          ENGINE'S OWN loader query rather than a reimplementation of it.
#
# WHY IT IMPORTS INSTEAD OF REIMPLEMENTING
#   v1 of this script hand-wrote the joins and got three of them wrong:
#     * joined `meets` on meet_id. It is keyed on div_id and disambiguated by
#       source -- `m.div_id = r.div_id AND m.source = r.source`.
#     * joined `athletes` on the composite (athlete_id, school). tfrrs XC has
#       athlete_id NULL on 100% of rows, so that join can never resolve them;
#       the engine uses a LATERAL on COALESCE(person_id, athlete_id).
#     * looked up course_difficulties by course_name. The real key is
#       (canonical_id, distance_m); name alone now legitimately returns several
#       rows, which is the collision the canonical split exists to expose.
#   Every one of those was a wrong number, not an error. So this version calls
#   _xcQuery and lets the engine compute venue, distance and gender. A
#   diagnostic must replicate the producing code; only a VERIFIER must not.
#
# HOW THE RESTRICTION WORKS
#   _xcQuery's text ends inside its WHERE clause, so appending
#   `AND r.meet_id = %s AND r.div_id = %s` narrows it to one division without
#   touching a single join. If the engine's filters change, this follows.
#
# WRITES NOTHING. It also passes tw="" (no twin table), so unlike streamResults
#   it creates no scratch table -- at the cost of not applying cross-source
#   dedup. canon_meet_id is reported so you can see whether that matters here.
#
# USAGE
#   Run from the project root (the sys.path inserts are relative):
#     python scripts\probe_meet.py --meet 26785 --div 0
#     python scripts\probe_meet.py --meet 26785 --div 0 --control-meet 270909 \
#                                  --control-div 1079426

import argparse
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                      # the established connector
from speed_ratings_db import (                    # the established SQL
    COLUMNS,            # ("result_id","person_id","normalized_time","grade",
                        #  "source","school","date","sport","venue","gender")
    _xcQuery,           # the engine's real XC loader
    _splitVenueKey,     # venue string -> (display_name, canonical_id, distance)
    loadCanonicalNames, # {canonical_id_text: canonical_name}
)


# =============================================================================
# SECTION 0 - CONSTANTS AND TINY PRIMITIVES
# Nothing here touches the DB, so each one is trivially testable.
# =============================================================================

# streamResults' defaults. Kept identical so "the engine sees this row" means
# the same thing here as it does in a real run.
MIN_TIME, MAX_TIME = 600.0, 3600.0

METRES_PER_MILE = 1609.344

# Physics rails from the triage code. A distance implying a winner faster than
# the floor or a tail slower than the ceiling is REFUTED -- and this test never
# touches a rating, so bracket contamination cannot reach it.
WINNER_FLOOR_S_PER_MILE = 230.0
TAIL_CEIL_S_PER_MILE = 1020.0


def pacePerMile(seconds, metres):
    """Seconds per mile. `not metres` catches None and 0 together, which is the
    exact case this script exists to detect."""
    if not metres or not seconds:
        return None
    return seconds / (metres / METRES_PER_MILE)


def fmtSecs(seconds):
    """m:ss.t for human eyes. `04.1f` zero-pads to width 4 with one decimal."""
    if seconds is None:
        return "-"
    minutes = int(seconds // 60)
    return f"{minutes}:{seconds - minutes * 60:04.1f}"


def banner(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def fetch(cur, sql, params=()):
    """One query, all rows. One place to add timing or logging later."""
    cur.execute(sql, params)
    return cur.fetchall()


def asDicts(rows):
    """Zip the engine's tuples against COLUMNS so downstream code can say
    row["venue"] instead of row[8]. Positional unpacking of a 10-tuple is
    exactly how an added column silently shifts every field."""
    return [dict(zip(COLUMNS, r)) for r in rows]


# =============================================================================
# SECTION 1 - THE TWO POPULATIONS
# Q_ENGINE  what the engine actually loads (real joins, real filters)
# Q_RAW     every row in the division, unfiltered
# The DIFFERENCE between them is the set of rows the engine discards, and that
# set is itself a finding.
# =============================================================================

def loadEngineRows(cur, meet_id, div_id):
    """Run the engine's own XC query, narrowed to one division.

    Appending to _xcQuery's text is safe because that text ends inside its
    WHERE clause. tw="" disables the dedup anti-join (no scratch table).
    """
    sql = _xcQuery(MIN_TIME, MAX_TIME, tw="") + \
        "\n          AND r.meet_id = %s AND r.div_id = %s"
    return asDicts(fetch(cur, sql, (meet_id, div_id)))


def loadRawRows(cur, meet_id, div_id):
    """Every row in the division, with the columns the engine query does not
    return. Keyed by result_id so the two sets can be merged in Python."""
    rows = fetch(cur, """
        SELECT result_id, time_seconds, normalized_time, place, grade,
               school, source, athlete_id, person_id, canon_meet_id, date
        FROM results
        WHERE meet_id = %s AND div_id = %s
    """, (meet_id, div_id))
    keys = ("result_id", "time_seconds", "normalized_time", "place", "grade",
            "school", "source", "athlete_id", "person_id", "canon_meet_id",
            "date")
    return {r[0]: dict(zip(keys, r)) for r in rows}


def reportVisibility(engine_rows, raw_rows):
    """Itemise why rows were discarded, against the engine's actual filters.

    Each test mirrors one clause of _xcQuery's WHERE. A row can fail several;
    the first matching reason is reported, so the counts sum to the total.
    """
    seen = {r["result_id"] for r in engine_rows}
    reasons = Counter()

    for rid, r in raw_rows.items():
        if rid in seen:
            continue
        nt = r["normalized_time"]
        if nt is None:
            reasons["normalized_time NULL (dropped by a backfill guard)"] += 1
        elif not (MIN_TIME <= nt <= MAX_TIME):
            reasons[f"normalized_time outside [{MIN_TIME},{MAX_TIME}]"] += 1
        elif r["person_id"] is None:
            reasons["person_id NULL (unlinked / id-less / retracted)"] += 1
        elif r["date"] is None:
            reasons["date NULL"] += 1
        else:
            reasons["other (wheelchair/seated division, or a join)"] += 1

    print(f"  rows in results:      {len(raw_rows)}")
    print(f"  rows the engine sees: {len(engine_rows)}")
    for reason, n in reasons.most_common():
        print(f"    discarded {n:5d}  {reason}")
    return reasons


# =============================================================================
# SECTION 2 - THE VENUE KEY, DECODED
# The engine's `venue` string carries BOTH the course identity and the resolved
# distance: "<canonical_id|name:...>:d<distance>". _splitVenueKey is the
# established decoder, so importing it means the distance reported here is
# byte-for-byte the distance the engine used.
# =============================================================================

def decodeVenues(engine_rows, names):
    """Group the division's rows by decoded (course, distance).

    MORE THAN ONE GROUP IS THE HEADLINE. It means one div_id resolved to two
    different distances -- the women's-5000/men's-8000 case documented in
    _xcQuery, which is what produced the 895s "14:55 5k".
    """
    groups = defaultdict(list)
    for row in engine_rows:
        venue = row["venue"]
        if venue is None:
            groups[(None, None)].append(row)
            continue
        # _splitVenueKey expects the sport prefix that packResults adds.
        display, canonical_id, distance = _splitVenueKey("XC:" + venue, names)
        groups[(display, distance)].append(row)

    print(f"  {'course':<38} {'distance':>9} {'rows':>6} {'norm min':>9}")
    for (display, distance), rows in sorted(
            groups.items(), key=lambda kv: -len(kv[1])):
        fastest = min((r["normalized_time"] for r in rows
                       if r["normalized_time"]), default=None)
        print(f"  {str(display)[:38]:<38} {str(distance):>9} "
              f"{len(rows):6d} {fmtSecs(fastest):>9}")

    if len(groups) > 1:
        print("  ==> MORE THAN ONE DISTANCE IN ONE div_id. This is the "
              "two-distance-meet case; a division-wide fix is illegal here.")
    return groups


# =============================================================================
# SECTION 3 - IS THE STORED normalized_time CONSISTENT WITH THAT DISTANCE?
# The venue key comes from the ENGINE (fixed to read the per-division jsonb).
# normalized_time comes from the BACKFILL (which may still read only the
# scalar mt.distance). If those two disagree, that gap is the live bug.
# =============================================================================

def reportAppliedFactor(engine_rows, raw_rows, groups, label="SUSPECT"):
    """applied_factor = time_seconds / normalized_time, per distance group.

    A factor constant to ~5dp within a group means one multiplier hit
    everybody -- no pace-dependent term. Two groups with two constants means
    two distances were applied. Compare each group's factor against the
    distance the ENGINE decoded: if the distances differ but the factors are
    equal, the backfill normalised both against one distance and the fix has
    not reached normalized_time.
    """
    print(f"  {label}: {'distance':>9} {'n':>5} {'factor':>12} {'spread':>10}")
    out = {}
    for (display, distance), rows in sorted(
            groups.items(), key=lambda kv: -len(kv[1])):
        factors = []
        for r in rows:
            raw = raw_rows.get(r["result_id"])
            if raw and raw["time_seconds"] and r["normalized_time"]:
                factors.append(raw["time_seconds"] / r["normalized_time"])
        if not factors:
            continue
        mean = sum(factors) / len(factors)
        spread = max(factors) - min(factors)
        print(f"  {'':>9} {str(distance):>9} {len(factors):5d} "
              f"{mean:12.5f} {spread:10.2e}")
        out[distance] = mean

    if len(out) == 1 and len(groups) > 1:
        print("  ==> ONE factor across TWO decoded distances: normalized_time "
              "was written against a SINGLE distance. The jsonb fix has not "
              "reached backfill_normalize._resolveDistanceGender.")
    return out


# =============================================================================
# SECTION 4 - PHYSICS, FROM RAW TIMES ONLY
# Immune to bracket contamination because it never touches a rating.
# =============================================================================

def reportPhysics(raw_rows, distances):
    """Winner / median / tail pace under each decoded distance."""
    times = sorted(r["time_seconds"] for r in raw_rows.values()
                   if r["time_seconds"] and 0 < r["time_seconds"] < 99999
                   and r["place"] != 0)
    if not times:
        print("  no usable times after sentinel filtering")
        return

    fastest, median, tail = times[0], times[len(times) // 2], times[-1]
    print(f"  usable finishers {len(times)}   winner {fmtSecs(fastest)}  "
          f"median {fmtSecs(median)}  tail {fmtSecs(tail)}")

    print(f"  {'distance':>9} {'winner/mi':>10} {'median/mi':>10} "
          f"{'tail/mi':>9}   verdict")
    for d in sorted(x for x in distances if x):
        w, m, t = (pacePerMile(fastest, d), pacePerMile(median, d),
                   pacePerMile(tail, d))
        bad = w < WINNER_FLOOR_S_PER_MILE or t > TAIL_CEIL_S_PER_MILE
        print(f"  {d:9d} {fmtSecs(w):>10} {fmtSecs(m):>10} {fmtSecs(t):>9}   "
              f"{'REFUTED' if bad else 'possible'}")


# =============================================================================
# SECTION 5 - GENDER AND THE DIFFICULTY CELL
# =============================================================================

def reportGender(engine_rows):
    """Gender as the ENGINE resolves it -- the LATERAL, not a composite join.
    A NULL here is a row that will land in a *_unknown_gender pool."""
    counts = Counter(r["gender"] or "(unresolved)" for r in engine_rows)
    print("  gender (engine LATERAL):",
          ", ".join(f"{g}={n}" for g, n in counts.most_common()))
    if len(counts.keys() & {"M", "F"}) > 1:
        print("  ==> TWO GENDERS in one div_id -- needs a split, not an override")
    return counts


def reportDifficulty(cur, groups, names):
    """Look the fitted cell up on its REAL key: (canonical_id, distance_m).

    course_name is the namespaced DISPLAY name and two rows may legitimately
    share it now, so it is used only as a fallback when there is no
    canonical_id.
    """
    for (display, distance), rows in groups.items():
        if display is None:
            print("  venue NULL (placeholder or missing) -> votes on no course")
            continue
        found = fetch(cur, """
            SELECT course_name, canonical_id, distance_m,
                   difficulty, n_results, n_athletes
            FROM course_difficulties
            WHERE course_name = %s
              AND distance_m IS NOT DISTINCT FROM %s
        """, (display, distance))
        if not found:
            print(f"  {display} d={distance}: NO fitted cell")
            continue
        for name, cid, dm, delta, n_res, n_ath in found:
            flag = ""
            # |delta| > 0.23 is the unidentified-cell threshold from
            # linkage_check; thin cells are the ones shrinkByLinkage nulls.
            if delta is not None and abs(delta) > 0.23:
                flag = "  <- |delta|>0.23: UNIDENTIFIED-CELL signature"
            print(f"  {name} cid={cid} d={dm}: delta={delta} "
                  f"n_results={n_res} n_athletes={n_ath}{flag}")


# =============================================================================
# SECTION 6 - ORCHESTRATION
# main() only sequences; every real step is a helper above.
# =============================================================================

def runOne(cur, meet_id, div_id, names, label="SUSPECT"):
    banner(f"{label} - meet {meet_id} div {div_id}")

    engine_rows = loadEngineRows(cur, meet_id, div_id)
    raw_rows = loadRawRows(cur, meet_id, div_id)

    banner("1. ENGINE VISIBILITY")
    reportVisibility(engine_rows, raw_rows)

    if not engine_rows:
        print("\nThe engine loads nothing from this division. Stop here.")
        return {}

    banner("2. VENUE KEY, DECODED (engine's own distance)")
    groups = decodeVenues(engine_rows, names)

    banner("3. APPLIED FACTOR (backfill's distance, inferred)")
    factors = reportAppliedFactor(engine_rows, raw_rows, groups, label)

    banner("4. PHYSICS - raw times only")
    reportPhysics(raw_rows, [d for (_, d) in groups])

    banner("5. GENDER")
    reportGender(engine_rows)

    banner("6. DIFFICULTY CELL")
    reportDifficulty(cur, groups, names)

    return factors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--control-meet", type=int)
    ap.add_argument("--control-div", type=int)
    args = ap.parse_args()

    names = loadCanonicalNames()          # own connection, established helper

    with getConn() as conn:
        with conn.cursor() as cur:
            runOne(cur, args.meet, args.div, names)
            if args.control_meet and args.control_div:
                runOne(cur, args.control_meet, args.control_div, names,
                       label="CONTROL")
    # No conn.commit(). Nothing here writes.


if __name__ == "__main__":
    main()