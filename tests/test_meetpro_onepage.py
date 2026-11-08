"""The one-page MeetPro meet (owner, 2026-11-08: "you ever scrape diablo
timing?"): Diablo Timing's mp/ folders; sources.meetpro.parseSinglePage."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused")

from sources import discover, land, meetpro  # noqa: E402

PAGE = """<!DOCTYPE html><html><head><title>Results</title></head>
<!--DO NOT ERASE! VERSION:&MeetProTemplate_v2.0-->
<body><div class="col-sm-6 nav-title"><h4>Valley League Championships</h4></div>
<div class="row" id="meet-header"><div class="col-sm-4 col-md-5 m-info">
<p></p><p>Dublin, CA<br></p><p>Dublin High School</p><p>10/28/2026</p></div></div>
<section id="event2" class="event" ><div class="row event-header static"><div class='col-md-10'>
<div class="row ev-info"><div class='col-xs-8'><p class="bold-cell">Race #1</p>
<p class="bold-cell">6th Girls 1.25 Miles </p></div></div></div></div>
<div class="row results"><div class="col-md-10 team-scores static"><table class="scores-table table">
<thead><tr><th class="team-standings" colspan="2">Final Standings</th><th class="spread">Spread</th></tr></thead>
<tbody><tr><td class="team-place">1</td><td class="team">North</td><td class="spread">1:41.6</td></tr></tbody></table></div>
<div class="col-md-10 ind-results static"><table class="result-table table table-striped">
<thead><tr><th class="athlete-hdr" colspan="2">Athlete</th><th class="year">Yr.</th><th class="team">Team</th>
<th class="score">Score</th><th class="time">Time</th></tr></thead><tbody>
<tr><td class="place">1</td><td class="athlete">Runner, Ann</td><td class="year">6</td>
<td class="ath-team">North</td><td class="score">1</td><td class="time">8:11.2</td></tr>
<tr><td class="place">2</td><td class="athlete">Pacer, Bea</td><td class="year">6</td>
<td class="ath-team">South</td><td class="score">--</td><td class="time">8:20.9</td></tr>
</tbody></table></div></div></section></body></html>"""


def test_a_one_page_meet_lands_as_cross_country():
    assert meetpro.isSinglePage(PAGE)
    idx, res = meetpro.parseSinglePage(PAGE)
    assert (idx.name, idx.date, idx.place) == ("Valley League Championships", "2026-10-28", "Dublin, CA")
    assert [(e.number, e.title, e.gender) for e in idx.events] == [(2, "6th Girls 1.25 Miles", "F")]
    rows = res[2]
    assert [(r.place, r.first, r.last, r.grade, r.school, r.seconds) for r in rows] == \
        [(1, "Ann", "Runner", "6", "North", 491.2), (2, "Bea", "Pacer", "6", "South", 500.9)]
    lm = land.fromMeetPro(idx, res, "diablo-timing", "u")
    assert [(r.sport, len(r.rows)) for r in lm.races] == [("XC", 2)]


def test_an_unquoted_href_is_a_link():
    got = discover.links("<a href = mp/#event2>Results</a> <a href='x.htm'>X</a>",
                         "https://www.diablotiming.com/results/2026-10-28/")
    assert [u for u, _t in got] == ["https://www.diablotiming.com/results/2026-10-28/mp/",
                                    "https://www.diablotiming.com/results/2026-10-28/x.htm"]


def test_a_dated_folder_beats_a_stale_page_header():
    from sources import timers as T
    u = "https://www.diablotiming.com/results/2026-11-05/mp/"
    assert T.folderDate(u, "2025-11-06") == "2026-11-05"
    assert T.folderDate(u, "2026-11-06") == "2026-11-06"      # a meet posted the day after
    assert T.folderDate(u, "") == "2026-11-05"
    assert T.folderDate("https://x.com/mp/", "2025-11-06") == "2025-11-06"
