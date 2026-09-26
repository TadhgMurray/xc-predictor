"""meet_compile.teamStates: anet's team id decides a row's school state
before any inference (owner, 2026-09-26: "use use use")."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import meet_compile as M                                        # noqa: E402


class Cur:
    """Answers to_regclass with `tables`, team_identity and
    school_athlete_state with the rows given."""
    def __init__(self, teams, sas=(), tables=("team_identity",
                                              "school_athlete_state")):
        self.teams, self.sas, self.tables = teams, list(sas), set(tables)
        self._next = None

    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            name = sql.split("'")[1].replace("public.", "")
            self._next = [{"ok": name in self.tables}]
        elif "FROM team_identity" in sql:
            self._next = [{"team_id": t, "school": s, "state": st}
                          for t, (s, st) in self.teams.items()]
        elif "FROM school_athlete_state" in sql:
            self._next = [{"school": a, "person_id": b, "state": c}
                          for a, b, c in self.sas]
        else:
            self._next = []

    def fetchone(self):
        return self._next[0] if self._next else None

    def fetchall(self):
        return self._next


class TeamStates(unittest.TestCase):
    def test_the_team_id_names_the_state(self):
        rows = [{"school": "De La Salle", "team_id": 111, "person_id": 1},
                {"school": "De La Salle", "team_id": 222, "person_id": 2}]
        got = M.teamStates(Cur({111: ("De La Salle", "CA"),
                                222: ("De La Salle", "LA")}), rows)
        self.assertEqual(got, {111: "CA", 222: "LA"})

    def test_a_team_that_names_another_school_is_ignored(self):
        rows = [{"school": "Monte Vista", "team_id": 111, "person_id": 1}]
        self.assertEqual(M.teamStates(Cur({111: ("Cal High", "CA")}), rows), {})

    def test_tfrrs_rows_without_a_team_id_are_untouched(self):
        rows = [{"school": "Tufts", "team_id": None, "person_id": 1}]
        self.assertEqual(M.teamStates(Cur({}), rows), {})

    def test_the_fact_outranks_the_inference(self):
        rows = [{"school": "De La Salle", "team_id": 111, "person_id": 1}]
        cur = Cur({111: ("De La Salle", "CA")},
                  sas=[("De La Salle", 1, "LA")])
        M.stampSchoolStates(cur, rows)
        self.assertEqual(rows[0]["school_state"], "CA")


if __name__ == "__main__":
    unittest.main()
