"""A gap year between school and college (owner, 2026-10-11: Owen Staley,
senior 2024-25, Tufts FR-1 from 2026 after an injury year). link_freshmen
pairs a first-year with a senior two years back only when nobody of the
name was a senior the year before, on the same guards.

    python -m pytest -q tests/test_freshman_gap_year.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import link_freshmen as L                                       # noqa: E402

FRESH = {2026: {"p:1009167329": ("Owen Staley", "Tufts", None, 2)}}


def seniors(**gap):
    # seniors(y): seniors of y - 1. 2026 -> class of 2025 (none named Owen);
    # 2025 -> class of 2024 (Owen, Sleepy Hollow)
    return {2026: {}, 2025: {29603096: ("Owen Staley", "Sleepy Hollow", "M",
                                        gap.get("ht", False), gap.get("still", False))}}


def test_gap_year_joins():
    out = L.pairsAll(FRESH, seniors(), [2026])
    assert [(p[0], p[1]) for p in out[2026][0]] == [("p:1009167329", 29603096)]


def test_gap_year_keeps_every_guard():
    assert not L.pairsAll(FRESH, seniors(ht=True), [2026])[2026][0]
    assert not L.pairsAll(FRESH, seniors(still=True), [2026])[2026][0]
    two = seniors()
    two[2025][1] = ("Owen Staley", "Elsewhere", "M", False, False)
    assert not L.pairsAll(FRESH, two, [2026])[2026][0]


def test_a_senior_the_year_before_blocks_the_gap():
    s = seniors()
    s[2026][7] = ("Owen Staley", "Other HS", "M", True, False)   # refused, still blocks
    assert not L.pairsAll(FRESH, s, [2026])[2026][0]


def test_senior_years_cover_the_year_before():
    assert L.seniorYears([2024, 2025, 2026]) == [2023, 2024, 2025, 2026]
