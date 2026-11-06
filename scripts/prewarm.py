#!/usr/bin/env python3
"""
prewarm.py -- read the tables and indexes the pages hit into memory, right
after a pipeline swaps them in, so the first visitors after a go-live do not
pay for the disk.

    python scripts/prewarm.py                 # the default set, capped
    python scripts/prewarm.py --cap-gb 24 --dry-run

★ WHY (owner, 2026-11-06: "I do not care about warm timings, only cold
  timings"). Every run swaps in NEW tables -- ranking_results,
  athlete_season, season_comps, athlete_season_ev -- whose pages are in no
  cache at all, so every board, rank line and comps lookup after a go-live
  reads from disk until enough visitors have pulled the hot parts in. A
  page is cold exactly then. pg_prewarm (Postgres contrib) reads a relation
  into the OS page cache ('prefetch'), the box has 125 GB, and the hot set
  is a few GB.

! WHAT, AND HOW MUCH. Every index on the board and page tables and the
  smaller of those tables whole -- smallest first, until --cap-gb (default
  XCP_PREWARM_GB or 16) is used. ranking_results' heap (~23 GB) is left to
  the cap: its indexes are what the boards walk.

! HARMLESS WHEN IT CANNOT. No pg_prewarm extension (and no right to create
  it), a relation that is not there: it says so and exits 0. 'prefetch' asks
  the kernel to read ahead and returns; it never blocks a reader or holds a
  lock beyond the relation's ACCESS SHARE.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# the tables the pages read by index; their indexes always, the tables
# themselves while they fit in the cap
TABLES = ("athlete_season_ev", "season_comps", "season_comps_meta", "athlete_season",
          "person_gender", "athletes", "search_index", "ranking_results", "results",
          "results_tf", "grade_fix")


def plan(rels, cap_bytes):
    """[(name, bytes)] to prewarm: indexes first (smallest first), then
    tables (smallest first), while the running total stays under the cap.
    `rels` is [(name, kind 'i'|'r', bytes)]. Pure."""
    out, used = [], 0
    for kind in ("i", "r"):
        for name, k, size in sorted((r for r in rels if r[1] == kind), key=lambda r: r[2]):
            if used + size > cap_bytes:
                continue
            out.append((name, size))
            used += size
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cap-gb", type=float,
                    default=float(os.environ.get("XCP_PREWARM_GB", "16")))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from database import getConn
    with getConn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
            except Exception as exc:                        # noqa: BLE001
                print(f"prewarm: pg_prewarm unavailable ({str(exc).splitlines()[0]}) -- skipped")
                return 0
            cur.execute("""
                SELECT c.relname, c.relkind::text, pg_relation_size(c.oid)
                FROM   pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE  n.nspname = 'public'
                  AND  ((c.relkind = 'r' AND c.relname = ANY(%(t)s))
                     OR (c.relkind = 'i' AND c.oid IN (
                            SELECT indexrelid FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid
                            WHERE  t.relname = ANY(%(t)s) AND i.indisvalid)))
            """, {"t": list(TABLES)})
            todo = plan(cur.fetchall(), a.cap_gb * 1024 ** 3)
            total = sum(s for _n, s in todo)
            print(f"prewarm: {len(todo)} relations, {total / 1024 ** 3:.1f} GB "
                  f"(cap {a.cap_gb:g} GB){' -- dry run' if a.dry_run else ''}")
            for name, size in todo:
                if a.dry_run:
                    print(f"  {size / 1024 ** 2:9.0f} MB  {name}")
                    continue
                try:
                    cur.execute("SELECT pg_prewarm(%s::regclass, 'prefetch')", (name,))
                    print(f"  {size / 1024 ** 2:9.0f} MB  {name}", flush=True)
                except Exception as exc:                    # noqa: BLE001
                    print(f"  skipped {name}: {str(exc).splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
