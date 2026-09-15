"""The athlete edition of recruiting (282): the tiers, the time parsing,
the school table and the subject, offline, and the wiring pinned by source.

    python -m pytest -q tests/test_recruiting_athlete.py
"""
import contextlib
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    _db.initPool = lambda *a, **k: None
    sys.modules["database"] = _db

import recruiting as R                                          # noqa: E402
import build_recruiting as B                                    # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


DIST = {"min": 120.0, "p25": 130.0, "median": 140.0, "p75": 150.0, "max": 170.0}


# ---- pure helpers ------------------------------------------------------

def test_time_parsing_and_formatting():
    assert R.parseTime("16:32") == 992.0
    assert R.parseTime("16:32.4") == 992.4
    assert R.parseTime("4:21.5") == 261.5
    assert R.parseTime("1:02:03") == 3723.0
    assert R.parseTime("992") == 992.0
    assert R.parseTime("abc") is None and R.parseTime("") is None
    assert R.parseTime("16::32") is None and R.parseTime("5") is None
    assert R.fmtTime(992.4) == "16:32"          # a 5K to the second
    assert R.fmtTime(261.46) == "4:21.5"        # a track split to the tenth
    assert R.fmtTime(3725) == "1:02:05"
    assert R.fmtTime(None) == ""


def test_tiers_are_the_school_s_own_quartiles():
    keys = [R.tierFor(r, DIST, "NCAA DI")["key"] for r in (155, 145, 135, 125, 118, 110)]
    assert keys == ["top", "solid", "recruit", "walkon", "reach", "below"]
    # the boundaries belong to the band above
    assert R.tierFor(150, DIST)["key"] == "top"
    assert R.tierFor(120, DIST)["key"] == "walkon"
    assert R.tierFor(120 - R.REACH_PTS, DIST)["key"] == "reach"
    # the gap is to the next band up, none at the top
    t = R.tierFor(145, DIST, "NCAA DI")
    assert t["gap"] == 5.0 and t["next_label"] == R.SCHOLARSHIP_LABEL
    assert R.tierFor(155, DIST, "NCAA DI")["gap"] is None
    assert R.tierFor(None, DIST) is None and R.tierFor(140, None) is None


def test_scholarship_wording_only_where_aid_exists():
    assert R.tierFor(155, DIST, "NCAA DI")["label"] == R.SCHOLARSHIP_LABEL
    assert R.tierFor(155, DIST, "NAIA")["label"] == R.SCHOLARSHIP_LABEL
    assert R.tierFor(155, DIST, "NCAA DIII")["label"] == "Top recruit"
    assert R.tierFor(155, DIST, None)["label"] == "Top recruit"
    assert R.tierFor(145, DIST, "NCAA DIII")["next_label"] == "Top recruit"


def _rows():
    return [
        {"school": "Stanford", "state": "CA", "division": "NCAA DI", "conference": "PAC-12", "n": 12,
         "min": 150.0, "p25": 158.0, "median": 163.5, "p75": 168.0, "max": 175.0},
        {"school": "Tufts", "state": "MA", "division": "NCAA DIII", "conference": "NESCAC", "n": 8,
         "min": 128.0, "p25": 133.0, "median": 137.0, "p75": 141.0, "max": 149.0},
        {"school": "Williams", "state": "MA", "division": "NCAA DIII", "conference": "NESCAC", "n": 9,
         "min": 130.0, "p25": 134.0, "median": 139.0, "p75": 143.0, "max": 150.0},
        {"school": "Highland", "state": "UT", "division": None, "conference": None, "n": 3,
         "min": 110.0, "p25": 115.0, "median": 118.0, "p75": 121.0, "max": 125.0},
    ]


