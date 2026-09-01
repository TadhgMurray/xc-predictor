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


# ⚠ ensure_ascii=False, AND THIS DATABASE IS WHY. The cluster is SQL_ASCII,
#   which performs no encoding conversion -- it stores whatever bytes it is
#   given. json.dumps defaults to ensure_ascii=True and writes a non-ASCII
#   character as a \uXXXX ESCAPE; Postgres must then translate that escape
#   into the server encoding to parse the jsonb, and SQL_ASCII has nothing to
#   translate it to. The build died on one athlete:
#
#       unsupported Unicode escape sequence
#       DETAIL: Unicode escape value could not be translated to the server's
#               encoding SQL_ASCII
#       CONTEXT: JSON data ... "name": "Mikaela Marie Brabb\u00e9..."
#
#   Emitting the character as raw UTF-8 bytes instead sidesteps the
#   translation entirely, which is the same path every accented name already
#   takes through the text columns on this cluster.
#
# ! ONE NAME KILLED 1,200 COURSES. The per-course try/except above catches a
#   failure while BUILDING a course; this one happens at the INSERT, in a
#   batch of 200, so it escapes that guard and takes the whole step with it.
def _json(ctx):
    return psycopg2.extras.Json(
        ctx, dumps=lambda o: json.dumps(o, default=str, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    # ⚠ THE DEFAULT IS THE HEAD, NOT THE WORLD. Measured 2026-08-26:
    #   19,922 courses, and the top 500 alone took an hour -- the full
    #   sweep extrapolates past a day, inside a pipeline step. The
    #   precompute exists for the venues whose history is big enough to
    #   be slow cold; the long tail renders live in a few hundred ms and
    #   never needed a board. --all remains for a deliberate full build.
    ap.add_argument("--limit", type=int, default=1200,
                    help="build the N biggest courses (default 1200)")
    ap.add_argument("--all", action="store_true",
                    help="every course, however long it takes")
    ap.add_argument("--resume", action="store_true",
                    help="keep what course_boards_new already holds and "
                         "skip those courses (continue an interrupted run)")
    args = ap.parse_args()

    # app imports flask; on the pipeline machine that is the same env the
    # site runs in. db_timing rides along harmlessly.
    from app import buildCourseCtx

    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if not args.resume:
            cur.execute("DROP TABLE IF EXISTS course_boards_new")
        cur.execute("SELECT to_regclass('public.course_boards_new')")
        if next(iter(cur.fetchone().values())) is None:
            cur.execute(_DDL)
        conn.commit()
        done = set()
        if args.resume:
            cur.execute("SELECT DISTINCT course_name FROM course_boards_new")
            done = {r["course_name"] for r in cur.fetchall()}
            print(f"resuming: {len(done):,} courses already built",
                  flush=True)

        cur.execute("""
            SELECT course_name, count(DISTINCT div_id) AS n
            FROM meets
            WHERE course_name IS NOT NULL AND TRIM(course_name) <> ''
            GROUP BY course_name
            ORDER BY n DESC
        """)
        courses = [r["course_name"] for r in cur.fetchall()]
        if not args.all and args.limit:
            courses = courses[:args.limit]
        if done:
            courses = [c for c in courses if c not in done]
        print(f"building boards for {len(courses):,} courses",
              flush=True)

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
                print(f"  ! {cname}: {type(exc).__name__}: {exc}",
                      flush=True)
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
