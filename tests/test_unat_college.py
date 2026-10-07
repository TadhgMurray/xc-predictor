# Project: xc-predictor / tests
# File:    test_unat_college.py
# Purpose: "UNAT-<college>" pools as college (normalize_distance.poolFor),
#          and a school grade in a college pool prints as a college year.
#
# ★ OWNER, 2026-10-07: Soheib Dissa, "UNAT-Duke (NC) · 9", rated top 0.1% of
#   high-school boys; "if someone puts 9th grade, and we know they're a college
#   freshman, we'll print college freshman".
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, p))

import normalize_distance as nd                                  # noqa: E402
from grade_label import gradeLabel                               # noqa: E402


def test_unattached_under_a_known_college_is_college(monkeypatch):
    levels = {"stanford": "college", "duke university": "college", "kimball": "hs"}
    monkeypatch.setattr(nd, "levelForSchool", lambda s: levels.get(nd.normSchoolKey(s) or ""))
    assert nd.unattachedCollege("UNAT-Duke (NC)") == "Duke (NC)"   # via "Duke University"
    assert nd.unattachedCollege("Unattached - Stanford") == "Stanford"
    assert nd.poolFor("9", "M", "anet", "UNAT-Duke (NC)") == "college_m"


def test_nothing_else_moves(monkeypatch):
    levels = {"stanford": "college", "kimball": "hs"}
    monkeypatch.setattr(nd, "levelForSchool", lambda s: levels.get(nd.normSchoolKey(s) or ""))
    assert nd.unattachedCollege("Unattached") is None
    assert nd.unattachedCollege("UNAT-On Athletics Club") is None   # a pro club: unknown
    assert nd.unattachedCollege("UNAT-Kimball") is None             # a high school
    assert nd.unattachedCollege("Stanford") is None                 # no prefix: the grade rules
    assert nd.poolFor("9", "M", "anet", "UNAT-Kimball") == "hs_m"


def test_a_school_grade_in_a_college_pool_prints_as_the_year():
    assert gradeLabel("9", "college_m") == "FR-1"
    assert gradeLabel("12", "college_f") == "SR-4"
    assert gradeLabel("9", "hs_m") == "9"
    assert gradeLabel("14", "college_m") == "SO-2"
