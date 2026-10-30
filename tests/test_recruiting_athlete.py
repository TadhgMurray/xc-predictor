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
    assert cur.params[0] == (7, ['hs_m', 'hs_f'])
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
                   # weighted quantiles, not percentile_cont: each class
                   # decays by RECENCY_HALF_LIFE (_AGG_SQL's own comment)
                   ("FILTER (WHERE cum >= 0.25 * tot)", agg)])
    rows = R.schoolTable(cur, "m", "TF")
    assert [r["school"] for r in rows] == ["Stanford", "Tufts"]     # typical recruit, fastest first
    assert rows[1]["min"] == 128.0 and rows[1]["classes"] == "2023 to 2026"   # TF label years
    assert rows[0]["classes"] == "2022"
    assert set(rows[0]["times"]) == {"p25", "median", "p75"}
    assert "gender = %(gender)s AND sport = %(sport)s" in cur.sql[-1]
    assert cur.params[-1] == {"gender": "m", "sport": "TF", "min_n": R.MIN_RECRUITS,
                              "half_life": float(R.RECENCY_HALF_LIFE)}
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
    # (two more reasons are counted now, and neither applies to these rows)
    assert dropped == {"grade": 1, "school": 1, "not_college": 0, "foreign": 0,
                       "no_scale": 0} and len(rows) == 61
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
    # the states and units are resolved after the query (resolveRows), not
    # by the name's primary cluster
    assert "i.is_primary" not in sql and "p.state AS state" in sql


def test_resolve_rows_files_a_college_in_its_own_state():
    # Williams College races in CA; the name's biggest cluster is a CA high
    # school. teamState with the college pool says MA; the units are MA's.
    def ts(school, pool, state):
        if pool.startswith("college"):
            return {"Williams": "MA"}.get(school, state)
        return {"Highland": "UT"}.get(school, state)
    raw = [{"person_id": 1, "pool": "college_m", "sport": "XC", "school": "Williams", "state": "CA",
            "hs_school": "Highland", "hs_state": "ID", "division": None, "conference": None, "region": None},
           {"person_id": 2, "pool": "college_f", "sport": "TF", "school": "Williams", "state": "CA",
            "hs_school": "Highland", "hs_state": "ID", "division": "x", "conference": None, "region": None}]
    units = {("Williams", "MA"): [("TF", "NCAA DIII", "NESCAC", "New England"),
                                  ("XC", "NCAA DIII", "NESCAC XC", "New England")]}
    B.resolveRows(raw, ts, {("Highland", 2): "ID"}, units)
    assert raw[0]["state"] == "MA" and raw[0]["conference"] == "NESCAC XC" and raw[0]["unit_college"]
    # athlete 1 has no assignment: the HS teamState; athlete 2 is assigned to ID
    assert raw[0]["hs_state"] == "UT" and raw[1]["hs_state"] == "ID"
    assert raw[1]["conference"] == "NESCAC" and raw[1]["division"] == "NCAA DIII"
    # no team_state, no units: the rows stay as stored
    raw2 = [dict(raw[0], state="CA", conference=None, hs_state="ID")]
    B.resolveRows(raw2)
    assert raw2[0]["state"] == "CA" and raw2[0]["conference"] is None and not raw2[0]["unit_college"]


def _season(pid, year, school, rating, state="NC", grade=None, sport="XC"):
    return {"person_id": pid, "pool": "college_m", "sport": sport, "year": year, "school": school,
            "state": state, "grade": grade, "mean_rating": rating, "n_races": 5, "division": None,
            "conference": None, "region": None}


def test_a_transfer_is_the_first_season_at_a_second_college():
    def inst(se):
        return ({"Wake Forest University": "wake forest"}.get(se["school"], se["school"].lower()),
                se["state"])
    raw = [_season(1, 2023, "UNCG", 120.0), _season(1, 2024, "UNCG", 124.0),
           _season(1, 2025, "Wake Forest", 130.0, grade="SR-4"),
           # a spelling of one school is not a move
           _season(2, 2024, "Wake Forest", 110.0), _season(2, 2025, "Wake Forest University", 112.0),
           # back to the first school: a mislink, the whole history goes
           _season(3, 2023, "A", 100.0), _season(3, 2024, "B", 101.0), _season(3, 2025, "A", 102.0),
           # two schools in one stored year: not a move
           _season(4, 2025, "C", 100.0), _season(4, 2025, "D", 101.0),
           # an arrival before the classes kept
           _season(5, 2019, "E", 100.0), _season(5, 2020, "F", 101.0),
           # the new school is not a college
           _season(6, 2024, "G", 100.0), _season(6, 2025, "Club H", 101.0)]
    rows, dropped = B.shapeTransfers(raw, 2021, inst, gate=lambda se: "Club" not in se["school"])
    assert [(r["person_id"], r["from_school"], r["school"], r["year"]) for r in rows] == \
        [(1, "UNCG", "Wake Forest", 2025)]
    r = rows[0]
    assert r["from_rating"] == 124.0 and r["to_rating"] == 130.0 and r["grade"] == "SR-4"
    assert r["from_year"] == 2024 and r["gender"] == "m"
    assert dropped == {"back": 1, "same_year": 1, "not_college": 1, "foreign": 0, "old": 1}
    assert B.findTransfers(raw[:3], inst) == [(raw[1], raw[2])]


