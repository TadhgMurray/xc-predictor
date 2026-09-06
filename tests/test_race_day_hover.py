"""The race-day term as a hover on a difficulty (owner, 2026-09-06): the
dv macro with a day renders the bubble, without one the plain number;
the day filters clip at the cap and say the direction in words.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_race_day_hover.py
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("flask")
import app as site                                              # noqa: E402


def _render(src):
    t = site.app.jinja_env.from_string(src)
    with site.app.test_request_context("/"):
        return t.render()


def test_day_filters():
    assert site.dayPct(None) is None
    assert abs(site.dayPct(0.0) - 0.0) < 1e-12
    assert abs(site.dayPct(0.5) - site.dayPct(0.10)) < 1e-12, "clipped at the cap"
    assert site._daypct(-0.012) == "-1.2%"
    assert site._dayword(-0.012) == "fast" and site._dayword(0.03) == "slow"
    assert site._dayword(0.0) == "level"
    assert site._daycapped(0.14) and not site._daycapped(0.09)
    assert site._dayabs(-0.012) == "1.2%"


def test_dv_with_and_without_a_day():
    plain = _render('{% from "_explain.html" import dv %}{{ dv(0.046, "XC") }}')
    assert 'class="dv"' in plain and "+4.6%" in plain and "dv-day" not in plain
    fast = _render('{% from "_explain.html" import dv %}{{ dv(0.046, "XC", -0.012) }}')
    assert "dv-day" in fast and 'tabindex="0"' in fast
    assert "Race Day -1.2%" in fast
    assert "ran 1.2% fast" in fast and "lowered by 1.2%" in fast
    assert "Capped" not in fast
    slow = _render('{% from "_explain.html" import dv %}{{ dv(0.02, "XC", 0.14) }}')
    assert "ran 10.5% slow" in slow and "raised by 10.5%" in slow and "Capped at 10%" in slow
    # track: the day is shown but not applied (RACE_DAY_SPORTS = XC)
    assert site.RACE_DAY_SPORTS == ("XC",)
    tf = _render('{% from "_explain.html" import dv %}{{ dv(0.02, "TF", 0.03) }}')
    assert "ran 3.0% slow" in tf and "carry the venue only" in tf and "raised" not in tf
    none = _render('{% from "_explain.html" import dv %}{{ dv(None, "XC", 0.01) }}')
    assert none.strip() == "-" and "dv-day" not in none   # the empty placeholder is a hyphen (no em dashes, owner 2026-09-06)


def test_pages_still_parse():
    for name in ("athlete.html", "race.html", "race_tf.html", "home.html",
                 "_topbar.html", "rankings.html"):
        site.app.jinja_env.get_template(name)
