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


def test_a_track_page_converts_from_its_track_to_xc(stubbed):
    """From a track (source TF) to an average XC 5K: the same fitness is
    slower over grass and 3.4x the distance, and both clocks still rise."""
    pts = cv.equivalenceLine("hs_m", 1609.34, 5000, course_difficulty=0.01,
                             source_sport="TF", target_sport="XC")
    assert len(pts) > 100
    r, tc, tt = zip(*pts)
    assert list(tc) == sorted(tc) and list(tt) == sorted(tt)
    assert all(b > 2.5 * a for a, b in zip(tc, tt))


def test_course_to_course_scales_by_the_two_difficulties(stubbed):
    """★ "some way to compare course to course" (owner, 2026-09-25): at one
    distance the equivalent is the time times (1 + d_target) / (1 + d_here)."""
    pts = cv.equivalenceLine("hs_m", 4828, 4828, course_difficulty=0.081,
                             target_sport="XC", target_difficulty=0.107,
                             target_course="Mt. San Antonio College")
    for _r, here, there in pts[::20]:
        assert there / here == pytest.approx(1.107 / 1.081, rel=1e-6)


def test_a_race_page_states_its_group_rather_than_offering_one():
    """! "changing pool still changed the track time": the conversion is
    genuinely per group, so a race page fixes it to the race's group."""
    for name in ("race.html", "race_tf.html"):
        src = open(os.path.join(_ROOT, "racecast", "templates", name)).read()
        assert "fixed_pool=True" in src, name
    mac = open(os.path.join(_ROOT, "racecast", "templates", "_equiv_line.html")).read()
    assert '{% if fixed_pool %}' in mac and 'type="hidden" class="eq-pool"' in mac


def test_the_group_does_not_move_the_course_clock_in_the_widget():
    """! owner, 2026-09-25: "changing the pool changed the predicted time".
    A group change must reload AT THE CURRENT TIME, not reopen on the new
    group's average runner."""
    js = open(os.path.join(_ROOT, "racecast", "static", "equiv-line.js")).read()
    assert 'selPool.addEventListener("change", function () { load(st.cur); });' in js


def test_the_rating_follows_the_hs_equivalent_toggle():
    js = open(os.path.join(_ROOT, "racecast", "static", "equiv-line.js")).read()
    assert "rc-scale-change" in js and "st.hs" in js
    app = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    assert '"hs_factor": hs_factor' in app


def test_the_templates_parse_and_load_the_script():
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.join(_ROOT, "racecast", "templates")))
    for name in ("_equiv_line.html", "race.html", "course.html", "race_tf.html"):
        env.parse(env.loader.get_source(env, name)[0])
    for name in ("race.html", "course.html", "race_tf.html"):
        src = open(os.path.join(_ROOT, "racecast", "templates", name)).read()
        assert "equiv-line.js" in src and "equiv_line(" in src, name