def test_suggestions_group_by_band_fastest_programme_first():
    out = R.suggestions(_rows(), 147.5)          # 2.5 under Stanford's slowest: just under
    assert [g["key"] for g in out] == ["top", "reach"]
    top = out[0]
    assert [r["school"] for r in top["schools"]] == ["Williams", "Tufts", "Highland"]
    assert all(r["tier"]["key"] == "top" for r in top["schools"])
    assert top["label"] == "Top recruit"          # no scholarship division among them
    assert [r["school"] for r in out[1]["schools"]] == ["Stanford"]
    # a rating under every school's reach band lists nothing, never "below"
    assert R.suggestions(_rows(), 100.0) == []
    assert R.suggestions(_rows(), None) == []
    both = R.suggestions(_rows(), 170.0)
    assert both[0]["label"].startswith(R.SCHOLARSHIP_LABEL)


def test_school_filters_and_sorts():
    f, err = R.parseSchoolFilters({"gender": "f", "sport": "tf", "division": "ncaa diii,NAIA",
                                   "state": "ma", "sort": "n", "min_n": "5", "q": "TUF"})
    assert err is None
    assert f["gender"] == "f" and f["sport"] == "TF" and f["divisions"] == ["NCAA DIII", "NAIA"]
    assert f["states"] == ["MA"] and f["min_n"] == 5 and f["q"] == "tuf"
    assert R.parseSchoolFilters({"gender": "x"})[1]
    assert R.parseSchoolFilters({"state": "mass"})[1]
    assert R.parseSchoolFilters({"sort": "weird"})[1]
    default, _ = R.parseSchoolFilters({})
    assert default["gender"] == "m" and default["sport"] == "XC" and default["min_n"] == R.MIN_RECRUITS
    got = R.filterSchools(_rows(), dict(default, divisions=["NCAA DIII"], sort="n"))
    assert [r["school"] for r in got] == ["Williams", "Tufts"]
    got = R.filterSchools(_rows(), dict(default, q="tuf"))
    assert [r["school"] for r in got] == ["Tufts"]
    got = R.filterSchools(_rows(), dict(default, sort="floor"))
    assert got[0]["school"] == "Stanford" and got[-1]["school"] == "Highland"
    assert R.distinctUnits(_rows(), "division") == ["NCAA DI", "NCAA DIII"]


def test_subject_rating_falls_back_across_sports():
    sub = {"ratings": {"XC": 150.0}}
    assert R.subjectRating(sub, "XC") == 150.0 and R.subjectRating(sub, "TF") == 150.0
    assert R.subjectRating({"ratings": {"XC": 150.0, "TF": 140.0}}, "TF") == 140.0
    assert R.subjectRating(None, "XC") is None


# ---- the database-facing pieces, on a fake cursor -----------------------

class FakeCur:
    def __init__(self, answers):
        self.answers, self.sql, self.params = answers, [], []
        self.rows = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        self.rows = []
        for needle, rows in self.answers:
            if needle in self.sql[-1]:
                self.rows = rows
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


