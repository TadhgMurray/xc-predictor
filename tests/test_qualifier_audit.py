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
