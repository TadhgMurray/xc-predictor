#!/usr/bin/env python3
# =============================================================================
# census_tfrrs_geometry.py
# -----------------------------------------------------------------------------
# READ-ONLY. Answers ONE question with numbers: does tfrrs TF geometry actually
# EXIST and DO anything, or does every tfrrs TF row silently fall through to the
# no-op _emptyGeometry()? The geometry resolver READS tfrrs_meet_geometry
# (sport='TF'); if that table is empty or all-NULL, geometry "works" while
# correcting nothing — a silent no-op we want to catch, not assume away.
#
# THE HIERARCHY (three checks, weakest -> strongest):
#   CHECK 1  existence : does the table exist, and how many rows?
#   CHECK 2  coverage  : of tfrrs TF meets that HAVE result rows, how many have
#                        a geometry stamp at all?
#   CHECK 3  usefulness: of the stamps that exist, how many carry a REAL value
#                        (track_type / track_length / is_indoor not all NULL)?
#   A table can pass 1 and 2 yet fail 3 (rows present but all-NULL = useless).
#   CHECK 3 is the real verdict: "is geometry doing anything for tfrrs TF."
#
# THE INVARIANT (why every query pins source/sport):
#   meet_id alone is NEVER a key — identity is (meet_id, sport, source). tfrrs
#   geometry lives at (meet_id, sport='TF'); tfrrs TF results live in results_tf
#   WHERE source='tfrrs'. Every query below carries those scopes explicitly.
#
# USAGE:  python scripts/census_tfrrs_geometry.py
# =============================================================================

from database import getConn   # one-line knob if the helper lives elsewhere


# =============================================================================
# CONSTANTS — the load-bearing scopes, named once so no query drifts.
# =============================================================================

SOURCE_TFRRS = "tfrrs"   # results_tf.source for tfrrs rows (lowercase)
SPORT_TF     = "TF"      # tfrrs_meet_geometry.sport partition (UPPERCASE)
GEOM_TABLE   = "tfrrs_meet_geometry"   # the stamp table the resolver reads

# The geometry fields that count as "real geometry" if ANY is non-NULL. Mirrors
# the resolver's GEOMETRY_FIELDS (minus location_id, which is a venue pointer,
# not a track-shape fact — a row with only location_id still corrects nothing).
GEOM_VALUE_FIELDS = ("track_type", "track_length", "is_indoor")


# =============================================================================
# SECTION 1 — TINY DB HELPERS  (every query funnels through these two, so the
# check functions below stay short and read like plain questions)
# =============================================================================

def _scalar(conn, sql, params=None):
    """
    Purpose : run a SELECT returning ONE value; hand it back.
    Args    : conn   - open psycopg2 connection.
              sql    - a query whose first row/first column is the answer.
              params - optional tuple bound to %s placeholders (never f-string
                       values into SQL).
    Output  : the single cell, or None if no row came back.
    """
    with conn.cursor() as cur:            # `with` auto-closes the cursor
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def _pct(part, whole):
    """
    Purpose : format 'part of whole' as a percent, division-safe.
    Output  : e.g. '42.9%', or 'n/a' when whole is 0 (empty table = no crash).
    """
    return "n/a" if not whole else f"{100.0 * part / whole:.1f}%"


# =============================================================================
# SECTION 2 — CHECK 1: EXISTENCE  (is the table there, and does it have rows?)
# =============================================================================

def _tableExists(conn):
    """
    Purpose : does tfrrs_meet_geometry exist at all?
    How     : information_schema.tables is the catalog of tables; a hit means it
              exists. We ask by name (a literal constant, not user input).
    Output  : True if the table exists, else False.
    """
    got = _scalar(
        conn,
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (GEOM_TABLE,),
    )
    return got is not None


def _stampRowCount(conn):
    """
    Purpose : how many TF geometry stamps exist (rows scoped to sport='TF').
    Output  : an int count (0 if the partition is empty).
    """
    return _scalar(
        conn,
        f"SELECT COUNT(*) FROM {GEOM_TABLE} WHERE sport = %s",
        (SPORT_TF,),
    )


def checkExistence(conn):
    """
    CHECK 1 — existence. Prints the verdict; returns True if we can proceed
    (table exists). If it doesn't exist, later checks can't run.
    """
    print("CHECK 1 — existence")
    if not _tableExists(conn):
        print(f"  {GEOM_TABLE} does NOT exist. Geometry propagation was never "
              f"run -> every tfrrs TF row gets the no-op empty geometry.")
        return False
    rows = _stampRowCount(conn)
    print(f"  {GEOM_TABLE} exists; {rows:,} rows at sport='{SPORT_TF}'.")
    if rows == 0:
        print("  Table is EMPTY -> geometry is a silent no-op for tfrrs TF.")
    return True


