"""The course % opens the "How this was rated" panel (owner, 2026-10-30:
"the course difficulty thing shows up at the bottom rather than like the how
it was rated tab"); tips.js leaves it alone.

    python -m pytest -q tests/test_course_cell_panel.py
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_course_cell_is_the_explainer_s():
    rx = read("racecast", "static", "rating-explain.js")
    assert 'var COURSE = ".dv-day[data-title]";' in rx and "function openCourse(dv)" in rx
    assert 'anchorCell = dv.closest("td") || dv;' in rx
    tips = read("racecast", "static", "tips.js")
    assert 'el.classList.contains("dv-day") ? null : el' in tips
    # the macro still draws the badge both read
    assert 'class="dv dv-day flag" tabindex="0"' in read("racecast", "templates", "_explain.html")
