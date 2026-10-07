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


def test_green_for_harder_red_for_easier_none_for_zero():
    assert dv.tintFor(None) == dv.tintFor(0.0) == "transparent"
    assert dv.tintFor(4.0).startswith("rgba(22,163,74,")       # harder: green
    assert dv.tintFor(-4.0).startswith("rgba(220,38,38,")      # easier: red


def _alpha(t):
    return float(t.rsplit(",", 1)[1].rstrip(")"))


def test_deeper_with_size_and_full_at_ten_percent():
    assert _alpha(dv.tintFor(1.0)) < _alpha(dv.tintFor(4.0)) < _alpha(dv.tintFor(8.0))
    assert dv.tintFor(10.0) == dv.tintFor(25.0)


def test_the_templates_use_it():
    ex = open(os.path.join(ROOT, "racecast", "templates", "_explain.html"), encoding="utf-8").read()
    assert ex.count('style="background:{{ d|diffcolour(sport) }}"') == 2
    sv = open(os.path.join(ROOT, "racecast", "static", "scale-view.js"), encoding="utf-8").read()
    assert "el.style.backgroundColor = ratingTint(parseFloat(el.textContent));" in sv
