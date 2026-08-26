"""
build_course_boards.py -- precompute every course page.

    python racecast/build_course_boards.py
    python racecast/build_course_boards.py --limit 200      # smoke run

Run from the PROJECT ROOT after 10_rankings (stampRowsHs reads
ranking_results). Pipeline step 12b.

★ COLD TIME IS THE PRODUCT. The course page recomputes a venue's whole
  history per view -- Mt. SAC measured 19s cold -- and request-time
  caches only help the SECOND viewer, which on this site rarely exists.
  This builds the finished render context for every course (the overview
  and each distance scope) into course_boards, so the first viewer costs
  one primary-key lookup.

★ ONE CODE PATH. The context comes from app.buildCourseCtx, the exact
  function the live route falls back to -- the precomputed page cannot
  drift from the live one, and a course scraped after the build still
  renders (live, then in-process cached) until the next pipeline.

Swap discipline: builds into course_boards_new, renames over the old
table at the end -- readers see the old boards until the new ones are
complete.
"""

import argparse
import json
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")

import psycopg2.extras
from database import getConn

_DDL = """
CREATE TABLE course_boards_new (
    course_name text NOT NULL,
    -- 0 is the overview; otherwise the rounded distance scope
    dist        int  NOT NULL DEFAULT 0,
    ctx         jsonb NOT NULL,
    built_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (course_name, dist)
)
"""


def _json(ctx):
    return psycopg2.extras.Json(
        ctx, dumps=lambda o: json.dumps(o, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="only this many courses (smoke runs)")
    args = ap.parse_args()

    # app imports flask; on the pipeline machine that is the same env the
    # site runs in. db_timing rides along harmlessly.
    from app import buildCourseCtx

    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("DROP TABLE IF EXISTS course_boards_new")
        cur.execute(_DDL)
        conn.commit()

        cur.execute("""
            SELECT course_name, count(DISTINCT div_id) AS n
            FROM meets
            WHERE course_name IS NOT NULL AND TRIM(course_name) <> ''
            GROUP BY course_name
            ORDER BY n DESC
        """)
        courses = [r["course_name"] for r in cur.fetchall()]
        if args.limit:
            courses = courses[:args.limit]
        print(f"building boards for {len(courses):,} courses")

        built = skipped = failed = 0
        batch = []
        for i, cname in enumerate(courses):
            try:
                ctx = buildCourseCtx(cur, cname, None)
                if not ctx.get("header"):
                    skipped += 1
                    continue
                batch.append((cname, 0, _json(ctx)))
                for d in ctx["dist_values"]:
                    batch.append((cname, d,
                                  _json(buildCourseCtx(cur, cname, d))))
                built += 1
            except Exception as exc:      # noqa: BLE001 -- one course, not the run
                conn.rollback()
                failed += 1
                print(f"  ! {cname}: {type(exc).__name__}: {exc}")
                continue
            if len(batch) >= 200:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO course_boards_new (course_name, dist, ctx)
                    VALUES %s ON CONFLICT (course_name, dist) DO NOTHING
                """, batch)
                conn.commit()
                batch = []
            if (i + 1) % 500 == 0:
                mins = (time.time() - t0) / 60
                print(f"  {i + 1:,}/{len(courses):,} courses "
                      f"({mins:.1f} min)", flush=True)
        if batch:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO course_boards_new (course_name, dist, ctx)
                VALUES %s ON CONFLICT (course_name, dist) DO NOTHING
            """, batch)
            conn.commit()

        # the swap: readers keep the old table until the new one is whole
        cur.execute("DROP TABLE IF EXISTS course_boards")
        cur.execute("ALTER TABLE course_boards_new RENAME TO course_boards")
        cur.execute("ALTER INDEX course_boards_new_pkey "
                    "RENAME TO course_boards_pkey")
        cur.execute("ANALYZE course_boards")
        conn.commit()

    mins = (time.time() - t0) / 60
    print(f"done: {built:,} courses built, {skipped:,} empty, "
          f"{failed:,} failed, {mins:.1f} min")


if __name__ == "__main__":
    main()
