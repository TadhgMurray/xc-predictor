"""
division_group.divisionGroup (sweep 2026-10-10, A11): varsity-level labels
roll into one standings; JV, Frosh-Soph, Middle school and Para each keep
their own; anything unrecognised stays its own group, never merged by guess.
Track and cross-country spellings alike.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import pytest                                                   # noqa: E402

from division_group import (divisionGroup, groupRank,            # noqa: E402
                            groupLabel, GROUP_ORDER)

VARSITY = [
    None, "", "  ", "Varsity", "VARSITY", "Boys Varsity", "Girls Varsity",
    "Varsity Boys", "Invite", "Invitational", "Boys Invitational", "Open",
    "Elite", "Seeded", "Unseeded", "Championship", "Championships",
    "Boys Championship", "Heat 1", "Heat 12", "Flight 2", "Section 3",
    "Sect. 2", "Race 2", "Fast Heat", "Varsity A", "Varsity B",
    "Girls 5K Varsity A", "Varsity Large School", "Varsity Small",
    "Varsity Small School", "Varsity 4A", "Varsity Division 1", "V",
    "Boys 3200", "Girls 5K", "Men's 8K", "Women's 6000m", "Open 2 Mile",
    "Invitational Section 1", "Elite Heat", "Championship Race",
    "Boys' Varsity", "Women’s Open",
]
JV = ["JV", "jv", "J.V.", "Junior Varsity", "Boys JV", "JV Girls",
      "JV 5K", "JV2", "JV Invitational", "Junior Varsity Heat 2",
      "Open JV"]
FS = ["Frosh-Soph", "Frosh/Soph", "Frosh Soph", "F/S", "F-S", "FS",
      "Freshman", "Freshmen", "Frosh", "Novice", "Boys Frosh/Soph 2 Mile",
      "Freshman/Sophomore", "Fresh-Soph", "Girls Freshman", "9th Grade",
      "Frosh-Soph Invitational"]
MS = ["Middle School", "Middle School Boys", "Girls Middle School 2 Mile",
      "MS", "7th Grade", "8th Grade Girls", "7th/8th", "7/8", "7-8",
      "Junior High", "Jr. High", "Middle School Open"]
PARA = ["Para", "Adaptive", "Ambulatory", "Wheelchair", "Seated",
        "Unified", "Boys Para 100m", "Ambulatory Open", "Varsity Unified",
        "Seated 100m"]


@pytest.mark.parametrize("label", VARSITY)
def test_varsity_level(label):
    assert divisionGroup(label) == "varsity", label


@pytest.mark.parametrize("label", JV)
def test_jv(label):
    assert divisionGroup(label) == "jv", label


@pytest.mark.parametrize("label", FS)
def test_frosh_soph(label):
    assert divisionGroup(label) == "fs", label


@pytest.mark.parametrize("label", MS)
def test_middle_school(label):
    assert divisionGroup(label) == "ms", label


@pytest.mark.parametrize("label", PARA)
def test_para(label):
    assert divisionGroup(label) == "para", label


@pytest.mark.parametrize("label", [
    "Masters", "Alumni", "Gold", "Silver Race", "Varsity Gold",
    "Open Masters", "Large School", "Small School", "Division II", "4A",
    "Elementary", "EnRoute", "Special Olympics", "Rookie",
    "Varsity Alumni", "6th Grade",
])
def test_unrecognised_stays_its_own(label):
    g = divisionGroup(label)
    assert g.startswith("other:"), (label, g)


def test_unrecognised_labels_never_merge():
    assert divisionGroup("Gold") != divisionGroup("Silver")
    assert divisionGroup("Large School") != divisionGroup("Small School")
    # but the same label, spelled with different spacing/case, is one
    assert divisionGroup("Gold  Race") == divisionGroup("gold race")


def test_precedence_narrowest_level_wins():
    assert divisionGroup("Junior Varsity") == "jv"       # not varsity
    assert divisionGroup("Middle School Open") == "ms"
    assert divisionGroup("JV Invitational") == "jv"
    assert divisionGroup("Varsity Para") == "para"


def test_xc_compiled_labels():
    # owner, 2026-10-10: XC compiled results reuse this
    got = {lab: divisionGroup(lab) for lab in (
        "Varsity Large School", "Varsity Small", "Open", "JV",
        "Frosh/Soph", "Freshman", "Middle School", "Girls 5K Varsity A")}
    assert got == {"Varsity Large School": "varsity", "Varsity Small": "varsity",
                   "Open": "varsity", "JV": "jv", "Frosh/Soph": "fs",
                   "Freshman": "fs", "Middle School": "ms",
                   "Girls 5K Varsity A": "varsity"}


def test_order_and_labels():
    assert GROUP_ORDER[0] == "varsity"
    ranks = [groupRank(g) for g in ("varsity", "jv", "fs", "ms", "para",
                                    "other:gold")]
    assert ranks == sorted(ranks) and len(set(ranks)) == 6
    assert groupLabel("varsity") == "Varsity"
    assert groupLabel("other:gold") is None
