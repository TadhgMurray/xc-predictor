# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/census_ballooned_distance.py
# Purpose: SIZE the "unit monster" distance corruption at its SOURCE.
#
#   THE FINDING THIS CHASES
#   -----------------------
#   The XC normalize backfill produced normalized_time values near ZERO
#   (norm=0.19s) on rows whose event distance read like dist=4828032.0m /
#   8046720.0m. Those are not real races: they are a canonical XC distance
#   (3000 / 5000 / ...) that has been multiplied by the mile factor 1609.34
#   exactly once, as if a metre value had been mistaken for a mile value.
#     4828032 / 1609.34 = 3000.0    (ms 3000)
#     8046720 / 1609.34 = 5000.0    (hs 5000)
#   So the balloon is DETERMINISTIC and REVERSIBLE: divide by 1609.34 and the
#   quotient lands on a clean canonical. This census MEASURES the damage
#   (it writes nothing) so we can decide how to repair it with numbers on the
#   table instead of guesses.
#
#   WHERE THE CORRUPTION LIVES (the hierarchy — read this before the code)
#   ---------------------------------------------------------------------
#   For XC the backfill resolves distance as:  meets.distance  keyed by div_id.
#   (results has NO distance column; the distance is a DIVISION attribute.)
#   The identity spine is therefore:
#
#         meets (div_id PK, distance)          <-- ONE row per division
#            |   1
#            |
#            |   N
#         results (div_id FK, result_id PK)    <-- MANY finishers per division
#
#   Consequence: a single poisoned meets.distance balloons EVERY result under
#   that division. That is why the sanity panel showed long runs of adjacent
#   result_ids sharing one distance. So the natural unit of the damage is the
#   DIVISION (how many meets rows are corrupt), and the natural unit of the
#   IMPACT is the RESULT (how many finisher rows inherit the corruption). This
#   census reports BOTH, in that order: divisions first (the cause), results
#   second (the blast radius).
#
#   SCOPE: XC / anet only, by construction. The `results.source` domain is
#   {anet} (no tfrrs XC rows have landed yet), and `meets` is anet-only, so
#   there is no tfrrs distance to resolve here. tfrrs XC is blocked upstream on
#   the routing fix, NOT on this script.
#
#   WHAT THIS DOES *NOT* TOUCH
#   --------------------------
#   The SLOW tail (raw=54113s on a 1609m mile) is a different corruption class:
#   the DISTANCE is fine, the RAW TIME is absurd. There is no clean inverse for
#   a garbage time, so this census deliberately ignores that class. It sizes
#   ONLY the reversible balloon.
#
#   OUTPUT: a printed report. With --emit-fix it ALSO writes a CSV preview of
#   the proposed (div_id, old_distance, recovered_distance) corrections — still
#   no database write; the CSV is something you inspect before any UPDATE.
#
#   RUN:
#       python scripts/census_ballooned_distance.py
#       python scripts/census_ballooned_distance.py --emit-fix meets_fix.csv

import sys
import csv
import argparse

# scripts/ holds database.py; add it so the import resolves no matter where
# python is launched from (same convention as the backfill).
sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# THE CANONICAL TEST  —  the whole idea lives in one tiny helper.
#   A "unit monster" is: (a canonical XC distance) * 1609.34.
#   To detect it we DIVIDE by 1609.34 and ask whether the quotient
#   lands on a canonical. If it does, that quotient IS the true value.
# ================================================================== #

# The standard XC race distances in metres. Nothing between these is a real
# championship/regular distance, so a quotient landing on one is strong evidence
# the balloon hypothesis is right (not a coincidence of arithmetic).
_CANONICAL_XC = (3000.0, 3200.0, 4000.0, 5000.0, 6000.0, 8000.0, 10000.0)

# The mile factor the corruption multiplied by. This exact constant is what
# metersFromDistance uses for the legitimate miles->metres conversion; the
# corruption applied it to values that were ALREADY metres.
_MILE = 1609.34

# Relative tolerance for "did the quotient hit a canonical". RELATIVE (a fraction
# of the canonical), not absolute, because the raw data rounds: you saw
# dist=5149900 where 3200*1609.34 = 5149888, a ~12m miss on 3200 — 0.0002 in
# relative terms, comfortably inside 1%.
_REL_TOL = 0.01


