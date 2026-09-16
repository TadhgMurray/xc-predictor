"""The carry-forward window: a roster is not "whoever has raced so far".

    python -m pytest -q tests/test_roster_window.py

Owner, 2026-09-15: "once it goes to a new season, it should take the roster
from last season, remove the seniors, but keep everybody else on the roster
until they either don't run the first 3 races or until they race for another
team... For example 2026 roster for a team after their first race should
include ppl from last year who got sick during the first race."

⚠ WHAT WAS WRONG WAS ALL-OR-NOTHING. The old test was "does this school have
  ANY current-season row": one athlete of a forty-person programme runs a
  September opener and the other thirty-nine vanish from the field, the squad
  picker and the school page at once. These pin the window instead -- the
  TEAM's first three races -- on both pages, because two answers to "who is
  on this team" is the fault being fixed, not a detail of it.
"""
import datetime
import os
import sys

# predict.py -> pool_view -> conversions -> database -> config resolves a
# connection at import. Nothing here connects; the value is never used.
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import predict                                                  # noqa: E402
import roster                                                   # noqa: E402
import school as school_mod                                     # noqa: E402
from season_year import academicYear                            # noqa: E402

LIVE = academicYear(datetime.date.today())


class ScriptedCursor:
    """Answers by which query it was handed: the race count, or a season's
    rows. `seasons` is {year: [row, ...]}; `meets` is {school: n}."""

    def __init__(self, seasons, meets):
        self.seasons = seasons
        self.meets = meets
        self.rows = []
        self.queries = []

    def execute(self, sql, params=None):
        params = params or {}
        flat = " ".join(sql.split())
        self.queries.append((flat, params))
        if "count(DISTINCT rr.meet_id)" in flat:
            want = params.get("schools") or []
            self.rows = [{"school": s, "n": n}
                         for s, n in sorted(self.meets.items()) if s in want]
            return
        rows = list(self.seasons.get(params.get("yr")
                                     or params.get("year"), []))
        # the graduating class, when the query asked to drop it
        if "%(term_keys)s" in flat:
            rows = [r for r in rows if (r.get("grade") or "") not in
                    ("12", "Sr.", "SR-4", "16")]
        # and anyone racing elsewhere in the active season
        if "NOT EXISTS" in flat:
            rows = [r for r in rows if not r.get("elsewhere")]
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def row(pid, name, rating, grade=None, school="Alpha", **kw):
    d = {"person_id": pid, "school": school, "grade": grade, "pool": "hs_m",
         "name": name, "mean_rating": rating, "best_rating": rating,
         "n_races": 4, "first_race": None, "last_race": None}
    d.update(kw)
    return d


# ------------------------------------------------------------------ #
# the rule itself
# ------------------------------------------------------------------ #

def test_the_window_is_the_teams_first_three_races():
    assert roster.CARRY_RACES == 3
    cur = ScriptedCursor({}, {"Alpha": 0, "Beta": 2, "Gamma": 3, "Delta": 9})
    carrying = roster.carryingSchools(
        cur, ["Alpha", "Beta", "Gamma", "Delta"], "XC", LIVE)
    # ! A SCHOOL WITH NO MEETS IS CARRYING -- that is the preseason case the
    #   carry-forward was written for in the first place.
    assert carrying == {"Alpha", "Beta"}, carrying
    # ⚠ AND THE THIRD RACE CLOSES IT, not the fourth. "Don't run the first 3"
    #   can only be decided once three have been run.
    assert "Gamma" not in carrying


def test_the_terminal_set_is_keys_and_lives_in_one_place():
    assert roster.TERMINAL_KEYS == ("12", "sr")
    # predict.py must not grow a second copy of it
    assert predict._TERMINAL_KEYS is roster.TERMINAL_KEYS


def test_the_transfer_test_names_the_other_school():
    """⚠ IT USED TO LEAN ON A PREMISE THAT THIS CHANGE REMOVES. The clause
    was "has a current-season row at all", which meant "at another school"
    only because the carry ran solely for schools with no current rows. The
    window runs it for schools that DO have them."""
    sql = roster.transferredClause("s")
    assert "c.school IS DISTINCT FROM s.school" in sql, sql
    assert "c.year   = %(active_yr)s" in sql, sql


# ------------------------------------------------------------------ #
# the predictions field
# ------------------------------------------------------------------ #

