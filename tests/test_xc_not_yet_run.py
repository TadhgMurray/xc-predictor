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

    # these exercise the whole-meet path, now behind XCP_ANET_WHOLE_MEET=1
    monkeypatch.setenv("XCP_ANET_WHOLE_MEET", "1")
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


# ---- per division, the default since the whole-meet call closed ------------ #

def _perDiv(monkeypatch, meet_info, divisions, per_div):
    async def meetData(page, meet_id):
        return meet_info, divisions

    async def wholeMeet(page, meet_id, token):
        raise AssertionError("the whole-meet call is off by default")

    saved = {}

    def save(meet_id, label, meets, athletes, results, teams=None, team_scores=None):
        saved.update(meets=meets, results=results, teams=teams, scores=team_scores)
        return len(results)

    async def noSleep(_):
        return None

    monkeypatch.delenv("XCP_ANET_WHOLE_MEET", raising=False)
    monkeypatch.setattr(SR, "getMeetData", meetData)
    monkeypatch.setattr(SR, "getAllResultsXC", wholeMeet)
    monkeypatch.setattr(SR, "getMeetResults", per_div)
    monkeypatch.setattr(SR, "_saveXCMeet", save)
    monkeypatch.setattr(SR.asyncio, "sleep", noSleep)
    SR.EMPTY_FACTS.clear()
    got = asyncio.run(SR.scrapeMeetBySport(None, 288317, "XC", "[t]", None))
    return got, saved


def test_per_division_asks_each_division_with_results(monkeypatch, capsys):
    """288317: GetAllResultsData 403, GetResultsData3 per division 200."""
    asked = []

    async def perDiv(page, meet_id, div_id, token):
        asked.append(div_id)
        return ([{"AthleteID": div_id * 10, "IDResult": div_id}], [{"t": 1}],
                [{"score": div_id}])

    got, saved = _perDiv(monkeypatch,
                         {"ID": 288317, "MeetDate": LAST_WEEK, "HasResults": 1, "jwtMeet": "t"},
                         [{"IDMeetDiv": 1149714, "Result": 1}, {"IDMeetDiv": 1149715, "Result": 1},
                          {"IDMeetDiv": 1149716, "Result": 0}], perDiv)
    assert got == (2, True)
    assert asked == [1149714, 1149715]              # the entries-only one skipped
    assert [r[3] for r in saved["results"]] == [1149714, 1149715]
    assert saved["scores"] == [{"score": 1149714}, {"score": 1149715}]
    assert saved["teams"] == [{"t": 1}]
    assert "saved 2 results (3 divisions)" in capsys.readouterr().out


def test_one_division_failing_fails_the_meet_and_saves_nothing(monkeypatch):
    async def perDiv(page, meet_id, div_id, token):
        if div_id == 2:
            raise Exception("API returned status 500")
        return [], None, None

    got, saved = _perDiv(monkeypatch,
                         {"ID": 9, "MeetDate": LAST_WEEK, "HasResults": 1, "jwtMeet": "t"},
                         [{"IDMeetDiv": 1}, {"IDMeetDiv": 2}], perDiv)
    assert got == (-1, True) and saved == {}


def test_per_division_still_waits_for_race_day(monkeypatch):
    async def perDiv(page, meet_id, div_id, token):
        raise AssertionError("a meet still to come is not asked")

    got, _ = _perDiv(monkeypatch,
                     {"ID": 9, "MeetDate": TOMORROW, "HasResults": 0, "jwtMeet": "t"},
                     [{"IDMeetDiv": 1}], perDiv)
    assert got == (0, True)
    assert SR.EMPTY_FACTS[(288317, "XC")] == (TOMORROW, False)


def test_a_meet_with_no_results_in_any_division_is_noted_empty(monkeypatch):
    async def perDiv(page, meet_id, div_id, token):
        return [], None, None

    got, _ = _perDiv(monkeypatch,
                     {"ID": 9, "MeetDate": LAST_WEEK, "HasResults": 1, "jwtMeet": "t"},
                     [{"IDMeetDiv": 1}], perDiv)
    assert got == (0, True)
    assert (288317, "XC") in SR.EMPTY_FACTS
