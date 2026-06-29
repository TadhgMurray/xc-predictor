# Project: xc-predictor
# File:    backfill/locate_track_geometry.py
# Purpose: READ-ONLY. Before building the matched-pair geometry diagnostic, find
#          out WHERE track geometry actually lives and how populated it is. We
#          now have multiple meet-metadata tables (meets_tf, meets_tf_meta for
#          anet, meets_tfrrs for tfrrs) and the anet crawl is mid-flight, so we
#          don't assume which carries track_type/track_length/is_indoor or how
#          full it is. This probe reports, per candidate table:
#            - whether the geometry columns EXIST
#            - how many rows have them populated (and % of the table)
#            - the distinct VALUES (Banked/Flat/Oval; 200/400; indoor 0/1)
#            - whether results_tf joins to it by meet_id (the matched-pair query
#              will need this join, so confirm it returns rows)
#          Writes nothing. This decides the join the geometry diagnostic uses.

import sys
sys.path.insert(0, "scripts")

from database import initPool, getConn, closePool

# The tables that might carry track geometry, and the geometry columns we care
# about. We check each table for each column rather than assuming a schema.
_CANDIDATE_TABLES = ["meets_tf", "meets_tf_meta", "meets_tfrrs"]
_GEOMETRY_COLS    = ["track_type", "track_length", "is_indoor"]


# _columnsPresent
# Purpose: For one table, ask the catalog which of the geometry columns actually
#          exist on it. A table might have none (wrong table), some, or all.
# Arguments:
#           conn:  open read-only connection
#           table: table name to inspect
# Output:  set of geometry column names that exist on this table
def _columnsPresent(conn, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM   information_schema.columns
            WHERE  table_name = %s
              AND  column_name = ANY(%s)
        """, (table, _GEOMETRY_COLS))
        return {row[0] for row in cur.fetchall()}


# _tableExists
# Purpose: Confirm a table exists at all before probing it (meets_tfrrs may not
#          exist yet on every box; meets_tf_meta is new). Avoids a hard error on
#          a missing table so the probe reports "absent" cleanly instead.
# Arguments:
#           conn:  open connection
#           table: table name
# Output:  True if the table exists
def _tableExists(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (table,))
        return cur.fetchone()[0] is not None


# _coverage
# Purpose: For one table + one existing geometry column, how many rows are
#          non-NULL, the table's total rows, and the percentage. This is the
#          "is it actually populated" number — a column can exist but be empty
#          (e.g. mid-crawl, or geometry only present on indoor meets).
# Arguments:
#           conn:   open connection
#           table:  table name
#           column: geometry column known to exist on it
# Output:  (non_null_count, total_rows)
def _coverage(conn, table, column):
    with conn.cursor() as cur:
        # Two scalars in one pass: total rows and rows where the column is set.
        cur.execute(f"""
            SELECT COUNT(*) AS total,
                   COUNT({column}) AS non_null
            FROM   {table}
        """)
        total, non_null = cur.fetchone()
    return non_null, total


# _valueSpread
# Purpose: The distinct VALUES a geometry column holds, with counts. This is the
#          sanity check that the data means what we think: track_type should be
#          'Banked'/'Flat'/'Oval'-ish, track_length ~200/400, is_indoor 0/1. If
#          we see junk here, the matched-pair contrasts can't be defined.
# Arguments:
#           conn:   open connection
#           table:  table name
#           column: geometry column
# Output:  list of (value, count) ordered by count desc, capped at 15
def _valueSpread(conn, table, column):
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {column}, COUNT(*)
            FROM   {table}
            WHERE  {column} IS NOT NULL
            GROUP  BY {column}
            ORDER  BY COUNT(*) DESC
            LIMIT  15
        """)
        return cur.fetchall()


# _joinsToResults
# Purpose: Confirm results_tf actually joins to this table by meet_id and that
#          the join surfaces geometry. We count results whose meet has a non-NULL
#          track_type via meet_id. If this is 0, the matched-pair query keyed on
#          this table would return nothing — exactly what we need to know before
#          building it. Scoped to source='anet' since geometry is anet-sourced
#          here (tfrrs geometry, when present, is a separate later contrast).
# Arguments:
#           conn:  open connection
#           table: table that has track_type
# Output:  (results_with_geometry, sample_rows)  — sample is a few joined rows
def _joinsToResults(conn, table):
    with conn.cursor() as cur:
        # Count anet TF results that reach a track_type through meet_id.
        cur.execute(f"""
            SELECT COUNT(*)
            FROM   results_tf r
            JOIN   {table} m ON m.meet_id = r.meet_id
            WHERE  r.source = 'anet'
              AND  m.track_type IS NOT NULL
        """)
        n = cur.fetchone()[0]

        # A few example joined rows so we can eyeball event + geometry together.
        cur.execute(f"""
            SELECT r.meet_id, r.event_short, m.track_type, m.track_length, m.is_indoor
            FROM   results_tf r
            JOIN   {table} m ON m.meet_id = r.meet_id
            WHERE  r.source = 'anet'
              AND  m.track_type IS NOT NULL
            LIMIT  8
        """)
        sample = cur.fetchall()
    return n, sample


# _reportTable
# Purpose: Run the full probe for one candidate table and print its findings.
# Arguments:
#           conn:  open connection
#           table: candidate table name
# Output:  none (prints)
def _reportTable(conn, table):
    print(f"\n========== {table} ==========")

    if not _tableExists(conn, table):
        print("    table does NOT exist")
        return

    present = _columnsPresent(conn, table)
    if not present:
        print("    exists, but has NONE of the geometry columns")
        return

    # Per-column coverage + value spread.
    for col in _GEOMETRY_COLS:
        if col not in present:
            print(f"    {col:13} column absent")
            continue
        non_null, total = _coverage(conn, table, col)
        pct = 100.0 * non_null / total if total else 0.0
        print(f"    {col:13} {non_null:>9,} / {total:<9,} populated ({pct:5.1f}%)")
        for value, count in _valueSpread(conn, table, col):
            print(f"        {str(value):16} {count:,}")

    # If this table has track_type, test the results_tf join — the thing the
    # matched-pair query depends on.
    if "track_type" in present:
        n, sample = _joinsToResults(conn, table)
        print(f"    results_tf(anet) reaching track_type via meet_id: {n:,}")
        for meet_id, event, ttype, tlen, indoor in sample:
            print(f"        meet={meet_id} event={event!r} "
                  f"type={ttype} len={tlen} indoor={indoor}")


# main
def main():
    initPool()
    try:
        with getConn() as conn:
            print("Locating track geometry across candidate meet tables...")
            for table in _CANDIDATE_TABLES:
                _reportTable(conn, table)

            print("\n--- read ---")
            print("    The table with the highest track_type coverage AND a")
            print("    nonzero results_tf join count is where the matched-pair")
            print("    geometry diagnostic should pull geometry from. If coverage")
            print("    is still low, the anet crawl hasn't populated it yet --")
            print("    re-run after the crawl finishes.")
    finally:
        closePool()


if __name__ == "__main__":
    main()