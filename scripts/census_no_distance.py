# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/census_no_distance.py
# Purpose: EXPLAIN the 6,062,226 XC rows (15.4%) the normalize backfill charged
#          to `no_distance`. That single skip reason hides TWO structurally
#          different failures, and this census splits them so the repair choice
#          can be made on numbers.
#
#   THE CONFLATION THIS CENSUS BREAKS
#   ---------------------------------
#   The backfill decides no_distance with:  meet_distances.get(div_id) is None
#   where meet_distances came from  SELECT div_id, distance FROM meets.
#   A Python dict .get() returns None in TWO physically different cases that it
#   cannot tell apart:
#
#         meet_distances.get(div_id) is None
#                        │
#          ┌─────────────┴──────────────┐
#     div_id NOT a key              div_id IS a key,
#     in the dict                   but its value is None
#          │                             │
#     the div_id has NO row         the meets row EXISTS but its
#     in meets at all               distance column is NULL
#          │                             │
#     META GAP                      DATA GAP
#     (scraper never wrote the      (division known, distance simply
#     division; XC analogue of      not recorded — potentially rescuable
#     the tfrrs no_meets_tf_row     from course_id / course_name metadata
#     finding)                      or canon-meet inheritance)
#
#   These have OPPOSITE fixes. A META GAP row is irrecoverable from meets (there
#   is nothing to inherit from). A DATA GAP row may be rescuable. The repair
#   decision hangs entirely on the RATIO between the two buckets, so we measure
#   it before proposing anything.
#
#   HIERARCHY (same one-to-many spine as the balloon work)
#   ------------------------------------------------------
#         meets (div_id PK, distance)     ONE row per division (may be ABSENT)
#            |  1
#            |  N
#         results (div_id FK)             MANY finishers; these are what we count
#
#   We count RESULT rows (the blast radius), grouped by which gap they fall into,
#   and — because the owner asked — also by YEAR, to separate "ancient and gone"
#   (anet-depopulated meets) from "recent gap worth chasing".
#
#   SCOPE: XC / anet only (results.source is {anet}; meets is anet-only). This
#   census does not touch TF.
#
#   READ-ONLY: writes nothing. Re-runnable any time.
#
#   RUN:
#       python scripts/census_no_distance.py

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# THE CORE QUERY  —  reproduce the backfill's no_distance predicate in
#   SQL, but LEFT JOIN meets so we can see the difference the dict hid:
#   whether the meets row is ABSENT vs PRESENT-with-NULL.
# ================================================================== #
#
# WHY A LEFT JOIN: an inner join would DROP the meta-gap rows (no meets row to
# join to) — exactly the rows we most need to count. LEFT JOIN keeps every
# result row and leaves meets columns NULL when there is no match, letting us
# distinguish the two gaps by inspecting m.div_id:
#   - m.div_id IS NULL           -> no meets row matched  -> META GAP
#   - m.div_id IS NOT NULL but
#     m.distance IS NULL         -> row present, no distance -> DATA GAP
#
# We must reproduce the backfill's OTHER skips too (sentinel time, etc.) or we
# would count rows the backfill never even reached the distance check on. The
# backfill charges the FIRST failing reason in order: sentinel_time FIRST, then
# no_distance. So a row only reaches no_distance if its time is a real time.
# We mirror that with the same sentinel test in the WHERE clause.

# The backfill's sentinel rule: time is None or > 100000 (DNF/DNS stored huge).
# We reproduce it so our population matches the backfill's no_distance set.
_SENTINEL_SQL = "r.time_seconds IS NOT NULL AND r.time_seconds <= 100000"


def _gapCensusSQL():
    """
    Purpose : the one SELECT that classifies every no_distance result row into
              META GAP vs DATA GAP and counts them per year.
    Output  : a SQL string (no parameters).

    Structure, explained:
      - FROM results r LEFT JOIN meets m ON r.div_id = m.div_id
          keeps ALL result rows; m.* is NULL where the division is missing.
      - WHERE (sentinel ok) AND (the distance is unusable)
          "unusable" = the SAME condition the backfill's dict.get(None) captured:
          either no meets row (m.div_id IS NULL) OR the row exists with NULL
          distance (m.distance IS NULL). Both collapse to "no distance".
          NOTE: m.distance IS NULL is TRUE in BOTH the absent case (all LEFT JOIN
          columns NULL) and the present-with-NULL case, so `m.distance IS NULL`
          alone captures the whole no_distance population — we then split it by
          m.div_id to name which gap each row is.
      - the CASE expression labels each row's gap.
      - substring(r.date, 1, 4) pulls the YEAR from the TEXT date column without
        parsing (dates are 'YYYY-MM-DD' text; the first 4 chars are the year).
        Garbage/short dates yield a short/empty string, bucketed as 'unknown'.
      - GROUP BY gap, yr gives per-(gap, year) counts in one pass.
    """
    return f"""
        SELECT
            CASE WHEN m.div_id IS NULL THEN 'meta_gap'
                 ELSE 'data_gap'
            END                                   AS gap,
            COALESCE(NULLIF(substring(r.date, 1, 4), ''), 'unknown') AS yr,
            COUNT(*)                              AS n
        FROM results r
        LEFT JOIN meets m ON r.div_id = m.div_id
        WHERE {_SENTINEL_SQL}
          AND m.distance IS NULL
        GROUP BY gap, yr
    """