def test_transfers_both_ways_on_the_college_page():
    rows_in = [{"person_id": 9, "year": 2025, "grade": "SR-4", "school": "Wake Forest", "state": "NC",
                "to_rating": 130.04, "from_school": "UNCG", "from_state": "NC", "from_year": 2024,
                "from_rating": 124.0, "from_division": None, "division": None, "name": "X"}]
    cur = FakeCur([("to_regclass", [{"to_regclass": "college_transfer"}]),
                   ("t.school = %(school)s", rows_in), ("t.from_school = %(school)s", [])])
    t = R.schoolTransfers(cur, "Wake Forest", "NC", "m", "XC")
    assert t["in_summary"] == {"n": 1, "before": 124.0, "after": 130.0, "gain": 6.0}
    assert t["out"] == [] and t["out_summary"]["n"] == 0 and t["in"][0]["gain"] == 6.0
    assert any("t.state = %(state)s" in q for q in cur.sql)
    assert R.schoolTransfers(FakeCur([("to_regclass", [{"to_regclass": None}])]), "W", None, "m", "XC") is None
    js = read("racecast", "static", "recruiting.js")
    assert "Transfers in / out" in js and "r.t_in" in js
    tpl = read("racecast", "templates", "recruiting_school.html")
    assert "b.transfers" in tpl
    # their own tab, linked from the header and the board (owner, 2026-10-29)
    assert 'data-tab="transfers"' in tpl and 'data-panel="transfers"' in tpl
    assert 'href="#transfers"' in tpl and '#transfers' in js
    # the board's colleges wear their college crest
    assert 'stampCrests(rows, pool="college_" + f["gender"])' in read("racecast", "app.py")


def test_a_board_row_without_a_pool_takes_the_board_s_level(monkeypatch):
    import school_logo as L
    seen = []
    monkeypatch.setattr(L, "crestUrl", lambda s, st, px=64, level=None: seen.append(level))
    L.stampCrests([{"school": "Wake Forest", "state": "NC"}], pool="college_m")
    L.stampCrests([{"school": "Wake Forest", "state": "NC", "pool": "hs_m"}], pool="college_m")
    L.stampCrests([{"school": "Wake Forest", "state": "NC"}])
    assert seen == ["college", "hs", None]
    assert "R.recruitStates(cur, school_name)" in read("racecast", "app.py")


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
    assert 'stepsN 10f_recruits "$PY -u racecast/build_recruiting.py"' in read("deploy", "run_pipeline.sh")
    assert ".r-pill-top" in read("racecast", "static", "style.css")


def test_personal_bests_are_the_athlete_s_own_fastest_per_event():
    rows = [
        {"sport": "XC", "distance": 5000.0, "time_seconds": 950.0, "race_date": "2025-10-01"},
        {"sport": "XC", "distance": 5020.0, "time_seconds": 932.4, "race_date": "2025-11-08"},   # a 5K, within tolerance
        {"sport": "XC", "distance": 4828.0, "time_seconds": 900.0, "race_date": "2025-09-20"},   # 3 mile, its own event
        {"sport": "TF", "distance": 3200.0, "time_seconds": 561.2, "race_date": "2025-05-10"},
        {"sport": "TF", "distance": 3200.0, "time_seconds": 570.0, "race_date": "2025-04-10"},
        {"sport": "TF", "distance": 1609.3, "time_seconds": 261.0, "race_date": "2025-05-24"},   # a mile is not a 1600
        {"sport": "TF", "distance": 5000.0, "time_seconds": 940.0, "race_date": "2025-05-30"},   # track 5000, not XC
        {"sport": "TF", "distance": 400.0, "time_seconds": 52.0, "race_date": "2025-03-01"},     # not an event we quote
    ]
    cur = FakeCur([("FROM ranking_results WHERE person_id", rows)])
    prs = R.personalBests(cur, 7)
    assert [(p["key"], p["time"]) for p in prs] == [("5k", "15:32"), ("3mi", "15:00"), ("mile", "4:21.0"),
                                                     ("3200", "9:21.2"), ("5000", "15:40")]
    assert prs[0]["date"] == "2025-11-08" and prs[0]["seconds"] == 932.4
    assert R.personalBests(FakeCur([]), 7) == []
    assert '"prs": personalBests(cur, int(person_id))' in read("racecast", "recruiting.py")
    assert "rating equivalents" in read("racecast", "static", "recruiting.js")


