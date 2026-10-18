"""scoreboard: flatten and render put each test beside the last run's.

    python -m pytest -q tests/test_scoreboard.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import scoreboard as S                                           # noqa: E402


def test_render_beside_last_run():
    now = {"meta": {"date": "2026-10-19"},
           "course_credit": {"120-130": {"b_course": 0.002, "b_day": 0.05, "n": 100}},
           "fall_pairs_pct": {"all": -0.1, "n": 7000}}
    prev = {"course_credit": {"120-130": {"b_course": 0.002, "b_day": 0.177, "n": 90}}}
    text = S.render(now, prev)
    assert "course_credit / 120-130 / b_day" in text and "+0.0500" in text and "+0.1770" in text
    assert "meta" not in text and "/ n " not in text