def _loadGapCensus(cur):
    """
    Purpose : run the census query and return its rows.
    Argument: cur — an open read cursor.
    Output  : a list of (gap, year_str, count) tuples.

    This is a single grouped aggregate over results; the heavy lifting is in
    Postgres. We pull the (small) grouped result set into Python to summarise.
    """
    cur.execute(_gapCensusSQL())
    return cur.fetchall()


# ================================================================== #
# SUMMARISING  —  fold the (gap, year, count) rows into the two views
#   the operator wants: totals per gap, and the year profile per gap.
#   Two tiny pure helpers, each computing exactly one thing.
# ================================================================== #

def _totalsByGap(rows):
    """
    Purpose : total result rows in each gap (meta vs data) — the headline ratio.
    Argument: rows — (gap, year, count) tuples.
    Output  : { gap : total_count }.

    Mechanics: accumulate count into a dict keyed by gap; .get(gap, 0) seeds a
    fresh zero the first time each gap appears.
    """
    totals = {}
    for gap, _yr, n in rows:
        totals[gap] = totals.get(gap, 0) + n
    return totals


def _yearProfile(rows, gap):
    """
    Purpose : the per-year counts for ONE gap, so we can see whether the misses
              are ancient (depopulated meets) or recent (worth chasing).
    Arguments: rows — (gap, year, count) tuples; gap — which gap to profile.
    Output  : a list of (year_str, count) for that gap, sorted by year.

    Mechanics: filter rows to the requested gap, then sort by the year string
    (string sort is fine — 'YYYY' sorts chronologically; 'unknown' sorts last
    because letters sort after digits).
    """
    profile = [(yr, n) for g, yr, n in rows if g == gap]
    return sorted(profile, key=lambda yn: yn[0])


# ================================================================== #
# REPORTING  —  small printers, one job each.
# ================================================================== #

def _printHeadline(totals):
    """
    Purpose : print the two gap totals and their split as percentages — the
              number the repair decision hinges on.
    Argument: totals — { gap : count } from _totalsByGap.
    Output  : None (prints).
    """
    meta = totals.get("meta_gap", 0)
    data = totals.get("data_gap", 0)
    grand = meta + data
    denom = grand if grand else 1                     # divide-by-zero guard
    print("=" * 70)
    print("NO_DISTANCE CENSUS  (why 6M XC result rows have no resolvable distance)")
    print("=" * 70)
    print(f"total no_distance results : {grand:>12,}")
    print(f"  META GAP (div absent from meets)     : {meta:>12,}  "
          f"({100.0 * meta / denom:5.1f}%)  [irrecoverable from meets]")
    print(f"  DATA GAP (div present, distance NULL): {data:>12,}  "
          f"({100.0 * data / denom:5.1f}%)  [rescuable from metadata?]")


def _printYearProfile(title, profile, top=12):
    """
    Purpose : print a gap's year breakdown, biggest years first, so "ancient vs
              recent" is visible at a glance.
    Arguments: title — heading; profile — (year, count) list; top — how many
               years to show (the rest are folded into an 'other' line).
    Output  : None (prints).

    Mechanics:
      - sort by count DESCENDING to surface the heaviest years.
      - slice [:top]; sum the remainder into one 'other (N years)' line so a long
        tail of tiny years doesn't flood the panel.
    """
    print("-" * 70)
    print(f"{title}  (result rows by year, heaviest first):")
    by_count = sorted(profile, key=lambda yn: yn[1], reverse=True)
    for yr, n in by_count[:top]:
        print(f"  {yr:>8}  {n:>12,}")
    rest = by_count[top:]
    if rest:
        rest_n = sum(n for _yr, n in rest)
        print(f"  {'other':>8}  {rest_n:>12,}  ({len(rest)} more years)")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _run(conn):
    """
    Purpose : load the census, summarise, print all three panels.
    Argument: conn — a read connection.
    Output  : None (prints).

    Sequence: one query -> totals (headline ratio) -> year profile per gap. Each
    step is a helper call so this stays a short, legible pipeline.
    """
    with conn.cursor() as cur:
        rows = _loadGapCensus(cur)

    totals = _totalsByGap(rows)
    _printHeadline(totals)
    _printYearProfile("META GAP", _yearProfile(rows, "meta_gap"))
    _printYearProfile("DATA GAP", _yearProfile(rows, "data_gap"))


def main():
    ap = argparse.ArgumentParser(
        description="Census (read-only) of XC no_distance rows: meta vs data gap.")
    ap.parse_args()                                   # no flags; parse for --help

    initPool()
    conn = getConn()
    if not hasattr(conn, "cursor") and hasattr(conn, "__enter__"):
        conn = conn.__enter__()

    _run(conn)


if __name__ == "__main__":
    main()