def test_a_returner_survives_the_opener():
    """The reported case: one race run, so the athlete who was sick for it is
    still on the roster."""
    cur = ScriptedCursor(
        seasons={LIVE:     [row(1, "Raced Already", 130.0)],
                 LIVE - 1: [row(1, "Raced Already", 128.0, grade="11"),
                            row(2, "Sick That Day", 135.0, grade="11"),
                            row(3, "Graduated",     140.0, grade="12"),
                            row(4, "Transferred",   132.0, grade="11",
                                elsewhere=True)]},
        meets={"Alpha": 1})
    out = predict._currentSquads(cur, ["Alpha"], "XC", LIVE)
    names = [r["name"] for r in out["Alpha"]]
    assert "Sick That Day" in names, names
    # ★ MERGED, NOT REPLACED: the athlete who HAS raced keeps this season's
    #   row, and appears exactly once.
    assert names.count("Raced Already") == 1, names
    assert [r["rating"] for r in out["Alpha"] if r["name"] == "Raced Already"] \
        == [130.0]
    # the two exits
    assert "Graduated" not in names, names
    assert "Transferred" not in names, names
    # ! AND IN RATING ORDER, not raced-then-carried order. Two ordered lists
    #   concatenated are not one ordered list.
    assert names == ["Sick That Day", "Raced Already"], names
    # only the carried row is marked as such
    marked = {r["name"] for r in out["Alpha"] if r.get("carried")}
    assert marked == {"Sick That Day"}, marked


def test_the_window_closes_after_three():
    cur = ScriptedCursor(
        seasons={LIVE:     [row(1, "Raced Already", 130.0)],
                 LIVE - 1: [row(2, "Never Turned Up", 135.0, grade="11")]},
        meets={"Alpha": 3})
    out = predict._currentSquads(cur, ["Alpha"], "XC", LIVE)
    assert [r["name"] for r in out["Alpha"]] == ["Raced Already"]
    # and nothing was even asked about last season
    assert not any(q[1].get("yr") == LIVE - 1 for q in cur.queries)


def test_one_school_racing_does_not_empty_another():
    """The all-or-nothing bug, at the scale it actually bit: a field of two
    schools where one has opened its season and one has not."""
    cur = ScriptedCursor(
        seasons={LIVE:     [row(1, "Alpha Runner", 130.0, school="Alpha")],
                 LIVE - 1: [row(2, "Alpha Returner", 120.0, grade="11",
                                school="Alpha"),
                            row(3, "Beta Returner", 125.0, grade="11",
                                school="Beta")]},
        meets={"Alpha": 1})
    out = predict._currentSquads(cur, ["Alpha", "Beta"], "XC", LIVE)
    assert sorted(out) == ["Alpha", "Beta"], sorted(out)
    assert [r["name"] for r in out["Beta"]] == ["Beta Returner"]
    assert len(out["Alpha"]) == 2, out["Alpha"]


# ------------------------------------------------------------------ #
# the school page -- "in the schools roster too"
# ------------------------------------------------------------------ #

def test_the_school_roster_carries_the_same_people():
    cur = ScriptedCursor(
        seasons={LIVE:     [row(1, "Raced Already", 130.0)],
                 LIVE - 1: [row(2, "Sick That Day", 135.0, grade="11"),
                            row(3, "Graduated", 140.0, grade="12")]},
        meets={"Alpha": 2})
    out = school_mod.schoolRoster(cur, "Alpha", LIVE, "XC")
    names = [r["name"] for r in out]
    assert names == ["Sick That Day", "Raced Already"], names
    carried = [r for r in out if r.get("carried")]
    assert len(carried) == 1
    # ! THE ROW SAYS WHICH SEASON ITS NUMBERS ARE FROM. There is nothing else
    #   to show about a season they have not raced, and an unlabelled stale
    #   rating reads as a current one.
    assert carried[0]["carried_year"] == LIVE - 1
    assert "Graduated" not in names


def test_a_picked_year_is_history_and_gets_no_carry():
    cur = ScriptedCursor(
        seasons={2019: [row(1, "Back Then", 120.0)],
                 2018: [row(2, "Year Before", 130.0, grade="11")]},
        meets={"Alpha": 0})
    out = school_mod.schoolRoster(cur, "Alpha", 2019, "XC")
    assert [r["name"] for r in out] == ["Back Then"]


def test_carry_can_be_turned_off():
    cur = ScriptedCursor(
        seasons={LIVE: [row(1, "Raced Already", 130.0)],
                 LIVE - 1: [row(2, "Sick That Day", 135.0, grade="11")]},
        meets={"Alpha": 0})
    out = school_mod.schoolRoster(cur, "Alpha", LIVE, "XC", carry=False)
    assert [r["name"] for r in out] == ["Raced Already"]
