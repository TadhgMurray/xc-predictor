"""Unit chips read biggest first and a class is never a bare token (issue 134).

    python -m pytest -q tests/test_unit_chip_order.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None

import school_units as SU                                        # noqa: E402


def test_hs_chips_widest_first():
    c = SU._HS_CHIPS
    assert c.index("state_div") < c.index("section") < c.index("section_div") \
        < c.index("area") < c.index("league")
    assert c.index("class") < c.index("section")


def test_class_label_is_never_bare():
    assert SU._label("class", "6A", long=False) == "Class 6A"
    assert SU._label("class", "6A", long=True) == "Class 6A"
    assert SU._label("section_div", "2", False, {"section": "NCS"}) == "NCS D2"
