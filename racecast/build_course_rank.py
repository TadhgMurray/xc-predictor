"""
build_course_rank.py -- rank the courses themselves.

    python racecast/build_course_rank.py
    python racecast/build_course_rank.py --min-results 200

Run from the PROJECT ROOT, after the engine has written course_difficulties.
Reads course_difficulties and meets, writes course_rank.

★ DIFFICULTY IS ALREADY THE RANKING, AND IT MEANS SOMETHING EXACT. The solve
  re-centres every difficulty to mean zero on every iteration -- see
  speed_ratings' identifiability note -- so the number is not a score somebody
  chose, it is "how much harder than an average course this is". +0.05 says a
  field runs 5% slower here than the same field would on a neutral course.

★ AND IT IS ALREADY DISTANCE-NEUTRAL, which is what makes a board of courses
  possible at all. Difficulty is applied AFTER distance normalisation --
  rating = pool_mean / (normalized_time / (1 + difficulty)) -- so an 8k and a
  5k can sit in one list without the 8k looking hard merely for being long.

⚠ A DIFFICULTY FROM FEW RACES IS NOISE, AND THE BOARD REFUSES TO PUBLISH IT.
  The number comes from athletes who raced here and elsewhere; with a handful
  of results it is one hard afternoon, or one fast field, not a property of
  the ground. MIN_RESULTS is the floor, n_results and n_athletes ride along on
  every row so a reader can see how much is behind it, and the page says so.

⚠ CROSS COUNTRY ONLY. course_difficulties holds track venues too, keyed
  "TF:loc:<id>:in|out" -- but an indoor 200m oval and a hilly 5k are not two
  entries in one ranking of "courses", and the venue pages already handle
  track. This reads the "XC:" keys and nothing else.
"""

import io
import sys
import time
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn
from build_ranking_results import copyField

# ⚠ THE SAME 51 CODES THE OTHER BOARDS USE, and for the same reason: state is
#   the only geography stored, and a null test would keep exactly the foreign
#   meets it is meant to exclude. Imported rather than retyped.
from rankings import US_STATES

# Below this the difficulty is an anecdote. 100 results is roughly one large
# meet -- enough that a single unusual field cannot set the number, and low
# enough that a small course raced every year for a decade still qualifies.
MIN_RESULTS = 100

_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    course_name  text    NOT NULL PRIMARY KEY,
    state        text,
    distance_m   int,
    difficulty   double precision NOT NULL,
    n_results    int     NOT NULL,
    n_athletes   int     NOT NULL,
    n_meets      int     NOT NULL
);
"""

_COLUMNS = ("course_name", "state", "distance_m", "difficulty",
            "n_results", "n_athletes", "n_meets")

# ! THE KEY CARRIES THE SPORT AS A PREFIX, so the join is on the stripped
#   name and the filter is on the prefix. Matching "XC:%" and then trimming
#   five characters is the whole of it -- but it has to be done in that order,
#   because a course legitimately named "TF: The Farm" would otherwise be read
#   as a track venue.
_SOURCE_SQL = """
    WITH xc AS (
        SELECT substring(cd.course_name FROM 4) AS course_name,
               cd.difficulty, cd.n_results, cd.n_athletes
        FROM   course_difficulties cd
        WHERE  cd.course_name LIKE 'XC:%%'
          AND  cd.difficulty IS NOT NULL
          AND  cd.n_results >= %(min_results)s
    ), venue AS (
        -- ★ THE MODAL STATE AND DISTANCE, not the first or the average. A
        --   course is in one state; if two rows disagree one of them is a
        --   scrape error, and the common answer is the right one. Averaging
        --   the distance of a course that races a 5k and a 2-mile would
        --   invent a distance nobody ran.
        SELECT m.course_name,
               mode() WITHIN GROUP (ORDER BY m.state)    AS state,
               mode() WITHIN GROUP (ORDER BY m.distance) AS distance_m,
               count(DISTINCT m.meet_id)                 AS n_meets
        FROM   meets m
        WHERE  m.course_name IS NOT NULL
        GROUP  BY m.course_name
    )
    SELECT xc.course_name,
           v.state,
           v.distance_m,
           xc.difficulty,
           xc.n_results,
           xc.n_athletes,
           COALESCE(v.n_meets, 0) AS n_meets
    FROM   xc
    LEFT   JOIN venue v ON v.course_name = xc.course_name
    WHERE  v.state IS NULL OR v.state = ANY(%(states)s)
    ORDER  BY xc.difficulty DESC
