"""Adding a team must use the SAME squad rule as the field it joins.

Owner, 2026-09-01: "No one from Woodbridge College has raced this season."
It was never about that school. schoolSquad was its own query with a hard
`year = current`, while every other path to a squad goes through
_currentSquads -- which carries last season's roster forward when the new one
is empty (#82), ages out the 12s/SRs, and drops transfers already racing
elsewhere (#83).

So all preseason, the meet's own teams came back full and adding ANY team
reported it empty. Two implementations of "who runs here now" is two answers.

    python tests/test_add_team_squad.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = io.open(os.path.join(ROOT, "racecast", "predict.py"),
             encoding="utf-8").read()

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def body(name):
    i = PY.index(f"def {name}(")
    return PY[i:PY.index("\ndef ", i + 1)]


sq = body("schoolSquad")

# 1. It delegates rather than re-querying.
ok("_currentSquads(" in sq,
   "schoolSquad goes through _currentSquads, the one rule for who is on a "
   "squad")
ok("cur.execute(" not in sq,
   "and has no query of its own left -- a second query is a second answer, "
   "and that is exactly what this cost")
ok("s.year   = %(yr)s" not in sq and "athlete_season" not in sq,
   "the hard current-season filter is gone")

# 2. The carry-forward it now inherits is the real thing, not a copy.
# _currentSquads decides WHICH season to read; _squadsForYear, which it
# calls, applies the aging-out and the transfer rule to that season.
cs = body("_currentSquads")
sy = body("_squadsForYear")
ok("academicYear" in cs,
   "_currentSquads still tests whether the current season is OVER -- that "
   "test is what makes the carry-forward fire at all (#82)")
ok("_squadsForYear" in cs, "and it reads a season through _squadsForYear")
ok("exclude_terminal" in cs,
   "asking the previous season to drop its 12s/SRs")
# ! THE TWO CLAUSES LIVE IN roster.py NOW, because the school page applies
#   the same rule and two spellings of "who graduated" is two rosters for one
#   team. What _squadsForYear must still do is APPLY them.
for what, needle in (("ages out the terminal grades",
                      "roster.graduatedClause"),
                     ("drops transfers racing elsewhere",
                      "roster.transferredClause")):
    ok(needle in sy, f"_squadsForYear still {what} -- schoolSquad now "
                     f"depends on it doing so")

# ★ AND THE CARRY-FORWARD IS A WINDOW, NOT AN EMPTINESS TEST (owner,
#   2026-09-15). "Has this school any current-season row" meant one athlete
#   running a September opener took the whole rest of the programme off the
#   squad; the window is the TEAM's first three races.
ok("carryingSchools" in cs,
   "_currentSquads asks roster.py how many races the team has run")
# ⚠ THE OLD TEST, BY ITS CODE AND NOT BY ITS NAME. The word "missing" still
#   appears in _currentSquads -- in the comments explaining what this
#   replaced, which is exactly where it belongs.
ok("if not squads.get(" not in cs,
   "and the all-or-nothing emptiness test is gone, not sitting beside it")

# 3. The response shape the page reads is unchanged.
ok('"runners"' in sq, "the payload still has runners")
ok('"season_year"' in sq, "and the season it answered for")
ok('k != "school"' in sq,
   "_currentSquads puts `school` on every entry; the page keys off the "
   "top-level one, so it is not sent twice")

# 4. Gender still reaches it -- a school has a boys team and a girls team,
#    and the endpoint filters to one side.
ok(re.search(r"_currentSquads\([^)]*gender=gender", sq) is not None,
   "the gender is passed through, or 'add from squad' offers both teams")

# 5. The limit is applied after, not by a LIMIT the delegate does not take.
ok("[:limit]" in sq, "the caller's limit still bounds the list")

if failed:
    for f in failed:
        print("  FAIL " + f)
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)

print("  adding a team uses the field's own squad rule ..... OK")
print("  which carries forward, ages out and drops movers .. OK")
print("  the payload and the gender filter are unchanged ... OK")
print("\nall add-team squad checks passed")