def test_crests_before_the_school_names():
    """★ Crests on the recruiting boards (owner, 2026-10-11), the way the
    other boards draw them: the APIs stamp r.crest (school_logo.stampCrests),
    the JS draws a .school-mark only where it is, and the school page's high
    schools take the `crest` global."""
    app = read("racecast", "app.py")
    for route in ('"/api/recruiting/schools"', '"/api/recruiting")'):
        body = app[app.index("@app.route(" + route):]
        body = body[:body.index("\n@app.route(", 1)]
        assert "stampCrests(rows, pool=" in body, route
    sch = app[app.index('@app.route("/api/recruiting/schools")'):]
    assert sch.index("stampCrests(rows, pool=") < sch.index("R.suggestions(rows")   # the bands copy the rows
    for js, cell in (("recruiting.js", '<td class="rc-school">${crestMark(r.crest)}<a href="${schoolHref(r)}">'),
                     ("recruiting-search.js", '${crestMark(r.crest)}<a href="/school/')):
        src = read("racecast", "static", js)
        assert cell in src, js
        assert '`<img class="school-mark" src="${esc(url)}" alt=""`' in src, js
        assert 'width="18" height="18" loading="lazy"' in src, js
    assert "{{ crest(r.hs_school, r.hs_state, level='hs') }}" in read("racecast", "templates", "recruiting_school.html")


def test_the_board_has_a_transfers_section_on_the_public_scale(monkeypatch):
    # owner, 2026-10-30: "These should be like separate sections in that tab.
    # transfers should use a collegiate rating" -- "it doesn't use hs
    # equivalent rating": college seasons, shown as their HS-equivalents
    tpl = read("racecast", "templates", "recruiting.html")
    assert 'data-set="view" data-value="recruits"' in tpl and 'data-set="view" data-value="transfers"' in tpl
    js = read("racecast", "static", "recruiting.js")
    assert '"/api/recruiting/transfers?"' in js and "function renderTransfers(d)" in js
    app = read("racecast", "app.py")
    assert '@app.route("/api/recruiting/transfers")' in app
    assert 'stampFiveK(rows, "median", pool="hs_" + f["gender"], sport=f["sport"])' in app
    import pool_view
    monkeypatch.setattr(pool_view, "toHs", lambda r, pool, sport=None: r * 1.2 if pool == "college_m" else None)
    rows = [{"school": "Wake Forest", "state": "NC", "division": "NCAA DI", "conference": "ACC",
             "to_rating": 125.0, "from_rating": 120.0, "year": 2025, "from_school": "UNCG", "from_state": "NC"},
            {"school": "Wake Forest", "state": "NC", "division": None, "conference": None,
             "to_rating": 115.0, "from_rating": 110.0, "year": 2024, "from_school": "Elon", "from_state": "NC"}]
    cur = FakeCur([("to_regclass", [{"to_regclass": "college_transfer"}]), ("FROM college_transfer", rows)])
    R._TRANSFERS.clear()
    t = R.transferTable(cur, "m", "XC")
    by = {r["school"]: r for r in t}
    w = by["Wake Forest"]
    assert w["n"] == 2 and w["n_out"] == 0 and w["division"] == "NCAA DI"
    assert w["median"] == 144.0 and w["max"] == 150.0 and w["before"] == 138.0 and w["gain"] == 6.0
    assert w["classes"] == "2024 to 2025"
    assert by["UNCG"]["n"] == 0 and by["UNCG"]["n_out"] == 1 and by["UNCG"]["median"] is None
    R._TRANSFERS.clear()


def test_the_transfers_section_places_the_college_athlete():
    # owner, 2026-10-30: "transfers doesn't change it to a collegiate profile
    # of the person, it keeps it as their hs profile/seasons"
    seasons = [{"pool": "college_m", "sport": "XC", "year": 2025, "grade": "JR-3", "school": "Wake Forest",
                "state": "NC", "mean_rating": 125.04, "n_races": 6, "last_race": "2025-11-22",
                "grade_num": None, "name": "Test Runner"}]
    cur = FakeCur([("FROM athlete_season s", seasons)])
    s = R._athleteSubject(cur, 7, level="college")
    assert s["level"] == "college" and s["ratings"] == {"XC": 125.0} and s["grad_year"] is None
    assert cur.params[0][1] == ["college_m", "college_f"]
    cur = FakeCur([("FROM athlete_season s", [])])
    sub, err = R.subjectFrom(cur, {"athlete": "7"}, level="college")
    assert sub is None and err == "That athlete has no college season to place."
    assert R._levelPool("f", "college") == "college_f" and R._levelPool("m") == "hs_m"
    app = read("racecast", "app.py")
    assert 'R.subjectFrom(cur, request.args, level="college")' in app
    assert 'R.timesFor(rating, f["gender"])' in app
    assert "renderSubject(d);" in read("racecast", "static", "recruiting.js").split("function renderTransfers(d)")[1][:300]