def test_subject_from_the_query_string():
    seasons = [
        {"pool": "hs_f", "sport": "XC", "year": 2025, "grade": "11", "school": "Great Oak", "state": "CA",
         "mean_rating": 140.4, "n_races": 6, "last_race": "2025-11-20", "grade_num": 11, "name": "Test Athlete"},
        {"pool": "hs_f", "sport": "TF", "year": 2024, "grade": "10", "school": "Great Oak", "state": "CA",
         "mean_rating": 135.0, "n_races": 6, "last_race": "2025-05-20", "grade_num": 10, "name": "Test Athlete"},
        {"pool": "hs_f", "sport": "XC", "year": 2024, "grade": "10", "school": "Great Oak", "state": "CA",
         "mean_rating": 131.0, "n_races": 6, "last_race": "2024-11-20", "grade_num": 10, "name": "Test Athlete"},
    ]
    cur = FakeCur([("FROM athlete_season s", seasons)])
    sub, err = R.subjectFrom(cur, {"athlete": "7"})
    assert err is None and sub["kind"] == "athlete" and sub["gender"] == "f"
    assert sub["ratings"] == {"XC": 140.4, "TF": 135.0}          # the latest season per sport
    assert sub["seasons"] == {"XC": 2025, "TF": 2025}            # 2024 TF is the 2025 track season
    assert sub["grad_year"] == 2027 and sub["name"] == "Test Athlete"
    assert cur.params[0] == (7,)
    # the accounts seam: a signed-in athlete fills the slot when the query names nobody
    cur = FakeCur([("FROM athlete_season s", seasons)])
    sub, err = R.subjectFrom(cur, {}, account_person_id=7)
    assert sub and sub["person_id"] == 7
    assert R.subjectFrom(FakeCur([]), {})[0] is None
    assert R.subjectFrom(FakeCur([]), {"athlete": "7"})[1].startswith("That athlete")
    assert R.subjectFrom(FakeCur([]), {"athlete": "x"})[1]
    assert R.subjectFrom(FakeCur([]), {"event": "5k", "time": "abc"})[1].startswith("time must")
    assert R.subjectFrom(FakeCur([]), {"event": "swim", "time": "16:00"})[1].startswith("event must")
    assert R.subjectFrom(FakeCur([]), {"event": "5k", "time": "16:00", "gender": "q"})[1]


def test_school_table_reads_the_recruit_table_and_stamps_labels():
    R._SCHOOLS.clear()
    agg = [
        {"school": "Tufts", "state": "MA", "division": "NCAA DIII", "conference": "NESCAC", "n": 8, "n_hs": 2,
         "min": 128.04, "p25": 133.0, "median": 137.0, "p75": 141.0, "max": 149.0, "first_year": 2022, "last_year": 2025},
        {"school": "Stanford", "state": "CA", "division": "NCAA DI", "conference": "PAC-12", "n": 12, "n_hs": 9,
         "min": 150.0, "p25": 158.0, "median": 163.5, "p75": 168.0, "max": 175.0, "first_year": 2021, "last_year": 2021},
    ]
    cur = FakeCur([("to_regclass", [{"to_regclass": "college_recruit"}]),
                   ("percentile_cont(0.25)", agg)])
    rows = R.schoolTable(cur, "m", "TF")
    assert [r["school"] for r in rows] == ["Stanford", "Tufts"]     # typical recruit, fastest first
    assert rows[1]["min"] == 128.0 and rows[1]["classes"] == "2023 to 2026"   # TF label years
    assert rows[0]["classes"] == "2022"
    assert set(rows[0]["times"]) == {"p25", "median", "p75"}
    assert "gender = %(gender)s AND sport = %(sport)s" in cur.sql[-1]
    assert cur.params[-1] == {"gender": "m", "sport": "TF", "min_n": R.MIN_RECRUITS}
    # cached: a second call asks the database nothing
    n = len(cur.sql)
    assert R.schoolTable(cur, "m", "TF") is rows and len(cur.sql) == n
    R._SCHOOLS.clear()
    assert R.schoolTable(FakeCur([("to_regclass", [{"to_regclass": None}])]), "m", "XC") == []
    R._SCHOOLS.clear()


def test_place_rows_stamps_a_tier_per_school():
    rows = R.placeRows(_rows(), 160.0)
    assert rows[0]["tier"]["key"] == "recruit" and rows[1]["tier"]["key"] == "top"
    assert R.placeRows(_rows(), None)[0]["tier"] is None


# ---- the builder --------------------------------------------------------

