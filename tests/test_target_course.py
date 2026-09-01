"""
The course must be changeable WITHOUT changing the meet (owner, 2026-09-01).

"What would these teams run at the state course" was reachable only through a
`manual` target, which throws the meet away -- its field, its division, its
date. These pin that a meet-mode target accepts a course override, that it
changes only the venue, and that absence still means the meet's own course.
"""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
import predict                                                  # noqa: E402


class CourseCursor:
    """Returns `row` for any query; records how many ran and with what."""
    def __init__(self, row=None):
        self.row = row
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params or {}))

    def fetchone(self):
        return self.row


STATE_COURSE = {"course_name": "Woodward Park", "gps_lat": 36.85,
                "gps_long": -119.79, "altitude_meters": 100.0,
                "canonical_id": 4242, "course_difficulty": 0.061}


def test_a_named_course_replaces_only_the_venue():
    spec = {"sport": "XC", "distance_meters": 5000.0,
            "course_name": "Home Course", "canonical_id": 1,
            "course_difficulty": -0.02, "date": "2025-09-13",
            "meet_id": 7, "location_id": 9}
    cur = CourseCursor(STATE_COURSE)
    predict._applyCourse(cur, spec, "Woodward Park", "XC")

    assert spec["course_name"] == "Woodward Park"
    assert spec["canonical_id"] == 4242
    assert spec["course_difficulty"] == 0.061
    # ! EVERYTHING THAT IS NOT THE VENUE SURVIVES.
    assert spec["date"] == "2025-09-13"
    assert spec["meet_id"] == 7
    assert spec["location_id"] == 9
    print("  venue swapped, date/meet/division untouched ........ OK")


def test_the_difficulty_is_looked_up_at_the_spec_distance():
    """Difficulty is per (course, distance) -- the same course at another
    length is a different cell, so the distance must reach the query."""
    spec = {"distance_meters": 8000.0}
    cur = CourseCursor(STATE_COURSE)
    predict._applyCourse(cur, spec, "Woodward Park", "XC")
    assert cur.queries[0][1]["dist"] == 8000.0, cur.queries[0][1]
    print("  difficulty looked up at 8000m, not a default ....... OK")


def test_no_course_means_no_query_at_all():
    for name in (None, "", "   "):
        cur = CourseCursor(STATE_COURSE)
        spec = {"course_name": "Home Course"}
        predict._applyCourse(cur, spec, name, "XC")
        assert cur.queries == [], f"{name!r} still queried"
        assert spec["course_name"] == "Home Course"
    print("  absent course -> no query, meet's own kept ......... OK")


def test_track_is_refused():
    """A track meet's course is the track, and difficulty is not modelled per
    venue there -- an override would be a number with nothing behind it."""
    cur = CourseCursor(STATE_COURSE)
    spec = {"course_name": None}
    predict._applyCourse(cur, spec, "Woodward Park", "TF")
    assert cur.queries == [], cur.queries
    print("  TF override refused ................................ OK")


def test_a_miss_leaves_the_meet_alone():
    """An unknown course name must not blank the meet's own venue."""
    cur = CourseCursor(None)
    spec = {"course_name": "Home Course", "course_difficulty": -0.02}
    predict._applyCourse(cur, spec, "Nowhere At All", "XC")
    assert spec["course_name"] == "Home Course"
    assert spec["course_difficulty"] == -0.02
    print("  an unknown course does not blank the meet's ........ OK")


def test_nulls_do_not_overwrite():
    """A course with no GPS on record must not null out the meet's."""
    cur = CourseCursor({"course_name": "Bare Course", "gps_lat": None,
                        "gps_long": None, "altitude_meters": None,
                        "canonical_id": None, "course_difficulty": 0.0})
    spec = {"course_name": "Home", "gps_lat": 1.5, "gps_long": 2.5,
            "altitude_meters": 30.0}
    predict._applyCourse(cur, spec, "Bare Course", "XC")
    assert spec["course_name"] == "Bare Course"
    assert spec["gps_lat"] == 1.5, "a NULL overwrote a real coordinate"
    assert spec["altitude_meters"] == 30.0
    print("  NULL columns leave the meet's values in place ...... OK")


if __name__ == "__main__":
    for fn in [test_a_named_course_replaces_only_the_venue,
               test_the_difficulty_is_looked_up_at_the_spec_distance,
               test_no_course_means_no_query_at_all,
               test_track_is_refused,
               test_a_miss_leaves_the_meet_alone,
               test_nulls_do_not_overwrite]:
        fn()
    print("\nall target-course tests passed")
