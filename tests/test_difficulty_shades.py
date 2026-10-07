# Project: xc-predictor / tests
# File:    test_difficulty_shades.py
# Purpose: difficulty and the race day in shades of red/green by size
#          (difficulty_view.shadeFor); the rating's text gradient is in
#          scale-view.js.
#
# ★ OWNER, 2026-10-07: "make difficulty have different shades of red/green.
#   give rating a color gradient."
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
import difficulty_view as dv                                    # noqa: E402


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def test_red_for_slower_green_for_faster_grey_for_none():
    assert dv.shadeFor(None) == ""
    assert dv.shadeFor(0.0) == "#6b7280"
    r, g, b = _rgb(dv.shadeFor(4.0))
    assert r > g and r > b                     # a red
    r, g, b = _rgb(dv.shadeFor(-4.0))
    assert g > r and g > b                     # a green


def test_deeper_with_size_and_full_at_ten_percent():
    small, big = _rgb(dv.shadeFor(1.0)), _rgb(dv.shadeFor(8.0))
    assert sum(big) < sum(small)               # darker = bigger effect
    assert dv.shadeFor(10.0) == dv.shadeFor(25.0) == "#991b1b"
    assert dv.shadeFor(-10.0) == "#14532d"


def test_the_templates_use_it():
    ex = open(os.path.join(ROOT, "racecast", "templates", "_explain.html"), encoding="utf-8").read()
    assert ex.count('style="color:{{ d|diffcolour(sport) }}"') == 2
    sv = open(os.path.join(ROOT, "racecast", "static", "scale-view.js"), encoding="utf-8").read()
    assert "el.style.color = ratingColour(parseFloat(el.textContent));" in sv
