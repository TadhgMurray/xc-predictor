# Project: xc-predictor / scripts
# File:    audit_columns.py
# Purpose: Per-column weight report for the big tables, with a code-usage
#          verdict -- the "are we shipping columns nobody reads" question,
#          answered from pg_stats and a repo grep instead of guesses.
#
#     python scripts/audit_columns.py
#     python scripts/audit_columns.py results_tf results
#
# Read-only. Notes that matter for the dump:
#   - pg_total_relation_size includes INDEXES, which pg_dump does not ship;
#     the heap+toast line here is what actually travels.
#   - dropping a column takes effect in the dump IMMEDIATELY (no vacuum);
#     the local disk reclaims later, whenever a rewrite happens.
#   - a column is only a DROP CANDIDATE when its name appears NOWHERE in
#     the codebase -- a false positive in the grep means we keep it, which
#     is the safe direction.

import argparse
import os
import re
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

from database import getConn                          # noqa: E402

_DEFAULT = ["results_tf", "results", "ranking_results", "meets_tf",
            "athletes", "search_index", "grade_fix", "meet_extras",
            "athlete_agg", "athlete_season", "athlete_ratings", "weather",
            "athlete_named", "athlete_school", "athlete_season_level"]

_CODE_DIRS = ("racecast", "engine", "backfill", "scripts", "model")


def _codeText():
    chunks = []
    for d in _CODE_DIRS:
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.endswith((".py", ".ps1", ".html", ".js")):
                    try:
                        with open(os.path.join(root, f),
                                  encoding="utf-8", errors="ignore") as fh:
                            chunks.append(fh.read())
                    except OSError:
                        pass
    return "\n".join(chunks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tables", nargs="*", default=None)
    ap.add_argument("--min-gb", type=float, default=0.05,
                    help="only print columns estimated above this (default "
                         "0.05 GB)")
    args = ap.parse_args()
    tables = args.tables or _DEFAULT

    print("\n  loading code text for column-usage grep ...")
    code = _codeText()

    candidates = []
    with getConn() as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute("SELECT to_regclass(%s)", (t,))
            if cur.fetchone()[0] is None:
                print(f"\n  {t}: absent")
                continue
            cur.execute("""
                SELECT c.reltuples::bigint,
                       pg_relation_size(c.oid),
                       COALESCE(pg_relation_size(c.reltoastrelid), 0),
                       pg_indexes_size(c.oid)
                FROM pg_class c JOIN pg_namespace n
                  ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = %s""", (t,))
            n_rows, heap, toast, idx = cur.fetchone()
            print(f"\n  {t}: {n_rows:,} rows -- heap+toast "
                  f"{(heap + toast) / 1e9:,.2f} GB (this travels), "
                  f"indexes {idx / 1e9:,.2f} GB (rebuilt on restore)")
            cur.execute("""
                SELECT a.attname,
                       COALESCE(s.avg_width, 0),
                       COALESCE(s.null_frac, 0)
                FROM pg_attribute a
                LEFT JOIN pg_stats s
                  ON s.tablename = %s AND s.attname = a.attname
                   AND s.schemaname = 'public'
                WHERE a.attrelid = %s::regclass
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY COALESCE(s.avg_width, 0)
                         * (1 - COALESCE(s.null_frac, 0)) DESC""", (t, t))
            for col, width, nullf in cur.fetchall():
                est = n_rows * width * (1.0 - nullf)
                if est / 1e9 < args.min_gb:
                    continue
                used = re.search(rf"\b{re.escape(col)}\b", code) is not None
                tag = "used" if used else "UNREFERENCED -> drop candidate"
                print(f"    {est / 1e9:7.2f} GB  {col:<26} "
                      f"width {width:>4}  null {nullf:4.0%}  {tag}")
                if not used:
                    candidates.append((t, col, est))

    if candidates:
        print("\n  DROP CANDIDATES (name appears nowhere in the codebase):")
        for t, col, est in sorted(candidates, key=lambda x: -x[2]):
            print(f"    ALTER TABLE {t} DROP COLUMN {col};"
                  f"   -- ~{est / 1e9:,.2f} GB out of the dump")
        print("\n  Review, then run the ALTERs by hand -- a dropped column "
              "leaves the dump\n  immediately; local disk reclaims on a "
              "later rewrite.")
    else:
        print("\n  no unreferenced columns above the size floor -- the "
              "schema is as lean\n  as the code that reads it.")


if __name__ == "__main__":
    main()
