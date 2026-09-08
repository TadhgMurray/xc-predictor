"""A division is a FIELD on the team board, not a slice of one.

Owner, 2026-09-08: "when you do like a div/section filter the place numbers
should update, so it's actually 1,2,3 not just like 45, 150, 200."

Two things were wrong, and only one of them was the numbering:

  1. rankings.js shows the division / region / conference / section combos
     by POOL, not by board -- syncUnitRows keys off $("pool").value alone --
     so the Teams tab has always offered them, buildQuery has always SENT
     them, and teams.parseFilters never read them. Picking Division III did
     nothing, silently.

  2. The renumbering itself is refused on purpose. teams.py: "renumbering
     them 1, 2, 3 would invent a championship that was never run", and the
     alternative it names is to hold the meet -- raceStored. So the fix is
     not to renumber a sliced board, it is to let the division REACH the
     field, after which serveBoard races it and there is one first place
     because one race was run.

    python tests/test_team_unit_filters.py
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
from rankings import UNIT_FILTERS                                 # noqa: E402


# no database and no school list: the filter is a column comparison now


def _filters(**over):
    f = {"sport": "XC", "pool": "college_m", "scope": "usa", "state": [],
         "school": [], "year": [], "board_scope": "usa", "min_athletes": 5,
         "limit": 50, "offset": 0, "span": "alltime"}
    for k in UNIT_FILTERS:
        f[k] = []
    f.update(over)
    return f


# ---- 1. the filters are parsed at all --------------------------------- #
class _Args(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)

    def getlist(self, k):
        v = dict.get(self, k)
        return [v] if v is not None else []


f, err = teams.parseFilters(_Args(sport="XC", pool="college_m",
                                  division="NCAA DIII"))
ok(err is None, f"parseFilters rejected a division: {err}")
ok(f is not None and f.get("division") == ["NCAA DIII"],
   f"division not parsed: {f.get('division') if f else None}")
for key in UNIT_FILTERS:
    ok(f is not None and key in f, f"parseFilters drops the {key} filter")


# ---- 2. a division narrows the FIELD ---------------------------------- #
params = {}
where = teams._fieldWhere(_filters(division=["NCAA DIII"]), params)
ok('t."division" = ANY(%(division_vals)s)' in where,
   "a division must narrow the field, so raceStored races only those teams")
ok(params.get("division_vals") == ["NCAA DIII"],
   "the value must be bound, not interpolated")

# ⚠ THE REGRESSION: on the TEAM'S OWN stamped column, never on a list of
#   school names. Owner, 2026-09-08: "DI school in DIII filter: Washington
#   WA ... NCAA DI". "Washington" is DI in WA and DIII in MO, so a name
#   match pulled the DI team into a DIII board -- the same class of error
#   the (school, state) team key exists to prevent.
ok("_schools" not in where and "t.school = ANY" not in where,
   "a unit filter must not match by school NAME: shared names put a DI "
   "school in the DIII field")

# every unit filter team_season stores reaches the field, not just division
for key in UNIT_FILTERS:
    p = {}
    w = teams._fieldWhere(_filters(**{key: ["X"]}), p)
    cols = [c for c in __import__("rankings").UNIT_COLUMNS[key]
            if c in teams.TEAM_UNIT_COLS]
    if cols:
        ok(f"%({key}_vals)s" in w,
           f"the {key} filter never reaches the field")
    else:
        ok(f"%({key}_vals)s" not in w,
           f"{key} is not stored on team_season and must be skipped, not "
           f"matched against a column that is not there")


# ---- 3. a school stays a SUBJECT -------------------------------------- #
#   The distinction is the whole point: race the school filter and every
#   filtered squad comes first. _fieldWhere must not touch it.
p = {}
w = teams._fieldWhere(_filters(school=["Amherst"]), p)
ok("school" not in w,
   "a school name must not narrow the field -- see _fieldWhere's docstring")
p2 = {}
w2 = teams._subjectWhere(_filters(school=["Amherst"]), p2)
ok("lower(btrim(t.school))" in w2, "a school is still a subject filter")


# ---- 4. no filter set adds no clause ---------------------------------- #
p3 = {}
w3 = teams._fieldWhere(_filters(), p3)
for key in UNIT_FILTERS:
    ok(f"{key}_vals" not in p3,
       f"an unset {key} filter must add no bind (it would kill the index)")


# ---- 5. the stored columns match what the build writes ---------------- #
#   Three lists have to agree or a filter binds a column that is not there:
#   the DDL, build_team_season.UNIT_COLS, and teams.TEAM_UNIT_COLS.
BTS = io.open(os.path.join(ROOT, "racecast", "build_team_season.py"),
              encoding="utf-8").read()
import re as _re
_build_units = _re.search(r"UNIT_COLS = \((.*?)\)", BTS, _re.S).group(1)
_build_units = tuple(_re.findall(r'"([a-z_]+)"', _build_units))
ok(set(_build_units) == set(teams.TEAM_UNIT_COLS),
   f"build writes {sorted(_build_units)} but teams filters "
   f"{sorted(teams.TEAM_UNIT_COLS)}")
_ddl = BTS[BTS.index("_DDL = "):BTS.index("_COLUMNS = ")]
for c in _build_units:
    ok(f'{c}' in _ddl, f"team_season DDL has no {c} column")
# and both passes stamp them -- the all-time board is the one the site opens
ok(BTS.count("_unitTuple(t)") == 2,
   "both toRows and toAlltimeRows must stamp the units")


# ---- 6. and the numbering is NOT hand-rolled -------------------------- #
#   If a later change renumbers a sliced board instead of racing it, the
#   module's own argument is being ignored.
TE = io.open(os.path.join(ROOT, "racecast", "teams.py"), encoding="utf-8").read()
ok("raceStored(getTeamField(cur, f))" in TE,
   "serveBoard must still race the field it selected")
# ! SCOPED TO THE TEAM-BOARD FUNCTIONS. getCoursePerformances further down
#   uses row_number() for its own per-race dedup, which is a different
#   board and a different question.
_team_half = TE[TE.index("def getTeamRankings("):TE.index("#  SINGLE RACES")]
ok("row_number()" not in _team_half,
   "the team board must not renumber a sliced board -- hold the meet "
   "instead (see the module docstring)")
ok("row_number()" in TE[TE.index("#  SINGLE RACES"):],
   "the course board's own row_number should still be there -- if this "
   "fails the slice above is looking at the wrong half of the file")


if __name__ == "__main__":
    for m in failed:
        print("FAIL:", m)
    print(f"\n{'FAILED' if failed else 'ok'}: {len(failed)} failure(s)")
    sys.exit(1 if failed else 0)
