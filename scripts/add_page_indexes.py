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

# (table, leading column, name, full spec or None). With a spec, the
# check requires an index whose WHOLE definition matches it -- a plain
# leading-column index does not satisfy a covering INCLUDE spec.
WANTED = [
    # stampRowsHs on every race/compiled/course/compare page -- without
    # this each of those pages seq-scans ranking_results (56M rows).
    ("ranking_results", "result_id", "idx_rr_result", None),
    ("ranking_results", "school",  "idx_rr_school", None),
    # stampRecordFlags: index-only career reads for the PR/SR badges
    # (1.4s of random heap fetches per race page without it)
    ("ranking_results", "person_id", "idx_rr_person_cover",
     "(person_id) INCLUDE (sport, race_date, year, distance, time_seconds)"),
    ("results_tf",      "school",  "idx_results_tf_school", None),
    ("results_tf",      "meet_id", "idx_results_tf_meet", None),
    ("results",         "school",  "idx_results_school", None),
    # the course pages' driving filter (live fallback + board builds)
    ("meets",           "course_name", "idx_meets_course_name", None),
    # ★ ISSUE #17 (2026-08-27). Every course helper joins
    #   `results ON r.div_id = m.div_id` after filtering meets by
    #   course_name -- with no div_id-leading index the planner hash-joins
    #   by scanning all 39M result rows PER QUERY, ~6 queries per course:
    #   the measured 7-19s per course, and why the board build
    #   extrapolated past a day. With it, each query is a handful of
    #   index probes and the full --all build becomes an hour, not a day.
    ("results",         "div_id",  "idx_results_div", None),
    ("athlete_season",  "school",  "idx_athlete_season_school", None),
]


def main():
    check_only = "--check" in sys.argv
    cm = getConn()
    conn = cm.__enter__()
    try:
        cur = conn.cursor()
        todo = []
        drop_first = []
        for table, col, name, spec in WANTED:
            # ⚠ VALIDITY, NOT JUST EXISTENCE. A failed CREATE INDEX
            #   CONCURRENTLY leaves an INVALID index behind: pg_indexes
            #   lists it, the planner ignores it, and "OK" would be a lie
            #   while the page stays a sequential scan.
            # ⚠ AND THE LEADING COLUMN MUST MATCH EXACTLY. The first
            #   version matched '%(school%', which "(school_source, ..."
            #   also satisfies -- so results_tf reported OK on the wrong
            #   index, the school index never built, and the PRs page
            #   kept seq-scanning 37M rows at 55s a view.
            cur.execute(r"""
                SELECT i.relname, idx.indisvalid, pg_get_indexdef(idx.indexrelid)
                FROM   pg_index idx
                JOIN   pg_class i ON i.oid = idx.indexrelid
                JOIN   pg_class t ON t.oid = idx.indrelid
                WHERE  t.relname = %s
                  AND  pg_get_indexdef(idx.indexrelid)
                       ~* ('\(\s*' || %s || '\s*[,)]')
            """, (table, col))
            rows = cur.fetchall()

            # ⚠ A PARTIAL INDEX DOES NOT COUNT. An index built with a
            #   WHERE clause only serves queries whose predicate implies
            #   it -- the planner showed a valid school index while
            #   seq-scanning 63M rows for a plain school lookup. Usable
            #   here means valid AND unconditional -- AND matching the
            #   full spec where one is given (a covering INCLUDE index is
            #   not satisfied by a plain one on the same column).
            def norm(s):
                i = s.find("(")
                return s[i:].replace(" ", "").lower() if i >= 0 else s

            def fits(idxdef):
                return norm(idxdef) == norm(spec) if spec else True

            usable = [r for r in rows
                      if r[1] and " where " not in r[2].lower()
                      and fits(r[2])]
            partial = [r for r in rows if r[1] and " where " in r[2].lower()]
            invalid = [r for r in rows if not r[1]]
            if usable:
                print(f"OK    {table}({col}): {usable[0][2][:110]}")
                continue
            if partial:
                print(f"PART  {table}({col}): only a PARTIAL index exists "
                      f"({partial[0][0]}) -- unusable for page lookups, "
                      f"building a full one")
                print(f"      {partial[0][2][:110]}")
            if invalid:
                print(f"BAD   {table}({col}): {invalid[0][0]} is INVALID "
                      f"(a concurrent build failed) -- will drop and rebuild")
                drop_first.append(invalid[0][0])
            if not partial and not invalid:
                print(f"MISS  {table}({col})")
            # never collide with an existing name (the partial may own it)
            taken = {r[0] for r in rows}
            while name in taken:
                name += "_f"
            todo.append((table, name, spec or f"({col})"))

        if check_only or not todo:
            print("nothing to build" if not todo else "(check only)")
            return

        # CONCURRENTLY cannot run inside a transaction block
        conn.rollback()
        old = conn.autocommit
        conn.autocommit = True
        try:
            for bad in drop_first:
                print(f"dropping invalid index {bad}...")
                cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {bad}")
            for table, name, spec in todo:
                print(f"building {name} on {table} {spec} "
                      f"(concurrent, minutes on the big tables)...")
                cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                            f"{name} ON {table} {spec}")
                print(f"  done {name}")
        finally:
            conn.autocommit = old
    finally:
        cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()
