# Project: xc-predictor / tests
# File:    test_ncaa_pages.py
# Purpose: the /ncaa page's view model and card data from a toy projection,
#          and the page rendering through the real template (2026-10-10).
#          No database: the stored rows are handed in.
#
#   python -m pytest -q tests/test_ncaa_pages.py
import datetime
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

import build_ncaa_projection as B                              # noqa: E402
import ncaa_pages as NP                                        # noqa: E402
from test_ncaa_projection import toySeason                     # noqa: E402

_CACHE = {}


def _payload(n_races, seed=1):
    if n_races not in _CACHE:
        athletes, races = toySeason("d1", n_races=n_races)
        teams, team_of = B.buildTeams(athletes, "d1", "M", {}, {})
        _CACHE[n_races] = B.project("d1", "men", teams, team_of, races, draws=40,
                                    seed=seed, asof="2026-10-10")
    return _CACHE[n_races]


def test_view_model_shapes_and_movement():
    now, before = _payload(14), _payload(4)
    vm = NP.viewModel(now, before)
    assert len(vm["auto"]) == 18
    assert len(vm["field"]) == 32
    assert len(vm["regions"]) == 9
    # every team has a week-over-week number when last week had it
    assert all(t["d_qual"] is not None for t in vm["teams"])
    # a team in the field is never also "bubble"
    assert not any(t["bubble"] for t in vm["field"])
    # every round's pick is among its candidates, marked as the pick
    for rd in vm["rounds"]:
        assert any(picked and t["id"] == rd["pick"]["id"] for t, _p, picked in rd["candidates"])
        assert rd["why"]


def test_card_data():
    d = NP.cardData(_payload(14), "d1", "men")
    assert d["rows"] and len(d["rows"]) <= 10
    assert d["title"].startswith("NCAA Division I Men")


def test_page_renders():
    flask = pytest.importorskip("flask")                      # noqa: F841
    import app as A
    got = [(datetime.date(2026, 10, 9), datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc),
            _payload(14)),
           (datetime.date(2026, 10, 2), datetime.datetime(2026, 10, 3, tzinfo=datetime.timezone.utc),
            _payload(4))]
    old = NP._fetch
    NP._fetch = lambda division, gender: got if (division, gender) == ("d1", "men") else []
    try:
        c = A.app.test_client()
        html = c.get("/ncaa/d1/men").get_data(as_text=True)
        assert "who makes nationals" in html and "Berth 1" in html and "Unofficial" in html
        assert 'data-panel="nationals"' in html
        empty = c.get("/ncaa/d2/women")
        assert empty.status_code == 200 and "not been computed yet" in empty.get_data(as_text=True)
        assert c.get("/ncaa").status_code == 200
        assert c.get("/ncaa/d4/men").status_code == 404
    finally:
        NP._fetch = old
