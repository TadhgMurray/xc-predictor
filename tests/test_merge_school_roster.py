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


def test_unattached_for_a_college_is_not_its_team():
    ros = {"Adams State": set(range(55)), "UNAT-Adams State": set(range(51)),
           "Augustana (S.D.)": set(range(100, 127)), "UNA-Augustana (S.D.)": set(range(100, 127))}
    assert M.rosterPairs(ros, {n: "college" for n in ros}) == []


# ---- a high school renamed (owner, 2026-11-08, /athlete/26510177) --------
def test_a_renamed_high_school_is_one_school():
    """'Covenant School' (2024), 'Covenant Christian' (2025), 'The Covenant
    School of Jacksonville' (2026): the same team under three names."""
    ros = {"Covenant School": set(range(0, 40)),
           "Covenant Christian": set(range(20, 60)),
           "The Covenant School of Jacksonville": set(range(40, 80))}
    lvl = {n: "hs" for n in ros}
    pairs = M.renamedPairs(ros, lvl)
    assert ("Covenant Christian", "Covenant School", "renamed") in pairs
    assert ("Covenant Christian", "The Covenant School of Jacksonville", "renamed") in pairs
    fl = {n: {"FL"} for n in ros}
    merged, _r = M.judge(pairs, ros, fl, level_of=lvl)
    assert len(merged) == 2


def test_a_transfer_or_a_common_word_is_not_a_rename():
    lvl = {}
    # two runners moving between two schools: not a team
    ros = {"Covenant School": set(range(40)), "Covenant Day": set(range(38, 80))}
    lvl = {n: "hs" for n in ros}
    assert M.renamedPairs(ros, lvl) == []
    # many shared runners but only a common word in common ("Christian")
    ros = {"Grace Christian": set(range(40)), "Faith Christian": set(range(0, 40))}
    lvl = {n: "hs" for n in ros}
    assert M.renamedPairs(ros, lvl) == []


def test_a_rename_needs_one_known_state_and_two_high_schools():
    ros = {"Covenant School": set(range(40)), "Covenant Christian": set(range(20, 60))}
    lvl = {n: "hs" for n in ros}
    pairs = M.renamedPairs(ros, lvl)
    merged, refused = M.judge(pairs, ros, {"Covenant School": {"FL"}, "Covenant Christian": {"GA"}},
                              level_of=lvl)
    assert not merged and refused
    merged, refused = M.judge(pairs, ros, {}, level_of=lvl)        # no state known
    assert not merged and "one known state" in refused[0][7]
    assert M.renamedPairs(ros, {n: "ms" for n in ros}) == []      # middle schools: never


def test_a_rename_without_a_team_record_takes_its_runners_state():
    """owner's dry run, 2026-11-08: Reading / Reading Memorial refused for
    want of a team record's state"""
    ros = {"Reading": set(range(0, 3000)), "Reading Memorial": set(range(2300, 3700))}
    lvl = {n: "hs" for n in ros}
    pairs = M.renamedPairs(ros, lvl)
    assert pairs
    merged, _r = M.judge(pairs, ros, {"Reading": {"MA"}}, level_of=lvl,
                         identity_states={"Reading Memorial": {"MA"}})
    assert merged
    # a name its runners spread over many states gets no state, so no merge
    merged, refused = M.judge(pairs, ros, {"Reading": {"MA"}}, level_of=lvl, identity_states={})
    assert not merged


def test_a_schools_unattached_runners_are_not_its_team():
    """owner's dry run, 2026-11-08: 'Amarillo HS UNATT', 'Ada Unat'"""
    ros = {"Amarillo": set(range(60)), "Amarillo HS UNATT": set(range(0, 14)),
           "Forest Hills Eastern Ada": set(range(100, 160)), "Ada Unat": set(range(100, 113))}
    assert M.renamedPairs(ros, {n: "hs" for n in ros}) == []


def test_a_name_the_feed_cut_off_is_the_full_name():
    """owner, 2026-11-08: 'The Covenant School of Jackson'"""
    names = ["The Covenant School of Jacksonville", "The Covenant School of Jackson",
             "Alabama School of Cyber Technology & Engineering", "Alabama School of Cyber T",
             "Lincoln Park Academy", "Lincoln Park Academy East"]       # a whole word: no
    pairs = M.candidatesAll(names)
    assert ("The Covenant School of Jackson", "The Covenant School of Jacksonville", "cut") in pairs
    assert any(r == "cut" and "Cyber T" in (a + b) for a, b, r in pairs)
    assert not any(r == "cut" and "Lincoln Park" in a for a, b, r in pairs)
    ros = {"The Covenant School of Jacksonville": set(range(34)),
           "The Covenant School of Jackson": set(range(28, 34))}
    merged, _r = M.judge([("The Covenant School of Jackson", "The Covenant School of Jacksonville", "cut")],
                         ros, {"The Covenant School of Jacksonville": {"FL"}})
    assert merged
