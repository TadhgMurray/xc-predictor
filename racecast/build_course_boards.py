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
from database import getConn, _Utf8Json
from dbfast import swapTable

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
    return _Utf8Json(
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
    # ★ SHARDS (2026-09-02). Step 12b took 41,047 s single-file: one
    #   process, one course at a time, most of it waiting on the database.
    #   --prepare makes the shadow table once; N --shard K/N workers each
    #   build every Nth course into it side by side; --finish swaps. The
    #   pipeline runs three shards. A plain run (no flag) still does the
    #   whole thing in one process.
    ap.add_argument("--prepare", action="store_true",
                    help="create the empty shadow table and stop")
    ap.add_argument("--shard", default=None,
                    help="K/N: build courses K, K+N, K+2N... into the "
                         "existing shadow table; no swap")
    ap.add_argument("--finish", action="store_true",
                    help="swap the shadow table in and stop")
    args = ap.parse_args()

    if args.prepare:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS course_boards_new")
                cur.execute(_DDL)
            conn.commit()
        print("course_boards_new: empty shadow table ready", flush=True)
        return
    if args.finish:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM course_boards_new")
                n = cur.fetchone()[0]
                if not n:
                    sys.exit("course_boards_new is empty: refusing to swap")
            conn.commit()
            swapTable(conn, "course_boards",
                      renames=[("course_boards_new_pkey", "course_boards_pkey")])
        print(f"course_boards: swapped in, {n:,} rows", flush=True)
        return
    shard_k = shard_n = None
    if args.shard:
        shard_k, shard_n = (int(x) for x in args.shard.split("/"))
        assert 0 <= shard_k < shard_n

    # app imports flask; on the pipeline machine that is the same env the
    # site runs in. db_timing rides along harmlessly.
    from app import buildCourseCtx

    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if not args.resume and shard_n is None:
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
            ORDER BY n DESC, course_name
        """)
        courses = [r["course_name"] for r in cur.fetchall()]
        if not args.all and args.limit:
            courses = courses[:args.limit]
        if shard_n is not None:
            # the same deterministic list in every worker; each takes
            # every Nth entry, so the big courses spread across workers
            courses = courses[shard_k::shard_n]
            print(f"shard {shard_k}/{shard_n}: {len(courses):,} courses",
                  flush=True)
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

        # the swap: readers keep the old table until the new one is whole.
        # A shard leaves it to --finish, once every shard is done.
        if shard_n is None:
            conn.commit()
            swapTable(conn, "course_boards",
                      renames=[("course_boards_new_pkey", "course_boards_pkey")])

    mins = (time.time() - t0) / 60
    print(f"done: {built:,} courses built, {skipped:,} empty, "
          f"{failed:,} failed, {mins:.1f} min")


if __name__ == "__main__":
    main()