"""


def toRow(r):
    """One database row -> the COPY tuple, in _COLUMNS order."""
    return (r["course_name"], r["state"],
            int(r["distance_m"]) if r["distance_m"] else None,
            float(r["difficulty"]), int(r["n_results"]),
            int(r["n_athletes"]), int(r["n_meets"]))


def build(conn, min_results):
    """Fill course_rank, replacing what is there.

    ⚠ SHADOW TABLE AND ONE SWAP, like every other builder here. A TRUNCATE
      plus a long insert serves an empty board for the length of the build,
      and a crash halfway serves half a board with no error anywhere.
    """
    import psycopg2.extras
    started = time.time()
    with conn.cursor() as cur:
        cur.execute(_DDL.format(name="course_rank"))
        cur.execute("DROP TABLE IF EXISTS course_rank_new")
        cur.execute(_DDL.format(name="course_rank_new"))
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_SOURCE_SQL, {"min_results": min_results,
                                  "states": list(US_STATES)})
        rows = cur.fetchall()

    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(copyField(v) for v in toRow(r)))
        buf.write("\n")
    buf.seek(0)
    with conn.cursor() as cur:
        if rows:
            cur.copy_expert(
                f"COPY course_rank_new ({', '.join(_COLUMNS)}) FROM STDIN", buf)
        cur.execute("DROP TABLE IF EXISTS course_rank_old")
        cur.execute("ALTER TABLE course_rank RENAME TO course_rank_old")
        cur.execute("ALTER TABLE course_rank_new RENAME TO course_rank")
        cur.execute("DROP TABLE course_rank_old")
        cur.execute("CREATE INDEX ON course_rank (difficulty)")
        cur.execute("CREATE INDEX ON course_rank (state)")
        cur.execute("ANALYZE course_rank")
    conn.commit()

    n_state = sum(1 for r in rows if r["state"])
    print(f"  courses   {len(rows):,} ranked (n_results >= {min_results})")
    print(f"  state     {n_state:,} located, {len(rows) - n_state:,} without "
          f"a state -- those came from course_difficulties with no meets row")
    if rows:
        print(f"  hardest   {rows[0]['difficulty']:+.3f}  "
              f"{rows[0]['course_name'][:44]}")
        print(f"  easiest   {rows[-1]['difficulty']:+.3f}  "
              f"{rows[-1]['course_name'][:44]}")
    print(f"  took      {time.time() - started:.0f}s")
    # ⚠ COMPARE `courses` AGAINST SELECT count(*) FROM course_difficulties
    #   WHERE course_name LIKE 'XC:%'. The gap is MIN_RESULTS doing its job --
    #   or doing too much of it, if a course you know is missing.
    print("  (compare against SELECT count(*) FROM course_difficulties "
          "WHERE course_name LIKE 'XC:%' -- the gap is MIN_RESULTS)")


def main():
    ap = argparse.ArgumentParser(
        description="Rank cross country courses by how much harder than "
                    "average they are. Run after the engine writes "
                    "course_difficulties.")
    ap.add_argument("--min-results", type=int, default=MIN_RESULTS,
                    help=f"results a course needs to be published "
                         f"(default {MIN_RESULTS})")
    args = ap.parse_args()
    with getConn() as conn:
        build(conn, args.min_results)


if __name__ == "__main__":
    main()
