"""Race Roster (sources/raceroster.py), off real responses fetched
2026-11-10 (organizer contact fields stripped): Paul Enke XC Invitational
(MO, Start 2 Finish; structured rows with School and Grade columns) and the
Keokuk Invitational varsity girls (IA, ERC Timing; a fixed-width text report).

    python -m pytest -q tests/test_sources_raceroster.py
"""
import asyncio
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sources import raceroster as R                              # noqa: E402

FIX = os.path.join(_ROOT, "tests", "fixtures", "raceroster")


def raw(name):
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def test_the_meet_is_its_local_day_and_its_races_less_the_team_scores():
    ev = R.parseEvent(raw("event_paul_enke_2026.json"))
    assert (ev["name"], ev["date"], ev["state"]) == ("Paul Enke XC Invitational", "2026-09-19", "MO")
    assert [s["name"] for s in ev["subs"]] == ["Boys Varsity", "Girls Varsity", "Boys JV", "Girls JV",
                                               "Boys Freshman", "Girls Freshman"]
    assert R.localDate("2026-09-03T23:30:00.000Z", "America/Chicago") == "2026-09-03"
    assert R.localDate("2026-09-04T02:30:00.000Z", "America/Chicago") == "2026-09-03"


def test_structured_rows_map_by_label_not_column():
    # "Team Place" sits on teamName; the school is a custom column labelled School
    rows = R.structuredRows(raw("rows_paul_enke_boys_varsity.json"))
    assert len(rows) == 75
    first = rows[0][0]
    assert (first.place, first.first, first.last, first.grade, first.school, first.seconds) == \
        (1, "William", "Leary", "11", "Parkway South", 1043.4)
    assert [r.place for r, _g in rows] == list(range(1, 76))


def test_a_text_report_is_read_by_its_header_columns():
    rows = R.textRows(raw("text_keokuk_2026_varsity_girls.txt").decode())
    assert len(rows) == 44
    a = rows[0][0]
    assert (a.place, a.first, a.last, a.grade, a.school, a.seconds) == \
        (1, "Hannah", "Ritter", "10", "Notre Dame / West Burlington", 1265.9)
    assert [r.place for r, _g in rows] == list(range(1, 45))
    assert all(r.school and r.grade for r, _g in rows)


def test_the_title_gives_the_gender_and_a_gender_column_splits_one_that_does_not():
    rows = R.structuredRows(raw("rows_paul_enke_boys_varsity.json"))
    (e,) = R.events("Boys Varsity", rows, 1, 5000)
    assert (e.gender, e.meters, e.sport, len(e.rows)) == ("M", 5000, "XC", 75)
    mixed = [(r, "F" if i % 3 == 0 else "M") for i, (r, _g) in enumerate(rows)]
    ev = R.events("Open 5K", mixed, 2, 5000)
    assert [(e.gender, len(e.rows)) for e in ev] == [("F", 25), ("M", 50)]


def test_road_races_are_not_school_meets():
    assert R.isSchool({"name": "Paul Enke XC Invitational", "country": "US", "subs": []})
    assert R.isSchool({"name": "Keokuk Invitational", "country": "US", "subs": []})
    assert not R.isSchool({"name": "Turkey Trot 5K Run/Walk", "country": "US", "subs": []})
    assert not R.isSchool({"name": "Bay College XC High School Invitational", "country": "CA", "subs": []})


class _Store:
    def meta(self, url):
        return {}


class _Api:
    async def fetch(self, url, max_age=None):
        from sources.fetch import Fetched
        if "/results?" in url:
            body = raw("rows_paul_enke_boys_varsity.json") if "306146" in url else json.dumps(
                {"data": [], "meta": {"flattenedColumns": []}}).encode()
        else:
            body = raw("event_paul_enke_2026.json")
        return Fetched(url, 200, body, True, "")

    store = _Store()


def test_a_meet_lands_its_races():
    lm, _why = asyncio.run(R.readMeet(_Api(), "weqpcfuabssbbxws",
                                      R.eventKey(141714, 2026), 0))
    assert lm.source == "timer:race-roster" and lm.date == "2026-09-19" and lm.state == "MO"
    assert [(r.gender, r.meters, len(r.rows)) for r in lm.races] == [("M", 5000, 75)]


def test_the_titles_distance_wins_over_the_placeholder():
    # Texas Youth State XC 2026: every race at distanceMeters 1000
    assert R.raceMeters("BOYS 4K", 1000) == 4000
    assert R.raceMeters("Boys Varsity", 5000) == 5000
    assert R.raceMeters("Boys Varsity", 1000) is None
    assert R.raceMeters("Girls 3 Mile Overall Results", 4828) == 4828


def test_an_open_race_with_no_school_or_grade_is_left_out():
    # Big Timber XC Challenge 2026: no runner with a school or a grade
    d = json.loads(raw("rows_paul_enke_boys_varsity.json"))
    for r in d["data"]:
        r["custom38141490"], r["custom38141491"] = "", ""
    bare = json.dumps(d).encode()

    class _Bare(_Api):
        async def fetch(self, url, max_age=None):
            from sources.fetch import Fetched
            if "/results?" in url and "306146" in url:
                return Fetched(url, 200, bare, True, "")
            return await _Api.fetch(self, url, max_age)
    lm, _ = asyncio.run(R.readMeet(_Bare(), "c", "k", 0))
    assert lm.races == [] and lm.skipped == 75


def test_a_text_report_with_a_ruler_and_no_time_heading():
    # OnlineRaceResults' 2025 Muscatine Invitational on Race Roster: "Place
    # TmPl No. First Name Gr School T4 Pace" over a ruler of =; the time
    # column is "T4". Before: 17 of the meet's 418 runners read.
    rows = R.textRows(raw("text_orr_muscatine_2025_jv_male.txt").decode())
    assert len(rows) == 26
    a = rows[0][0]
    assert (a.place, a.first, a.last, a.grade, a.school, a.seconds) == (1, "Dylon", "Mason", "12",
                                                                      "Muscatine", 1158.0)
    assert [r.place for r, _g in rows] == list(range(1, 27))
    assert all(r.seconds > 1000 for r, _g in rows)          # the time, never the pace


def test_the_archive_redirect_is_resolved_without_following_it():
    assert R.ORR_REDIRECT.format(25701).endswith("entity=event&id=25701")
