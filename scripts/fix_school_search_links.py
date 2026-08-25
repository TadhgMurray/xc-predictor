"""
fix_school_search_links.py -- repoint school search hits at /school pages.

The search index was built before /school existed, so every school entry
links to a filtered athlete search ("/search?q=...&kind=athlete") and the
topbar drops you on a list of athletes instead of the school page. This
rewrites those links in place -- seconds, versus the hours a full index
rebuild costs. search_index.py itself now writes the right link, so any
future rebuild stays correct without this script.

Usage:  python scripts/fix_school_search_links.py
"""

import sys
from urllib.parse import quote

sys.path.insert(0, "scripts")
from database import getConn   # noqa: E402

import psycopg2.extras         # noqa: E402


def main():
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT label FROM search_index
            WHERE kind = 'school' AND link NOT LIKE '/school/%'
        """)
        labels = [r[0] for r in cur.fetchall()]
        print(f"school entries to repoint: {len(labels):,}")
        if not labels:
            print("nothing to do")
            return

        pairs = [(f"/school/{quote(s, safe='')}", s) for s in labels]
        psycopg2.extras.execute_values(cur, """
            UPDATE search_index AS si
            SET    link = v.link
            FROM   (VALUES %s) AS v(link, label)
            WHERE  si.kind = 'school' AND si.label = v.label
        """, pairs, page_size=5000)
        conn.commit()
        print(f"repointed {len(pairs):,} school links")


if __name__ == "__main__":
    main()
