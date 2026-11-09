"""Timer report layouts and landing rules found while making every enabled
timing company produce results (2026-11-09). One small synthetic fixture
per class; no real athletes' names.

    python -m pytest -q tests/test_sources_timer_layouts.py
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sources import dedup, discover, hytek, land, meetpro, meetreport      # noqa: E402
from sources.fetch import Fetched                                           # noqa: E402


# ------------------------------------------------------------- MeetPro's print, one column
ONE_COLUMN = """RIVERBEND CONFERENCE CHAMPIONSHIPS 2026                         MEET OFFICIALS
Lakeport, LA                                                    Meet Director:
Hillside Park
Friday, October 30, 2026                                        www.timer.example       printed: 10/31/2026 9:06 AM


 Race #1
 6K Women, 8:30am                                                                       Final Results
TEAM SCORING SUMMARY
Final Standings                     Score   Scoring Order                      Total     Avg.     Spread
  1   NORTH STATE                    32     2-3-6-8-13(17)(18)                 1:42:30 20:30       1:02.6

INDIVIDUAL RESULTS
Athlete                              YR      #    Team (Team Place)   Score   Time        Gap      Avg. Mile     Avg. kM     Split 1        Split 2
  1   ALPHA, Anna                    JR     92    NORTH STATE (1        1     18:53.6     ---    5:03.9        3:08.9       4:52.2         9:54.6

  2   BRAVO, Beth                    SO     84    SOUTH STATE           2     19:59.7   1:06.1   5:21.6        3:19.9       5:05.1         10:28.8

  3   CHARLIE, Cara                  FR     83    SOUTH STATE           -     19:59.9     .2     5:21.7        3:20.0       5:15.4         10:24.6
"""

NO_GRADE = """FALL CLASSIC XC                                                MEET OFFICIALS
Lakeport, MO
Sunday, November 9, 2025                                        OFFICIAL MEET REPORT


 Race #1
 Varsity Boys 5K                                                                          Final Results
INDIVIDUAL RESULTS
Athlete                     YR      #     Team                Score   Time        Gap      Avg. Mile   Avg. kM
  1   DELTA, Dan                   1197   North High           1        16:01.1    13.2    5:16.3       3:30.5
  2   ECHO, Eli                    1264   Unattached           -        16:15.3    27.4    5:27.8       3:37.6
"""


def test_the_one_column_report_reads_bib_gap_paces_and_splits():
    meet = meetreport.parseReport(ONE_COLUMN)
    assert meet.date == "2026-10-30"           # the print stamp on that line is not the date
    rows = meet.events[0].rows
    assert [(r.last, r.grade, r.school, r.seconds) for r in rows] == [
        ("ALPHA", "JR", "NORTH STATE", 1133.6), ("BRAVO", "SO", "SOUTH STATE", 1199.7),
        ("CHARLIE", "FR", "SOUTH STATE", 1199.9)]
    assert meet.events[0].sport == "XC"


def test_a_bib_with_no_grade_is_not_the_team():
    rows = meetreport.parseReport(NO_GRADE).events[0].rows
    assert [(r.last, r.grade, r.school, r.seconds) for r in rows] == [
        ("DELTA", "", "North High", 961.1), ("ECHO", "", "Unattached", 975.3)]


# ------------------------------------------------------------- RunMeet's report
RUNMEET = """                                                              Thu, Mar 12, 11:45 am

                                     Valley Conference Championships
                                        Lakeside Park | Tue, Oct 21, 2025
                                                  Results

Girls 5,000 meters Varsity
Team Scores
Pl     Team                              Score                Scoring Order                         Total
1      North                             15         1     2    3    4    5     (6)                  2:01:20.3

Individual Results
Pl     Pts              Name                            Yr Team                              Time               Gap           Av Mile      Av Km
1      1        #1105   Alpha, Anna                      9 NORTH                             22:58.7            --            7:23.8       4:35.8
2      --       #1093   Bravo, Beth                     10 SOUTH                             23:13.7            15.1          7:28.6       4:38.8
13     (6)      #1109   Charlie, Cara                    9 NORTH                             28:42.0            5:43.4        9:14.3       5:44.4
"""


def test_runmeet_reports_read_heading_rows_and_date():
    assert meetreport.isRunMeet(RUNMEET) and not meetreport.isReport(RUNMEET)
    meet = meetreport.parseRunMeet(RUNMEET)
    assert (meet.name, meet.date, meet.venue) == ("Valley Conference Championships", "2025-10-21", "Lakeside Park")
    e = meet.events[0]
    assert (e.title, e.gender, e.meters, e.sport) == ("Girls 5,000 meters Varsity", "F", 5000.0, "XC")
    assert [(r.place, r.first, r.last, r.grade, r.school) for r in e.rows] == [
        (1, "Anna", "Alpha", "9", "NORTH"), (2, "Beth", "Bravo", "10", "SOUTH"), (13, "Cara", "Charlie", "9", "NORTH")]


# ------------------------------------------------------------- tab-separated export
TABBED = ("Boys Class C - 5K Run\nPlace\tAthlete\tGrade\tSchool\tTime\n"
          "1\tAdam Alpha\t11\tNorth High\t15:19.3\n2\tBen Bravo\t12\tSouth High\t16:03.8\n"
          "3\tCal Charlie\t9\tSouth High\tDNF\n\n"
          "Girls Class C - 5K Run\nPlace\tAthlete\tGrade\tSchool\tTime\n1\tDana Delta\t10\tNorth High\t19:00.0\n")


def test_a_tab_separated_export_names_its_meet_from_file_and_folder():
    url = "https://timer.example/2026/cross/11-07-26-SectionV/results-Section%20V%20Championships.html"
    assert meetreport.isTabbed(TABBED)
    meet = meetreport.parseTabbed(TABBED, url)
    assert (meet.name, meet.date) == ("Section V Championships", "2026-11-07")
    assert [(e.title, e.gender, len(e.rows)) for e in meet.events] == [
        ("Boys Class C - 5K Run", "M", 3), ("Girls Class C - 5K Run", "F", 1)]
    boys = meet.events[0].rows
    assert boys[1].school == "South High" and (boys[2].mark, boys[2].seconds, boys[2].place) == ("DNF", None, None)


# ------------------------------------------------------------- Hy-Tek "(M)"
HYTEK_LETTER = """ Timer, LLC - Contractor License                      HY-TEK's Meet Manager
                        Valley XC Championships - 11/7/2026
                              Riverside Park

Event 1  (M) 8k Run CC
=======================================================================
    Name                    Year School                  Finals  Points
=======================================================================
  1 Alpha, Adam               SR North                 23:07.34    1
  2 Bravo, Ben                JR South                 23:25.02    2
"""


def test_a_hytek_event_may_name_its_gender_as_a_letter():
    meet = hytek.parseReport(HYTEK_LETTER)
    e = meet.events[0]
    assert (e.gender, e.title, e.meters, len(e.rows)) == ("M", "Men 8k Run CC", 8000.0, 2)


# ------------------------------------------------------------- MeetPro one-page, template v1.0
V10 = """<!--DO NOT ERASE! VERSION:&MeetProTemplate_v1.0--><body>
<div class="col-sm-6 nav-title"><h4>Ridge Invite XC</h4></div>
<div class="col-sm-4 col-md-5 m-info"><p></p><p>Ridge, ID</p><p>Hill GC</p><p>9/8/2016</p></div>
<section id="event3" class="event"><p class="bold-cell">Race #1</p><p class="bold-cell">Boys Varsity</p>
<table class="table"><thead><tr><th class="team-standings">Final Standings</th><th class="scoring-order">Scoring Order</th></tr></thead>
<tbody><tr><td class='team-place'>1</td><td class='scoring-order'>1-2-3-4-5</td></tr></tbody></table>
<table class="table table-striped table-condensed"><thead><tr><th class="athlete-hdr" colspan="2">Athlete</th>
<th class="ath-team">Team</th><th class="ath-score">Score</th><th class="time">Time</th></tr></thead><tbody>
<tr><td class="ath-place">1</td><td class="ath-name">Alpha, Adam</td><td class="ath-team">North</td><td class="ath-score">1</td><td class="time">18:11.0</td></tr>
<tr><td class="ath-place">2</td><td class="ath-name">Bravo, Ben</td><td class="ath-team">South</td><td class="ath-score">2</td><td class="time">18:15.3</td></tr>
</tbody></table></section></body>"""


def test_the_v1_meetpro_one_page_reads_its_ath_cells():
    assert meetpro.isSinglePage(V10)
    idx, res = meetpro.parseSinglePage(V10)
    assert (idx.name, idx.date, idx.place) == ("Ridge Invite XC", "2016-09-08", "Ridge, ID")
    assert [(r.place, r.last, r.school, r.seconds, r.points) for r in res[3]] == [
        (1, "Alpha", "North", 1091.0, 1), (2, "Bravo", "South", 1095.3, 2)]
    assert idx.events[0].xc


# ------------------------------------------------------------- landing rules
def _race(n, title, rows):
    return land.LandedRace(n=n, title=title, gender="", sport="XC", meters=2000.0,
                           rows=[land.LandedRow(name=nm, first="", last=nm, grade="", school="X",
                                                place=i + 1, seconds=s, mark="", status=None)
                                 for i, (nm, s) in enumerate(rows)])


def test_a_combined_race_that_reprints_the_gendered_ones_lands_once():
    both = _race(1, "Boys and Girls 2K", [("a", 500), ("b", 510), ("c", 520), ("d", 530)])
    boys = _race(2, "Boys 2K", [("a", 500), ("c", 520)])
    girls = _race(3, "Girls 2K", [("b", 510), ("d", 530)])
    other = _race(4, "Varsity 5K", [("e", 900), ("f", 910)])
    assert [r.n for r in land.withoutReprints([both, boys, girls, other])] == [2, 3, 4]
    copy = _race(5, "Varsity 5K", [("e", 900), ("f", 910)])
    assert [r.n for r in land.withoutReprints([other, copy])] == [4]


def test_age_group_and_community_races_are_not_school_races():
    for t in ("Boys 8 and Under 2K", "Girls 9-10 Year Old 3K", "11-12 yr old 3K", "Bantam Girls", "14U Boys 3K"):
        assert land.isAgeGroup(t), t
    for t in ("Varsity Boys 5K", "Girls 3200 Meters", "Boys Class 2 - 5K Run", "JV 2 Mile"):
        assert not land.isAgeGroup(t), t
    town = [hytek.Row(place=i, last="x", first="y", grade="", school=s, seconds=1000.0 + i, mark="")
            for i, s in enumerate(["Lakeport, CA", "Ridge, CA", "Lakeport, CA", "North High"], 1)]
    school = [hytek.Row(place=i, last="x", first="y", grade="", school=s, seconds=1000.0 + i, mark="")
              for i, s in enumerate(["North High", "South", "St. Mark's", "Lakeport, CA"], 1)]
    assert land.isCommunity(town) and not land.isCommunity(school)


def test_a_timers_second_copy_is_a_twin_of_the_first():
    landed = [dedup.LRow(1, k, nm, ts, "North") for k, (nm, ts) in
              enumerate([("Ann Alpha", 1133.6), ("Beth Bravo", 1199.7), ("Cara Charlie", 1199.9)])]
    feed = [dedup.FRow("ext", 7, 1, nm, ts, "NORTH STATE") for _r, _k, nm, ts, _s in landed]
    v = dedup.judge(landed, feed, "XC")[1]
    assert v.twin and v.source == "ext" and v.meet_id == 7


# ------------------------------------------------------------- discovery
def test_programs_manuals_and_entries_are_not_results_but_a_distance_named_file_is():
    page = ('<a href="/f/meet-program-men.pdf">Men Meet Program</a>'
            '<a href="/f/Participant_Manual_2026.pdf">Participant Manual</a>'
            '<a href="/f/female_entries_by_team_2026.pdf">Declared Women Entries</a>'
            '<a href="/f/men-8k.pdf">Men</a><a href="/f/5000m-girls.pdf">Girls</a>')
    got = [u for u, _k in discover.candidates(page, "https://www.timer.example/meets/1", 2026)]
    assert got == ["https://www.timer.example/f/men-8k.pdf", "https://www.timer.example/f/5000m-girls.pdf"]


def test_a_short_year_in_link_text_dates_the_link():
    assert discover._years("Ridge Invite XC 9-8-16") == [2016]
    assert discover.tooOld("https://timer.example/x/", "Ridge Invite XC 9-8-16", 2026)
    assert not discover.tooOld("https://timer.example/x/", "Ridge Invite 10/21/26", 2026)
    assert discover._years("1600m 4:12.55") == []


class Pages:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    async def fetch(self, url, max_age=None):
        self.asked.append(url)
        body = self.pages.get(url)
        return Fetched(url, 200 if body else 404, body.encode() if body else None)


def test_an_own_site_index_htm_that_is_a_frameset_is_read_through_its_frames():
    base = "https://timer.example/STATE_XC/"
    fetcher = Pages({
        "https://timer.example/results/": '<a href="/STATE_XC/index.htm">State XC</a>',
        base + "index.htm": '<iframe src="header.htm"></iframe><iframe id="menu" src="menu.htm"></iframe>',
        base + "header.htm": "<h2>State XC Championships</h2>",
        base + "2026/Valley/Results/c2_boys.htm": "<pre>HY-TEK's Meet Manager</pre>",
        base + "menu.htm": ('<a href="2026/Valley/Results/c2_boys.htm">Class 2 Boys</a>'
                            '<a href="2025/Valley/Results/c2_boys.htm">Class 2 Boys</a>'),
    })
    src = type("S", (), {"results_url": "https://timer.example/results/", "website": "", "slug": "t"})()
    urls, _note = asyncio.run(discover.discover(fetcher, src, 2026, 5))
    assert urls == [(base + "2026/Valley/Results/c2_boys.htm", "hytek")]


# ------------------------------------------------------------- one-page MeetPro, a race scored by gender
def _section(num, title, names):
    rows = "".join(f'<tr><td class="place">{i}</td><td class="athlete">{n}</td><td class="year">7</td>'
                   f'<td class="ath-team">North</td><td class="time">12:0{i}.0</td></tr>'
                   for i, n in enumerate(names, 1))
    return (f'<section id="event{num}" class="event"><p class="bold-cell">Race #1</p>'
            f'<p class="bold-cell">{title}</p><table class="result-table"><thead><tr>'
            f'<th class="athlete-hdr">Athlete</th></tr></thead><tbody>{rows}</tbody></table></section>')


def test_two_sections_with_one_id_are_two_races_each_its_own_gender():
    page = ('<div class="nav-title"><h4>Valley All-Comers</h4></div>'
            + _section(5, "MEN &amp; WOMEN (Middle School 3K) • 3 Kilometers Boys", ["ALPHA, Al", "BRAVO, Bo"])
            + _section(5, "MEN &amp; WOMEN (Middle School 3K) • 3 Kilometers Girls", ["DELTA, Di"]))
    idx, res = meetpro.parseSinglePage(page)
    lm = land.fromMeetPro(idx, res, "t", "u")
    assert [(r.gender, [x.last for x in r.rows]) for r in lm.races] == [("M", ["ALPHA", "BRAVO"]), ("F", ["DELTA"])]


def test_a_title_naming_both_genders_is_the_last_one():
    assert meetpro._gender("MEN & WOMEN (High School) • 5 Kilometers Women's") == "F"
    assert meetreport._gender("Boys and Girls 2K Boys") == "M"
    assert meetpro._gender("Girls 3200 Meters") == "F" and meetpro._gender("Open 5K") == ""


def test_an_elementary_race_is_left_out_and_a_middle_school_race_is_not():
    def rows(grades):
        return [hytek.Row(place=i, last="x", first="y", grade=g, school="North", seconds=600.0 + i, mark="")
                for i, g in enumerate(grades, 1)]
    assert land.isElementary(rows(["3", "2", "K", "4", "7"]))
    assert not land.isElementary(rows(["6", "7", "8", "5", "8"]))
    assert not land.isElementary(rows(["FR", "SO", "", ""]))


def test_a_race_with_no_distance_run_in_cross_country_times_is_cross_country():
    def rows(secs):
        return [hytek.Row(place=i, last="x", first="y", grade="", school="", seconds=s, mark="")
                for i, s in enumerate(secs, 1)]
    assert land.sportByTimes("TF", None, rows([1500.0, 1520.0, 1600.0])) == "XC"
    assert land.sportByTimes("TF", None, rows([12.1, 12.4])) == "TF"
    assert land.sportByTimes("TF", 5000.0, rows([1000.0, 1010.0])) == "TF"


# ------------------------------------------------------------- data-attribute rows (EliteFeats)
DATA_PAGE = """<html><head><title>Valley XC Championships elitefeats results 11/8/26</title></head><body>
<div>Cross Country &middot; 11/8/26 &middot; Lakeport, NJ</div>
<select id="xrDivSelect"><option value="./?ID=7&amp;Div=Men+8K" selected="selected">Men 8K</option>
<option value="./?ID=7&amp;Div=Women+5K">Women 5K</option></select>
<table><tbody>
<tr data-bib="1" data-name="Adam Alpha" data-team="North (Del.)" data-grade="FR" data-div="Men 8K" data-sec="1566.8" data-place="1"><td>1</td></tr>
<tr data-bib="2" data-name="Ben Bravo" data-team="South" data-grade="SO" data-div="Men 8K" data-sec="1568.1" data-place="2"><td>2</td></tr>
<tr data-bib="3" data-name="Cal Charlie" data-team="South" data-grade="JR" data-div="Men 8K" data-sec="" data-place=""><td></td></tr>
</tbody></table></body></html>"""


def test_data_rows_pages_read_their_division_and_point_at_the_others():
    from sources import datarows
    url = "https://timer.example/xc-results?ID=7"
    assert datarows.isDataPage(DATA_PAGE) and discover.isReport(DATA_PAGE.encode())
    assert datarows.divisionUrls(DATA_PAGE, url) == ["https://timer.example/xc-results?ID=7&div=Women+5K"]
    meet = datarows.parsePage(DATA_PAGE, url)
    assert (meet.name, meet.date, meet.venue) == ("Valley XC Championships", "2026-11-08", "Lakeport, NJ")
    e = meet.events[0]
    assert (e.title, e.gender, e.meters, e.sport) == ("Men 8K", "M", 8000.0, "XC")
    assert [(r.place, r.first, r.last, r.grade, r.school, r.seconds) for r in e.rows] == [
        (1, "Adam", "Alpha", "FR", "North (Del.)", 1566.8), (2, "Ben", "Bravo", "SO", "South", 1568.1),
        (None, "Cal", "Charlie", "JR", "South", None)]