def _unBalloon(distance):
    """
    Purpose : test ONE stored distance for the *1609.34 balloon and, if present,
              return the recovered TRUE canonical metres.
    Argument: distance — a raw meets.distance value (metres; may be None/garbage).
    Output  : the recovered canonical distance (float) when `distance` is a
              ballooned canonical; otherwise None (meaning: not this corruption).

    Syntax / mechanics, line by line:
      - `distance is None or distance <= 0` : refuse missing/nonsense inputs up
        front so the division below is always safe and meaningful.
      - `q = distance / _MILE` : undo the erroneous mile multiply. If the value
        really was canonical*1609.34, q is now ~that canonical.
      - `abs(q - c) / c` : RELATIVE error between the quotient and a candidate
        canonical c — "how far off is q, as a fraction of c".
      - `next((c for c in _CANONICAL_XC if ...), None)` : a generator scans the
        canonicals and yields the ones q matches; next(..., None) takes the FIRST
        match, or returns None if the generator is empty (no canonical matched).
        This is the idiomatic "find first item satisfying a test, else nothing".
    """
    if distance is None or distance <= 0:
        return None
    q = distance / _MILE                                  # undo the *1609.34
    return next((c for c in _CANONICAL_XC                 # first canonical q hits
                 if abs(q - c) / c <= _REL_TOL), None)


# ================================================================== #
# LOADING THE DIVISIONS  —  read every (div_id, distance) once.
#   meets is anet-only and small relative to results, so a single
#   full read into memory is cheap and lets us classify in Python
#   with the one shared helper (no SQL re-implementation of the test).
# ================================================================== #

def _loadDivisions(cur):
    """
    Purpose : pull every division's stored distance so we can classify each one
              with _unBalloon (the SINGLE source of truth for the test).
    Argument: cur — an open cursor on the read connection.
    Output  : a list of (div_id, distance) tuples straight from meets.

    WHY a list and not a dict: we iterate once and never look up by key here;
    a plain list keeps the intent obvious and the memory flat.
    """
    cur.execute("SELECT div_id, distance FROM meets")
    return cur.fetchall()


def _classifyDivisions(divisions):
    """
    Purpose : split the divisions into the ballooned ones and everything else,
              recording the recovered distance alongside each corrupt div.
    Argument: divisions — the (div_id, distance) list from _loadDivisions.
    Output  : a list of (div_id, old_distance, recovered_distance) for the
              corrupt divisions ONLY. Clean divisions are simply not included.

    Mechanics:
      - We call _unBalloon(distance) once per division. A non-None return means
        "corrupt, and here is the truth"; None means "leave it alone".
      - We keep old_distance too, because the fix preview and the report both
        want the before/after pair, and re-deriving `old` later would be silly.
    """
    corrupt = []
    for div_id, distance in divisions:
        recovered = _unBalloon(distance)
        if recovered is not None:                         # ballooned canonical
            corrupt.append((div_id, distance, recovered))
    return corrupt


# ================================================================== #
# WEIGHTING BY BLAST RADIUS  —  how many RESULT rows inherit each
#   corrupt division. divisions are the cause; results are the impact.
#   We ask Postgres to count, grouped by div_id, only for the corrupt
#   set — cheap because div_id is indexed on results.
# ================================================================== #

def _countAffectedResults(cur, corrupt_div_ids):
    """
    Purpose : for the corrupt divisions, count how many result rows sit under
              each — the true size of the damage in the results table.
    Arguments: cur — read cursor; corrupt_div_ids — a list/collection of the
               poisoned div_ids (the first field of each _classifyDivisions row).
    Output  : a dict { div_id : result_row_count } (missing div => 0 results).

    Mechanics / syntax:
      - `WHERE div_id = ANY(%s)` : psycopg2 adapts a Python list to a Postgres
        array, and `= ANY(array)` is the set-membership test. This is one
        indexed query, NOT a Python loop of N queries.
      - `GROUP BY div_id` with `COUNT(*)` : one count per division in a single
        pass. idx_results_meet_id doesn't help here, but div_id membership +
        group is still a bounded, targeted scan over only the matching rows.
      - We build the dict from the rows; any corrupt div with zero results just
        never appears as a key, so callers use dict.get(div_id, 0).
    """
    if not corrupt_div_ids:                               # nothing to count
        return {}
    cur.execute("""
        SELECT div_id, COUNT(*)
        FROM results
        WHERE div_id = ANY(%s)
        GROUP BY div_id
    """, (list(corrupt_div_ids),))
    return {div_id: n for div_id, n in cur}


