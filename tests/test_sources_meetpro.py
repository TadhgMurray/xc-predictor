"""MeetPro results sites (sources/meetpro.py). Synthetic names in the real
layout (B&B Race KC on results.tfmeetpro.com, 2022).

    python -m pytest -q tests/test_sources_meetpro.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import meetpro as M                                  # noqa: E402

INDEX = """<html><head><title>Lakeside Region Meet | MeetPro by DirectAthletics</title></head><body>
<div class="container"><div class="header">
<h3 class="text-muted"><a class="home-link-hdr" href="index.html">Lakeside Region Meet</a><span style="font-size: 12px;">Lakeside, KS<br></span><span style="font-size: 12px;">5/20/2022</span></h3>
</div><div class="row"><div class="col-md-6"><h3>Boys</h3>
<table class="events-table table"><thead><tr><th class="event-num"></th><th class="event">Event</th><th class="start-list">Start List</th><th class="results">Results</th></tr></thead><tbody>
<tr><td class="event-num">2</td><td class="event">Boys 4 x 800m Relay</td><td class="start-list"><a href='start_list_28.html'>Start List</a></td><td class="results"><a href='results_28.html'>Results</a></td></tr>
<tr><td class="event-num">8</td><td class="event">Boys 1600 Meters</td><td class="start-list"><a href='start_list_29.html'>Start List</a></td><td class="results"><a href='results_29.html'>Results</a></td></tr>
<tr><td class="event-num">9</td><td class="event">Girls 3200 Meters</td><td class="start-list"><a href='start_list_30.html'>Start List</a></td><td class="results"></td></tr>
</tbody></table></div></div></div></body></html>"""

RESULTS = """<h3>#8 Boys 1600 Meters Results</h3>
<table class="results-table table"><thead><tr><th class="place">PL</th><th class="jd">JD</th><th class="athlete">Athlete</th><th class="year">Yr</th><th class="team">Team</th><th class="time">Time</th><th class="points">Pts</th></tr></thead><tbody>
<tr><td class="place">1</td><td class="jd"></td><td class="athlete">Aaron ALPHA</td><td class="year">11</td><td class="team">NORTHFIELD HIGH SCHOOL</td><td class="time">4:43.56</td><td class="points">10</td></tr>
<tr><td class="place">2</td><td class="jd"></td><td class="athlete">Ben VAN BRAVO</td><td class="year">12</td><td class="team">SOUTH HARBOR</td><td class="time">4:43.59 q</td><td class="points">&nbsp;</td></tr>
<tr><td class="place"></td><td class="jd"></td><td class="athlete">Carl CHARLIE</td><td class="year">10</td><td class="team">SOUTH HARBOR</td><td class="time">DNF</td><td class="points"></td></tr>
</tbody></table>
<h3>Finals Section: 1</h3>
<table class="results-table table"><thead><tr><th class="hplace">HPL</th><th class="athlete">Athlete</th><th class="team">Team</th><th class="time">Time</th></tr></thead><tbody>
<tr><td class="hplace">1</td><td class="athlete">Aaron ALPHA</td><td class="team">NORTHFIELD HIGH SCHOOL</td><td class="time">4:43.56</td></tr>
</tbody></table>"""


def test_the_meet_index():
    idx = M.parseMeetIndex(INDEX)
    assert (idx.name, idx.place, idx.date) == ("Lakeside Region Meet", "Lakeside, KS", "2022-05-20")
    assert [(e.number, e.gender, e.meters, e.results_href, e.relay) for e in idx.events] == [
        (2, "M", 800.0, "results_28.html", True),
        (8, "M", 1600.0, "results_29.html", False),
        (9, "F", 3200.0, "", False)]           # no results posted yet


def test_results_overall_table_only():
    rows = M.parseResults(RESULTS)
    assert len(rows) == 3                    # the heat table's repeat is not read
    a = rows[0]
    assert (a.place, a.last, a.first, a.grade, a.school, a.seconds, a.points) == \
        (1, "Alpha", "Aaron", "11", "NORTHFIELD HIGH SCHOOL", 283.56, 10)
    assert (rows[1].last, rows[1].first, rows[1].seconds, rows[1].points) == \
        ("Van Bravo", "Ben", 283.59, None)
    assert (rows[2].place, rows[2].mark, rows[2].seconds) == (None, "DNF", None)


def test_no_grade_column_is_an_empty_grade():
    page = RESULTS.replace('<th class="year">Yr</th>', "").replace('<td class="year">11</td>', "") \
        .replace('<td class="year">12</td>', "").replace('<td class="year">10</td>', "")
    assert [r.grade for r in M.parseResults(page)] == ["", "", ""]
