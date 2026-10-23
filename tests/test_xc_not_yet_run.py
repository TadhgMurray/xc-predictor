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


def test_has_results_zero_is_empty(monkeypatch):
    got = _run(monkeypatch, {"ID": 288341, "MeetDate": LAST_WEEK,
                             "HasResults": 0}, {})
    assert got == (0, True)


def test_a_past_meet_with_results_but_no_payload_still_fails(monkeypatch, capsys):
    got = _run(monkeypatch, {"ID": 288380, "MeetDate": LAST_WEEK,
                             "HasResults": True}, {"error": "x"})
    assert got == (-1, True)
    assert (288380, "XC") not in SR.EMPTY_FACTS
    out = capsys.readouterr().out
    assert "failed" in out and LAST_WEEK in out and "['error']" in out
    assert "HTTP" in out


# ---- the whole-meet call refused: per division (2026-10-23) --------------- #

def test_a_refused_whole_meet_call_falls_back_to_per_division(monkeypatch, capsys):
    """GetAllResultsData answered 403 for 288317 (results on the page); the
    per-division call the results page itself makes still answered."""
    divisions = [{"IDMeetDiv": 1149714, "Result": 1}, {"IDMeetDiv": 1149715, "Result": 1},
                 {"IDMeetDiv": 1149716, "Result": 0}]

    async def meetData(page, meet_id):
        return {"ID": meet_id, "MeetDate": LAST_WEEK, "HasResults": 1,
                "jwtMeet": "t"}, divisions

    async def refused(page, meet_id, token):
        raise SR.FastPathRefused("HTTP 403")

    asked = []

    async def perDiv(page, meet_id, div_id, token):
        asked.append(div_id)
        return ([{"AthleteID": div_id * 10, "IDResult": div_id}], [{"t": 1}],
                [{"score": div_id}])

    saved = {}

    def save(meet_id, label, meets, athletes, results, teams=None, team_scores=None):
        saved.update(meets=meets, results=results, teams=teams, scores=team_scores)
        return len(results)

    async def noSleep(_):
        return None

    monkeypatch.setattr(SR, "getMeetData", meetData)
    monkeypatch.setattr(SR, "getAllResultsXC", refused)
    monkeypatch.setattr(SR, "getMeetResults", perDiv)
    monkeypatch.setattr(SR, "_saveXCMeet", save)
    monkeypatch.setattr(SR.asyncio, "sleep", noSleep)
    got = asyncio.run(SR.scrapeMeetBySport(None, 288317, "XC", "[t]", None))
    assert got == (2, True)
    assert asked == [1149714, 1149715]              # the entries-only one skipped
    assert [r[3] for r in saved["results"]] == [1149714, 1149715]
    assert saved["scores"] == [{"score": 1149714}, {"score": 1149715}]
    assert "per division" in capsys.readouterr().out


def test_one_division_failing_fails_the_whole_meet(monkeypatch):
    async def meetData(page, meet_id):
        return {"ID": meet_id, "MeetDate": LAST_WEEK, "HasResults": 1,
                "jwtMeet": "t"}, [{"IDMeetDiv": 1}, {"IDMeetDiv": 2}]

    async def refused(page, meet_id, token):
        raise SR.FastPathRefused("HTTP 403")

    async def perDiv(page, meet_id, div_id, token):
        if div_id == 2:
            raise Exception("API returned status 403")
        return [], None, None

    async def noSleep(_):
        return None

    monkeypatch.setattr(SR, "getMeetData", meetData)
    monkeypatch.setattr(SR, "getAllResultsXC", refused)
    monkeypatch.setattr(SR, "getMeetResults", perDiv)
    monkeypatch.setattr(SR, "_saveXCMeet", lambda *a, **k: pytest_fail())
    monkeypatch.setattr(SR.asyncio, "sleep", noSleep)
    assert asyncio.run(SR.scrapeMeetBySport(None, 9, "XC", "[t]", None)) == (-1, True)


def pytest_fail():
    raise AssertionError("a meet with a failed division must not be saved")


def test_track_refused_goes_per_division_not_to_recovery(monkeypatch):
    async def refused(page, meet_id, token):
        raise SR.FastPathRefused("HTTP 403")

    called = {}

    async def perDiv(page, meet_id, meet_info, events_dict, event_divs, label, rot):
        called["yes"] = (meet_id, len(event_divs))
        return 17

    monkeypatch.setattr(SR, "getAllResultsTF", refused)
    monkeypatch.setattr(SR, "_scrapeMeetTFPerDiv", perDiv)
    monkeypatch.setattr(SR, "_saveDivisionsForRecovery",
                        lambda *a: (_ for _ in ()).throw(AssertionError("recovery")))
    got = asyncio.run(SR.scrapeMeetTF(None, 676765, {"jwtMeet": "t"}, {1: {}},
                                      [{"e": 1, "d": 2}], "[t]", None))
    assert got == 17 and called["yes"] == (676765, 1)
