#!/usr/bin/env python3
"""
meet_class_census.py -- what the championship rule (engine/meet_class.py)
does to the REAL meet names, so a misclassification is seen before it is
trusted. Read-only.

    python scripts/meet_class_census.py                 # top names per class, both sports
    python scripts/meet_class_census.py --top 40
    python scripts/meet_class_census.py --find 'preview'   # every distinct name matching, with its class
    python scripts/meet_class_census.py --sport XC

The classes: 0 ordinary, 1 league-level championship, 2 state/national-
level championship or its qualifier. The SQL here is the same expression
the pack uses (meet_class.sql), so what prints is what the solve sees.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402
import meet_class as mcl                                      # noqa: E402

# per sport: the table, its name column, the flag expression, the row-count
# table to weight by
_SOURCES = {
    "XC": [
        ("meets", "meet_name", None,
         "SELECT m.meet_name AS name, count(*) AS n_rows FROM results r "
         "JOIN meets m ON m.div_id = r.div_id AND m.source = r.source "
         "WHERE r.normalized_time IS NOT NULL GROUP BY 1"),
        ("meets_tfrrs", "meet_name", "COALESCE(mt.is_championship, 0) = 1",
         "SELECT mt.meet_name AS name, count(*) AS n_rows, "
         "max(COALESCE(mt.is_championship, 0)) AS flag FROM results r "
         "JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id AND mt.sport = 'XC' "
         "WHERE r.source = 'tfrrs' AND r.normalized_time IS NOT NULL GROUP BY 1"),
    ],
    "TF": [
        ("meets_tf", "meet_name", None,
         "SELECT m.meet_name AS name, count(*) AS n_rows FROM results_tf r "
         "JOIN meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id "
         "AND m.event_id = r.event_id AND m.source = r.source "
         "WHERE r.normalized_time IS NOT NULL GROUP BY 1"),
    ],
}


def census(sport, top, find):
    by_class = {0: {}, 1: {}, 2: {}}
    with getConn() as conn, conn.cursor() as cur:
        for table, _col, flag_expr, sql_rows in _SOURCES[sport]:
            cur.execute(sql_rows)
            for row in cur.fetchall():
                name, n = row[0], int(row[1])
                flag = bool(row[2]) if len(row) > 2 else False
                c = mcl.classify(name, flag)
                by_class[c][name or ""] = by_class[c].get(name or "", 0) + n
    if find:
        rx = find.lower()
        print(f"\n== {sport}: names matching '{find}'")
        rows = [(c, n, name) for c in (0, 1, 2) for name, n in by_class[c].items()
                if rx in (name or "").lower()]
        for c, n, name in sorted(rows, key=lambda t: -t[1])[:top]:
            print(f"   class {c}  {n:>9,}  {name}")
        return
    tot = {c: sum(by_class[c].values()) for c in (0, 1, 2)}
    all_rows = max(sum(tot.values()), 1)
    print(f"\n== {sport}: rows by class  ordinary {tot[0]:,} ({100 * tot[0] / all_rows:.1f}%)"
          f"  league-level {tot[1]:,} ({100 * tot[1] / all_rows:.1f}%)"
          f"  state/national-level {tot[2]:,} ({100 * tot[2] / all_rows:.1f}%)")
    for c in (2, 1, 0):
        print(f"\n-- class {c}, the {top} biggest meet names:")
        for name, n in sorted(by_class[c].items(), key=lambda t: -t[1])[:top]:
            print(f"   {n:>9,}  {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--sport", choices=("XC", "TF"))
    ap.add_argument("--find", help="substring of a meet name to look up")
    a = ap.parse_args()
    for sport in ((a.sport,) if a.sport else ("XC", "TF")):
        census(sport, a.top, a.find)


if __name__ == "__main__":
    main()
