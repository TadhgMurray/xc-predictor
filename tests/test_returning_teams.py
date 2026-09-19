"""Team rankings with some grades taken out -- next year's squad.

Owner, 2026-09-08: "I think you should be able to get team rankings by
removing certain grades."

⚠ THE STORED BOARD CANNOT ANSWER THIS. team_season.ratings is seven bare
  numbers -- no person, no grade -- so there is nothing to take a senior
  OUT of, and racing "the stored seven minus the last two" would be a guess
  about which runners the grades belonged to. So this path goes back to
  athlete_season, where a row is one athlete and carries their grade, and
  re-scores with the same rankTeams the build uses.

    python tests/test_returning_teams.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


import teams                                                     # noqa: E402
import rankings                                                  # noqa: E402
from team_rank import rankTeams                                  # noqa: E402


class _Args(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)

    def getlist(self, k):
        v = dict.get(self, k)
        return [v] if v is not None else []


def _f(**over):
    f = {"pool": "hs_m", "sport": "XC", "year": [2025], "exclude_grade": ["12"],
         "state": [], "board_scope": "usa", "school": [], "span": "season",
         "min_athletes": 5}
    for k in rankings.UNIT_FILTERS:
        f[k] = []
    f.update(over)
    return f


# ---- 1. it needs exactly one year ------------------------------------- #
#   "Next year's team" is a question about a season. An all-time board with
#   the seniors removed is a field of squads from thirty different seasons,
#   which would look like a working answer.
f, err = teams.parseFilters(_Args(sport="XC", pool="hs_m",
                                  exclude_grade="12", year="2025"))
ok(err is None and f["exclude_grade"] == ["12"],
   f"one year with an exclusion was rejected: {err}")
ok(teams.gradeExcluded(f), "gradeExcluded must see the filter")

# ! THE WORDING MOVED, THE RULE DID NOT. parseFilters now FILLS the current
#   season when it knows one ("it needs one season, and now it picks one
#   instead of refusing"), and refuses only when no season can be found at all
#   -- which is this call, in a test process with no season loaded. So the
#   assertion is on the refusal and on its subject, not on the old sentence
#   "one year", which is what it used to match.
_, err2 = teams.parseFilters(_Args(sport="XC", pool="hs_m", exclude_grade="12"))
ok(err2 and ("season" in err2 or "one year" in err2),
   f"an exclusion with no year must be refused, not silently served: {err2!r}")

_, err3 = teams.parseFilters(_Args(sport="XC", pool="hs_m",
                                   exclude_grade="12", year="2024,2025"))
ok(err3, "an exclusion across two years must be refused")

f4, err4 = teams.parseFilters(_Args(sport="XC", pool="hs_m", year="2025"))
ok(err4 is None and not teams.gradeExcluded(f4),
   "no exclusion means the ordinary stored board")


# ---- 2. excluded by MEANING, not spelling ----------------------------- #
#   The feeds write a senior as 12, 12th, Sr, Senior, SR-4 or 16. Comparing
#   raw text leaves every senior a feed spelled "Sr" in the returning squad
#   -- the bug the athlete board's grade filter already had.
p = {}
w = teams._athleteFieldWhere(_f(), p)
ok(p.get("ex_grade_keys") == ["12"],
   f"the excluded grade must go through _gradeKey: {p.get('ex_grade_keys')}")
ok("array_position" in w and "LIKE 'sr%%'" in w,
   "the exclusion must use the grade-key expression, not a text compare")

# ...and the expression is QUALIFIED on both columns it names
ok("s.grade" in w, "the grade key must be qualified to athlete_season")
ok("s.pool LIKE" in w, "the grade key names pool too, and it must be qualified")
ok(" pool LIKE" not in w.replace("s.pool LIKE", ""),
   "a bare `pool` would be ambiguous once the name lateral is joined")

# a row with no grade stays in: unknown is not evidence of leaving
ok("IS NULL" in w, "an athlete with no grade recorded must not be dropped")


# ---- 3. the qualified builder is honest -------------------------------- #
ok(rankings.GRADE_KEY_SQL == rankings.gradeKeySql(),
   "the module constant must be the unqualified build of the same rule")
q = rankings.gradeKeySql("s")
ok("trim(grade)" not in q, "gradeKeySql left a bare grade column behind")
ok("'college%%'" in q,
   "the 'college%' literal must survive qualification -- a string replace "
   "of the word `pool` would have eaten it")


# ---- 4. the same eligibility as the build ----------------------------- #
BTS = io.open(os.path.join(ROOT, "racecast", "build_team_season.py"),
              encoding="utf-8").read()
import re                                                        # noqa: E402
build_min = int(re.search(r"^MIN_RACES = (\d+)", BTS, re.M).group(1))
ok(teams.TEAM_MIN_RACES == build_min,
   f"teams.TEAM_MIN_RACES is {teams.TEAM_MIN_RACES}, the build uses "
   f"{build_min} -- the returning board would rank a different squad")

TE = io.open(os.path.join(ROOT, "racecast", "teams.py"), encoding="utf-8").read()

# ⚠ THE POOL CEILING IS PAIRED, WHICHEVER WAY IT GOES. Owner, 2026-09-09:
#   "There probably shouldn't be a straight 150 cap btw ... just remove it
#   for now flag if an issue later" -- so today it drops nobody in either
#   place. What must never happen is one place dropping and the other not:
#   the returning board would score a squad on five runners while the
#   ordinary board scored it on six, and two boards would disagree about a
#   team that had not changed. So this checks the BEHAVIOUR of both, not the
#   presence of a name -- build_team_season still calls withinPool to COUNT.
ns = {"withinPool": lambda pool, rating: False}   # everything is implausible
exec(compile(BTS[BTS.index("def railCheckedRows"):BTS.index("def countingRows")],
             "build_team_season", "exec"), ns)
stats = {"above_rail": {}}
kept = list(ns["railCheckedRows"](
    [{"pool": "hs_m", "rating": 999.0}, {"pool": "hs_m", "rating": 1.0}], stats))
ok(len(kept) == 2,
   "the build's rail must pass every row through -- it counts, it does not "
   "drop")
ok(stats["above_rail"] == {"hs_m": 2},
   "...and it must still COUNT them, or the build stops being able to say "
   "what the old rail would have removed")
ok("withinPool" not in TE,
   "the build's rail drops nobody, so the returning board must not drop "
   "anybody either -- if the ceiling comes back, it comes back in both")
ok("teamState(" in TE,
   "the returning board must resolve the school's state, or BYU is three "
   "teams here and one on the board beside it")
ok("rankTeams(field)" in TE,
   "the returning board must score with the same rankTeams the build uses")


# ---- 5. scoring really does change when a grade goes ------------------ #
#   The point of the feature, on real rankTeams: a squad that loses its
#   seniors falls behind one that does not.
def squad(school, ratings, grades):
    return [{"school": school, "state": "CA", "rating": r, "grade": g,
             "pool": "hs_m", "person_id": f"{school}-{i}", "name": f"{school} {i}"}
            for i, (r, g) in enumerate(zip(ratings, grades), start=1)]


senior_heavy = squad("Seniorville", [150, 149, 148, 147, 146, 145, 144],
                     ["12", "12", "12", "12", "12", "11", "11"])
young = squad("Youngtown", [148, 147, 146, 145, 144, 143, 142],
              ["10", "10", "11", "11", "10", "11", "10"])

full = {t["school"]: t for t in rankTeams(senior_heavy + young)}
ok(full["Seniorville"]["rank"] == 1,
   "with everyone racing, the stronger squad should win")

returning = [a for a in senior_heavy + young if a["grade"] != "12"]
after = {t["school"]: t for t in rankTeams(returning)}
ok(after.get("Youngtown", {}).get("rank") == 1,
   "with the seniors removed the younger squad should come first -- that is "
   "the whole feature")
ok("Seniorville" not in after,
   "a squad with two runners left cannot field five and must not score")


# ---- 6. the control that carries the feature now ---------------------- #
#
# ⚠ THIS SECTION USED TO ASSERT A CONTROL THAT WAS DELIBERATELY REMOVED, and
#   it asserted it with JS.index(...) at MODULE level -- so the removal turned
#   the whole file into a ValueError at import, and under pytest that is a
#   collection error rather than a failure. It read:
#
#       i = JS.index('q.set("exclude_grade"')
#
#   The teams-only "Graduating (removed)" combo it looked for was replaced by
#   the ONE grade combo, which on the teams board BUILDS the squad ("tick 9 and
#   the board is every school's freshman team, and next year's squad is the
#   same control with the seniors left unticked"). So the UI no longer sends
#   exclude_grade at all, on purpose, while teams.py still accepts it for links
#   already out in the world -- see racecast/templates/rankings.html and
#   teams.py:666. What follows asserts THAT, which is the live contract.
JS = io.open(os.path.join(ROOT, "racecast", "static", "rankings.js"),
             encoding="utf-8").read()
HTML = io.open(os.path.join(ROOT, "racecast", "templates", "rankings.html"),
               encoding="utf-8").read()
ok('q.set("exclude_grade"' not in JS,
   "the UI must not send exclude_grade any more -- the grade combo replaced "
   "the teams-only control")
ok('data-field="exclude_grade"' not in HTML,
   "the removed combo must not come back in the template")
ok('data-field="grade"' in HTML,
   "the grade combo IS the returning-teams feature now")
TEAMS = io.open(os.path.join(ROOT, "racecast", "teams.py"),
                encoding="utf-8").read()
ok('_multiValue(args, "exclude_grade")' in TEAMS,
   "the API must keep accepting exclude_grade for links already sent")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
