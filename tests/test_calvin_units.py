"""Calvin's units (owner, 2026-11-05): 'NCAA DIV III' is a division, an
NCAA regional names an NCAA region, the Michigan MIAA is a conference, and a
lone runner at another league's meet votes no conference."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import check_school_units as C                                  # noqa: E402


def _u(name, state="OH"):
    return C.parseUnits(name, "", college=True, state=state)


def test_div_iii_is_a_division_and_its_region_is_clean():
    got = _u("NCAA DIV III Great Lakes Region XC Championship")
    assert ("division", "NCAA DIII") in got
    assert ("region", "GREAT LAKES") in got


def test_a_regional_titled_great_lakes_valley_is_the_great_lakes_region():
    got = _u("NCAA DIII Great Lakes Valley XC Regional (Entries on DA)", "KY")
    assert ("region", "GREAT LAKES") in got
    assert C._ncaaRegion("SOUTH CENTRAL") == "SOUTH CENTRAL"
    assert C._ncaaRegion("MIDWEST") == "MIDWEST"
    assert C._ncaaRegion("MID-ATLANTIC") == "MID-ATLANTIC"


def test_the_michigan_miaa_is_a_conference():
    assert ("conference", "MIAA") in _u("NCAA D3 MIAA XC Championships", "MI")
    assert ("conference", "MIAA") in _u("NCAA DIII Michigan Intercollegiate (MIAA) Championships 2009", "MI")
    assert ("conference", "MIAA") not in _u("MIAA Cross Country Championships", "MO")


class _Cur:
    def __init__(self, rows):
        self.rows, self.out = rows, []

    def execute(self, sql, args=None):
        self.out = self.rows if "FROM results r" in sql and "meets m " in sql else []

    def fetchall(self):
        return self.out

    def fetchone(self):
        return (None,)


def test_a_lone_runner_votes_no_conference(monkeypatch):
    monkeypatch.setattr(C, "_levelMaps", lambda cur: {})
    monkeypatch.setattr(C, "_nameAliases", lambda cur: {})
    rows = [("Calvin", "2019", "CIAA Cross Country Championships", "Men", "NC", "anet", 1.0, 1),
            ("Hope", "2019", "Landmark Conference Championships", "Men", "PA", "anet", 1.0, 7)]
    votes, *_x = C.buildVotes(_Cur(rows), "XC")
    assert "conference" not in votes.get(("Calvin", "NC"), {})
    assert votes[("Hope", "PA")]["conference"]
