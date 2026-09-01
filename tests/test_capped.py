"""Issue #57 point 2: a capped query must say the cap bit.

The entry calls this "the only one that lets the site show a partial answer as
if it were a complete one" -- schoolMeets and get_course_meets stop at 2000
rows, schoolBest and schoolTopAthletes at 100, and none of them used to say
so. Worse, the staged reveal then ran out AT the cap and looked like the end
of the data.

    python tests/test_capped.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))

from capped import Capped, fetchCapped          # noqa: E402

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


class FakeCur:
    def __init__(self, n):
        self.n = n

    def fetchall(self):
        return list(range(self.n))


# ---- 1. the helper ------------------------------------------------------ #
r = fetchCapped(FakeCur(101), 100)
ok(len(r) == 100, "a cap that bit returns exactly the limit")
ok(r.truncated, "and reports it")
ok(r.shown == 100, "and can name the limit it applied")

r = fetchCapped(FakeCur(100), 100)
ok(len(r) == 100 and not r.truncated,
   "exactly the limit is NOT truncated -- the +1 row is what proves it")

r = fetchCapped(FakeCur(7), 100)
ok(len(r) == 7 and not r.truncated, "a short list is not truncated")
ok(fetchCapped(FakeCur(0), 100) == [], "an empty result is still a list")

# It IS a list, which is what lets every existing caller and template keep
# working without being edited.
ok(isinstance(r, list), "Capped is a list")
ok(r[:3] == [0, 1, 2] and list(reversed(r))[0] == 6,
   "slicing and iteration are untouched")
ok(not Capped().truncated, "the default is 'not truncated', never unset")

# ---- 2. the LIMIT and the fetch are one change -------------------------- #
# fetchCapped cannot see the query, so passing the plain limit would leave
# truncated permanently False -- the old silent behaviour, not a crash.
for mod, n in (("school.py", 3), ("app.py", 1)):
    src = read("racecast", mod)
    calls = len(re.findall(r"return fetchCapped\(cur, limit\)", src))
    plus = len(re.findall(r"limit \+ 1", src))
    ok(calls == n, f"{mod}: expected {n} capped readers, found {calls}")
    ok(plus >= calls,
       f"{mod}: {calls} fetchCapped calls but only {plus} 'limit + 1' -- "
       f"a reader that does not over-fetch reports truncated=False forever")

# ---- 3. the templates say it -------------------------------------------- #
cap = read("racecast", "templates", "_capped.html")
ok("rows.truncated" in cap, "the notice is conditional on the cap biting")
ok("rows.shown" in cap, "and names how many are shown")
for t in ("school.html", "course.html"):
    ok("capped_note(" in read("racecast", "templates", t),
       f"{t} renders the notice")

# ---- 4. one fold, one step (point 1) ------------------------------------ #
for t in ("school.html", "course.html", "school_prs.html", "compare.html",
          "_tf_points.html"):
    src = read("racecast", "templates", t)
    for m in re.finditer(r'data-step="(\d+)"', src):
        ok(m.group(1) == "20", f"{t}: step {m.group(1)}, expected 20")
    # ⚠ ONLY row-hidden FOLDS. school_prs also folds its COURSE CHIPS at 8
    #   with chip-hidden and its own yb-more button -- a different control
    #   with a different job, and sweeping it in here made this test fail on
    #   a file it had no business policing.
    for m in re.finditer(r"row-hidden'\|safe if (?:loop\.index0|i) >= (\d+)",
                         src):
        ok(m.group(1) == "15", f"{t}: fold at {m.group(1)}, expected 15")
    ok("Show 50 more" not in src, f"{t}: an old step size survived in a label")

# ---- 5. it goes back (point 3) ------------------------------------------ #
js = read("racecast", "static", "staged.js")
ok("Show less" in js, "the footer becomes a collapse once fully expanded")
ok(".more-row" not in js or "remove()" not in js,
   "the footer is no longer removed -- removing it is what made 'more' "
   "one-way, with nothing left to press")
ok("revealed.push" in js and "revealed.forEach" in js,
   "collapse re-hides exactly what was revealed, rather than re-deriving "
   "the fold and drifting from whatever the template chose")

# ---- 6. one spelling (point 4) ------------------------------------------ #
for t in ("course.html", "school.html", "school_prs.html"):
    src = read("racecast", "templates", t)
    for stale in ("View all on the rankings board", "View all times on",
                  "All meets at this course", "View all meets",
                  "View all in rankings"):
        ok(stale not in src, f"{t}: '{stale}' is a fifth spelling of View all")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  a cap that bit is reported, one row of overhead ... OK")
print("  every capped reader over-fetches by one ........... OK")
print("  the templates say so, only when it bit ............ OK")
print("  one fold (15) and one step (20) everywhere ........ OK")
print("  the staged reveal collapses again ................. OK")
print("  one spelling of 'View all' ........................ OK")
print("\nall capped-table checks passed")
