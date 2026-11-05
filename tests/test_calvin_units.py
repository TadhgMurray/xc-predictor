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


def test_a_high_school_div_iii_is_no_ncaa_division():
    assert not [f for f in _u("VPA Div III State Championships", "VT") if f[0] == "division"]
    assert ("division", "NCAA DIII") in _u("NCAA Div III Regional")


def test_all_atlantic_keeps_its_name():
    assert ("region", "ALL-ATLANTIC") in _u("All-Atlantic Region Track & Field Championships", "NY")


def test_community_colleges_are_not_the_miaa():
    assert ("conference", "MIAA") not in _u("MCCAA / MIAA Cross Country Championships", "MI")
    assert C.JUCO_NAME_RX.search("Mott CC") and not C.JUCO_NAME_RX.search("Calvin")


def test_one_guest_vote_for_a_known_conference_does_not_win():
    """Marygrove: USCAA x2 and one MIAA in 2012."""
    from collections import Counter
    got = C.current(Counter({("USCAA", "2012"): 2, ("MIAA", "2012"): 1}), prefer={"MIAA"})
    assert got[0] == "USCAA"
    got = C.current(Counter({("JUNK", "2012"): 1, ("MIAA", "2011"): 3}), prefer={"MIAA"})
    assert got[0] == "MIAA"
