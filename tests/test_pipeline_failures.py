"""The four failures of the 2026-09-01 pipeline run, each pinned.

A six-hour run ended with FAILED STEPS: 04_grade_sanity 10_rankings
10b_school_ids 12b_course_pages 17_checklist. They were four independent
bugs, and three of them had the same shape: a code path that had never
actually executed.

    python tests/test_pipeline_failures.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ================================================================== #
# 1. 04_grade_sanity -- "column reference person_id is ambiguous"
# ================================================================== #
GS = read("engine", "grade_sanity.py")
real = re.search(r'_AGE_BAND_REAL = \("""(.*?)"""\)', GS, re.S).group(1)
none = re.search(r'_AGE_BAND_NONE = \("""(.*?)"""\)', GS, re.S).group(1)

# The whole bug: the two fragments are alternatives for the SAME query, so
# whatever one exposes the other must expose. The real one used to join
# age_band_result whole -- (sport, result_id, person_id, grade) -- against a
# SELECT that names person_id unqualified.
ok("age_band_result ab" not in real,
   "the real fragment must not expose age_band_result's whole row: it "
   "carries person_id and grade, which _BUILD names unqualified")
ok("SELECT result_id FROM age_band_result" in real,
   "it exposes result_id alone, matching the stand-in")
ok(re.search(r"WHERE sport = '\{sport\}'", real) is not None,
   "and still filters to one sport, which the PK serves")

exposed = lambda frag: set(re.findall(r"AS (\w+)", frag)) | \
                       set(re.findall(r"SELECT (\w+) FROM", frag))
ok(exposed(real) == exposed(none),
   f"the two fragments expose different columns: "
   f"{exposed(real)} vs {exposed(none)} -- the query compiles with one and "
   f"not the other, which is exactly how this reached production")

# Only ab.result_id is ever read, so nothing else is needed from it.
uses = set(re.findall(r"ab\.(\w+)", GS))
ok(uses <= {"result_id"},
   f"_BUILD reads ab.{uses - {'result_id'}} -- the fragment no longer "
   f"provides those")

# ================================================================== #
# 2 + 3. 10_rankings and 10b_school_ids -- DeadlockDetected
# ================================================================== #
# A swap renames tables a live reader holds in the OPPOSITE order: an ABBA
# deadlock, which Postgres's detector fires on (deadlock_timeout, 1s) long
# before lock_timeout (3s). Catching only LockNotAvailable threw away six
# hours of completed work at the very last statement.
SWAPS = [("racecast", "build_ranking_results.py"),
         ("racecast", "build_school_identity.py"),
         ("backfill", "backfill_normalize.py"),
         ("engine", "merge_column.py")]
for parts in SWAPS:
    src = read(*parts)
    name = parts[-1]
    for m in re.finditer(r"except\s*\(?([^\n:]*LockNotAvailable[^\n:]*)\)?:",
                         src):
        ok("DeadlockDetected" in m.group(1),
           f"{name}: a swap catches LockNotAvailable but not "
           f"DeadlockDetected -- they are the same event ('a reader was in "
           f"the way'), both roll the transaction back, and both retry")
    ok("DeadlockDetected" in src, f"{name}: no deadlock handling at all")

# Both tables locked in ONE statement, so the failure mode is a timeout the
# retry understands rather than a deadlock it did not.
BR = read("racecast", "build_ranking_results.py")
SI = read("racecast", "build_school_identity.py")
ok("LOCK TABLE ranking_results, athlete_season" in BR,
   "swapIn takes both locks up front, in one statement")
ok(BR.index("LOCK TABLE ranking_results, athlete_season")
   < BR.index("ALTER TABLE ranking_results RENAME TO ranking_results_old"),
   "and does it BEFORE the first rename, or the window is still open")

# build_school_identity had no retry whatsoever, and dropped live tables.
ok("_SWAP_ATTEMPTS" in SI, "build_school_identity has a retry loop at all")
ok("LOCK TABLE person_home_state, school_identity" in SI,
   "and takes both its locks together")
ok(SI.index("BEGIN") < SI.index("DROP TABLE IF EXISTS {t}"),
   "its swap is one transaction -- half a swap leaves school_identity "
   "dropped with nothing in its place")
ok("ANALYZE" in SI.split("time.sleep(_SWAP_BACKOFF)")[-1],
   "ANALYZE runs after the commit, not inside the exclusive window")

# ================================================================== #
# 4. 12b_course_pages -- UntranslatableCharacter on SQL_ASCII
# ================================================================== #
CB = read("racecast", "build_course_boards.py")
ok("ensure_ascii=False" in CB,
   "course boards must emit raw UTF-8, not \\uXXXX escapes: this cluster is "
   "SQL_ASCII and Postgres cannot translate an escape into it")
jf = CB[CB.index("def _json("):CB.index("def main(")]
ok("json.dumps" in jf and "ensure_ascii=False" in jf,
   "the fix is on the dumps that feeds psycopg2.extras.Json")
ok("SQL_ASCII" in CB, "and the reason is written down where the next person "
                      "will change it back")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  grade_sanity: both age-band joins expose one column ... OK")
print("  every swap retries on deadlock, not just timeout ..... OK")
print("  both swaps take their locks in one statement ......... OK")
print("  course boards emit raw UTF-8 for an SQL_ASCII server . OK")
print("\nall pipeline-failure checks passed")
