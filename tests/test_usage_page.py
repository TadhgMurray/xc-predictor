"""The usage page draws visitors as their own chart and day table (owner,
2026-11-03: "can we add the visitors as its own table/chart?")."""
import datetime as dt
import os
import sys

os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import app   # noqa: E402


def test_visitors_chart_and_table():
    days = [dt.date(2026, 10, 28) + dt.timedelta(days=i) for i in range(7)]
    rep = {"daily": [(d, 100 + i) for i, d in enumerate(days)], "peak": 106, "views": 721,
           "days": 7, "since": days[0], "visitors": 40,
           "visitors_daily": [(days[-1], 25), (days[-2], 15)],
           "phone": 1, "desktop": 1, "phone_pct": 50, "pages": [], "events": [], "refs": []}
    with app.app.test_request_context("/account/status/usage"):
        html = app.render_template("status_usage.html", rep=rep, days=7, held=0, error=None)
    assert 'id="visitors"' in html and "Visitors per day" in html
    assert "25 visitors" in html and "peak 25" in html
    assert "Views per visitor" in html and "<td class=\"n\">4.2</td>" in html
