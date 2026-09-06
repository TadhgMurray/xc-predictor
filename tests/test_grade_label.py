"""One spelling for a grade (2026-09-06): numbers in school pools, the
eligibility spelling in college pools, the database untouched.

    python -m pytest -q tests/test_grade_label.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
from grade_label import gradeLabel                              # noqa: E402


def test_school_pools_read_numbers_and_college_pools_read_eligibility():
    assert gradeLabel("Sr", "hs_m") == "12" and gradeLabel("Fr", "hs_f") == "9"
    assert gradeLabel("12", "hs_m") == "12" and gradeLabel("08", "ms_m") == "8"
    assert gradeLabel("JR-3", "hs_m") == "11"
    assert gradeLabel("Fr", "college_f") == "FR-1" and gradeLabel("sr-4", "college_m") == "SR-4"
    assert gradeLabel("14", "college_m") == "SO-2"
    assert gradeLabel("7", "ms_f") == "7"


def test_no_pool_keeps_the_feed_s_own_meaning():
    assert gradeLabel("So", None) == "10"          # a bare word is anet's
    assert gradeLabel("FR-1", None) == "FR-1"      # the eligibility spelling is tfrrs's
    assert gradeLabel(None, "hs_m") is None and gradeLabel("", "hs_m") == ""
    assert gradeLabel("Unknown", "hs_m") == "Unknown"


def test_the_boards_mirror_it():
    js = open(os.path.join(ROOT, "racecast", "static", "rankings.js"), encoding="utf-8").read()
    assert "function gradeLabel(grade, pool)" in js
    assert js.count("gradeLabel(r.grade, r.pool || poolNow())") == 3
