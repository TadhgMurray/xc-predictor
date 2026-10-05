"""The state-meet projection (racecast/projections.py, owner 2026-10-04: "Who
wins state?").

What is pinned here is the WIRING, not new rules: the field is each school's
best seven by season rating, the race is scored by predict._score (five
scorers, two displacers, the unattached and short teams lifted out), and the
order is the season rating exactly as published -- with thin ratings flagged,
never adjusted (owner: "what are we supposed to do early season, they've only
run one race")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import projections as P  # noqa: E402


def _runner(pid, school, rating, n=3, grade="11"):
    return {"person_id": pid, "name": f"R{pid}", "school": school,
            "school_state": "CA", "grade": grade, "pool": "hs_m",
            "rating": rating, "n_races": n, "best_rating": rating + 1,
            "last_race": "2026-10-03"}


def _field():
    """Three full teams, one four-runner school, one unattached star.

    Ratings are chosen so the finish order is fully determined:
        A: 130 125 120 115 110 105 100 95   (eight: the eighth is not entered)
        B: 129 124 119 114 109 104 99
        C: 128 127 126 90 89 88 87
        D: 140 139 138 137                  (four runners: no team score)
        Unattached: 150
    """
    f = []
    pid = 1
    for school, ratings in (("A", (130, 125, 120, 115, 110, 105, 100, 95)),
                            ("B", (129, 124, 119, 114, 109, 104, 99)),
                            ("C", (128, 127, 126, 90, 89, 88, 87)),
                            ("D", (140, 139, 138, 137))):
        for r in ratings:
            f.append(_runner(pid, school, float(r)))
            pid += 1
    f.append(_runner(pid, "Unattached", 150.0, n=1))
    return f


def test_top_per_school_caps_teams_not_the_unattached():
    rows = [_runner(i, "A", 100.0 + i) for i in range(10)]
    rows += [_runner(100 + i, "Unattached", 90.0 + i) for i in range(9)]
    got = P.topPerSchool(rows)
    a = [r for r in got if r["school"] == "A"]
    assert len(a) == 7
    # the best seven, not the first seven
    assert sorted(r["rating"] for r in a) == [103.0 + i for i in range(7)]
    # nine unattached runners are nine individuals, not a capped squad
    assert len([r for r in got if r["school"] == "Unattached"]) == 9


def test_score_field_uses_standard_xc_scoring():
    field = P.topPerSchool(_field())
    teams, finishers = P.scoreField(field)

    # ★ the finish order is the rating order, everyone included
    ratings = [r["rating"] for r in finishers]
    assert ratings == sorted(ratings, reverse=True)
    assert finishers[0]["school"] is None          # the unattached star, 1st
    assert finishers[0]["place"] == 1
    assert finishers[0]["score_place"] is None     # ...takes no team points

    by = {t["team"]: t for t in teams}
    # D has four runners: listed (incomplete teams sort last), but no score
    assert set(by) == {"A", "B", "C", "D"}
    assert by["D"]["score"] is None and "4" in by["D"]["note"]
    # Scoring places, with the unattached runner and D's four lifted out:
    #   A 130=1  B 129=2  C 128=3  C 127=4  C 126=5  A 125=6  B 124=7
    #   A 120=8  B 119=9  A 115=10 B 114=11 A 110=12 B 109=13 A 105=14
    #   B 104=15 A 100=16 B 99=17  C 90=18  C 89=19  C 88=20  C 87=21
    assert by["A"]["score"] == 1 + 6 + 8 + 10 + 12
    assert by["B"]["score"] == 2 + 7 + 9 + 11 + 13
    assert by["C"]["score"] == 3 + 4 + 5 + 18 + 19
    assert by["A"]["scorer_places"] == [1, 6, 8, 10, 12]
    # ★ displacers count: A's sixth and seventh take places 14 and 16
    assert by["A"]["displacer_places"] == [14, 16]
    # the eighth A runner (95) was never in the field
    assert all(r["rating"] != 95.0 for r in finishers)
    assert [t["team"] for t in teams] == ["A", "B", "C", "D"]


def test_tie_is_broken_by_the_sixth_runner():
    # Places 1..14 split so both top fives sum to 28 (found by search):
    #   X  2 3 6 8 9 | 10 12      Y  1 4 5 7 11 | 13 14
    # Y has the race winner, so _score meets Y first and a stable sort on the
    # score alone would leave Y on top. The real rule: X's sixth (10th)
    # beats Y's sixth (13th), so X wins.
    places = {"X": (2, 3, 6, 8, 9, 10, 12), "Y": (1, 4, 5, 7, 11, 13, 14)}
    rows = [_runner(p, team, 200.0 - p)
            for team, ps in places.items() for p in ps]
    teams, _ = P.scoreField(rows)
    sc = {t["team"]: t["score"] for t in teams}
    assert sc["X"] == sc["Y"] == 28
    assert [t["team"] for t in teams] == ["X", "Y"]


def test_thin_ratings_are_flagged_not_changed():
    rows = [_runner(i, "A", 120.0 - i, n=(1 if i == 0 else 4)) for i in range(5)]
    teams, finishers = P.scoreField(rows)
    first = finishers[0]
    assert first["thin"] is True and first["n_races"] == 1
    assert first["rating"] == 120.0          # used exactly as published
    assert teams[0]["thin"] == 1
    assert P.isThin(P.THIN_RACES) and not P.isThin(P.THIN_RACES + 1)
    assert not P.isThin(None)


def test_division_slugs_and_order():
    assert P.divisionSlug("6A") == "6a"
    assert P.divisionSlug(" Open Small ") == "open-small"
    vals = ["5A", "AAA", "2", "10", "1", "A", "6A", "AA", "II"]
    got = sorted(vals, key=P.divisionSortKey)
    assert got.index("1") < got.index("2") < got.index("10")
    assert got.index("A") < got.index("AA") < got.index("AAA")
    assert got.index("5A") < got.index("6A")
    assert P.divisionLabel("CA", "2", "state_div") == "CA Division 2"
    assert P.divisionLabel("TX", "6A", "class") == "TX Class 6A"


def test_pick_course_is_the_habit_venue_and_this_genders_distance():
    rows = []
    for mid in (1, 2, 3):
        rows.append({"course_name": "Woodward Park", "distance": 5000,
                     "division": "Varsity Boys D1", "meet_id": mid})
        rows.append({"course_name": "Woodward Park", "distance": 4000,
                     "division": "Varsity Girls D1", "meet_id": mid})
    rows.append({"course_name": "Mt. SAC", "distance": 5000,
                 "division": "Boys", "meet_id": 9})
    boys = P.pickCourse(rows, "boys")
    girls = P.pickCourse(rows, "girls")
    assert boys == {"course_name": "Woodward Park", "distance": 5000,
                    "editions": 3}
    assert girls["distance"] == 4000
    # one edition is an anecdote: neutral course
    assert P.pickCourse([rows[-1]], "boys") is None
    assert P.pickCourse([], "boys") is None


def test_middle_school_state_meets_are_not_the_state_meet():
    assert P._NOT_HS_MEET.search("California Middle School State Championships")
    assert P._NOT_HS_MEET.search("JH State Meet")
    assert not P._NOT_HS_MEET.search("CIF State Cross Country Championships")


def test_the_two_templates_parse():
    """A Jinja syntax error would 500 the page on first view; parse both
    here, where it costs nothing."""
    import jinja2
    tdir = os.path.join(_ROOT, "racecast", "templates")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(tdir))
    for name in ("projections.html", "breakouts.html"):
        with open(os.path.join(tdir, name), encoding="utf-8") as fh:
            env.parse(fh.read())


def test_fmt_time():
    assert P.fmtTime(None) == ""
    assert P.fmtTime(942.4) == "15:42"
    assert P.fmtTime(3723) == "1:02:03"


def test_course_distance_is_the_newest_editions():
    """CA at Woodward over 6437 m (owner, 2026-10-05): decades of editions
    at an old distance must not outvote what the meet runs now."""
    old = [{"course_name": "Woodward Park", "distance": 6437, "division": "Boys D1",
            "meet_id": m} for m in range(100, 130)]
    new = [{"course_name": "Woodward Park", "distance": 5000, "division": "Boys D1",
            "meet_id": 500}]
    got = P.pickCourse(old + new, "boys")
    assert got["distance"] == 5000
    assert got["editions"] == 31
