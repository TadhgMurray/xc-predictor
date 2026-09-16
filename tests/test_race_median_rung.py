"""A race's reading as a median over its voters -- a rung, not a change.

    python -m pytest -q tests/test_race_median_rung.py

★ THE SAME SHAPE AS joint_solve.topFractionWeights, which took Slaney's
  filter verbatim "SO IT CAN BE TESTED RATHER THAN ARGUED ABOUT".
  voter_agg="mean" is what shipped and stays the default.

⚠ WHY IT MIGHT WIN. A mean has NO BREAKDOWN POINT. Five honest voters at
  +0.018 plus one scraped 1-second time read as -0.652 -- the mean says the
  course is 65% easy -- while the median moves from +0.020 to +0.015. This
  corpus demonstrably contains such rows: an unclamped one is what took the
  first full-corpus training run to NaN. The rest of the engine already knows
  it (joint_solve uses medians in five places, conversions.default_difficulty
  takes a weighted median); bracket_engine had none.

⚠ WHY IT MIGHT NOT. The reading is already trimmed to the top fraction of
  each field, so part of the tail is gone before this sees it, and a median
  of three voters is noisier than their mean when all three are honest. Not
  obvious -- which is the reason to run it rather than reason about it.
"""
import io
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "engine", "bracket_engine.py"),
              encoding="utf-8").read()

# ! LOADED WITHOUT IMPORTING THE MODULE. bracket_engine pulls in the engine's
#   data files and a database config; the reduction under test needs neither.
_NS = {"np": np}
_i = SRC.index("def _raceMedian")
exec(compile(SRC[_i:SRC.index("\ndef fit(")], "bracket_engine", "exec"), _NS)
raceMedian = _NS["_raceMedian"]


def test_it_is_numpys_median_grouped():
    """! GROUPED MEDIAN WITHOUT A PYTHON LOOP, which is the only part that
    could be subtly wrong. 300 random shapes against np.median."""
    rng = np.random.default_rng(0)
    for _ in range(300):
        n = int(rng.integers(1, 8))
        size = int(rng.integers(0, 40))
        race = rng.integers(0, n, size)
        vals = rng.normal(size=size).round(3)
        got, cnt = raceMedian(race, vals, n)
        want = np.array([np.median(vals[race == k]) if (race == k).any()
                         else 0.0 for k in range(n)])
        assert np.allclose(got, want, atol=1e-9), (got, want)
        assert np.array_equal(cnt, np.bincount(race, minlength=n))


def test_even_counts_average_the_two_middles():
    got, _c = raceMedian(np.zeros(4, dtype=int),
                         np.array([1.0, 2.0, 4.0, 8.0]), 1)
    assert abs(float(got[0]) - 3.0) < 1e-12, got


def test_a_race_with_no_voters_is_zero_not_a_crash():
    got, cnt = raceMedian(np.array([], dtype=int), np.array([]), 3)
    assert list(got) == [0.0, 0.0, 0.0], got
    assert list(cnt) == [0.0, 0.0, 0.0], cnt
    # ...and a gap in the middle of the race ids
    got, cnt = raceMedian(np.array([0, 2]), np.array([5.0, 7.0]), 3)
    assert list(got) == [5.0, 0.0, 7.0], got


def test_the_breakdown_point_is_the_whole_argument():
    honest = np.array([0.01, 0.02, 0.03, 0.02, 0.01])
    med, _c = raceMedian(np.zeros(5, dtype=int), honest, 1)
    assert abs(float(med[0]) - 0.02) < 1e-12

    # one absurd row -- the class that took training to NaN
    bad = np.append(honest, -4.0)
    mean = float(bad.mean())
    med2, _c = raceMedian(np.zeros(6, dtype=int), bad, 1)
    assert mean < -0.6, mean                     # "this course is 65% easy"
    assert abs(float(med2[0]) - 0.015) < 1e-9    # the median barely moves
    assert abs(float(med2[0]) - float(med[0])) < 0.01


def test_the_default_is_unchanged_behaviour():
    """! A RUNG. Nothing moves until someone passes voter_agg="median"."""
    i = SRC.index("def fit(")
    sig = SRC[i:SRC.index('"""', i)]
    assert 'voter_agg="mean"' in sig, sig
    # and the mean branch is still the literal arithmetic that shipped
    j = SRC.index("def cellStep(")
    body = SRC[j:j + 1800]
    assert 'if voter_agg == "median":' in body, body
    assert "num / np.maximum(cnt, 1)" in body, body


def test_the_counts_diagnostic_asks_its_questions():
    d = io.open(os.path.join(ROOT, "scripts", "diag_engine_counts.py"),
                encoding="utf-8").read()
    # A: how much of the corpus is rated against an unmeasured course
    assert "cd.difficulty IS NULL" in d, d
    # B: is there an anchor population, and is it representative
    assert "is_indoor, 0) = 0" in d, d
    assert "BETWEEN 4900 AND 5100" in d, d
    assert "median_rating" in d, d
    # C: does the model's extraction see what the engine rates
    assert "in_meets" in d, d


def test_section_a_joins_the_venue_the_engine_joins():
    """⚠ THE FIRST VERSION JOINED ONLY `meets` AND REPORTED 26.7% OF COLLEGE
    ROWS AS HAVING NO VENUE. That was this query's bug, not the engine's:
    `meets` is the anet table and tfrrs XC venues live in meets_tfrrs, keyed
    on meet_id alone. speed_ratings_db._xcQuery COALESCEs the two.

    ! Measuring an engine with a join the engine does not use measures
      nothing, and the number it produced looked like a finding.
    """
    d = io.open(os.path.join(ROOT, "scripts", "diag_engine_counts.py"),
                encoding="utf-8").read()
    i = d.index("def sectionA(")
    body = d[i:d.index("\n# ---", i)]
    assert "meets_tfrrs" in body, body
    assert "COALESCE(m.course_name, mt.venue_name)" in body, body
    assert "COALESCE(m.gps_lat, mt.gps_lat)" in body, body

    # ...and the engine really does coalesce them, so this mirrors it
    eng = io.open(os.path.join(ROOT, "engine", "speed_ratings_db.py"),
                  encoding="utf-8").read()
    assert "COALESCE(m.course_name, mt.venue_name)" in eng, "engine changed"


if __name__ == "__main__":
    for fn in [test_it_is_numpys_median_grouped,
               test_even_counts_average_the_two_middles,
               test_a_race_with_no_voters_is_zero_not_a_crash,
               test_the_breakdown_point_is_the_whole_argument,
               test_the_default_is_unchanged_behaviour,
               test_the_counts_diagnostic_asks_its_questions,
               test_section_a_joins_the_venue_the_engine_joins]:
        fn()
    print("  ok")
