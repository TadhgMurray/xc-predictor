"""engine/gender_pace_pin.py: a men's XC race filed at the women's distance
(owner, 2026-10-13: Al Shirley 2026, men 35:40 vs women 23:17 at "5000 m")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
import gender_pace_pin as G  # noqa: E402


def test_al_shirley_men_pinned_to_8000():
    rows = [(286842, 1145427, 5000.0, "Pima CC West", "M", 25, 35.67 * 60),
            (286842, 1145428, 5000.0, "Pima CC West", "F", 13, 23.28 * 60)]
    got = G.pins(rows)
    assert [(m, d, s) for m, d, _x, s, _w in got] == [(286842, 1145427, 8000.0)]


def test_ordinary_meets_are_left_alone():
    rows = [  # high school 5K: boys 22:23, girls 30:00 (hot, slow girls field)
        (134854, 552596, 5000.0, "c", "M", 71, 22.38 * 60),
        (134854, 552597, 5000.0, "c", "F", 55, 30.00 * 60),
        # JV boys a little slower than varsity girls: not a distance
        (9, 1, 5000.0, "c", "M", 40, 21.5 * 60),
        (9, 2, 5000.0, "c", "F", 60, 20.5 * 60),
        # no women at that meet and distance
        (10, 3, 5000.0, "c", "M", 40, 40.0 * 60)]
    assert G.pins(rows) == []


def test_block_is_replaced_whole():
    text = "A = {}\n"
    one = G.replaceBlock(text, G.block([(1, 2, 5000.0, 8000.0, "x")]))
    assert "(1, 2): 8000" in one
    two = G.replaceBlock(one, G.block([]))
    assert "(1, 2)" not in two and two.count(G.BLOCK_START) == 1
    assert two.startswith("A = {}")
