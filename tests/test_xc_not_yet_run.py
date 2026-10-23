"""A meet still to come is empty, not failed (owner, 2026-10-23).

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_xc_not_yet_run.py

The first scrape on the new server printed "claims 2 divisions but
GetAllResultsData returned no flatEvents — failed" for 50 of the newest
athletic.net ids: athletic.net sets divisions up before race day. A meet
with HasResults false or a date after today is now noted empty (state 1
and its date, re-asked after race day); a real miss still fails and says
its date, HasResults and the payload's keys.
"""
import asyncio
import datetime
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import scrape_results as SR                                       # noqa: E402

TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
LAST_WEEK = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()


def _run(monkeypatch, meet_info, payload):
    async def meetData(page, meet_id):
        return meet_info, [{"id": 1}, {"id": 2}]

    async def allResults(page, meet_id, token):
        return payload

    monkeypatch.setattr(SR, "getMeetData", meetData)
    monkeypatch.setattr(SR, "getAllResultsXC", allResults)
    SR.EMPTY_FACTS.clear()
    return asyncio.run(SR.scrapeMeetBySport(None, 288380, "XC", "[t]", None))


def test_a_meet_tomorrow_is_empty_and_noted(monkeypatch, capsys):
    got = _run(monkeypatch, {"ID": 288380, "MeetDate": TOMORROW + "T00:00:00",
                             "HasResults": None}, {})
    assert got == (0, True)
    assert SR.EMPTY_FACTS[(288380, "XC")] == (TOMORROW, None)
    assert "no results yet" in capsys.readouterr().out


def test_has_results_false_is_empty(monkeypatch):
    got = _run(monkeypatch, {"ID": 288380, "MeetDate": LAST_WEEK,
                             "HasResults": False}, {"flatEvents": []})
    assert got == (0, True)
    assert SR.EMPTY_FACTS[(288380, "XC")] == (LAST_WEEK, False)


def test_a_past_meet_with_results_but_no_payload_still_fails(monkeypatch, capsys):
    got = _run(monkeypatch, {"ID": 288380, "MeetDate": LAST_WEEK,
                             "HasResults": True}, {"error": "x"})
    assert got == (-1, True)
    assert (288380, "XC") not in SR.EMPTY_FACTS
    out = capsys.readouterr().out
    assert "failed" in out and LAST_WEEK in out and "['error']" in out
