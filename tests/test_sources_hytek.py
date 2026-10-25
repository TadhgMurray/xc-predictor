"""Hy-Tek Meet Manager reports from timing companies' own sites
(sources/hytek.py). Synthetic names; the layouts are the real ones
(YenTiming's Crystal Reports HTML, 2026-09-12; classic <pre> reports).

    python -m pytest -q tests/test_sources_hytek.py
"""
import os
import shutil
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import hytek as H                                    # noqa: E402

REPORT = """\
Sample Timing Services - Contractor License   Hy-Tek's MEET MANAGER  2:42 PM  9/12/2026  Page 1
                    Lakeside Cross Country Invitational - 9/12/2026
                                 Lakeside Park
                                    Results

Event 6  Boys 5k Run CC Varsity
=======================================================================
    Name                    Year School                  Finals  Points
=======================================================================
  1 Alpha, Aaron              11 Northfield               16:40    1
  2 Bravo, Ben                12 Northfield               18:22    2
  3 Charlie-Delta, Carl       10 South Harbor-Bay         18:32    3
  4 Echolongername, Maximilian12 East Ridge               18:43
  5 Foxtrot, Finn              9 P.S. 123 Academy       1:02:03.4    4
 -- Golf, Gus                 10 Northfield                 DNF
Team Scores
=======================================================================
    Rank Team                    Total    1    2    3    4    5   *6   *7
  1 Northfield                     27    1    2    6    8   10

Event 7  Girls 2 Mile Run CC JV
=======================================================================
    Name                    Year School                  Finals  Points
=======================================================================
  1 Hotel, Hana               SO Westbrook                12:31.20  1
Sample Timing Services - Contractor License   Hy-Tek's MEET MANAGER  2:42 PM  9/12/2026  Page 2
                    Lakeside Cross Country Invitational - 9/12/2026
Event 7  Girls 2 Mile Run CC JV
=======================================================================
    Name                    Year School                  Finals  Points
=======================================================================
  2 India, Iris               FR Westbrook                12:40.55  2
"""


def test_the_meet_header():
    m = H.parseReport(REPORT)
    assert (m.name, m.date, m.venue, m.licensee) == (
        "Lakeside Cross Country Invitational", "2026-09-12", "Lakeside Park",
        "Sample Timing Services")


def test_rows_by_column_and_time_from_the_right():
    ev = H.parseReport(REPORT).events[0]
    assert (ev.number, ev.gender, ev.meters) == (6, "M", 5000.0)
    first = ev.rows[0]
    assert (first.place, first.last, first.first, first.grade, first.school,
            first.seconds, first.points) == (1, "Alpha", "Aaron", "11", "Northfield", 1000.0, 1)
    assert ev.rows[2].school == "South Harbor-Bay" and ev.rows[2].last == "Charlie-Delta"
    # a name long enough to run into the grade column keeps its grade
    assert (ev.rows[3].last, ev.rows[3].first, ev.rows[3].grade, ev.rows[3].points) == \
        ("Echolongername", "Maximilian", "12", None)
    # a digit in the school name, an hour-long mark
    assert ev.rows[4].school == "P.S. 123 Academy" and ev.rows[4].seconds == 3723.4
    assert (ev.rows[5].place, ev.rows[5].mark, ev.rows[5].seconds) == (None, "DNF", None)
    assert ev.skipped == 0


def test_team_scores_are_not_runners():
    ev = H.parseReport(REPORT).events[0]
    assert len(ev.rows) == 6 and all(r.last != "Northfield" for r in ev.rows)


def test_a_page_break_continues_the_same_event():
    m = H.parseReport(REPORT)
    assert [e.number for e in m.events] == [6, 7]
    girls = m.events[1]
    assert (girls.gender, girls.meters) == ("F", 3218.7)
    assert [(r.last, r.grade, r.seconds) for r in girls.rows] == \
        [("Hotel", "SO", 751.2), ("India", "FR", 760.55)]


def test_positioned_html_rebuilds_the_lines():
    lines = REPORT.splitlines()[:14]
    divs = []
    for i, line in enumerate(lines):
        if line.strip():
            text = line.replace(" ", "&nbsp;")
            divs.append(f'<div class="a" nowrap="true" style="z-index:25;top:{i * 15}px;'
                        f'left:0px;width:768px;height:15px;"><span class="f">{text}</span></div>')
    page = "<HTML><BODY>" + "\n".join(divs) + \
        '<a href="Varsity Boys2.html">[Next Page&gt;]</a></BODY></HTML>'
    m = H.parseReport(H.textFromPositionedHtml(page))
    assert m.name == "Lakeside Cross Country Invitational" and len(m.events[0].rows) == 5
    assert H.nextPageLinks(page) == ["Varsity Boys2.html"]


def test_pre_pages_and_the_pick():
    page = "<html><body><pre>" + REPORT.replace("&", "&amp;") + "</pre></body></html>"
    assert H.textFromAny(page.encode()).strip().startswith("Sample Timing")
    assert len(H.parseReport(H.textFromAny(page.encode())).events) == 2


@pytest.mark.parametrize("title,meters", [
    ("5k Run CC Varsity", 5000.0), ("3200 Meter Run", 3200.0), ("8000 Meter Run CC", 8000.0),
    ("2 Mile Run CC JV", 3218.7), ("Three Mile Run", 4828.0), ("Open Run", None)])
def test_distances(title, meters):
    assert H.metersOf(title) == meters


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler-utils not installed")
def test_pdf_goes_through_pdftotext():
    assert callable(H.textFromPdf)


def test_gender_last_on_the_event_line():
    """LiveTrackResults youth pages: 'Event 1  2000 Meter Run CC 8 & Under Division Girls'"""
    text = REPORT.replace("Event 6  Boys 5k Run CC Varsity",
                          "Event 6  2000 Meter Run CC 8 & Under Division Boys")
    ev = H.parseReport(text).events[0]
    assert (ev.number, ev.gender, ev.meters, len(ev.rows)) == (6, "M", 2000.0, 6)


def test_a_line_that_merely_starts_with_event_is_not_an_event():
    m = H.parseReport(REPORT.replace("Results\n", "Event schedule changed due to weather\n", 1))
    assert [e.number for e in m.events] == [6, 7]


RANKINGS = """Lakeside Timing - Contractor License Hy-Tek's MEET MANAGER  2:43 PM  9/12/2026  Page 1
Lakeside Cross Country Invitational - 9/12/2026
Lakeside Park
Rankings
Boys 2400 Meter Run CC Modified
      Comp#  Name                            School                           Time Points
=========================================================================================
    1   200  Alder, Aaron                 8  Northfield                      11:09
    2   210  Birch, Ben                   9  South Harbor                    11:29.40    1
"""


def test_a_rankings_report_has_no_event_line_and_a_bib_column():
    m = H.parseReport(RANKINGS)
    assert [(e.gender, e.title, e.meters) for e in m.events] == [("M", "2400 Meter Run CC Modified", 2400.0)]
    a, b = m.events[0].rows
    assert (a.place, a.last, a.first, a.grade, a.school, a.seconds) == (1, "Alder", "Aaron", "8", "Northfield", 669.0)
    assert (b.last, b.grade, b.school, b.seconds, b.points) == ("Birch", "9", "South Harbor", 689.4, 1)