# ================================================================== #
# SUMMARISING  —  turn the corrupt list + counts into the numbers a
#   human reads: how many divisions, how many results, and the mix
#   broken down by which canonical each corrupt div really was.
#   Small pure helpers so each computes exactly one thing.
# ================================================================== #

def _tallyByRecovered(corrupt, results_by_div):
    """
    Purpose : group the damage by the RECOVERED canonical, so we can see e.g.
              "the 5000m divisions are the bulk of it".
    Arguments: corrupt — (div_id, old, recovered) list; results_by_div — the
               {div_id: count} dict from _countAffectedResults.
    Output  : a dict { recovered_distance : (n_divisions, n_results) }.

    Mechanics:
      - For each corrupt division we add 1 to that canonical's division tally and
        add its result count to that canonical's result tally.
      - `bucket.get(key, (0, 0))` seeds a fresh (divisions, results) pair the
        first time we see a canonical; tuple-unpack, add, store back.
    """
    bucket = {}
    for div_id, _old, recovered in corrupt:
        divs, rows = bucket.get(recovered, (0, 0))
        bucket[recovered] = (divs + 1, rows + results_by_div.get(div_id, 0))
    return bucket


def _totalResults(corrupt, results_by_div):
    """
    Purpose : total result rows across ALL corrupt divisions (the headline blast
              radius). Argument shapes as above; Output: an int.
    Mechanics: sum the per-div counts (default 0 for a div with no results).
    """
    return sum(results_by_div.get(div_id, 0) for div_id, _o, _r in corrupt)


# ================================================================== #
# REPORTING  —  print the census. Kept as its own printers so main()
#   is a thin orchestrator and each print has one job.
# ================================================================== #

def _printHeadline(n_divisions_total, corrupt, total_results):
    """
    Purpose : the top-line summary — how much of meets is corrupt and how many
              results inherit it.
    Arguments: n_divisions_total — every division scanned; corrupt — the corrupt
               list; total_results — the summed blast radius.
    Output  : None (prints).
    """
    n_corrupt = len(corrupt)
    denom = n_divisions_total if n_divisions_total else 1     # divide-by-zero guard
    print("=" * 70)
    print("BALLOONED DISTANCE CENSUS  (meets.distance == canonical * 1609.34)")
    print("=" * 70)
    print(f"divisions scanned      : {n_divisions_total:>12,}")
    print(f"divisions ballooned    : {n_corrupt:>12,}  "
          f"({100.0 * n_corrupt / denom:.3f}% of divisions)")
    print(f"result rows affected   : {total_results:>12,}  "
          f"(the blast radius in results)")


def _printByRecovered(bucket):
    """
    Purpose : the breakdown by which canonical each corrupt div really was —
              this is the proof the balloon hypothesis holds (every corrupt row
              maps to a CLEAN canonical, nothing lands 'between' distances).
    Argument: bucket — {recovered: (n_divisions, n_results)} from _tallyByRecovered.
    Output  : None (prints).

    Mechanics: sort by recovered distance so the table reads small->large; print
    aligned columns.
    """
    print("-" * 70)
    print("by recovered distance (true value after / 1609.34):")
    print(f"  {'canonical':>10}  {'divisions':>10}  {'results':>12}")
    for recovered in sorted(bucket):
        divs, rows = bucket[recovered]
        print(f"  {recovered:>10.0f}  {divs:>10,}  {rows:>12,}")


