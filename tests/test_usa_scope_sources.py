"""The USA scope's college-results clause names result_id, which only the
per-result boards have (2026-09-28: every ability board 400ed)."""
import collections
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import rankings as R                                           # noqa: E402


def _where(with_dates):
    f = collections.defaultdict(lambda: None, {"scope": "usa", "sport": "XC",
                                               "pool": "hs_m", "year": 2025})
    return R._whereClauses(f, {}, with_dates=with_dates)


def test_season_boards_never_name_result_id():
    assert "result_id" not in _where(False), "athlete_season has no result_id"
    assert "state = ANY(%(us_states)s)" in _where(False)


def test_result_boards_keep_the_college_rows():
    assert "state IS NULL AND result_id < 0" in _where(True)
