"""The pack's team key (2026-10-18): anet TeamID first, the tfrrs slug, the
school string, and no team for unattached or blank.

    python -m pytest -q tests/test_pack_team.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import speed_ratings as S                                         # noqa: E402


def _row(school="Campolindo", team_id=None, slug=None):
    r = [None] * 17
    r[S._SCHOOL], r[S._TEAM], r[S._SLUG] = school, team_id, slug
    return tuple(r)


def test_team_key_order():
    assert S.teamKey(_row(team_id=1234, slug="ca-x")) == "a:1234"
    assert S.teamKey(_row(team_id=0, slug="college_m_mit")) == "t:college_m_mit"
    assert S.teamKey(_row(team_id=None)) == "s:campolindo"
    assert S.teamKey(_row(school="Unattached")) is None
    assert S.teamKey(_row(school="  ")) is None
