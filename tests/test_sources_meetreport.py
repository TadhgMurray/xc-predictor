"""sources/meetreport.py: the non-Hy-Tek report layouts (2026-11-01)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import meetreport as M  # noqa: E402

# MeetPro's desktop print: the right half carries the race on from the left,
# and starts above the left half's own header
MEETPRO = """\
TEST XC INVITE
Town, MN
Test Golf Course
Wednesday, August 27, 2025
                                                                 OFFICIAL MEET REPORT
  Race #3
  Girls 5K                                                         Final Results
TEAM SCORING SUMMARY                                                       TEAM SCORING SUMMARY (cont'd)
Final Standings     Score Scoring Order                       Total        Athlete             YR Team           Score Time
 1   Alpha          49    4-5-7-13-20(27)                    1:47:52    4 DOE, Ann          JR Alpha          30       24:00.3
 2   Beta           51    6-8-9-10-18(19)                    1:48:06    5 ROE, Bea          SR Beta          (31)      24:06.4

INDIVIDUAL RESULTS
Athlete                        YR Team              Score Time
 1 SMITH, Cara                 SO Gamma               1      20:30.6
 2 JONES, Dee                     Alpha               2      20:40.0
 3 BROWN, Eve                   8 Beta                -      20:43.0
"""

# two columns, team points after the time, a long name wrapping
TWO_COL = """\
                     Test County Invitational
                              Parma
                     Race 4 - Girls Varsity - 5K
                         Individual Results
Place Name                Grade         Team            Time     Points   Place Name                Grade         Team               Time     Points
 1   Ann Able          12     Alpha Sutherland 17:43.5     1                  3 Cat Cole            11          Beta           20:14.5    3
 2   Bea Bell          11          Gamma        18:09.3     2                 4 Dee                  10          Delta          20:24.1    4
                                                                                Long-Name
"""

# a gap behind the winner after the time, which looks like a time itself
GAP = """\
TEST CLASSIC
  Race #1
  Boys 2 Mile                                     Final Results
TEAM SCORING SUMMARY
INDIVIDUAL RESULTS
Athlete                      YR Team                Score Time       Gap
 1 ACE, Al                    8 Alpha               1     11:43.7 ---
 2 BAY, Bo                    7 Beta                2     11:55.1 11.4
 3 COE, Cy                    8 Gamma               -     16:08.8 4:25.1
"""


def test_meetpro_report_reads_both_halves():
    m = M.parseReport(MEETPRO)
    assert m.name == "TEST XC INVITE" and m.date == "2025-08-27" and "MN" in m.venue
    (e,) = m.events
    assert e.title == "Girls 5K" and e.gender == "F" and e.meters == 5000 and e.sport == "XC"
    assert [r.place for r in e.rows] == [1, 2, 3, 4, 5]
    r = {x.place: x for x in e.rows}
    assert (r[4].last, r[4].first, r[4].grade, r[4].school, r[4].seconds) == ("DOE", "Ann", "JR", "Alpha", 1440.3)
    assert r[2].grade == "" and r[2].school == "Alpha"
    assert r[3].grade == "8" and r[3].school == "Beta"


def test_two_columns_points_and_wrapped_name():
    m = M.parseReport(TWO_COL, "https://r/2026/cross/10-31-26-TCI/girls.pdf")
    assert m.date == "2026-10-31"
    (e,) = m.events
    assert e.number == 4 and e.gender == "F" and e.meters == 5000
    r = {x.place: x for x in e.rows}
    assert sorted(r) == [1, 2, 3, 4]
    assert (r[1].first, r[1].last, r[1].grade, r[1].school, r[1].seconds) == ("Ann", "Able", "12", "Alpha Sutherland", 1063.5)
    assert (r[4].first, r[4].last, r[4].school) == ("Dee", "Long-Name", "Delta")


def test_gap_after_time_is_not_the_time():
    (e,) = M.parseReport(GAP).events
    assert e.meters and round(e.meters) == 3219
    assert [(r.place, r.seconds) for r in e.rows] == [(1, 703.7), (2, 715.1), (3, 968.8)]


def test_hytek_text_is_not_taken():
    assert not M.isReport("Hy-Tek's MEET MANAGER\nEvent 1  Boys 5k Run CC\n")
