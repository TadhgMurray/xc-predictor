"""merge_school_names.rosterPairs: one college team by its runners, whatever
the names (owner, 2026-11-05: "U. of Chicago" / "Chicago", 622 shared)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import merge_school_names as M                                # noqa: E402


def test_a_shared_roster_proposes_and_merges_the_pair():
    ros = {"U. of Chicago": set(range(646)), "Chicago": set(range(24, 1489)),
           "Luther Alumni": set(range(5000, 5030)), "Luther": set(range(5000, 5400)),
           "Tufts": set(range(9000, 9300))}
    lvl = {n: "college" for n in ros}
    pairs = M.rosterPairs(ros, lvl)
    assert ("Chicago", "U. of Chicago", "roster") in pairs
    assert not any("Alumni" in a or "Alumni" in b for a, b, _r in pairs)
    merged, _refused = M.judge(pairs, ros, {"Chicago": {"IL"}}, level_of=lvl)
    assert any({m[0], m[1]} == {"Chicago", "U. of Chicago"} for m in merged)


def test_a_transfer_or_two_is_not_a_team():
    ros = {"Iowa": set(range(400)), "Iowa State": set(range(390, 800))}
    assert M.rosterPairs(ros, {n: "college" for n in ros}) == []
    # and high schools are never paired by roster
    ros = {"A HS": set(range(100)), "A High": set(range(100))}
    assert M.rosterPairs(ros, {n: "hs" for n in ros}) == []
