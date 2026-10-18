"""A college season is put in its campus's state (owner's boards,
2026-10-18: "Wyoming MI", "Louisville NE" -- the HS namesake was the
school's primary cluster).

    python -m pytest -q tests/test_campus_state.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import build_college_directory as D                              # noqa: E402
import build_ranking_results as B                                # noqa: E402


class FakeCur:
    def __init__(self, schools):
        self.schools = schools
        self.sql = []
        self.inserted = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._rows = [(s,) for s in self.schools] if "SELECT DISTINCT school" in sql else []
        if sql.lstrip().startswith("UPDATE"):
            self.rowcount = len(self.inserted)

    def fetchall(self):
        return self._rows


def test_college_seasons_take_the_campus_state(monkeypatch):
    entries = {"wyoming": [("WY", "WY")], "louisville": [("KY", "KY")],
               "trinity university": [("TX", "TX")], "trinity college": [("CT", "CT")]}
    monkeypatch.setattr(D, "loadDirectory", lambda cur, *f: entries)
    D._ANSWERS.clear()
    D._POSTINGS.clear()
    got = []
    monkeypatch.setattr(B.psycopg2.extras, "execute_values",
                        lambda cur, sql, pairs: (got.extend(pairs), cur.inserted.extend(pairs)))
    cur = FakeCur(["Wyoming", "Louisville", "Trinity"])
    n = B._campusStates(cur, "athlete_season_new")
    assert sorted(got) == [("Louisville", "KY"), ("Wyoming", "WY")]   # Trinity: two states, left alone
    assert n == 2
    upd = [s for s in cur.sql if s.lstrip().startswith("UPDATE")]
    assert upd and "starts_with(s.pool, 'college')" in upd[0]


def test_a_board_row_takes_the_athletes_state():
    B._UNITS["campus"] = {"colorado": [("CO", "CO")]}
    B._UNITS.pop("campus_cache", None)
    B._UNITS["si_states"] = {"Whitehall": {"MI"}, "Jesuit": {"CA", "OR"}}
    B._UNITS["si_primary"] = {"Whitehall": "MI", "Jesuit": "CA"}
    D._ANSWERS.clear()
    D._POSTINGS.clear()
    assert B._homeStateOf("Colorado", "WI", "college_m") == "CO"     # campus
    assert B._homeStateOf("Whitehall", "IN", "hs_m") == "MI"         # never seen in IN
    assert B._homeStateOf("Jesuit", "OR", "hs_m") == "OR"            # one of its own
    assert B._homeStateOf("Unattached", "WI", "hs_m") == "WI"
    assert B._homeStateOf("Nowhere HS", "TX", "hs_m") == "TX"        # unknown school
