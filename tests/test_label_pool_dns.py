"""A DNS / DNF / unrated row reads its grade at the race's level (owner,
2026-11-05: NESCAC men's DNS list showed "Amherst 9", "Colby 11")."""
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
          os.path.join(_ROOT, "scripts")):
    sys.path.insert(0, p)
pytest.importorskip("flask")
import app as A                                              # noqa: E402
import grade_label as G                                      # noqa: E402


def test_dns_rows_in_a_college_race_read_as_college_years():
    rows = [{"rating_pool": "college_m|XC"} for _ in range(20)] + \
           [{"rating_pool": None, "grade": "11"}, {"rating_pool": None, "grade": "9"}]
    assert A.fillLabelPools(rows) == "college_m"
    assert [G.gradeLabel(r["grade"], r.get("rating_pool") or r.get("label_pool"))
            for r in rows[-2:]] == ["JR-3", "FR-1"]


def test_a_mixed_race_guesses_nobody():
    rows = [{"rating_pool": "hs_m"} for _ in range(10)] + \
           [{"rating_pool": "college_m"} for _ in range(10)] + [{"rating_pool": None, "grade": "11"}]
    assert A.fillLabelPools(rows) is None and "label_pool" not in rows[-1]


def test_both_race_templates_fall_back_to_it():
    for t in ("race.html", "race_tf.html"):
        src = open(os.path.join(_ROOT, "racecast", "templates", t)).read()
        assert "grade_label(row.rating_pool)" not in src
        assert "grade_label(row.rating_pool or row.label_pool)" in src
