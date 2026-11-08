#!/usr/bin/env python3
"""
layout_probe.py -- the production numbers docs/TABLE-LAYOUT-EXPERIMENTS.md
asks for before any layout change. READ-ONLY: catalogue and statistics
views only, no scan of a table.

    /srv/venv/bin/python scripts/layout_probe.py
    /srv/venv/bin/python scripts/layout_probe.py --tables ranking_results

★ WHAT IT ANSWERS
  1. Which indexes the site actually reads: idx_scan per index since the
     table was swapped in (a rebuilt table starts its counters at zero, so
     after a day of traffic this is that day's use). An index at 0 scans
     after a week is a candidate to retire -- a tree the build writes,
     WAL-logs, vacuums and caches every run for nothing.
  2. What a partial index would save: the NULL share of speed_rating and
     time_seconds (pg_stats.null_frac), which is the share of rows a
     `WHERE speed_rating IS NOT NULL` rating index leaves out.
  3. What reordering the columns would save: the bytes each row spends on
     alignment padding in the live column order against the narrow order
     (8-byte, then 4-byte, then variable), from pg_stats.avg_width and
     null_frac -- an estimate, not a scan.
  4. Whether a BRIN index could work: pg_stats.correlation of the heap
     order with each column (BRIN needs |correlation| near 1).
  5. The btree fillfactor every index was built with (100 saves ~10% on a
     table that is built once and never updated).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

_ALIGN = {"int8": 8, "float8": 8, "timestamptz": 8, "timestamp": 8,
          "int4": 4, "float4": 4, "date": 4, "int2": 2, "bool": 1}


def padding(cols):
    """Average bytes lost to alignment, walking the columns in order. Each
    column: (name, typname, avg_width, null_frac). A NULL takes no space,
    so a column NULL on most rows mostly costs nothing; the expectation is
    taken per column on its null_frac."""
    off, lost = 0.0, 0.0
    for _n, typ, width, nf in cols:
        al = _ALIGN.get(typ)
        present = 1.0 - (nf or 0.0)
        if al:
            gap = (-off) % al
            lost += gap * present
            off += (gap + al) * present
        else:
            off += (width or 0) * present      # short varlena: 1-byte header, no alignment
    return lost


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", nargs="+", default=["ranking_results", "athlete_season"])
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        for t in a.tables:
            cur.execute("""SELECT c.reltuples::bigint, pg_relation_size(c.oid),
                                  pg_indexes_size(c.oid), c.relkind::text, s.stats_reset
                           FROM pg_class c
                           LEFT JOIN pg_stat_database s ON s.datname = current_database()
                           WHERE c.oid = to_regclass(%s)""", (t,))
            row = cur.fetchone()
            if not row:
                print(f"{t}: not found")
                continue
            n, heap, idx, kind, reset = row
            print(f"\n== {t}  ({kind})  rows ~{n:,}  heap {heap / 2**30:.2f} GB  "
                  f"indexes {idx / 2**30:.2f} GB  (database stats reset {reset})")
            cur.execute("""SELECT i.indexrelname, i.idx_scan, i.idx_tup_read,
                                  pg_relation_size(i.indexrelid),
                                  COALESCE(array_to_string(c.reloptions, ','), '') AS opts,
                                  pg_get_indexdef(i.indexrelid)
                           FROM pg_stat_user_indexes i JOIN pg_class c ON c.oid = i.indexrelid
                           WHERE i.relid = to_regclass(%s)
                           ORDER BY i.idx_scan, pg_relation_size(i.indexrelid) DESC""", (t,))
            print(f"  {'index':34s} {'scans':>10s} {'tuples read':>14s} {'MB':>8s}  options")
            for name, scans, tup, size, opts, _ddl in cur.fetchall():
                print(f"  {name[:34]:34s} {scans:>10,} {tup:>14,} {size / 2**20:>8,.0f}  {opts or 'fillfactor 90 (default)'}")
            cur.execute("""SELECT a.attname, ty.typname, s.avg_width, s.null_frac, s.correlation
                           FROM pg_attribute a
                           JOIN pg_type ty ON ty.oid = a.atttypid
                           LEFT JOIN pg_stats s ON s.schemaname = 'public' AND s.tablename = %s
                                               AND s.attname = a.attname
                                               AND s.inherited = (%s = 'p')
                           WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped
                           ORDER BY a.attnum""", (t, kind, t))
            cols = cur.fetchall()
            live = [(c[0], c[1], c[2], c[3]) for c in cols]
            narrow = (sorted([c for c in live if _ALIGN.get(c[1]) == 8], key=lambda c: c[0])
                      + [c for c in live if _ALIGN.get(c[1]) == 4]
                      + [c for c in live if _ALIGN.get(c[1]) == 2]
                      + [c for c in live if _ALIGN.get(c[1]) is None or _ALIGN.get(c[1]) == 1])
            p_live, p_narrow = padding(live), padding(narrow)
            print(f"  alignment padding: ~{p_live:.1f} bytes/row in the live order, "
                  f"~{p_narrow:.1f} narrow -> ~{(p_live - p_narrow) * n / 2**30:.2f} GB of heap")
            print(f"  {'column':16s} {'type':8s} {'avg_width':>9s} {'null_frac':>9s} {'correlation':>11s}")
            for name, typ, w, nf, corr in cols:
                print(f"  {name:16s} {typ:8s} {w if w is not None else '-':>9} "
                      f"{(f'{nf:.3f}' if nf is not None else '-'):>9s} "
                      f"{(f'{corr:+.3f}' if corr is not None else '-'):>11s}")


if __name__ == "__main__":
    main()
