"""scripts/qualifier_audit.py's pure parts: a feeder is the top of a race
that only part of went on, its rule is k teams and m individuals, and next
year's field is that rule on next year's feeder. No database.

    python -m pytest -q tests/test_qualifier_audit.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import qualifier_audit as Q                                  # noqa: E402


def _regional():
    # schools A..F, seven each; A best, F worst; times interleaved by team
    rows, pid = [], 0
    for rank in range(7):
        for k, s in enumerate("ABCDEF"):
            pid += 1
            rows.append((pid, s, 1000.0 + 10 * k + 60 * rank))
    return rows


def test_team_order_by_the_top_five():
    assert Q.teamOrder(_regional())[:3] == ["A", "B", "C"]


def test_a_feeder_and_its_rule():
    F = _regional()
    # state took teams A and B (all seven) and the best two of the rest
    went = {p for p, s, _t in F if s in "AB"}
    others = [p for p, s, _t in sorted(F, key=lambda r: r[2]) if s not in "AB"][:2]
    R = [(p, s, t) for p, s, t in F if p in went or p in others]
    assert Q.feederRule(R, F) == (2, 2)
    assert Q.qualifiers(F, 2, 2) == went | set(others)
    # a race everyone went on to is not a feeder
    assert Q.feederRule(F, F) is None
    # nor one whose slowest went on
    slow = sorted(F, key=lambda r: -r[2])[:8]
    assert Q.feederRule(slow, F) is None


def test_which_learned_routes_are_kept():
    def r(name, race="Varsity", n=5, f=0.9, p=0.9, **kw):
        return dict(meet_name=name, race=race, rounds=[{}] * n, own_found=f, own_precise=p, **kw)
    assert Q.keepRoute(r("IHSA Class 1A State XC Championships"))
    assert Q.keepRoute(r("USATF Region 15 Junior Olympic Cross Country", race="8U", n=2))
    assert not Q.keepRoute(r("Coastal Conference Finals", n=1))
    assert not Q.keepRoute(r("Cross Country City League Championship", n=2))
    assert Q.keepRoute(r("Big Sky Conference Championships", n=6))       # a strong league route
    assert not Q.keepRoute(r("2025 NCAA DII Southeast Region Cross Country", n=3))
    assert not Q.keepRoute(r("NXR Midwest Regional Championships"))
    assert not Q.keepRoute(r("State Meet", next_found=0.55))
    assert not Q.keepRoute(r("State Meet", f=0.7, p=0.7))


def test_a_name_that_is_not_a_qualified_level_needs_the_league_bar():
    def r(name, n=5, f=0.9, p=0.9):
        return dict(meet_name=name, race="Varsity", rounds=[{}] * n, own_found=f, own_precise=p)
    assert not Q.keepRoute(r("NAIC Championships", n=4, f=0.77, p=0.72))
    assert not Q.keepRoute(r("MSHSL Section 1A Championships", n=5, f=0.76, p=0.74))
    assert not Q.keepRoute(r("FCPS Middle School XC Championship", n=7, f=0.93, p=0.62))
    assert Q.keepRoute(r("AHSAA XC State Championship", n=4, f=0.82, p=0.68))
    assert Q.keepRoute(r("UIL Region IV Championship XC25", n=7, f=0.71, p=0.82))
    assert Q.keepRoute(r("2025 3C2A SoCal XC Championships", n=3, f=0.77, p=0.73))
    assert not Q.keepRoute(r("2025 European XC Championships", n=8, f=0.91, p=0.9))
