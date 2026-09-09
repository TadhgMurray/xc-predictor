"""Every distance a course races gets its own board row.

Owner, 2026-09-09: "the course board keeps only the most run distance of a
course, because it removes the other distances as 'courses called the same
thing at diff places'. It should include all of them."

★ TWO KEYS, ONE OF THEM WRONG. course_difficulties is keyed
  (canonical_id, DISTANCE) -- Mt. San Antonio College has 4828m with 5,831
  results, 5230m with 1,179 and 5000m with 360, three real cells with three
  real difficulties. build_course_rank de-duplicated on the canonical id
  ALONE and ordered by n_results DESC, so the 4828m row won and the other two
  disappeared from the board.

! THE NAME-COLLISION GUARD IS STILL THERE, ONLY NARROWED. Two venues with no
  canonical id sharing a name still collapse to one row PER DISTANCE, which
  is what it was for. It was never meant to merge a course's distances.

    python tests/test_course_board_distances.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "racecast", "build_course_rank.py"),
              encoding="utf-8").read()
APP = io.open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


bare = "\n".join(ln.split("--")[0] for ln in SRC.splitlines())

# ---- 1. distance is in the de-dup key --------------------------------- #
m = re.search(r"DISTINCT ON \((.*?)\)\s*\n", bare, re.S)
ok(m is not None, "the DISTINCT ON is gone entirely -- the guard still has a "
                  "job, it just must not eat distances")
if m:
    key = " ".join(m.group(1).split())
    ok("key_distance" in key,
       f"the distance must be part of the de-dup key, got: {key}")
    ok("canonical_id" in key,
       "...and the canonical id must still be, or two venues sharing a name "
       "collapse")

# ! THE ORDER BY MUST LEAD WITH THE SAME KEY or Postgres rejects the query
#   outright -- DISTINCT ON requires it.
o = bare[bare.rindex("ORDER  BY"):]
ok("key_distance" in o.split("n_results")[0],
   "ORDER BY must lead with the DISTINCT ON key, distance included, or "
   "Postgres refuses the statement")

# ---- 2. and the emitted key is unique per (course, distance) ----------- #
#   ⚠ course_key goes into a KEYED table. Leaving it canonical-id-only while
#     emitting one row per distance would collide three ways on Mt. SAC and
#     abort the build.
ok("':d' ||" in bare or "':d'||" in bare,
   "course_key must carry the distance too, or the rows it now emits "
   "collide in the keyed table")


# ---- 3. the venue page no longer hides a missing column ---------------- #
#   ⚠ get_course_cell_difficulties joins course_difficulties.canonical_id,
#     which is a MIGRATION rather than part of the base DDL, and the bare
#     except turned that into "-" on every distance of every course page
#     with nothing in the log.
i = APP.index("def get_course_cell_difficulties")
blk = APP[i:i + 3000]
ok("except Exception as e" in blk,
   "the exception must be captured, not discarded")
ok("app.logger" in blk or "print(" in blk,
   "a query that silently returns {} renders '-' everywhere and says "
   "nothing -- it has to report")
ok("canonical_id" in blk and "build_course_canonical" in blk,
   "and name the likely cause, since the fix is a specific script")
# ! STILL NOT FATAL. A course page without its difficulty is worth rendering.
ok("return {}" in blk, "but it must still degrade rather than 500")


if __name__ == "__main__":
    for msg in failed:
        print("FAIL:", msg)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
