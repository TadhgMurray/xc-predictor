"""The track-equivalents line on race and course pages (2026-09-25).

conversions.equivalenceLine is the maths; these hold its shape, and parse
the templates that carry it. The database-backed pool means are stubbed.

    python -m pytest -q tests/test_equivalence_line.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                    # noqa: E402
import conversions as cv                                         # noqa: E402


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(cv, "pool_mean",
                        lambda pool, sport=None: 1211.5 if pool == "hs_m" else 1400.0)
    monkeypatch.setattr(cv, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(cv, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "XC", 0.05)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "TF", 0.0)


def test_both_clocks_rise_together_and_the_ratings_fall(stubbed):
    pts = cv.equivalenceLine("hs_m", 4828, 5000, course_difficulty=0.081)
    assert len(pts) > 100
    r, tc, tt = zip(*pts)
    assert list(tc) == sorted(tc) and list(tt) == sorted(tt)
    assert list(r) == sorted(r, reverse=True)


def test_a_harder_course_is_slower_for_the_same_track_time(stubbed):
    easy = {r: (tc, tt) for r, tc, tt in
            cv.equivalenceLine("hs_m", 5000, 5000, course_difficulty=0.0)}
    hard = {r: (tc, tt) for r, tc, tt in
            cv.equivalenceLine("hs_m", 5000, 5000, course_difficulty=0.10)}
    for r in (80, 120, 150):
        assert hard[r][0] > easy[r][0]                    # slower on the course
        assert hard[r][1] == pytest.approx(easy[r][1])    # same fitness on track


def test_the_track_distance_moves_only_the_track_clock(stubbed):
    a = {r: (tc, tt) for r, tc, tt in cv.equivalenceLine("hs_m", 4828, 5000, 0.08)}
    b = {r: (tc, tt) for r, tc, tt in cv.equivalenceLine("hs_m", 4828, 3200, 0.08)}
    for r in (90, 130):
        assert a[r][0] == pytest.approx(b[r][0])
        assert b[r][1] < a[r][1]


def test_the_templates_parse_and_load_the_script():
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.join(_ROOT, "racecast", "templates")))
    for name in ("_equiv_line.html", "race.html", "course.html"):
        env.parse(env.loader.get_source(env, name)[0])
    for name in ("race.html", "course.html"):
        src = open(os.path.join(_ROOT, "racecast", "templates", name)).read()
        assert "equiv-line.js" in src and "equiv_line(" in src, name