def _printSamples(corrupt, results_by_div, n=15):
    """
    Purpose : show a few concrete (div_id, old -> recovered, #results) rows so a
              human can eyeball that the reversal is sane before trusting it.
    Arguments: corrupt list; results_by_div counts; n — how many samples.
    Output  : None (prints).

    Mechanics: sort the corrupt divisions by blast radius DESCENDING (most
    impactful first) via sorted(..., key=..., reverse=True), then slice [:n].
    """
    print("-" * 70)
    print(f"top {n} corrupt divisions by blast radius:")
    ordered = sorted(corrupt,
                     key=lambda t: results_by_div.get(t[0], 0),
                     reverse=True)
    for div_id, old, recovered in ordered[:n]:
        rows = results_by_div.get(div_id, 0)
        print(f"  div={div_id:<12}  {old:>14.1f}m  ->  {recovered:>7.0f}m  "
              f"({rows:,} results)")


# ================================================================== #
# OPTIONAL FIX PREVIEW  —  write a CSV of the proposed corrections.
#   STILL NO DB WRITE. This is the artifact you inspect (and could later
#   feed to an UPDATE) — separated so the census stays read-only and the
#   repair is a deliberate, reviewable second step.
# ================================================================== #

def _emitFixCsv(path, corrupt, results_by_div):
    """
    Purpose : write (div_id, old_distance, recovered_distance, n_results) rows to
              a CSV so the proposed meets.distance repair can be reviewed before
              any UPDATE is run.
    Arguments: path — output CSV path; corrupt — the corrupt list; results_by_div
               — the per-div result counts (for context in the file).
    Output  : None (writes the file, prints a confirmation).

    Mechanics / syntax:
      - `newline=""` on the file open is the csv module's required idiom on all
        platforms (it manages its own line endings; without this you get blank
        lines on Windows).
      - csv.writer + writerow(header) then a row per correction.
    """
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["div_id", "old_distance", "recovered_distance", "n_results"])
        for div_id, old, recovered in corrupt:
            w.writerow([div_id, f"{old:.1f}", f"{recovered:.0f}",
                        results_by_div.get(div_id, 0)])
    print("-" * 70)
    print(f"fix preview written: {path}  ({len(corrupt):,} proposed corrections)")
    print("  (NO database change made — inspect this file before any UPDATE.)")


# ================================================================== #
# ORCHESTRATION  —  one small runner ties the pieces together in the
#   order the hierarchy implies: divisions (cause) -> results (impact)
#   -> summarise -> report -> optional fix preview.
# ================================================================== #

def _runCensus(conn, fix_path):
    """
    Purpose : execute the whole census on one connection and print the report.
    Arguments: conn — a read connection; fix_path — CSV path or None (no preview).
    Output  : None (prints; optionally writes the CSV).

    The body reads as the pipeline: load divisions, classify them, count the
    results they poison, tally the mix, then print. Every step is one helper
    call so this function stays short and the sequence is legible.
    """
    with conn.cursor() as cur:
        divisions = _loadDivisions(cur)                   # (div_id, distance) all
        corrupt = _classifyDivisions(divisions)           # ballooned ones + truth
        corrupt_ids = [div_id for div_id, _o, _r in corrupt]
        results_by_div = _countAffectedResults(cur, corrupt_ids)

    total_results = _totalResults(corrupt, results_by_div)
    bucket = _tallyByRecovered(corrupt, results_by_div)

    _printHeadline(len(divisions), corrupt, total_results)
    _printByRecovered(bucket)
    _printSamples(corrupt, results_by_div)

    if fix_path:                                          # opt-in preview only
        _emitFixCsv(fix_path, corrupt, results_by_div)


# ================================================================== #
# MAIN
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Census (read-only) of *1609.34 ballooned meets.distance (XC).")
    ap.add_argument("--emit-fix", metavar="CSV", default=None,
                    help="also write a CSV preview of the proposed corrections "
                         "(still writes NOTHING to the database).")
    args = ap.parse_args()

    initPool()
    conn = getConn()
    # getConn() may return a raw connection or a context manager; normalise to a
    # connection object the same way the backfill does, without assuming which.
    if not hasattr(conn, "cursor") and hasattr(conn, "__enter__"):
        conn = conn.__enter__()

    _runCensus(conn, args.emit_fix)


if __name__ == "__main__":
    main()