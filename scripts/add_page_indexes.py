"""
add_page_indexes.py -- create the indexes the school/PR/TF pages filter by.

The school page, School PRs and compare all filter ranking_results and
results_tf by SCHOOL, the TF meet scorer fetches results_tf by MEET_ID,
and the filtered /meets view filters meets by STATE and counts results
by MEET_ID. Without an index each of those is a sequential scan of a
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
    # ★ THE OTHER HALF OF A COURSE (owner, 2026-09-18: tfrrs races were not
    #   on course pages at all). `meets` is anet-only, so the tfrrs side of
    #   a course is meets_tfrrs.venue_name -- and the first attempt at that
    #   fix filtered on COALESCE(m.course_name, mt.venue_name), which no
    #   index can serve, and made every course page crawl. The queries now
    #   resolve each feed by its own index and UNION the two; this is the
    #   one that side needs.
    #
    # ! (venue_name, sport) IN THAT ORDER. The lookup is `sport = 'XC' AND
    #   venue_name = %(course)s`, and the existence check above matches on
    #   the LEADING column -- sport-first would also be far less selective.
    ("meets_tfrrs",     "venue_name", "idx_meets_tfrrs_venue",
     "(venue_name, sport)"),
    # ★ ISSUE #17 (2026-08-27). Every course helper joins
    #   `results ON r.div_id = m.div_id` after filtering meets by
    #   course_name -- with no div_id-leading index the planner hash-joins
    #   by scanning all 39M result rows PER QUERY, ~6 queries per course:
    #   the measured 7-19s per course, and why the board build
    #   extrapolated past a day. With it, each query is a handful of
    #   index probes and the full --all build becomes an hour, not a day.
    ("results",         "div_id",  "idx_results_div", None),
    # ★ THE PREDICTION'S OWN LOOKUP (owner, 2026-09-16: the predictions page
    #   504'd on a championship field). _historyRows asks both result tables
    #   for one field's athletes by id -- and NEITHER TABLE HAD AN INDEX ON
    #   EITHER ID COLUMN, so each prediction seq-scanned 54M rows and then
    #   did it again for track.
    #
    # ! FOUR, BECAUSE THE FILTER HAS TWO BRANCHES AND THERE ARE TWO SPORTS.
    #   personResultsSql matches `person_id = ANY(...) OR (person_id IS NULL
    #   AND athlete_id = ANY(...))`; the planner BitmapOrs the two index
    #   scans, so both columns need one. Measured on a 2M-row stand-in:
    #   2,938 ms seq-scanned against 7.2 ms with these.
    ("results",         "person_id",  "idx_results_person", None),
    ("results",         "athlete_id", "idx_results_athlete", None),
    ("results_tf",      "person_id",  "idx_results_tf_person", None),
    ("results_tf",      "athlete_id", "idx_results_tf_athlete", None),
    # ★ AND THE anet TEAM ID, WHICH NOTHING INDEXED (owner, 2026-09-17:
    #   link_tfrrs_to_anet "hangs on results_tf"). It was not hanging: every
    #   query that filters `team_id = ANY(<2,034 college teams>)` was a
    #   SEQUENTIAL SCAN of a 54M-row table, and there are three of them --
    #   link_tfrrs_to_anet's evidence pass, build_school_identity's
    #   authoritativeStates AND its buildTeamStates. The first leg measured
    #   124 s on `results`; the join it feeds took 13 s, which is the shape
    #   of a missing index rather than a slow query.
    #
    # ! PARTIAL, BECAUSE THE COLUMN IS MOSTLY ABSENT. tfrrs rows carry no
    #   anet team, and 0 is anet's unattached sentinel rather than an id --
    #   every caller already writes `team_id IS NOT NULL AND team_id <> 0`,
    #   so an index over just those rows is a fraction of the size and
    #   matches the predicate exactly.
    ("results",         "team_id", "idx_results_team",
     "(team_id) WHERE team_id IS NOT NULL AND team_id <> 0"),
    ("results_tf",      "team_id", "idx_results_tf_team",
     "(team_id) WHERE team_id IS NOT NULL AND team_id <> 0"),
    ("athlete_season",  "school",  "idx_athlete_season_school", None),
    # ★ AND THE COMPOSITE THE FIELD ENDPOINT NEEDS (owner, 2026-09-15:
    #   /api/predict/field took 6.5 s on a 396-school meet). _squadsForYear
    #   filters `school = ANY(<396 values>) AND sport = ? AND year = ?`, and
    #   a plain (school) index leaves sport and year as heap filters -- so
    #   the planner reads every athlete-season of all 396 schools, across
    #   every sport and every year, to keep a fraction of them. The
    #   carry-forward then runs the same query again for last season.
    #
    # ⚠ build_ranking_results._CANONICAL_INDEXES ALREADY DECLARES THIS as
    #   as_school_idx (school, sport, year). It is missing from the live
    #   table because the run that would have built it did not reach its
    #   index step -- the same gap that left the school pages scanning
    #   61.6M rows after run 23. This builds it CONCURRENTLY on the table
    #   that is already there, and the next full rebuild makes it again.
    #
    # ! THE SPEC IS GIVEN, and that matters: the existence check matches on
    #   the LEADING column, so the plain (school) index above satisfies a
    #   specless entry and this composite would never be built.
    ("athlete_season",  "school",  "idx_athlete_season_school_sport_year",
     "(school, sport, year)"),
    # ★ HOW MANY RACES A TEAM HAS RUN THIS SEASON (roster.racesRun,
    #   2026-09-16). The carry-forward window asks that of every school on
    #   the page -- 396 of them for a championship field -- and the existing
    #   rr_school_sport_idx (school, sport) leaves `year` as a heap filter,
    #   so each school's ENTIRE history is read to count one season of it.
    # ! SPEC GIVEN, for the same reason as the entry above: idx_rr_school
    #   leads on (school) and would satisfy a specless entry forever.
    ("ranking_results", "school", "idx_rr_school_sport_year_meet",
     "(school, sport, year, meet_id)"),
    # ★ THE PREDICTIONS FIELD ENDPOINT (2026-09-01). meetField gained two
    #   lookups that filter athlete_season by PERSON -- _fieldGender, which
    #   reads a race's gender off the people who ran it, and
    #   _lastKnownRatings, which finds the last rating of everyone who is no
    #   longer racing. athlete_season was indexed on SCHOOL only, so both
    #   sequentially scanned the whole table, _fieldGender runs a second time
    #   inside _teamRosters, and picking several divisions fires all of them
    #   at once. That is the "insanely slowly if you press more than one"
    #   report: not the round trips, which are already parallel, but a full
    #   scan behind each of them.
    ("athlete_season",  "person_id", "idx_athlete_season_person", None),
    # ★ THE EVENTS WINDOW ON THE ATHLETES BOARD (2026-09-08,
    #   rankings._abilitySource). That board re-aggregates ranking_results
    #   over a distance range, and with sport='both' there is NO sport
    #   predicate -- so rr_board_rating_idx (pool, sport, year, ...) can only
    #   use `pool` as its leading equality and never reaches `year`. On
    #   college_m that is every row of the pool, on every page load: enough
    #   to hit the site's 55s statement_timeout and hand back the HTML error
    #   page the console reports as "Unexpected token '<'".
    #
    # ! LEADING (pool, year) SO THE YEAR IS USABLE WITHOUT A SPORT, with
    #   distance riding along so the range is an index condition rather than
    #   a heap filter. build_ranking_results creates the same index on the
    #   shadow at step 10; this is here so it can be built on the LIVE table
    #   now, without waiting for a rebuild.
    ("ranking_results", "pool", "rr_pool_year_dist_idx",
     "(pool, year, distance)"),
    # ★ THE TIME BOARDS, THE SAME GAP ONE BOARD LATER (owner, 2026-09-14:
    #   "best times/marks is pretty slow, same with performances").
    #   rr_board_time_idx is (pool, sport, year, time_seconds), so it
    #   reaches its ordering column only when a YEAR pins the third
    #   position -- and the Academic year filter defaults to Any. The
    #   default Best-times board therefore sorted every row of the pool to
    #   take the first fifty, which is exactly what rr_pool_rating_idx was
    #   added to stop on the rating boards; the time boards were never
    #   given the equivalent.
    #
    # ! TWO SHAPES, BECAUSE sport='both' EMITS NO PREDICATE AT ALL
    #   (rankings._whereClauses says why), so an index with sport in the
    #   second position cannot be used by the default board.
    #
    # ! AND DISTANCE IS INSIDE THE KEY, which it is not in the rating
    #   pair. The PR board RANKS THE CLOCK AT ONE DISTANCE -- that filter
    #   is what the board IS, not an optional narrowing -- so an index
    #   that cannot use it leaves a range between the equality and the
    #   ordering, and the sort comes back.
    #
    # ! build_ranking_results carries all three in _CANONICAL_INDEXES so a
    #   rebuild recreates them on the shadow; they are here so they can be
    #   built on the LIVE table now, the same arrangement
    #   rr_pool_year_dist_idx has.
    ("ranking_results", "pool", "rr_pr_time_idx",
     "(pool, distance, time_seconds)"),
    ("ranking_results", "pool", "rr_pr_sport_time_idx",
     "(pool, sport, distance, time_seconds)"),
    ("ranking_results", "pool", "rr_perf_dist_rating_idx",
     "(pool, sport, distance, speed_rating DESC)"),
    # ★ THE ATHLETE PICKER'S NAME MATCH (2026-09-01). /api/predict/athletes
    #   filters on `(first_name || ' ' || last_name) ILIKE '%tok%'`, and a
    #   LEADING wildcard cannot use a btree at all -- so every keystroke was
    #   a sequential scan of `athletes` joined to athlete_season. That is the
    #   owner's "search takes a while".
    #
    # ! A GIN TRIGRAM INDEX ON THE EXPRESSION, because the expression is what
    #   is searched -- one on first_name and last_name separately would not
    #   serve a match across the space between them. pg_trgm is already
    #   installed here; search_index's own search_text index is the same
    #   shape, which is why the topbar has never had this problem.
    #
    # ⚠ THE EXPRESSION MUST MATCH THE QUERY'S CHARACTER FOR CHARACTER, or the
    #   planner will not use it. If that concatenation changes in app.py,
    #   this changes with it.
    ("athletes", "first_name", "idx_athletes_name_trgm",
     "USING gin ((COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))"
     " gin_trgm_ops)"),
    # ★ THE FILTERED /meets VIEW (meets_filter.py). Its browse path filters
    #   meets/meets_tf by STATE -- the meet's state, not the athlete's -- and
    #   then counts results per surviving meet. Without these three the state
    #   filter seq-scans the meets tables and the per-meet count seq-scans
    #   39M result rows PER MEET, which is the difference between a page and
    #   a timeout.
    #
    # ! idx_results_meet EARNS ITS PLACE TWICE. get_meet_divisions
    #   (app.py) already does `WHERE r.meet_id = %(meet)s` on every meet page
    #   view with nothing meet_id-leading to serve it; results_tf has had its
    #   equivalent since the TF scorer needed one. This closes the XC half.
    ("meets",           "state",   "idx_meets_state", None),
    ("meets_tf",        "state",   "idx_meets_tf_state", None),
    # ★ THE MEET-NAME LOOKUPS (owner, 2026-09-02: "compare loads insanely
    #   slow", "meet pages load slowly"). compare.py, school.py and the TF
    #   meet page all read meets_tf BY meet_id -- compare once per plotted
    #   race, as a correlated subquery -- and meets_tf is 14M rows with no
    #   meet_id-leading index, so every one of those was a sequential scan.
    #   Same for the anet `meets` table, which every XC meet lookup probes.
    ("meets_tf",        "meet_id", "idx_meets_tf_meet", None),
    ("meets",           "meet_id", "idx_meets_meet", None),

    ("results",         "meet_id", "idx_results_meet", None),
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
                if not spec:
                    return True
                # ⚠ AN EXPRESSION INDEX CANNOT BE COMPARED AS TEXT. Postgres
                #   rewrites what you gave it -- adds ::text casts,
                #   re-parenthesises, schema-qualifies -- so
                #   norm(indexdef) == norm(spec) is False for a GIN trigram
                #   index that is in fact exactly the one asked for. Left as
                #   a text compare it reports MISS on every run and rebuilds
                #   nothing (CREATE ... IF NOT EXISTS no-ops by name), which
                #   is a report that lies rather than a broken build.
                #
                # ! FOR THOSE, THE TEST IS THE ACCESS METHOD AND THE OPERATOR
                #   CLASS, both of which survive the rewrite intact.
                if spec.strip().upper().startswith("USING"):
                    want = spec.lower()
                    have = idxdef.lower()
                    return (("using gin" in want) == ("using gin" in have)
                            and ("gin_trgm_ops" in want)
                            == ("gin_trgm_ops" in have))
                return norm(idxdef) == norm(spec)

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
