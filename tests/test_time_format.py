"""One server clock formatter, rounding before it splits (sweep 2026-10-10).

compiled.html and compare.py split off the minutes and then rounded, so
959.96 printed "15:60.0"; app.format_time's fraction printed 0.996 as ".10".

    python -m pytest -q tests/test_time_format.py
"""
import os
import sys

# panels imports config, which resolves a connection at import. Never used.
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))

import pytest                                                    # noqa: E402

import time_format as T                                          # noqa: E402

CASES = [
    (11.24, "11.24"),
    (11.239999771, "11.24"),          # a 4-byte real
    (59.996, "1:00"),                 # carries into the minute
    (898.2, "14:58.2"),
    (959.96, "15:59.96"),             # was 15:60.0 on compiled races
    (959.996, "16:00"),               # was 15:59.10
    (249.97, "4:09.97"),
    (3599.999, "1:00:00"),
    (3659.6, "1:00:59.6"),
    (3903.0, "1:05:03"),
    (20000.002, " - "),               # anet TF sentinel
    (999999, " - "),
]


@pytest.mark.parametrize("sec,want", CASES)
def test_format_time(sec, want):
    assert T.format_time(sec) == want


def test_compare_uses_the_one_formatter():
    import compare as C
    assert C.fmtTime(959.96) == "15:59.96"
    assert C.fmtTime(None) is None


def test_panels_twin_agrees():
    import panels as P
    for sec, want in CASES[:-2]:
        assert P._fmtTime(sec) == want


def test_compiled_template_uses_the_clock_filter():
    src = open(os.path.join(_ROOT, "racecast", "templates", "compiled.html"),
               encoding="utf-8").read()
    assert "%04.1f" not in src and "|clock" in src


# ---- the carry, in every fixed-place clock (complexity review, 2026-10-24) ----

def test_fixed_place_clocks_carry_instead_of_printing_sixty():
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racecast"))
    import social, cards, explain_rating, csv_export, race_story
    from time_format import clock_fixed
    assert clock_fixed(959.96, 1) == "16:00.0"
    assert clock_fixed(959.996, 2) == "16:00.00"
    assert clock_fixed(3725.04, 2, hour_places=1) == "1:02:05.0"
    for v in (959.96, 959.996, 59.996, 3599.996):
        for f in (social._clock, cards._clock, explain_rating.clock, csv_export.fmtTime, race_story._clock):
            out = f(v)
            assert ":60" not in out and not out.endswith("."), (f.__module__, v, out)
    assert cards._clock(959.96) == "16:00" and social._clock(883.7) == "14:43.7"
    assert race_story._clock(883.7) == "14:43.7" and csv_export.fmtTime(883.7) == "14:43.70"
