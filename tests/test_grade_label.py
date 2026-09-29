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


# ★ EVERY SPELLING THE FEEDS WRITE (owner, 2026-09-29: "it'll say senior for
#   somebody rated in hs instead of 12"). Full, short, plural and ordinal
#   spellings, in every pool kind, answered the same by the page and the boards.
_CASES = [(g, p) for g in ("Senior", "senior", "SENIORS", "Sophomore", "Soph.", "Junior",
                           "Freshman", "Freshmen", "Fresh", "12th", "9th", "11TH", "1st",
                           "08", "7th", "14", "16th", "SR-4", "Jr-3", "fr1", "So.",
                           "Unknown", "17-18", "19+", "0", "")
          for p in ("hs_m", "ms_f", "college_m", "pro_f", None)]


def test_class_words_and_ordinals_read_as_the_pool_s_spelling():
    assert gradeLabel("Senior", "hs_m") == "12" and gradeLabel("seniors", "hs_f") == "12"
    assert gradeLabel("Sophomore", "hs_m") == "10" and gradeLabel("Freshman", "hs_f") == "9"
    assert gradeLabel("12th", "hs_m") == "12" and gradeLabel("8th", "ms_m") == "8"
    assert gradeLabel("Senior", "college_m") == "SR-4" and gradeLabel("Junior", "college_f") == "JR-3"
    assert gradeLabel("Sophomore", "college_m") == "SO-2" and gradeLabel("14th", "college_m") == "SO-2"
    assert gradeLabel("Senior", None) == "12"      # a bare word is anet's
    assert gradeLabel("17-18", "hs_m") == "17-18"  # an age band is not a grade


def test_the_js_mirror_gives_the_same_answers():
    import json
    import re
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("no node")
    js = open(os.path.join(ROOT, "racecast", "static", "rankings.js"), encoding="utf-8").read()
    start = js.index("const _CLASS = {")
    fn = re.search(r"function gradeLabel\(grade, pool\) \{.*?\n\}\n", js[start:], re.S)
    src = js[start:start + fn.end()]
    prog = src + "\nconsole.log(JSON.stringify(%s.map(([g, p]) => gradeLabel(g, p))));" \
        % json.dumps(_CASES)
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    want = [gradeLabel(g, p) for g, p in _CASES]
    bad = [(c, w, j) for c, w, j in zip(_CASES, want, got) if w != j]
    assert not bad, bad