# =============================================================================
# SECTION 3 — CHECK 2: COVERAGE  (do meets-with-results have a stamp?)
# -----------------------------------------------------------------------------
# Two helpers: one counts the meets that SHOULD have geometry (they have tfrrs TF
# result rows), one counts how many of those actually have a stamp. Split so each
# does one job.
# =============================================================================

def _meetsWithResults(conn):
    """
    Purpose : count DISTINCT tfrrs TF meets that have result rows — the set that
              SHOULD have a geometry stamp to be useful.
    Output  : an int count.
    """
    return _scalar(
        conn,
        "SELECT COUNT(DISTINCT meet_id) FROM results_tf WHERE source = %s",
        (SOURCE_TFRRS,),
    )


def _meetsWithResultsAndStamp(conn):
    """
    Purpose : of those meets-with-results, how many have a TF geometry stamp.
    How     : the set of tfrrs TF result-meets, INNER-joined to the stamp table
              on meet_id AND sport='TF' (sport in the JOIN keeps the scope tight).
    Output  : an int count.
    """
    return _scalar(
        conn,
        f"""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT meet_id FROM results_tf WHERE source = %s
        ) rm
        JOIN {GEOM_TABLE} g
          ON g.meet_id = rm.meet_id AND g.sport = %s
        """,
        (SOURCE_TFRRS, SPORT_TF),
    )


def checkCoverage(conn):
    """
    CHECK 2 — coverage. What fraction of tfrrs TF meets-with-results have a stamp.
    Output  : (with_stamp, total) so the verdict line can reuse the numbers.
    """
    print("\nCHECK 2 — coverage")
    total = _meetsWithResults(conn) or 0
    with_stamp = _meetsWithResultsAndStamp(conn) or 0
    print(f"  tfrrs TF meets with results        : {total:,}")
    print(f"  ...of those, with a geometry stamp : {with_stamp:,} "
          f"({_pct(with_stamp, total)})")
    return with_stamp, total


# =============================================================================
# SECTION 4 — CHECK 3: USEFULNESS  (are the stamps REAL or all-NULL?)
# -----------------------------------------------------------------------------
# A stamp row that exists but has every geometry field NULL corrects nothing.
# This is the real verdict.
# =============================================================================

def _nonNullClause():
    """
    Purpose : build the 'ANY geometry field is non-NULL' SQL condition from the
              GEOM_VALUE_FIELDS list, so the field set lives in ONE place.
    Output  : a SQL string like
              "(track_type IS NOT NULL OR track_length IS NOT NULL OR ...)".
    """
    # " OR ".join stitches one "col IS NOT NULL" per field into the clause.
    return "(" + " OR ".join(f"{f} IS NOT NULL" for f in GEOM_VALUE_FIELDS) + ")"


def _realStampCount(conn):
    """
    Purpose : of the TF stamps, how many carry a REAL geometry value (any of the
              track-shape fields non-NULL).
    Output  : an int count.
    """
    return _scalar(
        conn,
        f"SELECT COUNT(*) FROM {GEOM_TABLE} "
        f"WHERE sport = %s AND {_nonNullClause()}",
        (SPORT_TF,),
    )


def checkUsefulness(conn):
    """
    CHECK 3 — usefulness (the real verdict). Of all TF stamps, how many aren't
    all-NULL. Output: (real, total_stamps).
    """
    print("\nCHECK 3 — usefulness (the verdict)")
    total_stamps = _stampRowCount(conn) or 0
    real = _realStampCount(conn) or 0
    print(f"  TF stamps total                 : {total_stamps:,}")
    print(f"  ...with a real (non-NULL) value : {real:,} "
          f"({_pct(real, total_stamps)})")
    return real, total_stamps


# =============================================================================
# SECTION 5 — ORCHESTRATION  (sequences the checks + one overall line)
# =============================================================================

def _finalLine(with_stamp, real):
    """
    Purpose : one plain-English verdict from the two numbers that matter —
              coverage (meets that have a stamp) and usefulness (stamps that
              aren't all-NULL).
    """
    print("\n" + "=" * 60)
    if with_stamp == 0 or real == 0:
        print("VERDICT: geometry is effectively a NO-OP for tfrrs TF right now "
              "(no useful stamps). Propagation likely not run / not landed.")
    else:
        print("VERDICT: tfrrs TF geometry is populated and carrying real values "
              "for at least some meets. Coverage % above is how much.")


def main():
    """Wire connection -> the three checks -> the verdict. Read-only; no commit."""
    with getConn() as conn:               # closes even if a check raises
        if not checkExistence(conn):      # CHECK 1: if no table, stop here
            print("\n" + "=" * 60)
            print("VERDICT: no geometry table -> total no-op for tfrrs TF.")
            return
        with_stamp, _ = checkCoverage(conn)     # CHECK 2
        real, _ = checkUsefulness(conn)         # CHECK 3
        _finalLine(with_stamp, real)


if __name__ == "__main__":
    main()