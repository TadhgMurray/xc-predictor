# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/course_merge.py
# Purpose: collapse XC course_names that are the SAME physical venue.
#
#   THE BUG
#   -------
#   The XC difficulty cell is keyed on course_name (plus distance). One venue
#   under several spellings is several cells, each fitted on a fraction of the
#   evidence:
#
#     XC:Dannehl XC Course, Univ. of Wisconsin Parkside   29,765 rows
#     XC:UW Parkside                                       5,103
#     XC:University of Wisconsin-Parkside                  1,047
#     XC:Mt. Sac                        13 and 25 rows, canonical_id 5573
#     XC:Mt. San Antonio College        170,294 rows,    canonical_id 13433
#
#   Foot Locker Midwest sits in `XC:UW Parkside` at 5,103 rows while the same
#   course under its full name carries 29,765. A championship field lands in
#   the thin cell, its delta is fitted almost entirely on championship races,
#   and the whole field reads fast.
#
#   Same disease as the TF location_id split, one layer over: there the key
#   was a scraper-minted id, here it is free text.
#
#   ★★ THE GUARD THAT MATTERS MOST ★★
#   ---------------------------------
#   `Mt. San Antonio College (rain course)` sits at EXACTLY the same
#   coordinates as `Mt. San Antonio College`. It is a DELIBERATE split, made
#   because the rain course is a different physical loop -- measured at
#   delta -0.0316 against the normal course's +0.0426 at the same distance,
#   a 7.4-point separation on 5,714 rows.
#
#   A naive coordinate merge would collapse it straight back and silently
#   undo that work. Any name matching _PROTECTED is never merged, in either
#   direction. Coordinates prove two names are in the same PLACE; they cannot
#   prove they are the same COURSE.

import os
import sys
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# Coordinate rounding for "the same venue". 3 decimals ~ 110 m of latitude.
#
# ★ TIGHTER THAN THE TF MERGE ON PURPOSE. There the question was "are these
#   two ids the same place" and 50 m was read off a histogram. Here the names
#   are already evidence of sameness, so the coordinate test only has to rule
#   out two genuinely different venues that happen to share a name -- and
#   those are far apart, not 200 m apart.
COORD_DP = 3

# Names that must NEVER be merged with anything, however close.
#
# ⚠ THESE ARE DELIBERATE SPLITS. A regex, not a literal list, because the
#   rain-course suffix is applied per meet and more venues may get one.
_PROTECTED = re.compile(r"\(rain course\)|\(alt\b|\(short\b|\(long\b", re.I)

# A name needs this many rows before it can be the canonical survivor. A
# 13-row spelling should never win over a 170,000-row one.
MIN_CANON_ROWS = 50


def isProtected(name):
    """True if `name` is a deliberate split and must stand alone."""
    return bool(name and _PROTECTED.search(name))


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE NAME TABLE
# ------------------------------------------------------------------ #

def buildNames(cur):
    """
    course_pts: one row per course_name with its coordinates and row weight.

    `rows` counts RESULTS, not meets -- the canonical name should be the one
    carrying the most evidence, because that is the cell whose delta is best
    measured and the one the fewest rows have to move into.
    """
    cur.execute("DROP TABLE IF EXISTS course_pts")
    cur.execute(f"""
        CREATE TABLE course_pts AS
        SELECT m.course_name,
               round(avg(m.gps_lat)::numeric,  {COORD_DP}) AS lat,
               round(avg(m.gps_long)::numeric, {COORD_DP}) AS lon,
               min(m.state)  AS state,
               count(*)      AS rows
        FROM   meets m
        JOIN   results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
        WHERE  m.course_name IS NOT NULL
          AND  btrim(m.course_name) <> ''
          AND  m.gps_lat IS NOT NULL AND m.gps_long IS NOT NULL
        GROUP  BY 1
    """)
    cur.execute("CREATE INDEX ON course_pts (lat, lon)")
    cur.execute("CREATE UNIQUE INDEX ON course_pts (course_name)")
    cur.execute("ANALYZE course_pts")
    cur.execute("SELECT count(*), sum(rows) FROM course_pts")
    n, rows = cur.fetchone()
    print(f"    {n:,} named courses with coordinates, {rows:,} result rows")
    return n


# ------------------------------------------------------------------ #
# CHUNK 2 -- GROUP AND CHOOSE
# ------------------------------------------------------------------ #

