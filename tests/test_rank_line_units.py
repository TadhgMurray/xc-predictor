"""
A nested unit chip on the athlete rank line must carry its parent into the
filter, or it counts a population it does not name.

The bug this pins (owner, 2026-08-31):

    Nation #520 - CA #52 - CA D2 #18 - NCS #4 - NCS D2 #11
                                       ^^^^^^   ^^^^^^^^^

NCS D2 is INSIDE NCS and can never rank worse than it. #76 scoped HS unit
chips to the athlete's state, which fixed CA D2; it does not reach one level
deeper, so "D2" scoped only to CA counted every D2 school in California across
every section.
"""
import os
import sys

# app.py deliberately refuses to import without credentials (issue #16). It
# does not connect at import time, so placeholders are enough.
os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import app                                                     # noqa: E402


UNITS = {
    "state_div":   {"kind": "state_div", "raw": "D2", "label": "CA D2"},
    "section":     {"kind": "section", "raw": "NCS", "label": "NCS"},
    "section_div": {"kind": "section_div", "raw": "D2", "label": "NCS D2"},
    "league":      {"kind": "league", "raw": "EBAL", "label": "EBAL"},
}


def test_section_div_carries_its_section():
    assert app.unitParentArgs("section_div", UNITS) == {"section": "NCS"}
    print("  section_div carries section=NCS .................. OK")


def test_scopes_with_no_parent_add_nothing():
    # state_div is already narrowed by the state on boardArgs(True); section
    # and league are direct children of the state. Only section_div nests.
    for kind in ("state_div", "section", "league", "division", "conference"):
        assert app.unitParentArgs(kind, UNITS) == {}, kind
    print("  state_div / section / league / college add none ... OK")


def test_missing_parent_is_not_invented():
    """A school with a section_div but no resolved section must not crash or
    fabricate a filter -- it just stays as wide as it was."""
    assert app.unitParentArgs("section_div", {}) == {}
    assert app.unitParentArgs("section_div", None) == {}
    assert app.unitParentArgs(
        "section_div", {"section_div": UNITS["section_div"]}) == {}
    print("  a missing section is not invented ................. OK")


def test_the_nesting_map_is_what_we_think():
    # If a future unit kind nests (say district inside section), it belongs
    # here rather than in a second ad-hoc branch.
    # area joined 2026-09-02: NCS -> Tri-Valley -> EBAL, ranked inside NCS
    assert app._UNIT_PARENT == {"section_div": "section",
                                "area": "section"}, app._UNIT_PARENT
    print(f"  nesting map {app._UNIT_PARENT} ....... OK")


if __name__ == "__main__":
    for fn in [test_section_div_carries_its_section,
               test_scopes_with_no_parent_add_nothing,
               test_missing_parent_is_not_invented,
               test_the_nesting_map_is_what_we_think]:
        fn()
    print("\nall rank-line unit tests passed")


def test_area_carries_its_section():
    units = dict(UNITS)
    units["area"] = {"kind": "area", "raw": "TRI-VALLEY", "label": "Tri-Valley"}
    assert app.unitParentArgs("area", units) == {"section": "NCS"}
    print("  area carries section=NCS ......................... OK")


if __name__ == "__main__":
    test_area_carries_its_section()
