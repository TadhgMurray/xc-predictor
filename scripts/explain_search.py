#!/usr/bin/env python3
"""
explain_search.py -- the search dropdown's exact query for a few typed
strings, with EXPLAIN (ANALYZE, BUFFERS) and how many rows each one matched.

    /srv/venv/bin/python scripts/explain_search.py "smith" "john smith" "arcadia"

Built with app._searchTerms / _searchOrderHead / _searchOrder, the same
functions /search/api and /search use, on a connection set up as the site's
(app.py turns JIT off). Read-only; rolled back.
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_QUIET", "1")


def dropdownSql(A, raw, limit=10):
    where, params, word_score = A._searchTerms(raw)
    params["lim"] = limit
    sql = (f"SELECT kind, label, sublabel, link FROM search_index "
           f"WHERE {' AND '.join(where)} "
           f"ORDER BY {A._searchOrderHead()} ({word_score}) DESC, {A._searchOrder()} "
           f"LIMIT %(lim)s")
    return sql, params, where


def main(argv=None):
    words = (argv if argv is not None else sys.argv[1:]) or ["smith", "john smith"]
    import app as A                     # sets XCP_DB_JIT=off before the pool exists
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            for raw in words:
                sql, params, where = dropdownSql(A, raw.lower())
                t = time.time()
                cur.execute(f"SELECT count(*) FROM search_index WHERE {' AND '.join(where)}",
                            params)
                n = cur.fetchone()[0]
                print(f"\n=== {raw!r}: {n:,} rows match ({time.time() - t:.2f} s to count)")
                cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
                for (line,) in cur.fetchall():
                    print("    " + line)
            conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