def buildGroups(cur):
    """
    course_canon: course_name -> the name it should become.

    Grouping is by exact rounded coordinate, so no clustering or transitive
    closure is needed -- every member of a coordinate group is mutually
    within ~110 m by construction. That is the whole reason for rounding
    rather than doing a distance join.
    """
    cur.execute("DROP TABLE IF EXISTS course_canon")
    cur.execute("""
        CREATE TABLE course_canon AS
        WITH grp AS (
            SELECT lat, lon, count(*) AS n_names, sum(rows) AS grp_rows
            FROM   course_pts
            GROUP  BY lat, lon
            HAVING count(*) > 1
        ),
        ranked AS (
            SELECT p.course_name, p.lat, p.lon, p.rows, p.state,
                   g.n_names, g.grp_rows,
                   first_value(p.course_name) OVER (
                       PARTITION BY p.lat, p.lon
                       ORDER BY p.rows DESC, length(p.course_name) DESC,
                                p.course_name
                   ) AS canon_name
            FROM   course_pts p
            JOIN   grp g ON g.lat = p.lat AND g.lon = p.lon
        )
        SELECT * FROM ranked
    """)
    cur.execute("CREATE INDEX ON course_canon (course_name)")
    cur.execute("ANALYZE course_canon")

    cur.execute("""SELECT count(*) FILTER (WHERE course_name <> canon_name),
                          count(DISTINCT canon_name)
                   FROM course_canon WHERE course_name <> canon_name""")
    moved, groups = cur.fetchone()
    print(f"    {moved:,} names would fold into {groups:,} canonical courses")
    return moved


def applyGuards(cur):
    """
    Remove from the plan anything that must not move.

    ★ THE PROTECTED CHECK RUNS BOTH WAYS. Excluding only the protected name
      is not enough: if `Mt. San Antonio College (rain course)` were chosen as
      a group's canonical, every ordinary name at that venue would be renamed
      INTO the rain course. Both sides of every pair are tested.

    ★ AND A THIN NAME MAY NOT BE THE SURVIVOR. `XC:Mt. Sac` holds 13 and 25
      results against the main venue's 170,294. Choosing it would move a
      six-figure cell into a two-figure one.
    """
    cur.execute("SELECT course_name, canon_name FROM course_canon")
    bad = {c for c, k in cur.fetchall()
           if isProtected(c) or isProtected(k)}
    if bad:
        cur.executemany("DELETE FROM course_canon WHERE course_name = %s",
                        [(b,) for b in sorted(bad)])
        print(f"    held {len(bad):,} names touching a deliberate split "
              f"(rain course / alt / short / long)")

    cur.execute(f"""
        DELETE FROM course_canon c
        WHERE  EXISTS (SELECT 1 FROM course_pts p
                       WHERE p.course_name = c.canon_name
                         AND p.rows < {MIN_CANON_ROWS})
    """)
    if cur.rowcount:
        print(f"    dropped {cur.rowcount:,} rows whose canonical name was "
              f"thinner than {MIN_CANON_ROWS} results")

    cur.execute("ANALYZE course_canon")


def report(cur, limit=40):
    cur.execute("""
        SELECT canon_name, count(*) AS names,
               sum(rows) FILTER (WHERE course_name <> canon_name) AS moving,
               string_agg(course_name, ' | ' ORDER BY rows DESC)  AS members
        FROM   course_canon
        GROUP  BY canon_name
        HAVING count(*) FILTER (WHERE course_name <> canon_name) > 0
        ORDER  BY sum(rows) FILTER (WHERE course_name <> canon_name) DESC
        LIMIT  %s
    """, (limit,))
    print(f"\n    {'moving':>9}  canonical  <-  members")
    for canon, names, moving, members in cur.fetchall():
        print(f"    {moving or 0:>9}  {members[:110]}")


# ------------------------------------------------------------------ #
# CHUNK 3 -- WRITE
# ------------------------------------------------------------------ #

def write(cur):
    """
    Back up, then rewrite meets.course_name to the canonical name.

    ⚠ THE BACKUP HOLDS THE ORIGINAL AND IS TAKEN BEFORE THE UPDATE, so the
      rollback is one statement and does not depend on course_canon
      surviving or on this script running again.
    """
    cur.execute("DROP TABLE IF EXISTS meets_course_name_bak")
    cur.execute("""
        CREATE TABLE meets_course_name_bak AS
        SELECT m.meet_id, m.div_id, m.course_name
        FROM   meets m
        JOIN   course_canon c ON c.course_name = m.course_name
        WHERE  c.course_name <> c.canon_name
    """)
    cur.execute("SELECT count(*) FROM meets_course_name_bak")
    print(f"\n    backed up {cur.fetchone()[0]:,} meets rows")

    cur.execute("""
        UPDATE meets m
        SET    course_name = c.canon_name
        FROM   course_canon c
        WHERE  c.course_name = m.course_name
          AND  c.course_name <> c.canon_name
    """)
    print(f"    {cur.rowcount:,} meets rows renamed")
    print("    ROLLBACK:\n"
          "      UPDATE meets m SET course_name = b.course_name\n"
          "      FROM meets_course_name_bak b\n"
          "      WHERE m.meet_id = b.meet_id AND m.div_id = b.div_id;")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            print("[course] building name table...")
            buildNames(cur)

            print("\n[course] grouping by coordinates...")
            buildGroups(cur)
            applyGuards(cur)
            report(cur)

            if live:
                write(cur)
                conn.commit()
                print("\n[course] WRITTEN. The XC difficulty cell is keyed on "
                      "course_name, so every merged venue's cell changes -- "
                      "re-solve.")
            else:
                conn.rollback()
                print("\n[course] DRY RUN -- pass --write to apply")


if __name__ == "__main__":
    main(live="--write" in sys.argv)