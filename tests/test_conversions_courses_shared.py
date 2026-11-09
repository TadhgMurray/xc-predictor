"""/conversions' default courses (app._default_xc_courses) are computed
once per COURSES_TTL for the box, per level distance (2026-11-09)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401


class _Cur:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return [{"name": "Woodward Park", "canonical_id": 1, "difficulty": 0.0, "distance": None},
                {"name": "Mt. SAC", "canonical_id": 2, "difficulty": 0.03, "distance": 8000.0}]


def test_one_query_per_level_and_ttl(tmp_path, monkeypatch):
    import app as A
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    cur = _Cur()
    a = A._default_xc_courses(cur, 10, None)
    b = A._default_xc_courses(cur, 10, None)
    assert a == b and len(cur.sql) == 1
    # NULL difficulty passes through; a missing distance is the 5K default
    assert a[0] == {"course_name": "Woodward Park", "canonical_id": 1,
                    "difficulty": 0.0, "distance": 5000.0}
    a[0]["course_name"] = "edited"           # a caller's edit is its own
    assert A._default_xc_courses(cur, 10, None)[0]["course_name"] == "Woodward Park"
    A._default_xc_courses(cur, 10, "college_m")     # another level: its own query
    assert len(cur.sql) == 2 and "round(cd.distance_m" in cur.sql[1][0]
