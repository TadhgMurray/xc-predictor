"""The area rule must not hand a section's sport word back as the area
(issue 168): "NCS Cross Country Championships" is NCS, no area;
"NCS Tri-Valley Area Championships" is the Tri-Valley area.

    python -m pytest -q tests/test_area_not_a_sport.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
# the parser's module imports the database client at import time; the
# parser itself never touches it
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import check_school_units as CU                                 # noqa: E402


def _areas(name):
    return [u for k, u in CU.parseUnits(name, "", college=False, state="CA")
            if k == "area"]


def test_sport_words_are_not_an_area():
    for name in ("NCS Cross Country Championships",
                 "CIF NCS Track & Field Championships",
                 "NCS XC Championships", "CCS Cross Country Finals"):
        assert _areas(name) == [], (name, _areas(name))


def test_a_real_area_still_parses():
    assert _areas("NCS Tri-Valley Area Championships") == ["TRI-VALLEY"]
    assert _areas("NCS Bay Shore Area Cross Country Championships") == ["BAY SHORE"]
