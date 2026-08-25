"""
add_page_indexes.py -- create the indexes the school/PR/TF pages filter by.

The school page, School PRs and compare all filter ranking_results and
results_tf by SCHOOL, and the TF meet scorer fetches results_tf by
MEET_ID. Without an index each of those is a sequential scan of a
61M-row table per page view. This script lists what exists, then builds
what is missing with CREATE INDEX CONCURRENTLY -- no table locks, safe
with the site running (CONCURRENTLY needs autocommit, hence the direct
connection handling).

Usage:  python scripts/add_page_indexes.py            # report + build
        python scripts/add_page_indexes.py --check    # report only
"""

import sys

sys.path.insert(0, "scripts")
from database import getConn   # noqa: E402

WANTED = [
    ("ranking_results", "school",  "idx_rr_school"),
    ("results_tf",      "school",  "idx_results_tf_school"),
    ("results_tf",      "meet_id", "idx_results_tf_meet"),
    ("results",         "school",  "idx_results_school"),
    ("athlete_season",  "school",  "idx_athlete_season_school"),
]


def main():
    check_only = "--check" in sys.argv
    cm = getConn()
    conn = cm.__enter__()
    try:
        cur = conn.cursor()
        todo = []
        for table, col, name in WANTED:
            cur.execute("""
                SELECT indexdef FROM pg_indexes
                WHERE tablename = %s AND indexdef ILIKE %s
            """, (table, f"%({col}%"))
            existing = [r[0] for r in cur.fetchall()]
            if existing:
                print(f"OK    {table}({col}): {existing[0][:90]}")
            else:
                print(f"MISS  {table}({col})")
                todo.append((table, col, name))

        if check_only or not todo:
            print("nothing to build" if not todo else "(check only)")
            return

        # CONCURRENTLY cannot run inside a transaction block
        conn.rollback()
        old = conn.autocommit
        conn.autocommit = True
        try:
            for table, col, name in todo:
                print(f"building {name} on {table}({col}) "
                      f"(concurrent, minutes on the big tables)...")
                cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                            f"{name} ON {table} ({col})")
                print(f"  done {name}")
        finally:
            conn.autocommit = old
    finally:
        cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()
