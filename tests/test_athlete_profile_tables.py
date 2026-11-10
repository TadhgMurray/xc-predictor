# Project: xc-predictor / tests
# File:    test_athlete_profile_tables.py
# Purpose: the athlete page's season tables after the profile pass
#          (owner, 2026-11-10): the rating beside the result with its 5K
#          under it, seasons that fold with only the newest open, track
#          events in ink with the outdoor 0.0% course cells blank, meet
#          names without the feed's season tag, Mile / 2 Mile, a 5K for
#          middle school too, the page opening on the sport raced last, and
#          the state grade rank naming its scope.

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))

TEMPLATE = os.path.join(ROOT, "racecast", "templates", "athlete.html")


def _src(path):
    return open(path, encoding="utf-8").read()


def _render(sport, races, open_=True, pool="hs_m"):
    pytest.importorskip("flask")
    import jinja2
    import app as A
    env = A.app.jinja_env.overlay(undefined=jinja2.ChainableUndefined)
    with A.app.test_request_context("/athlete/7"):
        m = env.get_template("athlete.html").make_module(
            vars={"athlete": {"name": "Owen X", "person_id": 7,
                              "rating": 124.6, "rating_hs": 124.6}})
        return str(m.season_block(2026, sport, {
            "races": races, "rating": 124.8, "rating_hs": 124.8,
            "school": "Jesuit", "pool": pool, "grade": "11"}, open=open_))


def _race(**kw):
    r = dict(result_id=5, date="2026-10-31", meet="State", sport="XC",
             event="5000", result="14:38.50", time_raw=878.5, place=2,
             speed_rating=125.8, hs_rating=125.8, difficulty=0.134,
             day_effect=None, pool="hs_m")
    r.update(kw)
    return r


def _ths(out):
    return re.findall(r'<th class="(c-[a-z]+)', out)


def test_the_rating_sits_beside_the_result_with_its_5k_under_it():
    out = _render("XC", [_race()])
    assert _ths(out) == ["c-date", "c-meet", "c-res", "c-rt", "c-pl", "c-co", "c-rec"]
    # no 5K column any more: the 5K rides in the rating cell
    assert 'class="fk' not in out.split("<tbody>")[0]
    cell = re.search(r'<td class="c-rt"[^>]*>(.*?)</td>', out, re.S).group(1)
    assert 'class="rv"' in cell
    # the date also under the meet, for phones (the date column hides there)
    assert 'class="meet-date"' in out


def test_only_the_newest_season_starts_open_and_a_closed_one_says_what_it_was():
    first = _render("XC", [_race(), _race(result_id=6, hs_rating=130.1,
                                          speed_rating=130.1, meet="Regional")])
    later = _render("XC", [_race()], open_=False)
    assert re.search(r'<details class="season" id="2026-XC" open>', first)
    assert re.search(r'<details class="season" id="2026-XC">', later)
    # the race count beside the year, the best race in the meta line
    assert "2 races" in first and "1 race<" in later
    best = re.search(r'class="sb-best">(.*?)</p>', first, re.S).group(1)
    assert "130.1" in best and "Regional" in best


def test_a_track_season_of_outdoor_races_has_no_course_column():
    out = _render("TF", [_race(sport="TF", event="3200m", difficulty=0.0)])
    assert "c-co" not in out


def test_an_indoor_race_keeps_the_course_column_and_outdoor_cells_go_blank():
    import difficulty_view as DV
    indoor = 0.02
    if DV.diffPct(indoor, "TF") in ("0.0%", " - "):
        pytest.skip("no track difficulty scale offline")
    out = _render("TF", [_race(sport="TF", event="3200m", difficulty=0.0, result_id=1),
                         _race(sport="TF", event="3200m", difficulty=indoor, result_id=2)])
    cells = re.findall(r'<td class="c-co">(.*?)</td>', out, re.S)
    assert len(cells) == 2 and cells[0].strip() == "" and "%" in cells[1]


def test_meet_names_lose_the_feeds_season_tag_but_keep_it_in_the_title():
    out = _render("TF", [_race(sport="TF", event="1600m", meet="UIL 6A District 05 TF26",
                               meet_id=9, event_id=3, div_id=4)])
    assert ">UIL 6A District 05</a>" in out
    assert 'title="UIL 6A District 05 TF26"' in out


def test_mile_and_two_mile_read_as_the_side_cards_say_them():
    pytest.importorskip("flask")
    import app as A
    assert A.event_label("1mile") == "Mile"
    assert A.event_label("2miles") == "2 Mile"
    assert A.event_label("5000") == "5000m"
    assert A.event_label("3200m") == "3200m"
    assert A.meet_clean("Nike XC Town Twilight") == "Nike XC Town Twilight"
    assert A.meet_clean(None) is None


