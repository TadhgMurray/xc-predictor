# Project: xc-predictor
# File:    scripts/export_for_analysis.py
# Purpose: dump analysis slices to Parquet for offline work.
#
# Read-only. Touches nothing, writes only to ./exports.

import os
import sys
sys.path.insert(0, "scripts")            # database.py lives in scripts/

import pandas as pd
from database import getConn, initPool, closePool

OUT_DIR = "exports"


# ------------------------------------------------------------------ #
# CHUNK 1 — HELPERS
# ------------------------------------------------------------------ #

# _dump
# Purpose:   run one query, write one Parquet file, report its size.
# Arguments: conn -- open connection; name -- output stem; sql -- the query.
# Output:    the DataFrame (returned so callers can chain checks off it).
# Syntax:    pd.read_sql_query hands the cursor straight to pandas, so the
#            column names come from the query and no manual unpacking is needed.
#            compression="zstd" beats the snappy default by roughly 2x here and
#            pandas reads it back with no extra argument.
def _dump(conn, name, sql):
    df = pd.read_sql_query(sql, conn)
    path = os.path.join(OUT_DIR, f"{name}.parquet")
    df.to_parquet(path, compression="zstd", index=False)
    mb = os.path.getsize(path) / 1e6
    print(f"[export] {name:<22} {len(df):>10,} rows   {mb:>7.1f} MB")
    return df


# _resultsSql
# Purpose:   the results slice, joined to venue and state.
# Arguments: season_start, season_end -- ISO date STRINGS (results.date is text).
#            sample_mod -- keep 1 athlete in N, or None for all.
# Output:    SQL string.
#
# ★ SAMPLING IS BY ATHLETE, NOT BY ROW. Every question here is within-athlete
#   -- "how does this person rate at venue A vs venue B" -- so a random row
#   sample would shred exactly the structure being measured. Hashing person_id
#   keeps each sampled athlete's FULL history and drops whole athletes instead.
def _resultsSql(season_start, season_end, sample_mod=None):
    sample = ""
    if sample_mod:
        sample = f"AND abs(hashtext(r.person_id::text)) % {sample_mod} = 0"

    return f"""
    WITH mc AS (
      -- meets is keyed (meet_id, div_id); collapse so the join can't fan out.
      SELECT meet_id,
             min(course_name)     AS course_name,
             min(distance_m) AS distance_meters,
             min(state)           AS state
      FROM meets
      GROUP BY meet_id
    )
    SELECT r.person_id, r.athlete_id, r.meet_id, r.date,
           r.time_seconds, r.normalized_time, r.speed_rating,
           r.grade, r.school, r.source,
           mc.course_name, mc.distance_meters, mc.state
    FROM results r
    JOIN mc USING (meet_id)
    WHERE r.time_seconds < 999999            -- DNF sentinel
      AND r.date ~ '^(19|20)[0-9]{{2}}'      -- year-corruption guard
      AND r.date >= '{season_start}'         -- text compare; date is TEXT
      AND r.date <  '{season_end}'
      {sample}
    """


# ------------------------------------------------------------------ #
# CHUNK 2 — THE RUN
# ------------------------------------------------------------------ #

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    initPool()
    try:
        with getConn() as conn:
            _dump(conn, "course_difficulties",
                  "SELECT * FROM course_difficulties")

            _dump(conn, "athlete_ratings",
                  "SELECT * FROM athlete_ratings")

            # One XC season. Drop sample_mod once you've seen the file size.
            _dump(conn, "results_2025xc",
                  _resultsSql("2025-07-01", "2026-07-01", sample_mod=None))
    finally:
        closePool()


if __name__ == "__main__":
    main()