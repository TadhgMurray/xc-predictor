"""race_outliers: a race far off the rest of its meet's day (Mt. SAC 2026)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import race_outliers as RO                               # noqa: E402


def test_the_one_fast_race_of_a_slow_day_is_listed():
    day = (273894, "2026-10-23")
    runners = ([(day, "r53", 0.06)] * 20 + [(day, "r52", -0.01)] * 40
               + [(day, "r54", 0.0)] * 40)
    got = RO.outliers(runners, 3.0)
    assert [g[1] for g in got] == ["r53"] and got[0][2] > 0.05


def test_thin_fields_and_lone_races_are_not_read():
    runners = [((1, "d"), "a", 0.2)] * 10 + [((1, "d"), "b", 0.0)] * 30   # a: 10 runners
    assert RO.outliers(runners, 3.0) == []
    assert RO.outliers([((2, "d"), "only", 0.2)] * 30, 3.0) == []     # nothing to compare