def test_builder_keeps_recruits_and_measures_the_freshman_gain():
    assert B.isRecruitSeason("FR-1", "college_m") and B.isRecruitSeason(None, "college_m")
    assert B.isRecruitSeason("So", "college_f") and not B.isRecruitSeason("JR-3", "college_m")
    assert not B.isRecruitSeason("sr", "college_f") and not B.isRecruitSeason("16", "college_m")
    assert B.keepSchool("Tufts") and not B.keepSchool("Unattached") and not B.keepSchool("Virginia Tech Club")
    raw = []
    for i in range(60):
        raw.append({"person_id": i, "sport": "XC", "school": "Tufts", "pool": "college_m", "first_year": 2025,
                    "grade": "FR-1", "first_rating": 120.0, "n_races": 5, "state": "MA",
                    "hs_rating": 147.0, "hs_year": 2024, "hs_school": "X", "hs_state": "MA",
                    "division": "NCAA DIII", "conference": "NESCAC", "region": None})
    raw.append(dict(raw[0], person_id=100, hs_rating=None, hs_year=None, hs_school=None, hs_state=None))
    raw.append(dict(raw[0], person_id=101, grade="SR-4"))
    raw.append(dict(raw[0], person_id=102, school="Unattached"))
    rows, dropped, gains = B.shapeRows(raw, {"college_m": 1.25, "college_f": None})
    assert dropped == {"grade": 1, "school": 1, "no_scale": 0} and len(rows) == 61
    # 120 * 1.25 = 150 on the HS scale against 147 as seniors: a gain of 3
    assert gains[("m", "XC")] == (3.0, 60) and gains[("f", "TF")] == (0.0, 0)
    linked = next(r for r in rows if r["person_id"] == 0)
    assert linked["recruit_rating"] == 147.0 and linked["source"] == "hs" and linked["hs_equiv"] == 150.0
    proxy = next(r for r in rows if r["person_id"] == 100)
    assert proxy["recruit_rating"] == 147.0 and proxy["source"] == "college"
    assert proxy["gender"] == "m"
    # no factor and no HS season: nothing to say, dropped
    rows2, dropped2, _ = B.shapeRows([dict(raw[0], hs_rating=None)], {"college_m": None})
    assert rows2 == [] and dropped2["no_scale"] == 1


def test_builder_sql_is_first_college_seasons_with_the_boards_floor():
    sql = " ".join(B.recruitSql(True).split())
    assert "min(year) AS first_year" in sql and "c.year = f.first_year" in sql
    assert "(c.n_races >= %(min_races)s OR c.year >= 2026)" in sql
    assert "h.year < f.first_year AND h.year >= f.first_year - 2" in sql
    assert "s.division AS row_division" in sql
    assert "NULL::text AS row_division" in " ".join(B.recruitSql(False).split())
    assert "i.is_primary" in sql and "u.is_college" in sql


# ---- the wiring, by source ---------------------------------------------

def test_the_pages_are_wired():
    app = read("racecast", "app.py")
    for route in ('@app.route("/recruiting")', '@app.route("/recruiting/search")',
                  '@app.route("/api/recruiting/schools")',
                  '@app.route("/recruiting/school/<path:school_name>")',
                  '@app.route("/api/recruiting")', '@app.route("/recruit/<int:person_id>")'):
        assert route in app, route
    assert 'render_template("recruiting.html"' in app
    assert 'render_template("recruiting_search.html"' in app
    assert 'render_template("recruiting_school.html"' in app
    assert 'href="/recruiting"' in read("racecast", "templates", "_topbar.html")
    assert "/recruiting?athlete={{ athlete.person_id }}" in read("racecast", "templates", "athlete.html")
    assert "/recruiting/school/{{ school|urlencode }}" in read("racecast", "templates", "school.html")
    assert "recruiting.js" in read("racecast", "templates", "recruiting.html")
    assert "recruiting-search.js" in read("racecast", "templates", "recruiting_search.html")
    assert "/api/recruiting/schools" in read("racecast", "static", "recruiting.js")
    assert "/api/recruiting?" in read("racecast", "static", "recruiting-search.js")
    assert 'step 10f_recruits     "$PY" -u racecast/build_recruiting.py' in read("deploy", "run_pipeline.sh")
    assert ".r-pill-top" in read("racecast", "static", "style.css")