def test_middle_school_reads_a_5k_too():
    import conversions as cv
    pools = (("hs_m", 1247.6), ("ms_m", 1420.0))
    cv._scale["map"] = {(p, s): (pm, -0.025, -0.0253) for p, pm in pools for s in ("XC", "TF")}
    cv._scale["at"] = 1e18
    cv._offsets["map"], cv._offsets["at"] = {}, 1e18
    cv._gain["map"], cv._gain["at"] = {}, 1e18
    cv._clock_tables.clear()
    assert cv.fiveK(110.0, "ms_m", "XC")["dist"] == "3200m"     # the old reading
    got = cv.fiveKHs(110.0, "ms_m", "XC", 120.0)
    assert got == cv.fiveK(120.0, "hs_m", "XC") and got["dist"] == "5K"
    # high school is untouched, and no HS value keeps the 3200
    assert cv.fiveKHs(124.6, "hs_m", "XC", 124.6) == cv.fiveK(124.6, "hs_m", "XC")
    assert cv.fiveKHs(110.0, "ms_m", "XC", None)["dist"] == "3200m"


def test_the_page_opens_on_the_sport_raced_last():
    src = _src(TEMPLATE)
    assert "data-default" in src and "_lead" in src
    js = _src(os.path.join(ROOT, "racecast", "static", "race-page.js"))
    assert '[data-tab][data-default]' in js
    # a link into a closed season opens it
    assert "function openFolds" in js and "openFolds(target())" in js


def test_the_state_grade_rank_names_its_scope():
    src = _src(os.path.join(ROOT, "racecast", "templates", "_grade_rank.html"))
    assert "</a> in {{ g.state }}" not in src
    assert "</a> of {{ g.state }}" in src
    assert ", all grades" in _src(TEMPLATE)


def test_chart_year_labels_never_overlap():
    js = _src(os.path.join(ROOT, "racecast", "static", "athlete-charts.js"))
    assert js.count("lastRight") >= 3 and js.count("lastRight2") >= 3


def test_badges_a_race_earns_by_default_are_not_shown():
    # owner, 2026-11-10: a first race is a PR, SR, CR, R and RSR by
    # definition; a PR already says SR and an R already says RSR
    pytest.importorskip("flask")
    import app as A

    def r(rid, date, t, rt, ev="5000", course="A", season="2026"):
        return {"result_id": rid, "date": date, "time_raw": t, "speed_rating": rt,
                "event": ev, "course": course, "season_label": season, "sport": "XC",
                "is_pr": True, "is_sr": True, "is_course_pr": True, "is_rating_pr": True,
                "is_rating_sr": True, "is_star_pr": False, "is_star_course": False,
                "is_star_rating": False}

    races = [r(1, "2026-09-01", 1000, 110.0),            # first of everything
             r(2, "2026-09-15", 990, 112.0),             # faster, same course
             r(3, "2026-10-01", 995, 111.0, course="B")]  # slower, a new course
    races[2].update(is_pr=False, is_rating_pr=False)     # it was neither
    by_distance = lambda x: x["event"]
    by_course = lambda x: x["course"]
    by_season_d = lambda x: (x["season_label"], x["sport"], x["event"])
    by_season = lambda x: (x["season_label"], x["sport"])
    A.quietAutomaticFlags(races, by_distance, by_course, by_season_d, by_season,
                          lambda x: x["time_raw"], lambda x: x["speed_rating"])
    first, second, third = races
    assert not any(first[k] for k in ("is_pr", "is_sr", "is_course_pr",
                                      "is_rating_pr", "is_rating_sr"))
    # a real PR and R: shown, and they stand in for SR and RSR
    assert second["is_pr"] and second["is_rating_pr"] and second["is_course_pr"]
    assert not second["is_sr"] and not second["is_rating_sr"]
    # a first run on course B is not a course record worth a badge
    assert not third["is_course_pr"]


def test_team_place_always_shows():
    # owner, 2026-11-10: "I prefer x on team always being there"
    for out in (_render("XC", [_race(team_place=1, team_n=3)]),
                _render("TF", [_race(sport="TF", event="1600m", team_place=1)])):
        assert "1st on team" in out


def test_rating_records_compare_hs_equivalents():
    # Newberry, 2026-11-10: an MS race's own-pool number beat his best race
    src = _src(os.path.join(ROOT, "racecast", "app.py"))
    assert 'rating_of   = lambda r: (r.get("hs_rating") if r.get("hs_rating") is not None' in src


def test_a_class_never_stands_beside_a_state_division():
    # Newberry, 2026-11-10: "TX D6A · Class 5A" off a meet naming both
    import school_units as SU
    row = {"state_div": "6A", "state": "TX"}
    for v in ("5A", "6A", "2", "AA", "Class D"):
        assert SU._classShadowed(v, row), v
    assert not SU._classShadowed("5A", {"state": "TX"})        # no division: it shows
    assert not SU._classShadowed("Catholic", row)               # not a classification


def test_a_league_label_drops_its_gender_word():
    # Josh Bodach, 2026-11-10: "IHSAA GIRLS" on a boy
    import school_units as SU
    assert "GIRLS" not in SU._label("league", "IHSAA GIRLS", False).upper()
    assert "BOYS" not in SU._label("league", "IHSAA BOYS", False).upper()


def test_no_follow_note_on_an_athlete_page():
    js = _src(os.path.join(ROOT, "racecast", "static", "follow.js"))
    assert "if (cfg.follow === 'athlete') return '';" in js
