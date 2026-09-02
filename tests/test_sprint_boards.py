"""Issue #46: sprint boards were empty by construction, and the fix must not
let unrated rows onto a RATING board.

The engine rates nothing under 800m on purpose -- the distance law is not
fitted there -- so sprints had no speed_rating and never reached
ranking_results, which is why every sprint PR board was empty. They now reach
it unrated. The danger is the other direction: `ORDER BY speed_rating DESC`
puts NULLs FIRST in Postgres, so an unguarded rating board would show blank
rows above the best race in the corpus.

    python tests/test_sprint_boards.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


BR = read("racecast", "build_ranking_results.py")
RK = read("racecast", "rankings.py")

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


# ---- 1. only sprints get in unrated ------------------------------------- #
ok("LEFT JOIN tmp_sprint_events se ON se.event_short = r.event_short" in BR,
   "the TF query joins the sprint whitelist")
ok("WHERE (r.speed_rating IS NOT NULL OR se.event_short IS NOT NULL\n"
   "               OR COALESCE(r.is_field, 0) = 1)" in BR,
   "and admits a row that is rated, a known timed event, or a field "
   "event (for its mark) -- nothing else")
ok("WHERE r.speed_rating IS NOT NULL" in BR,
   "the XC query is unchanged: cross country has no unrated distance")

# The whitelist is built from the engine's own event->distance mapping, not
# from a pattern match invented here.
sp = BR[BR.index("def prepareSprintEvents("):BR.index("def prepareTfStateTemp(")]
ok("_tfDistance(ev)" in sp,
   "sprint events are resolved with the shared mapping, not a LIKE")
ok("_SPRINT_MAX_DISTANCE" in sp and "800.0" in BR,
   "the floor is the engine's own 800m, named once")
ok("0 < float(d) < _SPRINT_MAX_DISTANCE" in sp,
   "a zero or negative distance is not a sprint")

# ---- 2. the second lock, in Python -------------------------------------- #
pr = BR[BR.index("if row.speed_rating is None:"):]
pr = pr[:pr.index("rating = float(row.speed_rating)")]
ok('sport != "TF"' in pr, "an unrated XC row is still dropped")
ok("distance is None" in pr, "an unrated row with no distance is dropped")
ok("_SPRINT_MAX_DISTANCE" in pr,
   "and the distance is re-checked here, independently of the SQL whitelist")
ok('_GATE[sport]["time_only"]' in pr, "the build counts what it published")
ok(BR.count('"time_only": 0') == 2, "both sports carry the counter")
ok("time-only (#46)" in BR, "and the build reports it")

# The published tuple must be positionally identical to the rated one --
# COPY is positional, and a short tuple would silently shift every column.
rated = re.search(r"return \(sport,\n\s+row\.result_id,", BR)
ok(rated is not None, "the rated return is still there to compare against")
timed = re.search(r"return \(sport, row\.result_id, row\.person_id, pool, None,",
                  BR)
ok(timed is not None, "the time-only return starts with the same five columns")
# three unrated returns now -- field mark, hurdle/steeple, sprint -- and
# every one carries the same unit spread in the same place
ok(pr.count("*_unitsOf(school, row.state)") == 3,
   "and every published tuple carries the same unit spread")

# ---- 3. the column must accept NULL ------------------------------------- #
ok("ALTER COLUMN speed_rating DROP NOT NULL" in BR,
   "the shadow is built LIKE the live table, so the constraint has to go "
   "there first or the first sprint fails the COPY")

# ---- 4. athlete_season stays rating-only -------------------------------- #
_i = BR.index("_ATHLETE_SEASON_SQL = f\"\"\"")
seas = BR[_i:BR.index('GROUP BY person_id, pool, sport, year;', _i)]
ok("WHERE speed_rating IS NOT NULL" in seas,
   "the season aggregate excludes unrated rows -- count(*) would inflate "
   "n_races, and decayed_rating's denominator counts rows its numerator "
   "skips, which dilutes every sprinter's rating toward zero")

# ---- 5. THE DANGEROUS ONE: no rating board may see a NULL --------------- #
ok("WHERE  speed_rating IS NOT NULL {where}" in RK,
   "the rating board excludes NULL in the CANDIDATE set, before the LIMIT")
ok("p.speed_rating DESC NULLS LAST" in RK,
   "and the tiebreak cannot float one to the top either")

# A standing guard: every ranking_results read that orders by speed_rating
# descending must either exclude NULL or say NULLS LAST.
for name in ("rankings.py", "compare.py", "school.py", "teams.py",
             "panels.py", "app.py"):
    src = read("racecast", name)
    for m in re.finditer(r"ORDER\s+BY\s+[\w.]*speed_rating\s+DESC(?!\s+NULLS)",
                         src):
        # ⚠ THE WINDOW LOOKS BOTH WAYS, and getting that wrong made this
        #   test cry wolf twice. A window function orders INSIDE the SELECT
        #   list, so its statement's WHERE clause is BELOW the ORDER BY, not
        #   above it. A backwards-only scan reported two guarded queries as
        #   unguarded.
        window = src[max(0, m.start() - 1600):m.start() + 1600]
        ok("speed_rating IS NOT NULL" in window or "speed_rating > 0" in window,
           f"{name}: 'ORDER BY speed_rating DESC' at offset {m.start()} "
           f"neither excludes NULL nor says NULLS LAST -- NULLs sort FIRST "
           f"under DESC, so unrated rows would head the list")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  only known sprints enter unrated, checked twice ... OK")
print("  the published row is positionally identical ....... OK")
print("  athlete_season still aggregates rated rows only ... OK")
print("  no rating board can see a NULL rating ............. OK")
print("\nall sprint-board checks passed")
