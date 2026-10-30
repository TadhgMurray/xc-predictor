"""Every badge -- the course %, "Rated, not ranked", the flags -- hovers as a
tooltip where a pointer can hover, and opens a compact panel beside it on a
press; a sheet only on a phone-width screen (owner, 2026-10-30: "the course
difficulty thing shows up at the bottom rather than like the how it was
rated tab ... rated not ranked should be hoverable? maybe others should
too").

    python -m pytest -q tests/test_course_cell_panel.py
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_badges_hover_and_open_a_panel_beside_them():
    tips = read("racecast", "static", "tips.js")
    assert '"(any-hover: hover)"' in tips and '"(hover: none)"' not in tips
    assert 'panel.className = "rx-pop rx-tip";' in tips
    assert 'window.matchMedia("(max-width: 700px)")' in tips      # the rating panel's own rule
    assert "function placePanel()" in tips and "function showSheet" not in tips
    # every badge, the course cell included
    assert 'closest(".flag[data-blurb], .flag[data-title]")' in tips and "dv-day" not in tips.split("function badgeOf")[1][:200]
    # the rating panel is its own again
    assert "openCourse" not in read("racecast", "static", "rating-explain.js")
    css = read("racecast", "static", "features.css")
    assert ".rx-pop.rx-tip .rx-head" in css and "border-bottom: 0" in css
