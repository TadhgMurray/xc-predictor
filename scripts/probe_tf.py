# Project: xc-predictor
# File:    scripts/probe_tf.py
# Purpose: READ-ONLY. Find TF's distance evidence, because adjudicate has none.
#
# THE PROBLEM
#   adjudicate_regressions on TF: "stored metadata found for 1/500", verdicts
#   {'WORKLIST': 500}. Not one division could be adjudicated. Its two evidence
#   sources are:
#       _storedAnet  -> meets WHERE div_id = ANY(...)
#       _storedTfrrs -> meets_tfrrs WHERE meet_id = ANY(...)
#   Neither holds TF. And the key shapes differ: XC anet divisions look like
#   div=726431 (a global id), while TF's look like div=7, div=4, div=16 -- a
#   PER-MEET event number. meets.div_id cannot match that.
#
#   The distance is reportedly parsed from event_short. If so, TF has no
#   metadata TABLE at all -- the evidence is a STRING ON THE ROW, and the whole
#   AGREE/STORED design (which compares an independent source to the label)
#   does not port. A "5000" that reads -20% would mean the event name is wrong,
#   or the parse is wrong, or the field genuinely ran slow. Different problem.
#
# WHAT THIS PRINTS
#   1. every meet/event/tf table, so nothing is guessed at
#   2. results_tf's columns
#   3. sample rows from the WORST flagged divisions: event_short + distance
#   4. whether event_short's implied distance matches the stored distance
#
# USAGE (~20 sec)
#   python scripts\probe_tf.py

import os
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool


# ================================================================== #
# CHUNK 1 -- SCHEMA
# ================================================================== #

def _tables(cur):
    """Purpose : every table that could plausibly hold TF metadata."""
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND (table_name LIKE '%meet%' OR table_name LIKE '%event%'
               OR table_name LIKE '%tf%' OR table_name LIKE '%division%')
        ORDER BY table_name
    """)
    return [r[0] for r in cur.fetchall()]


def _columns(cur, table):
    """Purpose : one table's columns, so the evidence source can be named
    instead of guessed."""
    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    return cur.fetchall()


# ================================================================== #
# CHUNK 2 -- WHAT THE FLAGGED TF DIVISIONS ACTUALLY LOOK LIKE
# ================================================================== #

def _flaggedPairs(path, limit=12):
    """Purpose : the worst |z| divisions from the z-cut, header-driven."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        head = None
        rows = []
        for line in f:
            if line.startswith("#"):
                cols = line.lstrip("#").strip().split("\t")
                head = {n.strip(): i for i, n in enumerate(cols)}
                continue
            if not line.strip() or head is None:
                continue
            p = line.rstrip("\n").split("\t")
            try:
                rows.append((abs(float(p[head["z"]])),
                             int(p[head["meet_id"]]), int(p[head["div_id"]])))
            except (ValueError, KeyError, IndexError):
                continue
    rows.sort(reverse=True)
    return [(m, d) for _z, m, d in rows[:limit]]


def _sampleDivision(cur, meet, div, cols):
    """
    Purpose : a few real rows from one flagged division, showing whatever
              distance-bearing columns exist.
    Arguments: cols -- column names confirmed to exist on results_tf.
    """
    want = [c for c in ("event_short", "event", "distance", "time_seconds",
                        "normalized_time", "speed_rating") if c in cols]
    sel = ", ".join(want)
    cur.execute(f"""
        SELECT {sel}
        FROM results_tf
        WHERE meet_id = %s AND div_id = %s
        LIMIT 3
    """, (meet, div))
    return want, cur.fetchall()


def _eventCensus(cur, cols):
    """
    Purpose : the distinct event_short values and their stored distances. If
              one event_short maps to MANY distances, the parse is the problem.
              If it maps to one, the label is the problem.
    """
    if "event_short" not in cols:
        return None
    dist_col = "distance" if "distance" in cols else None
    if not dist_col:
        cur.execute("""
            SELECT event_short, count(*) AS n
            FROM results_tf
            GROUP BY event_short ORDER BY n DESC LIMIT 25
        """)
        return [(e, n, None, None) for e, n in cur.fetchall()]
    cur.execute("""
        SELECT event_short, count(*) AS n,
               min(distance), max(distance)
        FROM results_tf
        GROUP BY event_short
        ORDER BY n DESC
        LIMIT 25
    """)
    return cur.fetchall()


# ================================================================== #
# CHUNK 3 -- ORCHESTRATION
# ================================================================== #

def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:

        print("=" * 66)
        print("  TABLES that could hold TF metadata")
        print("=" * 66)
        for t in _tables(cur):
            print(f"    {t}")

        print("\n" + "=" * 66)
        print("  results_tf COLUMNS")
        print("=" * 66)
        cols_info = _columns(cur, "results_tf")
        cols = {c for c, _t in cols_info}
        for c, t in cols_info:
            mark = "  <-- distance evidence?" if "event" in c or "dist" in c else ""
            print(f"    {c:<26} {t}{mark}")

        print("\n" + "=" * 66)
        print("  event_short -> distance  (does one name mean one distance?)")
        print("=" * 66)
        census = _eventCensus(cur, cols)
        if census is None:
            print("    NO event_short COLUMN on results_tf.")
        else:
            print(f"    {'event_short':<20} {'rows':>10} {'min_dist':>10} "
                  f"{'max_dist':>10}  consistent?")
            for row in census:
                e, n, lo, hi = row
                if lo is None:
                    print(f"    {str(e):<20} {n:>10,}")
                else:
                    ok = "yes" if lo == hi else "NO -- parse is ambiguous"
                    print(f"    {str(e):<20} {n:>10,} {lo:>10} {hi:>10}  {ok}")

        print("\n" + "=" * 66)
        print("  WORST FLAGGED TF DIVISIONS -- what do the rows say?")
        print("=" * 66)
        pairs = _flaggedPairs(os.path.join("scripts", "scored_div_tf.tsv"))
        if not pairs:
            print("    scored_div_tf.tsv not found or unreadable")
        for meet, div in pairs:
            want, rows = _sampleDivision(cur, meet, div, cols)
            print(f"\n    meet={meet} div={div}")
            if not rows:
                print("      (no rows)")
                continue
            print("      " + " | ".join(f"{w}" for w in want))
            for r in rows:
                print("      " + " | ".join(str(v) for v in r))

        conn.rollback()                   # read-only, always

    print("\n" + "=" * 66)
    print("  READ: if event_short maps 1:1 to distance and the distance is\n"
          "  already on the row, then TF has NO independent evidence source --\n"
          "  the label IS the parse. adjudicate's AGREE/STORED design compares\n"
          "  an independent source to the label and cannot work here. TF needs\n"
          "  a different adjudicator, not a port of the XC one.")
    print("=" * 66)


if __name__ == "__main__":
    main()