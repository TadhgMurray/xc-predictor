# Project: xc-predictor
# File:    scripts/census_tf_venue.py  (v2, 2026-07-04 — replaces v1)
# Purpose: Measure, per source, how many era-qualifying TF rows have usable
#          venue geometry after the loader's joins. v1 found the propagation
#          gap (tfrrs 100.0% no_meets_tf_row); v2's job is to measure what
#          propagation + the tfrrs_meet_geometry join BOUGHT.
#
#          V2'S STRUCTURAL UPGRADE: v1 kept its WHERE clause byte-identical
#          to the era loader's BY HAND (a marked mirror). v2 IMPORTS the
#          loader's _TF_SQL and wraps it as a subquery — identical BY
#          CONSTRUCTION. If the loader's joins change, this census changes
#          with them, automatically. (Import-not-mirror is correct here for
#          the same reason as the flag census: this extends a measurement,
#          it does not audit a write.)
#
#          What v2 reports per source:
#            qualifying    — rows passing the loader's SQL-level cuts
#            has_geometry  — a joined row supplied ANY geometry field
#                            (anet <- meets_tf, tfrrs <- tfrrs_meet_geometry)
#            no_geometry   — the honest residue (v1's finer row-vs-no-row
#                            split is gone; with the stamps live, "row but
#                            empty" vs "no row" no longer separates causes)
#          NOTE: geometry presence is measured at the JOIN — the Python-side
#          venue override changes VALUES, never presence, so it can't move
#          these numbers (its count lives in the loader's own ledger).
#
# Cost:    one aggregate over the full joined stream (~the era stream's
#          sort-less cousin; minutes, not seconds). Read-only, re-runnable.
# Usage:   python scripts/census_tf_venue.py

import sys
sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn
from fit_era_corrections import _TF_SQL      # the shared predicate, by import


# _buildCensusSQL
# Purpose:   Wrap the loader's stream as a subquery and aggregate it. The
#            ORDER BY is stripped — an aggregate doesn't need the sort, and
#            skipping it is what makes the census minutes instead of the
#            loader's sorted half-hour. rsplit from the RIGHT with maxsplit
#            1: only the final ORDER BY goes, whatever else the SQL says.
# Arguments: none (reads the imported _TF_SQL).
# Output:    the census SQL string.
def _buildCensusSQL():
    base = _TF_SQL.rsplit("ORDER BY", 1)[0]
    # The subquery's SELECT already exposes the COALESCEd aliases
    # (track_length / track_type / is_indoor), so the outer query reads the
    # SAME resolved fields the loader's Python reads. FILTER (WHERE ...)
    # counts only rows matching its condition — one pass, both buckets.
    return f"""
        SELECT source,
               count(*) AS qualifying,
               count(*) FILTER (WHERE track_length IS NOT NULL
                                   OR track_type   IS NOT NULL
                                   OR is_indoor    IS NOT NULL) AS has_geometry
        FROM ({base}) q
        GROUP BY source
        ORDER BY source
    """


# _printReport
# Purpose:   The v1-shaped report: counts and shares per source, plus the
#            reconciliation total (must equal the era stream's row count —
#            the +0 check that proved v1 and the loader saw the same rows).
# Arguments: rows — (source, qualifying, has_geometry) tuples.
def _printReport(rows):
    total = 0
    for source, qualifying, has_geo in rows:
        total += qualifying
        no_geo = qualifying - has_geo
        print(f"\n[{source}]  {qualifying:,} qualifying rows")
        print(f"    has_geometry: {has_geo:>13,}  ({100.0*has_geo/qualifying:5.1f}%)")
        print(f"    no_geometry : {no_geo:>13,}  ({100.0*no_geo/qualifying:5.1f}%)")
    print(f"\ntotal {total:,}  <- must equal the era stream's TF row count"
          f" (the +0 reconciliation)")


def main():
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '512MB'")   # the joins, not a sort
            cur.execute(_buildCensusSQL())
            rows = cur.fetchall()                   # <= a handful of rows
    _printReport(rows)


if __name__ == "__main__":
    main()